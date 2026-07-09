#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import os
import re
import shutil
import subprocess
import sys
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
MAX_FILES = int(os.getenv("GITOPS_PREVIEW_MAX_FILES", "200"))
MAX_APPS = int(os.getenv("GITOPS_PREVIEW_MAX_APPS", "30"))
MAX_APP_DIFF_CHARS = int(os.getenv("GITOPS_PREVIEW_MAX_APP_DIFF_CHARS", "18000"))


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
    value_files: tuple[str, ...]
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
    command: list[str]
    output: str
    notes: list[str]


def run(args: list[str], *, cwd: Path, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
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


def short_sha(value: str) -> str:
    return value[:12] if value else "unknown"


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


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
        repo_url = source.get("repoURL") or ""
        sources.append(
            Source(
                position=index,
                repo_url=repo_url,
                path=normalize_path(source["path"]) if source.get("path") else None,
                chart=source.get("chart"),
                ref=source.get("ref"),
                value_files=tuple(str(value) for value in value_files),
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
            manifest_app = apps_by_manifest.get(path)
            if manifest_app:
                reasons[manifest_app.name].add("Application manifest changed")
            elif path.startswith("argocd-apps/") and path.endswith((".yaml", ".yml")):
                name = Path(path).stem
                if name in apps_by_name:
                    reasons[name].add("Application manifest changed")
                else:
                    reasons[name].add("Application manifest changed")

            for app in apps:
                for source in app.sources:
                    if source.current_repo and source.path and is_under(path, source.path):
                        reasons[app.name].add(f"source path `{source_label(source)}` changed")

                if path in app_value_paths[app.name]:
                    reasons[app.name].add("Helm value file changed")

            parts = path.split("/")
            if len(parts) >= 2 and parts[0] in {"charts", "infra", "manifests"}:
                name = parts[1]
                if name in apps_by_name:
                    reasons[name].add(f"`{parts[0]}/{name}` changed")

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


def run_app_diff(repo: Path, app: App, head_sha: str, reasons: set[str], can_diff: bool, skip_reason: str) -> AppDiff:
    notes: list[str] = []
    if not app.from_head:
        notes.append("Application is not present in the PR head checkout; live diff was skipped.")
        return AppDiff(app=app, status="skipped", summary="app missing from PR head", command=[], output="", notes=notes)

    if any("Application manifest" in reason for reason in reasons):
        notes.append("The Application manifest changed. `argocd app diff` uses the live Application spec, so source/destination changes may need manual review.")

    if not can_diff:
        return AppDiff(app=app, status="skipped", summary=skip_reason, command=[], output="", notes=notes)

    command = [
        "argocd",
        *argo_base_args(),
        "app",
        "diff",
        app.name,
        "--exit-code=false",
        *revision_args(app, head_sha),
    ]
    completed = run(command, cwd=repo, allow_failure=True)
    output = completed.stdout.strip()

    if completed.returncode not in {0, 1}:
        return AppDiff(
            app=app,
            status="error",
            summary=f"`argocd app diff` exited with {completed.returncode}",
            command=command,
            output=output,
            notes=notes,
        )

    if output:
        return AppDiff(app=app, status="changed", summary="changes detected", command=command, output=output, notes=notes)

    return AppDiff(app=app, status="clean", summary="no rendered diff", command=command, output="", notes=notes)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... truncated {len(text) - limit} characters ..."


def command_for_display(command: list[str]) -> str:
    hidden_flags = {"--auth-token"}
    sanitized: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            skip_next = False
            continue
        if part in hidden_flags:
            sanitized.extend([part, "***"])
            skip_next = True
            continue
        sanitized.append(part)
    return " ".join(sanitized)


def status_label(status: str) -> str:
    return {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
    }.get(status, status.lower())


def build_markdown(
    *,
    base_sha: str,
    head_sha: str,
    changes: list[Change],
    reasons: dict[str, set[str]],
    app_diffs: list[AppDiff],
    apps: list[App],
    workflow_url: str,
) -> str:
    app_by_name = {app.name: app for app in apps}
    changed_app_names = sorted(reasons)
    file_rows: list[str] = []

    for change in changes[:MAX_FILES]:
        impacted = sorted(
            app_name
            for app_name, app_reasons in reasons.items()
            if any(path_reason_matches(change, app_reason, app_by_name.get(app_name)) for app_reason in app_reasons)
        )
        path = change.path if not change.old_path else f"{change.old_path} -> {change.path}"
        file_rows.append(f"| {escape_cell(status_label(change.status))} | `{escape_cell(path)}` | {escape_cell(', '.join(impacted) or '-') } |")

    if len(changes) > MAX_FILES:
        file_rows.append(f"| ... | ... | {len(changes) - MAX_FILES} more files omitted |")

    lines: list[str] = [
        COMMENT_MARKER,
        "### GitOps Preview",
        "",
        f"Base: `{short_sha(base_sha)}`",
        f"Head: `{short_sha(head_sha)}`",
    ]
    if workflow_url:
        lines.append(f"Run: [workflow logs]({workflow_url})")
    lines.extend(["", "#### Changed files", "", "| Status | Path | Impacted apps |", "|---|---|---|"])
    lines.extend(file_rows or ["| - | No GitOps file changes detected | - |"])

    lines.extend(["", "#### Impacted Argo CD apps", "", "| App | Namespace | Why | Diff |", "|---|---|---|---|"])

    diff_by_app = {item.app.name: item for item in app_diffs}
    for app_name in changed_app_names[:MAX_APPS]:
        app = app_by_name.get(app_name)
        diff = diff_by_app.get(app_name)
        namespace = app.namespace if app else ""
        reason = "<br>".join(sorted(reasons[app_name]))
        diff_status = diff.summary if diff else "not evaluated"
        lines.append(f"| `{escape_cell(app_name)}` | `{escape_cell(namespace or '-')}` | {reason} | {diff_status} |")

    if len(changed_app_names) > MAX_APPS:
        lines.append(f"| ... | ... | {len(changed_app_names) - MAX_APPS} more apps omitted | ... |")

    if not changed_app_names:
        lines.append("| - | - | No impacted app detected | - |")

    for diff in app_diffs[:MAX_APPS]:
        lines.extend(["", "<details>", f"<summary>{escape_cell(diff.app.name)}: {escape_cell(diff.summary)}</summary>", ""])
        for note in diff.notes:
            lines.append(f"> {note}")
        if diff.command:
            lines.extend(["", "Command:", "", f"`{command_for_display(diff.command)}`"])
        if diff.output:
            lines.extend(["", "```diff", truncate(diff.output.replace("```", "` ` `"), MAX_APP_DIFF_CHARS), "```"])
        lines.extend(["", "</details>"])

    return truncate("\n".join(lines).rstrip() + "\n", COMMENT_LIMIT)


def path_reason_matches(change: Change, reason: str, app: App | None) -> bool:
    if not app:
        return False
    paths = changed_paths(change)
    if "Application manifest" in reason:
        return app.manifest_path in paths
    if "Helm value file" in reason:
        values = helm_value_paths(app)
        return any(path in values for path in paths)
    if reason.startswith("source path"):
        for source in app.sources:
            if source.current_repo and source.path and any(is_under(path, source.path) for path in paths):
                return True
    match = re.match(r"`([^`]+)` changed", reason)
    if match:
        prefix = match.group(1)
        return any(is_under(path, prefix) for path in paths)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a sticky GitOps PR preview comment.")
    parser.add_argument("--base", default=os.getenv("GITHUB_BASE_SHA"), help="Base commit SHA.")
    parser.add_argument("--head", default=os.getenv("GITHUB_HEAD_SHA") or os.getenv("GITHUB_SHA"), help="Head commit SHA.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"), help="GitHub repository, owner/name.")
    parser.add_argument("--output", default="gitops-preview.md", help="Markdown output path.")
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
    changes = parse_name_status(diff_output)

    head_apps = load_apps_from_worktree(repo, current_repo_urls)
    base_apps = load_apps_from_git(repo, args.base, current_repo_urls)
    apps = merge_apps(head_apps, base_apps)
    reasons = map_changes_to_apps(changes, apps)

    can_diff, skip_reason = argo_available()
    app_diffs: list[AppDiff] = []
    for app_name in sorted(reasons)[:MAX_APPS]:
        app = next((item for item in apps if item.name == app_name), None)
        if not app:
            continue
        app_diffs.append(run_app_diff(repo, app, args.head, reasons[app_name], can_diff, skip_reason))

    run_id = os.getenv("GITHUB_RUN_ID")
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    workflow_url = f"{server_url}/{args.repo}/actions/runs/{run_id}" if run_id else ""

    markdown = build_markdown(
        base_sha=args.base,
        head_sha=args.head,
        changes=changes,
        reasons=reasons,
        app_diffs=app_diffs,
        apps=apps,
        workflow_url=workflow_url,
    )
    Path(args.output).write_text(markdown, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
