"""Shared, read-only planning and rendering for GitOps preview and reconciliation."""
from __future__ import annotations

import copy
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager

import yaml

POLICY = "gitops-policy.yaml"
PREFIXES = ("argocd-apps/", "charts/", "infra/", "manifests/")
COMMAND_TIMEOUT = int(os.getenv("GITOPS_COMMAND_TIMEOUT", "180"))



class GitOpsError(RuntimeError):
    pass


def cluster_key(document):
    destination = document.get("spec", {}).get("destination", {})
    return destination.get("server") or destination.get("name") or "<unspecified>"


class ScopeRegistry:
    """Authoritative scope evidence, isolated by destination and API group/kind.

    API versions share an object identity and must agree about scope. Missing scope
    is an error, never an assumption that an unfamiliar resource is namespaced.
    """
    def __init__(self):
        self.scopes = {}
        self.discovery_attempted = set()
        self.cluster_anchors = {}

    def register(self, cluster, group, kind, namespaced, origin):
        key = (cluster, group or "", kind)
        old = self.scopes.get(key)
        if old is not None and old[0] != namespaced:
            raise GitOpsError(f"Conflicting resource scope for {group or 'core'}/{kind} on {cluster}: {old[1]} versus {origin}")
        self.scopes[key] = (namespaced, origin)

    def inventory(self, applications):
        for app in applications.values():
            for item in app.get("status", {}).get("resources", []):
                self.register(cluster_key(app), item.get("group", ""), item["kind"],
                              bool(item.get("namespace")), "live Argo CD inventory")

    def crds(self, cluster, items, origin, *, live=False):
        for item in items:
            if item.get("kind") != "CustomResourceDefinition" or not item.get("apiVersion", "").startswith("apiextensions.k8s.io/"):
                continue
            metadata = item.get("metadata", {})
            if live and metadata.get("uid") and metadata.get("name"):
                self.cluster_anchors.setdefault(cluster, []).append((
                    "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/" + metadata["name"], metadata["uid"]))
            spec = item.get("spec", {})
            if spec.get("scope") not in {"Cluster", "Namespaced"} or not spec.get("group") or not spec.get("names", {}).get("kind"):
                raise GitOpsError("Invalid CustomResourceDefinition scope/group/kind")
            self.register(cluster, "apiextensions.k8s.io", "CustomResourceDefinition", False, "CRD API object")
            self.register(cluster, spec["group"], spec["names"]["kind"], spec["scope"] == "Namespaced", origin)

    def discover(self, cluster):
        if cluster in self.discovery_attempted:
            return
        self.discovery_attempted.add(cluster)
        if os.getenv("GITOPS_DISCOVERY", "true").lower() == "false":
            return
        # Discovery is read-only. An explicit kubeconfig/context or in-cluster
        # service account is necessary; never silently query localhost.
        if not (os.getenv("GITOPS_DISCOVERY_KUBECONFIG") or os.getenv("KUBECONFIG") or os.getenv("KUBERNETES_SERVICE_HOST") or os.getenv("GITOPS_KUBE_CONTEXT")):
            return
        command = ["kubectl"]
        if os.getenv("GITOPS_DISCOVERY_KUBECONFIG"):
            command += ["--kubeconfig", os.environ["GITOPS_DISCOVERY_KUBECONFIG"]]
        if os.getenv("GITOPS_KUBE_CONTEXT"):
            command += ["--context", os.environ["GITOPS_KUBE_CONTEXT"]]
        in_cluster = bool(os.getenv("KUBERNETES_SERVICE_HOST")) and not any(
            os.getenv(variable) for variable in ("GITOPS_DISCOVERY_KUBECONFIG", "KUBECONFIG", "GITOPS_KUBE_CONTEXT"))
        if in_cluster:
            server = "https://kubernetes.default.svc"
        else:
            config = json.loads(run([*command, "config", "view", "--minify", "-o", "json"]))
            server = next((entry.get("cluster", {}).get("server") for entry in config.get("clusters", [])), None)
        def get(path):
            return json.loads(run([*command, "get", "--raw", path], attempts=3))
        if server != cluster:
            # Internal and external API URLs may identify the same cluster. A
            # live CRD UID read through Argo is an independent identity anchor;
            # a proposed manifest can never supply this evidence.
            anchors = self.cluster_anchors.get(cluster, [])
            if not anchors or get(anchors[0][0]).get("metadata", {}).get("uid") != anchors[0][1]:
                raise GitOpsError(f"Kubernetes discovery destination mismatch: configured {server}, requested {cluster}; configure the matching context")
        paths = [f"/api/{version}" for version in get("/api").get("versions", [])]
        paths += [f"/apis/{version['groupVersion']}" for group in get("/apis").get("groups", []) for version in group.get("versions", [])]
        for path in paths:
            discovery = get(path)
            group_version = discovery.get("groupVersion", "")
            group = group_version.split("/", 1)[0] if "/" in group_version else ""
            for resource in discovery.get("resources", []):
                if "/" not in resource.get("name", "") and resource.get("kind") and isinstance(resource.get("namespaced"), bool):
                    self.register(cluster, group, resource["kind"], resource["namespaced"], "Kubernetes API discovery")

    def namespaced(self, cluster, group, kind):
        key = (cluster, group, kind)
        if key not in self.scopes:
            self.discover(cluster)
        if key not in self.scopes:
            raise GitOpsError(f"Unknown resource scope for {group or 'core'}/{kind} on {cluster}; provide Kubernetes discovery access or a rendered/live CRD definition")
        return self.scopes[key][0]

    def normalize(self, items, document):
        result = copy.deepcopy(items)
        cluster = cluster_key(document)
        namespace = document.get("spec", {}).get("destination", {}).get("namespace") or "default"
        for item in result:
            group, kind, current_namespace, _ = resource_key(item)
            if self.namespaced(cluster, group, kind):
                item["metadata"]["namespace"] = current_namespace or namespace
            elif current_namespace:
                raise GitOpsError(f"Cluster-scoped resource {group or 'core'}/{kind} cannot declare namespace {current_namespace}")
            else:
                item["metadata"].pop("namespace", None)
        # Argo CD multi-source precedence is last source wins, across API versions.
        return list({resource_key(item): item for item in result}.values())


@lru_cache(maxsize=32)
def cluster_capabilities(destination):
    data = json.loads(argo("cluster", "get", destination, "-o", "json", attempts=3))
    info = data.get("info", {})
    version = data.get("serverVersion") or info.get("serverVersion")
    versions = info.get("apiVersions")
    if not version or not isinstance(versions, list) or not versions:
        raise GitOpsError("Cluster capabilities unavailable; configure GITOPS_KUBE_VERSION and GITOPS_API_VERSIONS")
    return version, tuple(versions)


def run(args, cwd=None, *, attempts=1, timeout=COMMAND_TIMEOUT):
    """Keep credentials and manifest bodies out of logs; retry only read operations."""
    for attempt in range(attempts):
        try:
            result = subprocess.run(args, cwd=cwd, text=True, encoding="utf-8",
                                    errors="replace", capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if attempt + 1 == attempts:
                raise GitOpsError(f"{args[0]} {args[1]} timed out after {timeout}s") from exc
        else:
            if result.returncode == 0:
                return result.stdout
            if attempt + 1 == attempts:
                # Some errors contain signed download URLs. Do not publish their queries.
                message = re.sub(r"(https?://[^\s?\"]+)\?[^\s\"]+", r"\1?<redacted>",
                                 result.stderr or result.stdout)
                raise GitOpsError(message.strip()[:3000] or f"{args[0]} failed ({result.returncode})")
        time.sleep(2 * (attempt + 1))
    raise GitOpsError("command failed")


def argo(*args, attempts=1, timeout=COMMAND_TIMEOUT):
    options = []
    if os.getenv("ARGOCD_SERVER"):
        options += ["--server", os.environ["ARGOCD_SERVER"]]
    for variable, flag in (("ARGOCD_CORE", "--core"), ("ARGOCD_GRPC_WEB", "--grpc-web"),
                           ("ARGOCD_INSECURE", "--insecure"), ("ARGOCD_PLAINTEXT", "--plaintext")):
        if os.getenv(variable, "").lower() == "true":
            options.append(flag)
    return run(["argocd", *options, *args], attempts=attempts, timeout=timeout)


def git(repo, *args):
    return run(["git", *args], cwd=repo).strip()


def merge_revision(repo, base, head):
    """Render the merge result, including newer base changes, without changing files."""
    base = git(repo, "rev-parse", f"{base}^{{commit}}")
    head = git(repo, "rev-parse", f"{head}^{{commit}}")
    if base == head:
        return head
    tree = git(repo, "merge-tree", "--write-tree", base, head).splitlines()[0]
    env = {**os.environ, "GIT_AUTHOR_NAME": "GitOps Preview", "GIT_AUTHOR_EMAIL": "preview@localhost",
           "GIT_COMMITTER_NAME": "GitOps Preview", "GIT_COMMITTER_EMAIL": "preview@localhost",
           "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}
    result = subprocess.run(["git", "commit-tree", tree, "-p", base, "-p", head, "-m", "GitOps proposed merge"],
                            cwd=repo, env=env, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise GitOpsError("Cannot construct proposed merge revision")
    return result.stdout.strip()


def inside(root, path):
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise GitOpsError(f"Path escapes source repository: {path}")
    return candidate


@contextmanager
def snapshot(repo, revision):
    sha = git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
    with tempfile.TemporaryDirectory(prefix="gitops-snapshot-") as directory:
        root = Path(directory)
        archive = subprocess.run(["git", "archive", sha], cwd=repo, capture_output=True, timeout=180)
        if archive.returncode:
            raise GitOpsError("Cannot export repository revision")
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as stream:
            stream.extractall(root, filter="data")
        yield root


def kustomize_dependencies(root, source_path):
    pending, seen, dependencies = [inside(root, source_path)], set(), set()
    while pending:
        directory = pending.pop()
        if directory in seen:
            continue
        seen.add(directory)
        config = next((directory / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")
                       if (directory / name).is_file()), None)
        if config is None:
            continue
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        references = []
        for field in ("resources", "bases", "components", "crds", "configurations", "transformers", "patchesStrategicMerge"):
            references.extend(value for value in data.get(field, []) if isinstance(value, str) and "\n" not in value)
        for field in ("patches", "patchesJson6902", "replacements"):
            references.extend(item["path"] for item in data.get(field, []) if isinstance(item, dict) and item.get("path"))
        for field in ("configMapGenerator", "secretGenerator"):
            for generator in data.get(field, []):
                references.extend(value.split("=", 1)[-1] for value in generator.get("files", []))
                references.extend(generator.get("envs", []))
                if generator.get("env"):
                    references.append(generator["env"])
        for reference in references:
            if "://" in reference or "?ref=" in reference or reference.startswith("git@"):
                raise GitOpsError("Remote Kustomize references require a declared Git source or vendored base")
            target = inside(root, (directory / reference).relative_to(root))
            dependencies.add(target.relative_to(root).as_posix())
            if target.is_dir():
                pending.append(target)
    return sorted(dependencies)


def load_apps(root, repository=None):
    apps = {}
    for path in sorted((root / "argocd-apps").rglob("*")):
        if path.suffix not in {".yaml", ".yml"} or not path.is_file():
            continue
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not document:
                continue
            if document.get("kind") != "Application":
                raise GitOpsError(f"Unsupported object in {path.relative_to(root)}; expected Application")
            name = document.get("metadata", {}).get("name", "")
            if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", name):
                raise GitOpsError(f"Invalid Application name in {path.relative_to(root)}")
            if name in apps:
                raise GitOpsError(f"Duplicate Application name: {name}")
            if document.get("metadata", {}).get("namespace", "argocd") != "argocd":
                raise GitOpsError("Applications outside the argocd namespace require a separate planner")
            dependencies = []
            for source in sources(document):
                if source.get("path") and repo_url(source.get("repoURL", "")) == repo_url(repository or ""):
                    dependencies.extend(kustomize_dependencies(root, source["path"]))
            apps[name] = {"document": document, "path": path.relative_to(root).as_posix(), "sourceDependencies": dependencies}
    return apps


def sources(document):
    spec = document.get("spec", {})
    result = spec.get("sources") or ([spec["source"]] if spec.get("source") else [])
    if not result:
        raise GitOpsError("Application has no sources")
    return result


def repo_url(value):
    return value.replace("git@github.com:", "https://github.com/").removesuffix(".git").rstrip("/").lower()


def under(path, prefix):
    prefix = prefix.rstrip("/")
    return prefix in {"", "."} or path == prefix or path.startswith(prefix + "/")


def affected(paths, app, repository):
    if app["path"] in paths:
        return True
    if any(under(path, dependency) for path in paths for dependency in app.get("sourceDependencies", [])):
        return True
    srcs = sources(app["document"])
    refs = {source.get("ref"): source for source in srcs if source.get("ref")}
    for source in srcs:
        if repo_url(source.get("repoURL", "")) == repo_url(repository) and source.get("path"):
            if any(under(path, source["path"]) for path in paths):
                return True
        helm = source.get("helm") or {}
        values = [*helm.get("valueFiles", []), *[parameter["path"] for parameter in helm.get("fileParameters", [])]]
        for value in values:
            if value.startswith("$"):
                ref, _, path = value[1:].partition("/")
                if repo_url(refs.get(ref, {}).get("repoURL", "")) == repo_url(repository) and path in paths:
                    return True
            elif repo_url(source.get("repoURL", "")) == repo_url(repository) and source.get("path"):
                path = str(Path(source["path"], value)).replace("\\", "/")
                if path in paths:
                    return True
    return False


def plan(repo, base, head, repository, *, all_apps=False):
    base = git(repo, "rev-parse", f"{base}^{{commit}}")
    head = git(repo, "rev-parse", f"{head}^{{commit}}")
    # --no-renames includes both endpoints and avoids ambiguous tab-delimited rename parsing.
    paths = set(git(repo, "diff", "--no-renames", "--name-only", base, head).splitlines())
    with snapshot(repo, base) as root:
        old = load_apps(root, repository)
        old_policy = yaml.safe_load((root / POLICY).read_text()) if (root / POLICY).exists() else {}
    with snapshot(repo, head) as root:
        new = load_apps(root, repository)
        policy = yaml.safe_load((root / POLICY).read_text()) if (root / POLICY).exists() else {}
    policy = policy or {}
    old_policy = old_policy or {}
    if not isinstance(policy, dict) or any(not isinstance(policy.get(key, {}), dict) for key in ("deletions", "dependencies")):
        raise GitOpsError("gitops-policy.yaml must contain deletions and dependencies mappings")
    if set(policy) - {"deletions", "dependencies"}:
        raise GitOpsError("Unknown gitops-policy.yaml field")
    for name, decision in policy.get("deletions", {}).items():
        if not isinstance(name, str) or not isinstance(decision, str) or decision not in {"retain", "delete"}:
            raise GitOpsError("Deletion decisions must map Application names to retain or delete")
    for name, dependencies in policy.get("dependencies", {}).items():
        if not isinstance(name, str) or not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            raise GitOpsError("Dependencies must map Application names to lists of Application names")
    entries = []
    policy_names = set()
    if POLICY in paths:
        for section in ("dependencies", "deletions"):
            before_values, after_values = old_policy.get(section, {}) or {}, policy.get(section, {}) or {}
            policy_names.update(name for name in before_values.keys() | after_values.keys()
                                if before_values.get(name) != after_values.get(name))
    for name in sorted(old.keys() | new.keys()):
        before, after = old.get(name), new.get(name)
        if not (all_apps or name in policy_names or any(affected(paths, app, repository) for app in (before, after) if app)):
            continue
        action = "delete" if after is None else "create" if before is None else "update"
        notices = []
        if before and after:
            for key in ("destination", "project", "syncPolicy", "ignoreDifferences"):
                if before["document"]["spec"].get(key) != after["document"]["spec"].get(key):
                    notices.append(f"{key} changed")
        decision = policy.get("deletions", {}).get(name)
        if action == "delete":
            if decision not in {"retain", "delete"}:
                notices.append("BLOCKED: set deletions.<application> to retain or delete in gitops-policy.yaml")
            else:
                notices.append(f"Explicit deletion decision: {decision}")
        entry = {"name": name, "action": action, "before": before, "after": after,
                 "deletion": decision, "notices": notices,
                 "dependencies": policy.get("dependencies", {}).get(name, [])}
        if not isinstance(entry["dependencies"], list) or not all(isinstance(x, str) for x in entry["dependencies"]):
            raise GitOpsError(f"dependencies.{name} must be a list of Application names")
        metadata = (after or before)["document"].get("metadata", {})
        entry["wave"] = int((metadata.get("annotations") or {}).get("argocd.argoproj.io/sync-wave", "0"))
        entries.append(entry)
    names = set(new) | set(old)
    for entry in entries:
        if set(entry["dependencies"]) - names:
            raise GitOpsError(f"Unknown dependency for {entry['name']}")
    ordered = []
    pending = list(entries)
    while pending:
        ready = [entry for entry in pending if not any(
            other["name"] in entry["dependencies"] or other["wave"] < entry["wave"]
            for other in pending)]
        if not ready:
            raise GitOpsError("Application dependencies or sync waves contain a cycle")
        for entry in sorted(ready, key=lambda item: item["name"]):
            ordered.append(entry)
            pending.remove(entry)
    unmapped = sorted(path for path in paths if path.startswith(PREFIXES) and
                      not any(affected({path}, app, repository) for app in [*old.values(), *new.values()]))
    return {"base": base, "head": head, "repository": repository, "applications": ordered,
            "unmapped": unmapped}


class Renderer:
    def __init__(self, repo, revision, repository, temp, scopes=None):
        self.repo, self.revision, self.repository, self.temp = repo, revision, repository, temp
        self.identities = []
        self.cache = {}
        self.charts = {}
        self.scopes = scopes if scopes is not None else ScopeRegistry()
        (temp / "helm-cache").mkdir(exist_ok=True)
        self.helm_flags = ["--repository-config", str(temp / "helm-repositories.yaml"),
                           "--repository-cache", str(temp / "helm-cache")]

    def source_root(self, source):
        url = source.get("repoURL", "")
        revision = source.get("targetRevision", "HEAD")
        if repo_url(url) == repo_url(self.repository):
            default_branch = os.getenv("GITOPS_DEFAULT_BRANCH", "main")
            resolved = self.revision if revision in {default_branch, "HEAD"} else git(self.repo, "rev-parse", f"{revision}^{{commit}}")
            key = (url, resolved)
            if key not in self.cache:
                directory = self.temp / f"git-{len(self.cache)}"
                directory.mkdir()
                archive = subprocess.run(["git", "archive", resolved], cwd=self.repo, capture_output=True, timeout=180)
                if archive.returncode:
                    raise GitOpsError(f"Unable to export source {url}@{resolved}")
                with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as stream:
                    stream.extractall(directory, filter="data")
                self.cache[key] = directory
        else:
            # Git is executed without a shell; repository hooks/submodules are never run.
            key = (url, revision)
            if key not in self.cache:
                directory = self.temp / f"git-{len(self.cache)}"
                run(["git", "clone", "--no-checkout", "--", url, str(directory)], attempts=3)
                try:
                    resolved = git(directory, "rev-parse", f"{revision}^{{commit}}")
                except GitOpsError:
                    resolved = git(directory, "rev-parse", f"origin/{revision}^{{commit}}")
                run(["git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", resolved], cwd=directory)
                self.cache[key] = directory
            resolved = git(self.cache[key], "rev-parse", "HEAD")
        self.identities.append({"repository": url, "declaredRevision": revision, "resolvedRevision": resolved})
        return self.cache[key]

    def render(self, app, *, normalize=True):
        document = app["document"]
        srcs = sources(document)
        refs = {source["ref"]: source for source in srcs if source.get("ref")}
        if len(refs) != sum(bool(source.get("ref")) for source in srcs):
            raise GitOpsError("Duplicate source ref")
        result = []
        for index, source in enumerate(srcs):
            if set(source) - {"repoURL", "targetRevision", "path", "chart", "ref", "name", "helm", "directory", "kustomize"}:
                raise GitOpsError(f"Unsupported source options in source {index + 1}")
            is_chart = source.get("chart") or source.get("repoURL", "").startswith("oci://")
            root = None if is_chart else self.source_root(source)
            if not is_chart and not source.get("path"):
                if source.get("ref"):
                    continue
                raise GitOpsError("Git resource source requires a path")
            directory = inside(root, source["path"]) if root else None
            is_chart = is_chart or (directory / "Chart.yaml").is_file()
            if is_chart:
                text = self.helm(document, source, refs, directory, index)
            elif any((directory / name).exists() for name in ("kustomization.yaml", "kustomization.yml", "Kustomization")):
                if source.get("kustomize"):
                    raise GitOpsError("Application-level Kustomize overrides require an explicit renderer; move them into the kustomization")
                text = run(["kubectl", "kustomize", str(directory)])
            else:
                settings = source.get("directory") or {}
                if set(settings) - {"recurse", "include", "exclude"}:
                    raise GitOpsError("Unsupported directory source options")
                files = sorted(directory.rglob("*") if settings.get("recurse") else directory.glob("*"))
                texts = []
                for path in files:
                    if not path.is_file():
                        continue
                    relative = path.relative_to(directory).as_posix()
                    if settings.get("include") and not path.match(settings["include"]):
                        continue
                    if settings.get("exclude") and path.match(settings["exclude"]):
                        continue
                    if path.suffix in {".jsonnet", ".libsonnet"}:
                        raise GitOpsError(f"Unsupported Jsonnet source: {relative}")
                    if path.suffix in {".yaml", ".yml", ".json"}:
                        texts.append(path.read_text(encoding="utf-8"))
                text = "\n---\n".join(texts)
            result.extend(resources(text))
        self.scopes.crds(cluster_key(document), result, "rendered CRD")
        return self.scopes.normalize(result, document) if normalize else result


    def helm(self, document, source, refs, directory, index):
        settings = source.get("helm") or {}
        allowed = {"releaseName", "valueFiles", "values", "valuesObject", "parameters", "fileParameters",
                   "ignoreMissingValueFiles", "skipCrds", "skipTests", "skipSchemaValidation", "kubeVersion", "apiVersions", "version"}
        if set(settings) - allowed or settings.get("version", "v3") != "v3":
            raise GitOpsError("Unsupported Helm settings; preview cannot safely approximate them")
        chart_dir = Path(tempfile.mkdtemp(prefix=f"chart-{index}-", dir=self.temp))
        url = source.get("repoURL", "")
        if directory:
            chart = str(directory)
        else:
            revision = str(source.get("targetRevision", ""))
            if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", revision):
                raise GitOpsError("External charts must specify an exact version")
            pull = ["helm", "pull", url if url.startswith("oci://") else source["chart"],
                    "--version", revision, "--destination", str(chart_dir)]
            if not url.startswith("oci://"):
                pull += ["--repo", url]
            key = (url, source.get("chart"), revision)
            if key not in self.charts:
                run([*pull, *self.helm_flags], attempts=3)
                archives = list(chart_dir.glob("*.tgz"))
                if len(archives) != 1:
                    raise GitOpsError("Expected one downloaded Helm archive")
                self.charts[key] = archives[0]
            archive = self.charts[key]
            self.identities.append({"repository": url, "chart": source.get("chart"), "version": revision,
                                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
            chart = str(archive)
        namespace = document["spec"].get("destination", {}).get("namespace") or "default"
        command = ["helm", "template", settings.get("releaseName", document["metadata"]["name"]), chart,
                   "--namespace", namespace, "--include-crds"]
        if settings.get("skipCrds"):
            command.remove("--include-crds")
        if settings.get("skipTests"):
            command.append("--skip-tests")
        if settings.get("skipSchemaValidation"):
            command.append("--skip-schema-validation")
        kube_version = settings.get("kubeVersion") or os.getenv("GITOPS_KUBE_VERSION")
        api_versions = tuple(filter(None, os.getenv("GITOPS_API_VERSIONS", "").split(",")))
        if not kube_version or not api_versions:
            destination = document["spec"].get("destination", {})
            discovered_version, discovered_apis = cluster_capabilities(destination.get("server") or destination.get("name"))
            kube_version = kube_version or discovered_version
            api_versions = api_versions or discovered_apis
        self.identities.append({"kubeVersion": kube_version, "apiVersions": list(api_versions)})
        command += ["--kube-version", str(kube_version)]
        for api in [*api_versions, *settings.get("apiVersions", [])]:
            command += ["--api-versions", api]

        def value_path(value):
            if value.startswith("$"):
                ref, separator, relative = value[1:].partition("/")
                if not separator or ref not in refs:
                    raise GitOpsError(f"Unknown values reference: {value}")
                return inside(self.source_root(refs[ref]), relative)
            if not directory:
                # Values inside a packaged chart are resolved from a safely extracted archive.
                extracted = chart_dir / "unpacked"
                if not extracted.exists():
                    extracted.mkdir()
                    with tarfile.open(chart) as stream:
                        stream.extractall(extracted, filter="data")
                roots = list(extracted.iterdir())
                return inside(roots[0], value)
            return inside(directory, value)

        for value in settings.get("valueFiles", []):
            path = value_path(value)
            if not path.is_file():
                if settings.get("ignoreMissingValueFiles"):
                    continue
                raise GitOpsError(f"Missing Helm values file: {value}")
            command += ["--values", str(path)]
        # valuesObject overrides values, matching Argo CD precedence.
        inline = settings.get("valuesObject") if "valuesObject" in settings else settings.get("values")
        if inline is not None:
            path = chart_dir / "inline.yaml"
            path.write_text(inline if isinstance(inline, str) else yaml.safe_dump(inline), encoding="utf-8")
            command += ["--values", str(path)]
        for parameter in settings.get("parameters", []):
            command += ["--set-string" if parameter.get("forceString") else "--set",
                        f"{parameter['name']}={parameter['value']}"]
        for parameter in settings.get("fileParameters", []):
            command += ["--set-file", f"{parameter['name']}={value_path(parameter['path'])}"]
        if directory and (directory / "Chart.lock").exists():
            metadata = yaml.safe_load((directory / "Chart.yaml").read_text())
            for dependency in metadata.get("dependencies", []):
                repository = dependency.get("repository", "")
                if repository.startswith("https://"):
                    name = "source-" + hashlib.sha256(repository.encode()).hexdigest()[:12]
                    run(["helm", "repo", "add", name, repository, *self.helm_flags], attempts=3)
            run(["helm", "dependency", "build", str(directory), *self.helm_flags], attempts=3)
        elif directory and yaml.safe_load((directory / "Chart.yaml").read_text()).get("dependencies"):
            raise GitOpsError("Local chart dependencies require Chart.lock")
        return run([*command, *self.helm_flags], attempts=3)


class KubernetesLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
    # Kubernetes treats a plain '=' as a string. PyYAML's YAML 1.1 resolver
    # assigns an unsupported internal value tag (seen in Prometheus CRD enums).
    # Isolate this compatibility fix instead of changing PyYAML globally.
    yaml_implicit_resolvers = {
        first: [(tag, pattern) for tag, pattern in entries if tag != "tag:yaml.org,2002:value"]
        for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def resources(text):
    result = []
    for item in yaml.load_all(text, Loader=KubernetesLoader):
        if item is None:
            continue
        if not isinstance(item, dict) or not item.get("apiVersion") or not item.get("kind"):
            raise GitOpsError("Renderer returned an invalid Kubernetes object")
        if item["kind"] == "List":
            result.extend(resources(yaml.safe_dump_all(item.get("items", []))))
        elif not item.get("metadata", {}).get("name") and not item.get("metadata", {}).get("generateName"):
            raise GitOpsError("Rendered object has no metadata.name or generateName")
        else:
            result.append(item)
    return result


def resource_key(item):
    metadata = item.get("metadata") or {}
    return (item.get("apiVersion", "").split("/")[0] if "/" in item.get("apiVersion", "") else "",
            item.get("kind", ""), metadata.get("namespace") or "", metadata.get("name") or metadata.get("generateName") or "")


def public_resources(items):
    result = copy.deepcopy(items)
    for item in result:
        item.pop("status", None)
        metadata = item.get("metadata", {})
        for field in ("managedFields", "resourceVersion", "uid", "creationTimestamp", "generation"):
            metadata.pop(field, None)
        annotations = metadata.get("annotations") or {}
        annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
        annotations.pop("argocd.argoproj.io/tracking-id", None)
        if item.get("kind") == "Application":
            spec = item.get("spec", {})
            for source in spec.get("sources", [spec.get("source", {})]):
                helm = source.get("helm", {})
                for field in ("values", "valuesObject", "parameters", "fileParameters"):
                    if field in helm:
                        # Full inputs stay in temporary render directories, never artifacts.
                        helm[field] = "<input redacted; see rendered resource diff>"
            if isinstance(spec.get("sources"), list):
                # Position is significant in Argo CD. A display map lets dyff
                # show targetRevision edits instead of replacing whole list entries.
                spec["sources"] = {f"source-{index}": source for index, source in enumerate(spec["sources"], 1)}
        if item.get("kind") == "Secret":
            for field in ("data", "stringData"):
                if field in item:
                    item[field] = {key: "<redacted>" for key in (item[field] or {})}
    return sorted(result, key=resource_key)


def manifest_diff(before, after, before_label, after_label):
    old = {resource_key(item): item for item in public_resources(before)}
    new = {resource_key(item): item for item in public_resources(after)}
    original_old = {resource_key(item): item for item in before}
    original_new = {resource_key(item): item for item in after}
    sections = []
    with tempfile.TemporaryDirectory(prefix="gitops-dyff-") as directory:
        left, right = Path(directory) / "before.yaml", Path(directory) / "after.yaml"
        for key in sorted(old.keys() | new.keys()):
            group, kind, namespace, name = key
            identity = f"{group + '/' if group else ''}{kind} {namespace + '/' if namespace else ''}{name}"
            if key not in old:
                annotations = new[key].get("metadata", {}).get("annotations") or {}
                hook = " (sync hook)" if annotations.get("argocd.argoproj.io/hook") or annotations.get("helm.sh/hook") else ""
                sections.append(f"+ ADDED {identity}{hook}")
            elif key not in new:
                sections.append(f"- DELETED {identity}")
            elif old[key] != new[key]:
                left.write_text(yaml.safe_dump(old[key], sort_keys=True), encoding="utf-8")
                right.write_text(yaml.safe_dump(new[key], sort_keys=True), encoding="utf-8")
                diff = run(["dyff", "--color", "off", "between", "--output", "github", "--omit-header", str(left), str(right)])
                if diff.strip():
                    sections.append(f"===== {identity} (modified) =====\n{diff.strip()}")
            # Redaction must not hide changes in otherwise equal resource inputs.
            if key in old and key in new and original_old[key] != original_new[key]:
                if kind == "Secret" and any(original_old[key].get(field) != original_new[key].get(field) for field in ("data", "stringData")):
                    sections.append(f"! {identity}: Secret content changed (values redacted).")
                elif kind == "Application" and old[key] == new[key]:
                    sections.append(f"! {identity}: Application inline configuration changed (inputs redacted).")
    return "\n\n".join(sections) + ("\n" if sections else "")


def change_counts(before, after):
    old = {resource_key(item): item for item in public_resources(before)}
    new = {resource_key(item): item for item in public_resources(after)}
    secret_old = {resource_key(item): (item.get("data"), item.get("stringData")) for item in before if item.get("kind") == "Secret"}
    secret_new = {resource_key(item): (item.get("data"), item.get("stringData")) for item in after if item.get("kind") == "Secret"}
    return {"added": len(new.keys() - old.keys()), "deleted": len(old.keys() - new.keys()),
            "modified": sum(old[key] != new[key] or secret_old.get(key) != secret_new.get(key) for key in old.keys() & new.keys())}


def predict_sync(live_app, proposed_app, live, desired):
    """Predict managed resource changes, preserving resources sync will not prune.

    Existing resources use Argo's dry-run normalizer rather than treating API
    defaults as deleted fields. New resources and lifecycle decisions are local.
    """
    old = {resource_key(item): copy.deepcopy(item) for item in live}
    new = {resource_key(item): copy.deepcopy(item) for item in desired}
    notices = []
    deleting = proposed_app is None
    prune = deleting or bool((proposed_app.get("spec", {}).get("syncPolicy", {}).get("automated") or {}).get("prune"))
    for key in old.keys() - new.keys():
        annotations = old[key].get("metadata", {}).get("annotations") or {}
        options = {part.partition("=")[0].strip(): part.partition("=")[2].strip().lower()
                   for part in annotations.get("argocd.argoproj.io/sync-options", "").split(",")}
        setting = options.get("Delete" if deleting else "Prune", "true")
        reason = "pruning is disabled" if not prune else "resource retention is configured" if setting == "false" else "removal needs confirmation" if setting == "confirm" else ""
        if reason:
            new[key] = copy.deepcopy(old[key])
            _, kind, namespace, name = key
            notices.append(f"Retained {kind} {namespace + '/' if namespace else ''}{name}: {reason}.")
    matched = []
    for key in old.keys() & new.keys():
        # Hooks execute as sync tasks, not ordinary persistent workload updates.
        annotations = new[key].get("metadata", {}).get("annotations") or {}
        if annotations.get("argocd.argoproj.io/hook") or annotations.get("helm.sh/hook"):
            _, kind, namespace, name = key
            notices.append(f"Sync hook {kind} {namespace + '/' if namespace else ''}{name} runs during sync.")
            old.pop(key)
            new.pop(key)
        elif any(change_counts([old[key]], [new[key]]).values()):
            matched.append(key)
    if matched:
        if live_app is None or proposed_app is None:
            raise GitOpsError("Existing resources need a live Application for native comparison")
        import gitops_native_diff as native
        try:
            normalized, predicted = native.predict(live_app, proposed_app,
                                                    [old[key] for key in matched], [new[key] for key in matched])
        except native.NativeDiffError as exc:
            raise GitOpsError(str(exc)) from exc
        old.update({resource_key(item): item for item in normalized})
        new.update({resource_key(item): item for item in predicted})
    return list(old.values()), list(new.values()), notices


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")


def block(value, limit=9000):
    return "```diff\n" + value[:limit].replace("```", "` ` `") + ("\n… See full artifact.\n" if len(value) > limit else "\n") + "```\n"


def summary(plan_data, rows, title, marker):
    lines = [marker, f"## {title}", "", f"**Revision:** `{plan_data['head']}`", "",
             "| Application | Action | Result |", "|---|---|---|"]
    for row in rows:
        lines.append(f"| `{cell(row['name'])}` | {cell(row['action'])} | {cell(row['result'])} |")
    if not rows:
        state = "Not evaluated" if plan_data.get("fatal") or plan_data.get("applications") else "No deployment changes"
        lines.append(f"| — | — | {state} |")
    if plan_data.get("unmapped"):
        lines += ["", "**Unmapped deployment files (validation failed):**", *[f"- `{cell(x)}`" for x in plan_data["unmapped"]]]
    lines += ["", "Merge authorizes reconciliation. Deployment follows GitHub environment protection settings.",
              "Full per-Application comparisons and source identities are in the workflow artifact."]
    return "\n".join(lines) + "\n"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
