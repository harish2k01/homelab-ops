#!/usr/bin/env python3
"""Reconcile merged GitOps changes; persist an outcome for every Application."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import yaml
import gitops as g


def current_main(repo, expected):
    branch = os.getenv("GITOPS_DEFAULT_BRANCH", "main")
    current = g.git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0]
    if current != expected:
        raise g.GitOpsError(f"Superseded: {branch} advanced to {current}; the next reconciliation includes pending changes")


def diagnostics(name):
    app = json.loads(g.argo("app", "get", name, "--refresh", "-o", "json", attempts=3))
    status = app.get("status", {})
    return {
        "sync": status.get("sync", {}).get("status", "Unknown"),
        "health": status.get("health", {}).get("status", "Unknown"),
        "revisions": status.get("sync", {}).get("revisions") or [status.get("sync", {}).get("revision")],
        "operation": status.get("operationState", {}).get("phase", "None"),
        "message": status.get("operationState", {}).get("message", "")[:1000],
        "resources": [{key: item.get(key) for key in ("group", "kind", "namespace", "name", "status", "health")}
                      for item in status.get("resources", []) if item.get("status") != "Synced" or
                      item.get("health", {}).get("status") not in {None, "Healthy"}],
        "conditions": [{"type": item.get("type"), "message": item.get("message", "")[:1000]}
                       for item in status.get("conditions", [])],
    }


def request_sync(repo, expected, name, remaining):
    for attempt in range(3):
        state = diagnostics(name)
        if state["sync"] == "Synced" and state["health"] == "Healthy":
            return
        current_main(repo, expected)
        try:
            g.argo("app", "sync", name, "--async")
            return
        except g.GitOpsError as exc:
            if "another operation is already in progress" not in str(exc).lower() or attempt == 2:
                raise
            timeout = remaining()
            g.argo("app", "wait", name, "--operation", "--timeout", str(timeout), timeout=timeout + 30)


def reconcile(repo, data, output, *, dry_run=False, timeout=900):
    rows = [{"name": entry["name"], "action": entry["action"], "result": "not attempted", "error": ""}
            for entry in data["applications"]]
    failed, live = set(), {}
    deadline = time.monotonic() + int(os.getenv("GITOPS_DEPLOY_BUDGET", "6000"))

    def save():
        g.write_json(output / "results.json", {"base": data["base"], "head": data["head"], "applications": rows})
        body = g.summary(data, rows, "Argo CD Reconciliation", "<!-- friday-pa:argocd-apply -->")
        for row in rows:
            if row.get("error") or row.get("diagnostics"):
                body += f"\n### {g.cell(row['name'])}\n"
                body += g.cell(row.get("error", "")) + "\n"
                body += "```json\n" + json.dumps(row.get("diagnostics", {}), indent=2).replace("```", "` ` `")[:5000] + "\n```\n"
        (output / "report.md").write_text(body, encoding="utf-8")

    save()
    if data.get("unmapped"):
        raise g.GitOpsError("Deployment paths are not mapped to Applications; no changes applied")
    if not dry_run and rows:
        live = {app["metadata"]["name"]: app for app in json.loads(g.argo("app", "list", "-o", "json", attempts=3))}
    with tempfile.TemporaryDirectory(prefix="gitops-deploy-") as directory:
        for entry, row in zip(data["applications"], rows):
            try:
                if time.monotonic() >= deadline:
                    raise g.GitOpsError("Not attempted: reconciliation time budget exhausted")
                blockers = [other["name"] for other in data["applications"] if other["name"] in failed and
                            (other["name"] in entry["dependencies"] or other["wave"] < entry["wave"])]
                if blockers:
                    raise g.GitOpsError("Not attempted: failed dependencies/waves: " + ", ".join(blockers))
                if any(notice.startswith("BLOCKED:") for notice in entry["notices"]):
                    raise g.GitOpsError("Not attempted: deletion/rename requires explicit retain/delete policy")
                if dry_run:
                    row["result"] = "planned"
                    continue
                planned_names = {item["name"] for item in data["applications"]}
                for name in set(entry["dependencies"]) - planned_names:
                    status = live.get(name, {}).get("status", {})
                    if status.get("sync", {}).get("status") != "Synced" or status.get("health", {}).get("status") != "Healthy":
                        raise g.GitOpsError(f"Not attempted: dependency {name} is not synced and healthy")
                current_main(repo, data["head"])
                if entry["action"] == "delete":
                    if entry["name"] not in live:
                        row["result"] = "already absent"
                        continue
                    cascade = "true" if entry["deletion"] == "delete" else "false"
                    g.argo("app", "delete", entry["name"], f"--cascade={cascade}", "--yes")
                    g.argo("app", "wait", entry["name"], "--delete", "--timeout", str(timeout), timeout=timeout + 30)
                    row["result"] = "deleted with resources" if cascade == "true" else "Application removed; resources retained"
                    continue
                path = Path(directory) / f"{entry['name']}.yaml"
                path.write_text(yaml.safe_dump(entry["after"]["document"], sort_keys=False), encoding="utf-8")
                # No retries for writes with ambiguous completion. A rerun starts with current state.
                g.argo("app", "create", entry["name"], "--file", str(path), "--upsert")
                row["result"] = "definition applied; waiting for convergence"
                save()
                request_sync(repo, data["head"], entry["name"], lambda: max(1, min(timeout, int(deadline - time.monotonic()))))
                remaining = max(1, min(timeout, int(deadline - time.monotonic())))
                g.argo("app", "wait", entry["name"], "--sync", "--health", "--operation", "--timeout", str(remaining), timeout=remaining + 30)
                row["diagnostics"] = diagnostics(entry["name"])
                if row["diagnostics"]["sync"] != "Synced" or row["diagnostics"]["health"] != "Healthy":
                    raise g.GitOpsError("Application has not converged")
                current_main(repo, data["head"])
                row["result"] = "synced and healthy"
            except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
                failed.add(entry["name"])
                row["error"] = str(exc)
                row["result"] = "not attempted" if str(exc).startswith(("Not attempted:", "Superseded:")) else "FAILED"
                if not dry_run and entry["action"] != "delete" and row["result"] == "FAILED":
                    try:
                        row["diagnostics"] = diagnostics(entry["name"])
                        state = row["diagnostics"]
                        if state["health"] == "Healthy" and state["sync"] == "OutOfSync":
                            row["result"] = "FAILED: healthy but persistent drift"
                        elif state["health"] in {"Degraded", "Missing"}:
                            row["result"] = "FAILED: unhealthy resources"
                    except (g.GitOpsError, ValueError, OSError):
                        pass
            finally:
                save()
    return bool(failed)


def main():
    parser = argparse.ArgumentParser()
    for key in ("base", "head", "repo"):
        variable = {"base": "GITHUB_BASE_SHA", "head": "GITHUB_HEAD_SHA", "repo": "GITHUB_REPOSITORY"}[key]
        parser.add_argument(f"--{key}", default=os.getenv(variable), required=not os.getenv(variable))
    parser.add_argument("--all", action="store_true", help="Bootstrap: reconcile all current Applications")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output-dir", default="gitops-pr-apply-output")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # A failed plan must never publish an earlier attempt's successful report.
    for filename in ("report.md", "results.json"):
        (output / filename).unlink(missing_ok=True)
    try:
        repository = args.repo if "://" in args.repo else f"https://github.com/{args.repo}"
        data = g.plan(Path.cwd(), args.base, args.head, repository, all_apps=args.all)
        return int(reconcile(Path.cwd(), data, output, dry_run=args.dry_run, timeout=args.timeout))
    except (g.GitOpsError, ValueError, OSError, yaml.YAMLError) as exc:
        message = "Reconciliation failed: " + g.cell(str(exc))
        with (output / "report.md").open("a", encoding="utf-8") as report:
            report.write("\n" + message + "\n")
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
