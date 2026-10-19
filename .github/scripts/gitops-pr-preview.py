#!/usr/bin/env python3
"""Render complete base/proposed Applications without changing the cluster."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import yaml
import gitops as g


def inventory():
    return {app["metadata"]["name"]: app for app in json.loads(g.argo("app", "list", "-o", "json", attempts=3))}


def ownership_key(item, document, scopes):
    group, kind, namespace, name = g.resource_key(item)
    if scopes.namespaced(g.cluster_key(document), group, kind):
        namespace = namespace or document["spec"].get("destination", {}).get("namespace") or "default"
    else:
        namespace = ""
    return g.cluster_key(document), group, kind, namespace, name


def evaluate(repo, data, output, *, offline=False):
    rows = []
    live_apps = {} if offline or not data["applications"] else inventory()
    scopes = g.ScopeRegistry()
    scopes.inventory(live_apps)
    live_manifests = {}
    # CRD owners may have no instances. Read those definitions before rendering.
    for name, app in live_apps.items():
        if any(item.get("kind") == "CustomResourceDefinition" for item in app.get("status", {}).get("resources", [])):
            items = g.resources(g.argo("app", "manifests", name, "--source", "live", attempts=3))
            live_manifests[name] = items
            scopes.crds(g.cluster_key(app), items, f"live CRD owned by {name}", live=True)
    ownership = {}
    for name, app in live_apps.items():
        for resource in app.get("status", {}).get("resources", []):
            key = (g.cluster_key(app), resource.get("group", ""), resource["kind"],
                   resource.get("namespace", ""), resource["name"])
            ownership.setdefault(key, set()).add(name)
    proposed_owners = {}
    with tempfile.TemporaryDirectory(prefix="gitops-render-") as temp_name:
        temp = Path(temp_name)
        old_dir, new_dir = temp / "base", temp / "head"
        old_dir.mkdir()
        new_dir.mkdir()
        base_renderer = g.Renderer(repo, data["base"], data["repository"], old_dir, scopes)
        head_renderer = g.Renderer(repo, data["head"], data["repository"], new_dir, scopes)
        rendered = {}
        render_errors = {}
        # Render every source first. CRDs from a later Application can establish
        # scope for a CR in an earlier Application, independent of sync waves.
        for entry in data["applications"]:
            rendered[entry["name"]] = {}
            for side, renderer in (("before", base_renderer), ("after", head_renderer)):
                try:
                    items = renderer.render(entry[side], normalize=False) if entry[side] else []
                    rendered[entry["name"]][side] = items
                    if entry[side]:
                        scopes.crds(g.cluster_key(entry[side]["document"]), items, f"{side} CRD in {entry['name']}")
                except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
                    render_errors[entry["name"]] = str(exc)
                rendered[entry["name"]][side + "Sources"] = list(renderer.identities)
                renderer.identities.clear()
        for entry in data["applications"]:
            row = {"name": entry["name"], "action": entry["action"], "result": "not evaluated",
                   "notices": entry["notices"], "sources": {}, "error": ""}
            rows.append(row)
            app_dir = output / entry["name"]
            app_dir.mkdir(parents=True, exist_ok=True)
            try:
                if entry["name"] in render_errors:
                    raise g.GitOpsError(render_errors[entry["name"]])
                result = rendered[entry["name"]]
                before = scopes.normalize(result["before"], entry["before"]["document"]) if entry["before"] else []
                after = scopes.normalize(result["after"], entry["after"]["document"]) if entry["after"] else []
                spec_diff = g.manifest_diff(
                    [entry["before"]["document"]] if entry["before"] else [],
                    [entry["after"]["document"]] if entry["after"] else [], "base Application", "proposed Application")
                resource_diff = g.manifest_diff(before, after, "base resources", "proposed resources")
                (app_dir / "application.diff").write_text(spec_diff, encoding="utf-8")
                (app_dir / "pr.diff").write_text(resource_diff, encoding="utf-8")
                row["result"] = "resource changes" if resource_diff else "configuration only; no resource changes"
                row["resourceCounts"] = g.change_counts(before, after)
                if resource_diff:
                    counts = row["resourceCounts"]
                    row["result"] = ", ".join(f"{counts[key]} {key}" for key in ("added", "deleted", "modified"))
                    if not any(counts.values()):
                        row["result"] = "redacted Secret content changed"
                row["prDiff"], row["specDiff"] = resource_diff, spec_diff
                if not offline:
                    live = live_manifests.get(entry["name"])
                    if live is None:
                        live = g.resources(g.argo("app", "manifests", entry["name"], "--source", "live", attempts=3)) if entry["name"] in live_apps else []
                    if live:
                        scopes.crds(g.cluster_key(live_apps[entry["name"]]), live, "live CRD", live=True)
                        live = scopes.normalize(live, live_apps[entry["name"]])
                    live_diff = g.manifest_diff(live, after, "live resources", "proposed resources")
                    row["liveDiff"] = live_diff
                    (app_dir / "live.diff").write_text(live_diff, encoding="utf-8")
                else:
                    row["notices"] = [*row["notices"], "Offline: live comparison and ownership checks were not performed"]
                if entry["after"]:
                    for resource in after:
                        if (resource.get("metadata", {}).get("annotations") or {}).get("argocd.argoproj.io/hook"):
                            continue
                        key = ownership_key(resource, entry["after"]["document"], scopes)
                        owners = ownership.get(key, set()) - {entry["name"]}
                        if owners:
                            raise g.GitOpsError(f"Resource {key[2:]} is already tracked by {', '.join(sorted(owners))}")
                        proposed_owners.setdefault(key, set()).add(entry["name"])
                if any(notice.startswith("BLOCKED:") for notice in entry["notices"]):
                    raise g.GitOpsError("Application deletion/rename requires an explicit retain/delete policy")
            except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
                row["result"], row["error"] = "FAILED", str(exc)
            finally:
                row["sources"] = {"base": rendered[entry["name"]].get("beforeSources", []), "proposed": rendered[entry["name"]].get("afterSources", [])}
                base_renderer.identities.clear()
                head_renderer.identities.clear()
                g.write_json(app_dir / "result.json", row)
        for key, owners in proposed_owners.items():
            if len(owners) > 1:
                for row in rows:
                    if row["name"] in owners:
                        row["result"] = "FAILED"
                        row["error"] += f" Proposed ownership collision for {key[2:]}: {', '.join(sorted(owners))}"
    for row in rows:
        g.write_json(output / row["name"] / "result.json", row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    for key in ("base", "head", "repo"):
        variable = {"base": "GITHUB_BASE_SHA", "head": "GITHUB_HEAD_SHA", "repo": "GITHUB_REPOSITORY"}[key]
        parser.add_argument(f"--{key}", default=os.getenv(variable), required=not os.getenv(variable))
    parser.add_argument("--output", default="gitops-preview.md")
    parser.add_argument("--output-dir", default="gitops-rendered-output")
    parser.add_argument("--offline", action="store_true", help="Local diagnostics only; cannot pass required preview")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    data = {"base": args.base, "head": args.head, "applications": []}
    rows, fatal = [], ""
    try:
        repository = args.repo if "://" in args.repo else f"https://github.com/{args.repo}"
        merged = g.merge_revision(Path.cwd(), args.base, args.head)
        data = g.plan(Path.cwd(), args.base, merged, repository)
        data["reviewedHead"] = args.head
        rows = evaluate(Path.cwd(), data, output, offline=args.offline)
    except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
        fatal = str(exc)
    data["fatal"] = fatal
    body = g.summary(data, rows, "GitOps PR Preview", "<!-- friday-pa:argocd-diff -->")
    body += f"\n**PR head:** `{args.head}`. The proposed revision includes the current base branch.\n"
    if fatal:
        body += "\n**Preview failed:** " + g.cell(fatal) + "\n"
    body += "\nLive comparisons include Kubernetes defaults and existing drift; they are separate from the PR diff. "
    body += "Ownership checks cover Argo CD tracked resources, not unmanaged cluster objects.\n"
    budget = max(0, 58000 - len(body))
    for row in rows:
        detail = f"\n<details><summary>{g.cell(row['name'])}</summary>\n\n"
        detail += "\n".join(g.cell(notice) for notice in row["notices"]) + "\n"
        if row["error"]:
            detail += "\n**Error:** " + g.cell(row["error"]) + "\n"
        detail += "\n### Application configuration\n" + g.block(row.get("specDiff", ""), 2500)
        detail += "\n### What this PR changes (base → proposed)\n" + g.block(row.get("prDiff", ""), 6000)
        detail += "\n### Current cluster → proposed (includes drift/defaults)\n" + g.block(row.get("liveDiff", ""), 2500)
        detail += "\n</details>\n"
        if len(detail) <= budget:
            body += detail
            budget -= len(detail)
    body += "\n[Complete comparisons and source identities]({{GITOPS_PREVIEW_ARTIFACT_URL}})\n"
    if len(body) > 60000:
        body = "<!-- friday-pa:argocd-diff -->\n## GitOps PR Preview\nSummary exceeds the comment limit. See the workflow artifact.\n"
        fatal = fatal or "Summary exceeds the comment limit"
    Path(args.output).write_text(body, encoding="utf-8")
    # Plan definitions can contain inline secrets. Only source identities and sanitized results are published.
    g.write_json(output / "summary.json", {"base": data["base"], "head": data["head"], "fatal": fatal,
                                         "unmapped": data.get("unmapped", []), "applications": rows})
    failed = bool(fatal or data.get("unmapped") or any(row["result"] == "FAILED" for row in rows))
    return int(failed or (args.offline and bool(rows)))


if __name__ == "__main__":
    sys.exit(main())
