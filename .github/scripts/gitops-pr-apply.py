#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

try:
    import yaml
except ImportError as exc:
    print("PyYAML is required. Install it with: python -m pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from exc


def load_preview_module() -> ModuleType:
    module_path = Path(__file__).with_name("gitops-pr-preview.py")
    spec = importlib.util.spec_from_file_location("gitops_pr_preview", module_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy reviewer-approved GitOps PR changes through Argo CD.")
    parser.add_argument("--base", default=os.getenv("GITHUB_BASE_SHA"), help="PR base commit SHA.")
    parser.add_argument("--head", default=os.getenv("GITHUB_HEAD_SHA"), help="Approved PR head commit SHA.")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"), help="GitHub repository, owner/name.")
    parser.add_argument("--pr-number", default=os.getenv("GITHUB_PR_NUMBER"), help="Pull request number.")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for each Application.")
    parser.add_argument("--dry-run", action="store_true", help="Generate pinned Application files without deploying.")
    parser.add_argument("--output-dir", default="gitops-pr-apply-output", help="Dry-run output directory.")
    return parser.parse_args()


def pin_current_repo_sources(
    document: dict[str, Any],
    *,
    current_repo_urls: set[str],
    head_sha: str,
    normalize_repo_url: Any,
) -> int:
    spec = document.get("spec")
    if not isinstance(spec, dict):
        return 0

    if isinstance(spec.get("sources"), list):
        sources = [source for source in spec["sources"] if isinstance(source, dict)]
    elif isinstance(spec.get("source"), dict):
        sources = [spec["source"]]
    else:
        sources = []

    pinned = 0
    for source in sources:
        if normalize_repo_url(str(source.get("repoURL", ""))) not in current_repo_urls:
            continue
        source["targetRevision"] = head_sha
        pinned += 1
    return pinned


def application_name(document: dict[str, Any]) -> str:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("name"):
        raise ValueError("Application manifest has no metadata.name")
    return str(metadata["name"])


def deploy_application(
    preview: ModuleType,
    *,
    repo: Path,
    app_name: str,
    manifest_path: Path,
    timeout: int,
) -> None:
    base_args = preview.argo_base_args()
    commands = [
        [
            "argocd",
            *base_args,
            "app",
            "create",
            app_name,
            "--file",
            str(manifest_path),
            "--upsert",
        ],
        ["argocd", *base_args, "app", "sync", app_name],
        [
            "argocd",
            *base_args,
            "app",
            "wait",
            app_name,
            "--sync",
            "--health",
            "--operation",
            "--timeout",
            str(timeout),
        ],
    ]
    for command in commands:
        print(f"Running: {' '.join(command)}")
        preview.run(command, cwd=repo)


def main() -> int:
    args = parse_args()
    if not args.base or not args.head or not args.repo:
        print("--base, --head, and --repo are required.", file=sys.stderr)
        return 2

    preview = load_preview_module()
    repo = Path.cwd()
    current_repo_urls = {preview.normalize_repo_url(f"https://github.com/{args.repo}")}
    changes = preview.parse_name_status(
        preview.git(["diff", "--name-status", "--find-renames", args.base, args.head], cwd=repo)
    )
    head_apps = preview.load_apps_from_worktree(repo, current_repo_urls)
    base_apps = preview.load_apps_from_git(repo, args.base, current_repo_urls)
    all_apps = preview.merge_apps(head_apps, base_apps)
    reasons = preview.map_changes_to_apps(changes, all_apps)
    head_apps_by_name = {app.name: app for app in head_apps}

    selected_apps = [
        head_apps_by_name[name]
        for name in sorted(reasons)
        if name in head_apps_by_name
    ][: preview.MAX_APPS]
    removed_apps = sorted(set(reasons) - set(head_apps_by_name))

    if removed_apps:
        print("Application deletions are not performed before merge: " + ", ".join(removed_apps))
    if not selected_apps:
        print("No new or changed Applications need to be deployed.")
        return 0

    if not args.dry_run:
        available, unavailable_reason = preview.argo_available()
        if not available:
            print(unavailable_reason, file=sys.stderr)
            return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_manifest in output_dir.glob("*.yaml"):
        old_manifest.unlink()

    with tempfile.TemporaryDirectory(prefix="gitops-pr-apply-") as temp_name:
        temp_dir = Path(temp_name)
        for app in selected_apps:
            source_path = repo / app.manifest_path
            document = preview.load_yaml_document(source_path.read_text(encoding="utf-8"))
            if not document:
                raise ValueError(f"Unable to parse Application manifest: {app.manifest_path}")

            app_name = application_name(document)
            pinned_sources = pin_current_repo_sources(
                document,
                current_repo_urls=current_repo_urls,
                head_sha=args.head,
                normalize_repo_url=preview.normalize_repo_url,
            )
            rendered = yaml.safe_dump(document, sort_keys=False)
            output_path = output_dir / f"{app_name}.yaml"
            output_path.write_text(rendered, encoding="utf-8")

            reason_text = ", ".join(sorted(reasons[app_name]))
            print(
                f"{app_name}: {reason_text}; "
                f"pinned {pinned_sources} homelab-ops source(s) to {args.head}"
            )
            if args.dry_run:
                continue

            temporary_manifest = temp_dir / f"{app_name}.yaml"
            temporary_manifest.write_text(rendered, encoding="utf-8")
            deploy_application(
                preview,
                repo=repo,
                app_name=app_name,
                manifest_path=temporary_manifest,
                timeout=args.timeout,
            )

    action = "Prepared" if args.dry_run else "Deployed"
    print(f"{action} {len(selected_apps)} Application(s) for PR #{args.pr_number or 'unknown'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
