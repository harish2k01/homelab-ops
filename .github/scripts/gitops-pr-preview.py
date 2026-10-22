#!/usr/bin/env python3
"""Render complete base/proposed Applications without changing the cluster."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
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
    # ScopeRegistry lazily discovers missing kinds and trusted cluster anchors.
    # Global ownership still uses all Applications' lightweight inventories.
    ownership = {}
    for name, app in live_apps.items():
        for resource in app.get("status", {}).get("resources", []):
            key = (g.cluster_key(app), resource.get("group") or "", resource["kind"],
                   resource.get("namespace") or "", resource["name"])
            ownership.setdefault(key, set()).add(name)
    proposed_owners = {}
    with tempfile.TemporaryDirectory(prefix="gitops-render-") as temp_name:
        temp = Path(temp_name)
        old_dir, new_dir = temp / "base", temp / "head"
        old_dir.mkdir()
        new_dir.mkdir()
        charts = {}
        base_renderer = g.Renderer(repo, data["base"], data["repository"], old_dir, scopes, charts)
        head_renderer = g.Renderer(repo, data["head"], data["repository"], new_dir, scopes, charts)
        rendered = {}
        render_errors = {}
        # Render every source first. CRDs from a later Application can establish
        # scope for a CR in an earlier Application, independent of sync waves.
        for entry in data["applications"]:
            rendered[entry["name"]] = {}
            for side, renderer in (("after", head_renderer),):
                try:
                    items = renderer.render(entry[side], normalize=False) if entry[side] else []
                    rendered[entry["name"]][side] = items
                    if entry[side]:
                        scopes.crds(g.cluster_key(entry[side]["document"]), items, f"{side} CRD in {entry['name']}")
                except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
                    render_errors[entry["name"]] = str(exc)
                rendered[entry["name"]][side + "Sources"] = list(renderer.identities)
                renderer.identities.clear()

        def rendered_intent(entry, after, row, keys=None):
            """An optional old chart is diagnostic, never a live-preview prerequisite."""
            try:
                state = rendered[entry["name"]]
                if "before" not in state:
                    state["before"] = base_renderer.render(entry["before"], normalize=False) if entry["before"] else []
                    state["beforeSources"] = list(base_renderer.identities)
                    base_renderer.identities.clear()
                before = scopes.normalize(state["before"], entry["before"]["document"]) if entry["before"] else []
                after = scopes.normalize(after, entry["after"]["document"]) if entry["after"] else []
                if keys is not None:
                    before = [item for item in before if g.resource_key(item) in keys]
                    after = [item for item in after if g.resource_key(item) in keys]
                return g.manifest_diff(before, after, "previous render", "proposed render")
            except (g.GitOpsError, ValueError, OSError, yaml.YAMLError):
                row["notices"].append("Previous render unavailable for diagnostics. Redacted proposed resource fields are available in the artifact.")
                return ""
        for entry in data["applications"]:
            row = {"name": entry["name"], "action": entry["action"], "result": "not evaluated",
                   "notices": entry["notices"], "sources": {}, "error": ""}
            row_started = time.monotonic()
            rows.append(row)
            app_dir = output / entry["name"]
            app_dir.mkdir(parents=True, exist_ok=True)
            try:
                if entry["name"] in render_errors:
                    raise g.GitOpsError(render_errors[entry["name"]])
                result = rendered[entry["name"]]
                (app_dir / "proposed.yaml").write_text(yaml.safe_dump_all(g.public_resources(result["after"]), sort_keys=True), encoding="utf-8")
                after = scopes.normalize(result["after"], entry["after"]["document"]) if entry["after"] else []
                spec_diff = g.manifest_diff(
                    [entry["before"]["document"]] if entry["before"] else [],
                    [entry["after"]["document"]] if entry["after"] else [], "base Application", "proposed Application")
                (app_dir / "application.diff").write_text(spec_diff, encoding="utf-8")
                row["specDiff"] = spec_diff
                # Application policy changes matter even when manifests are identical.
                if entry["before"] and entry["after"]:
                    fields = ("destination", "project", "syncPolicy", "ignoreDifferences")
                    old_doc = live_apps.get(entry["name"], entry["before"]["document"])
                    old_settings = {key: old_doc.get("spec", {}).get(key) for key in fields}
                    new_settings = {key: entry["after"]["document"].get("spec", {}).get(key) for key in fields}
                    if old_settings != new_settings:
                        old_app = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
                                   "metadata": {"name": entry["name"]}, "spec": old_settings}
                        row["settingsDiff"] = g.manifest_diff([old_app], [{**old_app, "spec": new_settings}], "live settings", "proposed settings")
                if entry["after"]:
                    for resource in after:
                        annotations = resource.get("metadata", {}).get("annotations") or {}
                        if annotations.get("argocd.argoproj.io/hook") or annotations.get("helm.sh/hook"):
                            continue
                        key = ownership_key(resource, entry["after"]["document"], scopes)
                        owners = ownership.get(key, set()) - {entry["name"]}
                        if owners:
                            raise g.GitOpsError(f"Resource {key[2:]} is already tracked by {', '.join(sorted(owners))}")
                        proposed_owners.setdefault(key, set()).add(entry["name"])
                if not offline:
                    live = live_manifests.get(entry["name"])
                    if live is None:
                        live = g.resources(g.argo("app", "manifests", entry["name"], "--source", "live", attempts=3)) if entry["name"] in live_apps else []
                    if live:
                        scopes.crds(g.cluster_key(live_apps[entry["name"]]), live, "live CRD", live=True)
                        live = scopes.normalize(live, live_apps[entry["name"]])
                    # Removing an Application with retain leaves its workloads
                    # running. A cascaded deletion instead removes them.
                    if entry["action"] == "delete" and entry.get("deletion") == "retain":
                        normalized, target, notices, blockers = live, live, [], []
                    else:
                        proposed_app = entry["after"]["document"] if entry["after"] else None
                        normalized, target, notices, blockers = g.preview_sync(live_apps.get(entry["name"]), proposed_app, live, after)
                    row["notices"] = [*row["notices"], *notices]
                    if entry["name"] not in live_apps and entry["after"]:
                        row["notices"].append("New Application: Actions does not register it. New resources are rendered additions; admission is not dry-run validated.")
                    elif set(map(g.resource_key, after)) - set(map(g.resource_key, live)):
                        row["notices"].append("New resources are rendered additions; admission is not dry-run validated.")
                    live_diff = g.manifest_diff(normalized, target, "live resources", "predicted resources")
                    row["liveDiff"] = live_diff
                    row["resourceCounts"] = g.change_counts(normalized, target)
                    row["result"] = "Changes after sync" if live_diff else "No resource changes after sync"
                    if blockers:
                        row["blockers"] = blockers
                        row["result"] = "BLOCKED (partial comparison)"
                        keys = {tuple(blocker["identity"]) for blocker in blockers}
                        # Rendered intent is diagnostic only, never substituted
                        # for a successful live prediction or included in counts.
                        row["blockedIntentDiff"] = rendered_intent(entry, after, row, keys)
                        if any(blocker["identity"][1] == "StatefulSet" and blocker["category"] == "immutable" for blocker in blockers):
                            row["notices"].append("StatefulSet fields are immutable. PVC expansion and StatefulSet migration require separate operations; this preview performs neither.")
                    if entry["action"] == "delete" and entry.get("deletion") == "retain":
                        row["result"] = "Application removed; resources retained"
                    (app_dir / "live.diff").write_text(live_diff, encoding="utf-8")
                else:
                    row["result"] = "Live comparison unavailable (offline)"
                    row["notices"] = [*row["notices"], "Offline: live comparison and ownership checks were not performed"]
                if any(notice.startswith("BLOCKED:") for notice in entry["notices"]):
                    raise g.GitOpsError("Application deletion/rename requires an explicit retain/delete policy")
            except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
                row["result"], row["error"] = "FAILED", str(exc)
                if rendered[entry["name"]].get("after") is not None:
                    row["intentDiff"] = rendered_intent(entry, rendered[entry["name"]]["after"], row)
            finally:
                row["durationSeconds"] = round(time.monotonic() - row_started, 3)
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


def render_comment(data, rows, *, offline=False):
    lines = ["<!-- friday-pa:argocd-diff -->", "## GitOps PR Preview", "", "### Changes after sync", ""]
    fatal = data.get("fatal", "")
    if fatal:
        lines += ["**Preview failed:** " + g.cell(fatal), ""]
    if offline:
        lines += ["**Live comparison unavailable:** this preview ran offline.", ""]
    if data.get("unmapped"):
        lines += ["**Preview failed: deployment files could not be mapped to an Application.**",
                  *[f"- `{g.cell(path)}`" for path in data["unmapped"]], ""]
    if any(row.get("blockers") for row in rows):
        lines += ["**Sync blocked:** counts exclude unverified resources in the Blocked column. Validated results are retained below.", ""]
    lines += ["| Application | Added | Removed | Modified | Blocked | Result |", "|---|---:|---:|---:|---:|---|"]
    for row in rows:
        counts = row.get("resourceCounts") if not row.get("error") and not offline else None
        values = [str(counts[key]) if counts is not None else "—" for key in ("added", "deleted", "modified")]
        blocked = str(len(row.get("blockers", []))) if counts is not None else "—"
        lines.append(f"| `{g.cell(row['name'])}` | {' | '.join(values)} | {blocked} | {g.cell(row['result'])} |")
    if not rows:
        state = "Not evaluated" if fatal or data.get("unmapped") or data.get("applications") or offline else "No affected Applications; cluster comparison not needed"
        lines.append(f"| — | — | — | — | — | {state} |")
    body = "\n".join(lines) + "\n"
    footer = "\n[Full diff]({{GITOPS_PREVIEW_ARTIFACT_URL}})\n"
    omitted = False
    for row in rows:
        contents = []
        if row.get("error"):
            contents.append("**Preview failed:** " + g.cell(row["error"]))
        for blocker in row.get("blockers", []):
            group, kind, namespace, name = blocker["identity"]
            identity = f"{group + '/' if group else ''}{kind} {namespace + '/' if namespace else ''}{name}"
            contents.append(f"**Blocked: {g.cell(identity)}** — {g.cell(blocker['message'])}")
        contents.extend(g.cell(notice) for notice in row.get("notices", []))
        if row.get("settingsDiff"):
            contents.append("**Application settings changed**\n\n" + g.block(row["settingsDiff"], 4000))
        if row.get("liveDiff") and not offline:
            contents.append(g.block(row["liveDiff"], 12000))
        elif not row.get("error"):
            contents.append(g.cell(row["result"]))
        intent = row.get("blockedIntentDiff") or row.get("intentDiff")
        if intent:
            contents.append("**Rendered intent — live prediction unavailable for these changes**\n\n"
                            "This is the rendered configuration change, not a validated sync result.\n\n" + g.block(intent, 8000))
        detail = f"\n<details><summary>{g.cell(row['name'])}</summary>\n\n" + "\n\n".join(contents) + "\n\n</details>\n"
        if len(body) + len(detail) + len(footer) < 58000:
            body += detail
        else:
            omitted = True
    if omitted:
        body += "\nSome resource details are omitted from this comment. Open **Full diff** below.\n"
    if len(body) + len(footer) > 60000:
        body = "<!-- friday-pa:argocd-diff -->\n## GitOps PR Preview\nThe preview exceeds the comment limit. Open **Full diff** below.\n"
    return body + footer


def main():
    parser = argparse.ArgumentParser()
    for key in ("base", "head", "repo"):
        variable = {"base": "GITHUB_BASE_SHA", "head": "GITHUB_HEAD_SHA", "repo": "GITHUB_REPOSITORY"}[key]
        parser.add_argument(f"--{key}", default=os.getenv(variable), required=not os.getenv(variable))
    parser.add_argument("--output", default="gitops-preview.md")
    parser.add_argument("--output-dir", default="gitops-rendered-output")
    parser.add_argument("--offline", action="store_true", help="Local diagnostics only; cannot pass required preview")
    parser.add_argument("--plan-only", action="store_true", help="Credential-free affected-Application preflight")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    data = {"base": args.base, "head": args.head, "applications": []}
    rows, fatal = [], ""
    started = time.monotonic()
    try:
        repository = args.repo if "://" in args.repo else f"https://github.com/{args.repo}"
        merged = g.merge_revision(Path.cwd(), args.base, args.head)
        data = g.plan(Path.cwd(), args.base, merged, repository)
        data["reviewedHead"] = args.head
        if not args.plan_only:
            rows = evaluate(Path.cwd(), data, output, offline=args.offline)
    except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
        fatal = str(exc)
    data["fatal"] = fatal
    has_changes = bool(data.get("applications")) and not fatal and not data.get("unmapped")
    if args.plan_only and os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"has_changes={str(has_changes).lower()}\n")
    if args.plan_only and has_changes:
        # A later setup failure must publish a failure, never an old or pending
        # comment mistaken for a completed resource comparison.
        Path(args.output).unlink(missing_ok=True)
        g.write_json(output / "summary.json", {"phase": "planned", "applications": [
            {"name": item["name"], "action": item["action"]} for item in data["applications"]]})
        return 0
    body = render_comment(data, rows, offline=args.offline)
    Path(args.output).write_text(body, encoding="utf-8")
    # Plan definitions can contain inline secrets. Only source identities and sanitized results are published.
    g.write_json(output / "summary.json", {"base": data["base"], "head": data["head"], "fatal": fatal,
                                         "unmapped": data.get("unmapped", []), "applications": rows,
                                         "durationSeconds": round(time.monotonic() - started, 3)})
    failed = bool(fatal or data.get("unmapped") or any(row["result"] == "FAILED" or row.get("blockers") for row in rows))
    return int(failed or (args.offline and bool(rows)))


if __name__ == "__main__":
    sys.exit(main())
