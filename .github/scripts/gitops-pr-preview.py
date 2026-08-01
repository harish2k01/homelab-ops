#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    print("PyYAML is required. Install it with: python -m pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from exc


COMMENT_MARKER = "<!-- friday-pa:argocd-diff -->"
COMMENT_LIMIT = int(os.getenv("GITOPS_PREVIEW_COMMENT_LIMIT", "60000"))
MAX_APPS = int(os.getenv("GITOPS_PREVIEW_MAX_APPS", "30"))
MAX_APP_DIFF_CHARS = int(os.getenv("GITOPS_PREVIEW_MAX_APP_DIFF_CHARS", "45000"))
ARTIFACT_URL_PLACEHOLDER = "{{GITOPS_PREVIEW_ARTIFACT_URL}}"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclasses.dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None


@dataclasses.dataclass(frozen=True)
class Source:
    position: int
    repo_url: str
    path: str | None
    chart: str | None
    ref: str | None
    target_revision: str | None
    value_files: tuple[str, ...]
    release_name: str | None
    parameters: tuple[tuple[str, str, bool], ...]
    values: str | None
    values_object: dict[str, Any] | None
    current_repo: bool


@dataclasses.dataclass
class App:
    name: str
    manifest_path: str
    namespace: str
    sources: list[Source]
    from_head: bool


@dataclasses.dataclass
class AppDiff:
    app: App
    status: str
    summary: str
    output: str


REASON_APPLICATION_SPEC = "Application spec changed"
REASON_HELM_VALUES = "Helm values changed"


def run(args: list[str], *, cwd: Path, allow_failure: bool = False, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0 and not allow_failure:
        print(completed.stdout, file=sys.stderr)
        raise SystemExit(completed.returncode)
    return completed


def git(args: list[str], *, cwd: Path, allow_failure: bool = False) -> str:
    return run(["git", *args], cwd=cwd, allow_failure=allow_failure).stdout


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def normalize_repo_url(repo_url: str) -> str:
    value = (repo_url or "").strip()
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    value = value.removesuffix(".git").rstrip("/")
    return value.lower()


def is_under(path: str, prefix: str) -> bool:
    path = normalize_path(path)
    prefix = normalize_path(prefix)
    return path == prefix or path.startswith(prefix + "/")


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def clean_output(value: str) -> str:
    return ANSI_RE.sub("", value).replace("\r\n", "\n").strip()


def parse_name_status(output: str) -> list[Change]:
    changes: list[Change] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            changes.append(Change(status=status[0], old_path=normalize_path(parts[1]), path=normalize_path(parts[2])))
        elif len(parts) >= 2:
            changes.append(Change(status=status, path=normalize_path(parts[1])))
    return changes


def changed_paths(change: Change) -> list[str]:
    paths = [change.path]
    if change.old_path:
        paths.append(change.old_path)
    return paths


def load_yaml_document(text: str) -> dict[str, Any] | None:
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else None


def read_git_file(repo: Path, ref: str, path: str) -> str | None:
    result = run(["git", "show", f"{ref}:{path}"], cwd=repo, allow_failure=True)
    if result.returncode != 0:
        return None
    return result.stdout


def app_from_document(path: str, data: dict[str, Any], *, from_head: bool, current_repo_urls: set[str]) -> App | None:
    if data.get("kind") != "Application":
        return None

    metadata = data.get("metadata") or {}
    spec = data.get("spec") or {}
    name = metadata.get("name")
    if not name:
        return None

    destination = spec.get("destination") or {}
    namespace = destination.get("namespace") or ""

    raw_sources: list[dict[str, Any]] = []
    if isinstance(spec.get("sources"), list):
        raw_sources = [source for source in spec["sources"] if isinstance(source, dict)]
    elif isinstance(spec.get("source"), dict):
        raw_sources = [spec["source"]]

    sources: list[Source] = []
    for index, source in enumerate(raw_sources, start=1):
        helm = source.get("helm") or {}
        value_files = helm.get("valueFiles") or []
        if not isinstance(value_files, list):
            value_files = []
        parameters: list[tuple[str, str, bool]] = []
        for parameter in helm.get("parameters") or []:
            if not isinstance(parameter, dict) or not parameter.get("name"):
                continue
            parameters.append(
                (
                    str(parameter["name"]),
                    str(parameter.get("value", "")),
                    bool(parameter.get("forceString", False)),
                )
            )
        repo_url = source.get("repoURL") or ""
        sources.append(
            Source(
                position=index,
                repo_url=repo_url,
                path=normalize_path(source["path"]) if source.get("path") else None,
                chart=source.get("chart"),
                ref=source.get("ref"),
                target_revision=str(source["targetRevision"]) if source.get("targetRevision") is not None else None,
                value_files=tuple(str(value) for value in value_files),
                release_name=str(helm["releaseName"]) if helm.get("releaseName") else None,
                parameters=tuple(parameters),
                values=str(helm["values"]) if helm.get("values") is not None else None,
                values_object=helm.get("valuesObject") if isinstance(helm.get("valuesObject"), dict) else None,
                current_repo=normalize_repo_url(repo_url) in current_repo_urls,
            )
        )

    return App(
        name=str(name),
        manifest_path=normalize_path(path),
        namespace=str(namespace),
        sources=sources,
        from_head=from_head,
    )


def load_apps_from_worktree(repo: Path, current_repo_urls: set[str]) -> list[App]:
    apps: list[App] = []
    for path in sorted((repo / "argocd-apps").glob("*.y*ml")):
        data = load_yaml_document(path.read_text(encoding="utf-8"))
        app = app_from_document(str(path.relative_to(repo)), data or {}, from_head=True, current_repo_urls=current_repo_urls)
        if app:
            apps.append(app)
    return apps


def load_apps_from_git(repo: Path, ref: str, current_repo_urls: set[str]) -> list[App]:
    output = git(["ls-tree", "-r", "--name-only", ref, "argocd-apps"], cwd=repo, allow_failure=True)
    apps: list[App] = []
    for path in output.splitlines():
        path = normalize_path(path)
        if not path.endswith((".yaml", ".yml")):
            continue
        text = read_git_file(repo, ref, path)
        if text is None:
            continue
        data = load_yaml_document(text)
        app = app_from_document(path, data or {}, from_head=False, current_repo_urls=current_repo_urls)
        if app:
            apps.append(app)
    return apps


def merge_apps(head_apps: list[App], base_apps: list[App]) -> list[App]:
    apps_by_name: dict[str, App] = {app.name: app for app in base_apps}
    for app in head_apps:
        apps_by_name[app.name] = app
    return sorted(apps_by_name.values(), key=lambda item: item.name)


def helm_value_paths(app: App) -> set[str]:
    refs = {source.ref: source for source in app.sources if source.ref}
    paths: set[str] = set()

    for source in app.sources:
        for value_file in source.value_files:
            value_file = normalize_path(value_file)
            ref_match = re.match(r"^\$([^/]+)/(.+)$", value_file)
            if ref_match:
                ref_name, ref_path = ref_match.groups()
                ref_source = refs.get(ref_name)
                if ref_source and ref_source.current_repo:
                    paths.add(normalize_path(ref_path))
                continue

            if source.current_repo and source.path:
                paths.add(normalize_path(f"{source.path}/{value_file}"))

    return paths


def source_label(source: Source) -> str:
    if source.path:
        return source.path
    if source.ref:
        return f"ref:{source.ref}"
    if source.chart:
        return f"chart:{source.chart}"
    return source.repo_url


def map_changes_to_apps(changes: list[Change], apps: list[App]) -> dict[str, set[str]]:
    reasons: dict[str, set[str]] = defaultdict(set)
    apps_by_manifest = {app.manifest_path: app for app in apps}
    apps_by_name = {app.name: app for app in apps}
    app_value_paths = {app.name: helm_value_paths(app) for app in apps}

    for change in changes:
        for path in changed_paths(change):
            path_matches: dict[str, set[str]] = defaultdict(set)
            manifest_app = apps_by_manifest.get(path)
            if manifest_app:
                path_matches[manifest_app.name].add(REASON_APPLICATION_SPEC)
            elif path.startswith("argocd-apps/") and path.endswith((".yaml", ".yml")):
                name = Path(path).stem
                if name in apps_by_name:
                    path_matches[name].add(REASON_APPLICATION_SPEC)
                else:
                    path_matches[name].add(REASON_APPLICATION_SPEC)

            for app in apps:
                for source in app.sources:
                    if source.current_repo and source.path and is_under(path, source.path):
                        path_matches[app.name].add(f"`{source_label(source)}` source changed")

                if path in app_value_paths[app.name]:
                    path_matches[app.name].add(REASON_HELM_VALUES)

            parts = path.split("/")
            if len(parts) >= 2 and parts[0] in {"charts", "infra", "manifests"}:
                name = parts[1]
                if name in apps_by_name and name not in path_matches:
                    path_matches[name].add(f"`{parts[0]}/{name}` files changed")

            for app_name, app_reasons in path_matches.items():
                reasons[app_name].update(app_reasons)

    return reasons


def argo_available() -> tuple[bool, str]:
    if not shutil.which("argocd"):
        return False, "`argocd` CLI is not installed"

    if os.getenv("ARGOCD_CORE", "").lower() == "true":
        return True, ""

    if os.getenv("ARGOCD_SERVER") and os.getenv("ARGOCD_AUTH_TOKEN"):
        return True, ""

    return False, "`ARGOCD_SERVER` and `ARGOCD_AUTH_TOKEN` are not configured"


def argo_base_args() -> list[str]:
    args: list[str] = []
    if os.getenv("ARGOCD_CORE", "").lower() == "true":
        args.append("--core")
    if os.getenv("ARGOCD_SERVER"):
        args.extend(["--server", os.environ["ARGOCD_SERVER"]])
    if os.getenv("ARGOCD_GRPC_WEB", "").lower() == "true":
        args.append("--grpc-web")
    if os.getenv("ARGOCD_INSECURE", "").lower() == "true":
        args.append("--insecure")
    if os.getenv("ARGOCD_PLAINTEXT", "").lower() == "true":
        args.append("--plaintext")
    if os.getenv("ARGOCD_DIFF_REFRESH", "").lower() == "true":
        args.append("--refresh")
    return args


def revision_args(app: App, head_sha: str) -> list[str]:
    current_positions = [source.position for source in app.sources if source.current_repo]
    if len(app.sources) <= 1:
        if current_positions:
            return ["--revision", head_sha]
        return []

    args: list[str] = []
    for position in current_positions:
        args.extend(["--revisions", head_sha, "--source-positions", str(position)])
    return args


def resolve_value_file(repo: Path, source: Source, refs: dict[str, Source], value_file: str) -> Path:
    normalized = normalize_path(value_file)
    ref_match = re.match(r"^\$([^/]+)/(.+)$", normalized)
    if ref_match:
        ref_name, ref_path = ref_match.groups()
        ref_source = refs.get(ref_name)
        if not ref_source or not ref_source.current_repo:
            raise ValueError(f"cannot resolve value file {value_file!r} from this checkout")
        return repo / ref_path

    if source.current_repo and source.path:
        return repo / source.path / normalized

    raise ValueError(f"external Helm value file {value_file!r} must use a $ref source")


def render_helm_source(repo: Path, app: App, source: Source, refs: dict[str, Source], temp_dir: Path) -> str:
    if not shutil.which("helm"):
        raise ValueError("`helm` CLI is not installed")

    local_chart = repo / source.path if source.current_repo and source.path else None
    if source.repo_url.startswith("oci://"):
        chart_reference = source.repo_url
    elif source.chart:
        chart_reference = source.chart
    elif local_chart and (local_chart / "Chart.yaml").is_file():
        chart_reference = str(local_chart)
    else:
        raise ValueError(f"source {source.position} is not a renderable Helm chart")

    command = [
        "helm",
        "template",
        source.release_name or app.name,
        chart_reference,
        "--namespace",
        app.namespace or "default",
    ]
    if source.chart:
        command.extend(["--repo", source.repo_url])
    if source.target_revision and not (local_chart and chart_reference == str(local_chart)):
        command.extend(["--version", source.target_revision])

    for value_file in source.value_files:
        resolved = resolve_value_file(repo, source, refs, value_file)
        if not resolved.is_file():
            raise ValueError(f"Helm value file does not exist: {resolved.relative_to(repo)}")
        command.extend(["--values", str(resolved)])

    inline_values: list[str] = []
    if source.values:
        inline_values.append(source.values)
    if source.values_object:
        inline_values.append(yaml.safe_dump(source.values_object, sort_keys=False))
    if inline_values:
        values_path = temp_dir / f"source-{source.position}-values.yaml"
        values_path.write_text("\n".join(inline_values), encoding="utf-8")
        command.extend(["--values", str(values_path)])

    for name, value, force_string in source.parameters:
        command.extend(["--set-string" if force_string else "--set", f"{name}={value}"])

    env = os.environ.copy()
    helm_state = temp_dir / "helm"
    helm_state.mkdir(exist_ok=True)
    env["HELM_REPOSITORY_CONFIG"] = str(helm_state / "repositories.yaml")
    env["HELM_REPOSITORY_CACHE"] = str(helm_state / "repository")
    completed = run(command, cwd=repo, allow_failure=True, env=env)
    if completed.returncode != 0:
        raise ValueError(clean_output(completed.stdout) or f"`helm template` exited with {completed.returncode}")
    return clean_output(completed.stdout)


def render_directory_source(repo: Path, source: Source) -> str:
    if not source.path:
        return ""
    source_dir = repo / source.path
    if not source_dir.is_dir():
        raise ValueError(f"source directory does not exist: {source.path}")

    kustomization = next(
        (source_dir / name for name in ("kustomization.yaml", "kustomization.yml", "Kustomization") if (source_dir / name).is_file()),
        None,
    )
    if kustomization:
        if shutil.which("kubectl"):
            command = ["kubectl", "kustomize", str(source_dir)]
        elif shutil.which("kustomize"):
            command = ["kustomize", "build", str(source_dir)]
        else:
            raise ValueError(f"`{source.path}` is a Kustomize source, but neither `kubectl` nor `kustomize` is installed")
        completed = run(command, cwd=repo, allow_failure=True)
        if completed.returncode != 0:
            raise ValueError(clean_output(completed.stdout) or "Kustomize rendering failed")
        return clean_output(completed.stdout)

    documents: list[str] = []
    for path in sorted(source_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                documents.append(text)
    return "\n---\n".join(documents)


def added_resources_diff(app: App, rendered: str) -> str:
    lines = rendered.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    return f"--- /dev/null\n+++ {app.name}-rendered.yaml\n@@ -0,0 +1,{len(lines)} @@\n{body}"


def render_new_app(repo: Path, app: App) -> AppDiff:
    refs = {source.ref: source for source in app.sources if source.ref}
    rendered_sources: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="gitops-preview-") as temp_name:
            temp_dir = Path(temp_name)
            for source in app.sources:
                if source.ref and not source.path and not source.chart:
                    continue

                local_chart = bool(
                    source.current_repo
                    and source.path
                    and (repo / source.path / "Chart.yaml").is_file()
                )
                is_helm = bool(source.chart or source.repo_url.startswith("oci://") or local_chart)
                if is_helm:
                    rendered = render_helm_source(repo, app, source, refs, temp_dir)
                elif source.current_repo and source.path:
                    rendered = render_directory_source(repo, source)
                else:
                    raise ValueError(
                        f"source {source.position} cannot be rendered locally from repository {source.repo_url!r}"
                    )
                if rendered:
                    rendered_sources.append(rendered)
    except ValueError as exc:
        return AppDiff(
            app=app,
            status="error",
            summary="render failed",
            output=str(exc),
        )

    if not rendered_sources:
        return AppDiff(
            app=app,
            status="clean",
            summary="no resources rendered",
            output="",
        )

    output = added_resources_diff(app, "\n---\n".join(rendered_sources))
    return AppDiff(
        app=app,
        status="created",
        summary="resources to be created",
        output=output,
    )


def run_app_diff(
    repo: Path,
    app: App,
    head_sha: str,
    can_diff: bool,
    skip_reason: str,
    *,
    is_new: bool,
) -> AppDiff:
    if not app.from_head:
        return AppDiff(app=app, status="skipped", summary="app missing from PR head", output="")

    if is_new:
        return render_new_app(repo, app)

    if not can_diff:
        return AppDiff(app=app, status="skipped", summary=skip_reason, output="")

    command = [
        "argocd",
        *argo_base_args(),
        "app",
        "diff",
        app.name,
        "--exit-code=false",
        *revision_args(app, head_sha),
    ]
    env = os.environ.copy()
    env["KUBECTL_EXTERNAL_DIFF"] = os.getenv(
        "GITOPS_PREVIEW_EXTERNAL_DIFF",
        "diff -u -N",
    )
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    completed = run(command, cwd=repo, allow_failure=True, env=env)
    output = clean_output(completed.stdout)

    if completed.returncode not in {0, 1}:
        return AppDiff(
            app=app,
            status="error",
            summary=f"`argocd app diff` exited with {completed.returncode}",
            output=output,
        )

    if output:
        return AppDiff(app=app, status="changed", summary="rendered Kubernetes changes", output=output)

    return AppDiff(app=app, status="clean", summary="no rendered Kubernetes changes", output="")


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... truncated {len(text) - limit} characters ..."


def diff_result_label(diff: AppDiff | None) -> str:
    if not diff:
        return "not evaluated"
    if diff.status == "changed":
        return "rendered changes"
    if diff.status == "created":
        return "resources to be created"
    if diff.status == "clean":
        return "no rendered changes"
    if diff.status == "error":
        return "error"
    return diff.summary


def build_markdown(
    *,
    reasons: dict[str, set[str]],
    app_diffs: list[AppDiff],
    apps: list[App],
    new_app_names: set[str],
) -> str:
    app_by_name = {app.name: app for app in apps}
    changed_app_names = sorted(reasons)
    lines: list[str] = [
        COMMENT_MARKER,
        "## GitOps PR Preview",
        "",
        "### Summary",
        "",
        "| Application | Namespace | Changed inputs | Result |",
        "|---|---|---|---|",
    ]

    diff_by_app = {item.app.name: item for item in app_diffs}
    for app_name in changed_app_names[:MAX_APPS]:
        app = app_by_name.get(app_name)
        diff = diff_by_app.get(app_name)
        namespace = app.namespace if app else ""
        reason = "<br>".join(sorted(reasons[app_name]))
        lines.append(f"| `{escape_cell(app_name)}` | `{escape_cell(namespace or '-')}` | {reason} | {diff_result_label(diff)} |")

    if len(changed_app_names) > MAX_APPS:
        lines.append(f"| ... | ... | {len(changed_app_names) - MAX_APPS} more apps omitted | ... |")

    if not changed_app_names:
        lines.append("| - | - | No affected Application detected | - |")

    existing_app_diffs = [diff for diff in app_diffs if diff.app.name not in new_app_names]
    per_app_limit = min(
        MAX_APP_DIFF_CHARS,
        max(1000, (COMMENT_LIMIT - 15000) // max(1, len(existing_app_diffs))),
    )
    for diff in existing_app_diffs[:MAX_APPS]:
        lines.extend(["", "<details>", f"<summary>{escape_cell(diff.app.name)} - {escape_cell(diff.summary)}</summary>", ""])
        if diff.output:
            fence = "diff" if diff.status in {"changed", "created"} else "text"
            safe_output = diff.output.replace("```", "` ` `")
            lines.extend([f"```{fence}", truncate(safe_output, per_app_limit), "```"])
            if len(safe_output) > per_app_limit:
                lines.extend(
                    [
                        "",
                        f"[Download the complete rendered output]({ARTIFACT_URL_PLACEHOLDER})",
                    ]
                )
        else:
            lines.append(diff.summary.capitalize() + ".")
        lines.extend(["", "</details>"])

    return truncate("\n".join(lines).rstrip() + "\n", COMMENT_LIMIT)


def build_new_app_markdown(diff: AppDiff) -> str:
    marker = f"<!-- friday-pa:argocd-new-app:{diff.app.name} -->"
    fence = "diff" if diff.status == "created" else "text"
    prefix_lines = [
        marker,
        f"## New Argo CD Application: `{diff.app.name}`",
        "",
        f"Namespace: `{diff.app.namespace or '-'}`",
        "",
    ]
    prefix_lines.extend(
        [
            "<details open>",
            f"<summary>{escape_cell(diff.summary.capitalize())}</summary>",
            "",
            f"```{fence}",
        ]
    )
    prefix = "\n".join(prefix_lines)
    plain_suffix = "\n```\n\n</details>\n"
    artifact_suffix = (
        "\n```\n\n"
        f"[Download the complete rendered output]({ARTIFACT_URL_PLACEHOLDER})"
        "\n\n</details>\n"
    )
    safe_output = diff.output.replace("```", "` ` `") if diff.output else diff.summary.capitalize() + "."

    if len(prefix) + len(safe_output) + len(plain_suffix) <= COMMENT_LIMIT:
        return prefix + "\n" + safe_output + plain_suffix

    output_limit = max(1000, COMMENT_LIMIT - len(prefix) - len(artifact_suffix) - 200)
    return prefix + "\n" + truncate(safe_output, output_limit) + artifact_suffix


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a sticky GitOps PR preview comment.")
    parser.add_argument("--base", default=os.getenv("GITHUB_BASE_SHA"), help="Base commit SHA.")
    parser.add_argument("--head", default=os.getenv("GITHUB_HEAD_SHA") or os.getenv("GITHUB_SHA"), help="Head commit SHA.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"), help="GitHub repository, owner/name.")
    parser.add_argument("--output", default="gitops-preview.md", help="Markdown output path.")
    parser.add_argument("--output-dir", default="gitops-rendered-output", help="Directory for complete per-app output.")
    parser.add_argument(
        "--new-app-comments-dir",
        default="gitops-new-app-comments",
        help="Directory for separate new-Application comments.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path.cwd()
    if not args.base or not args.head:
        print("Both --base and --head are required.", file=sys.stderr)
        return 2
    if not args.repo:
        print("--repo or GITHUB_REPOSITORY is required.", file=sys.stderr)
        return 2

    current_repo_urls = {normalize_repo_url(f"https://github.com/{args.repo}")}
    diff_output = git(["diff", "--name-status", "--find-renames", args.base, args.head], cwd=repo)
    changes = [
        change
        for change in parse_name_status(diff_output)
        if not all(is_under(path, ".github") for path in changed_paths(change))
    ]

    head_apps = load_apps_from_worktree(repo, current_repo_urls)
    base_apps = load_apps_from_git(repo, args.base, current_repo_urls)
    apps = merge_apps(head_apps, base_apps)
    reasons = map_changes_to_apps(changes, apps)

    can_diff, skip_reason = argo_available()
    app_diffs: list[AppDiff] = []
    base_app_names = {app.name for app in base_apps}
    new_app_names = {
        app.name
        for app in head_apps
        if app.name not in base_app_names and app.name in reasons
    }
    for app_name in sorted(reasons)[:MAX_APPS]:
        app = next((item for item in apps if item.name == app_name), None)
        if not app:
            continue
        app_diff = run_app_diff(
            repo,
            app,
            args.head,
            can_diff,
            skip_reason,
            is_new=app.name not in base_app_names,
        )
        app_diffs.append(app_diff)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_output in output_dir.glob("*.diff"):
        old_output.unlink()
    for app_diff in app_diffs:
        if not app_diff.output:
            continue
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", app_diff.app.name)
        (output_dir / f"{safe_name}.diff").write_text(app_diff.output + "\n", encoding="utf-8")

    new_app_comments_dir = Path(args.new_app_comments_dir)
    new_app_comments_dir.mkdir(parents=True, exist_ok=True)
    for old_comment in new_app_comments_dir.glob("*.md"):
        old_comment.unlink()
    for app_diff in app_diffs:
        if app_diff.app.name not in new_app_names:
            continue
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", app_diff.app.name)
        (new_app_comments_dir / f"{safe_name}.md").write_text(
            build_new_app_markdown(app_diff),
            encoding="utf-8",
        )

    markdown = build_markdown(
        reasons=reasons,
        app_diffs=app_diffs,
        apps=apps,
        new_app_names=new_app_names,
    )
    Path(args.output).write_text(markdown, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
