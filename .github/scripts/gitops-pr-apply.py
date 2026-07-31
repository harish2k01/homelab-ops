#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


OPERATION_IN_PROGRESS = "another operation is already in progress"
SYNC_ATTEMPTS = 3


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
    parser = argparse.ArgumentParser(
        description="Deploy reviewer-approved merged GitOps changes through Argo CD."
    )
    parser.add_argument("--base", default=os.getenv("GITHUB_BASE_SHA"), help="PR base commit SHA.")
    parser.add_argument(
        "--head",
        default=os.getenv("GITHUB_HEAD_SHA"),
        help="Approved PR merge commit SHA.",
    )
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"), help="GitHub repository, owner/name.")
    parser.add_argument("--pr-number", default=os.getenv("GITHUB_PR_NUMBER"), help="Pull request number.")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for each Application.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate merged Application files without deploying.",
    )
    parser.add_argument("--output-dir", default="gitops-pr-apply-output", help="Dry-run output directory.")
    parser.add_argument(
        "--metadata-output",
        default="gitops-pr-apply-metadata.json",
        help="JSON summary used by the PR deployment comment.",
    )
    return parser.parse_args()


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
    create_command = [
        "argocd",
        *base_args,
        "app",
        "create",
        app_name,
        "--file",
        str(manifest_path),
        "--upsert",
    ]
    print(f"Running: {' '.join(create_command)}", flush=True)
    preview.run(create_command, cwd=repo)

    deadline = time.monotonic() + timeout
    sync_command = ["argocd", *base_args, "app", "sync", app_name]
    for attempt in range(1, SYNC_ATTEMPTS + 1):
        print(f"Running: {' '.join(sync_command)}", flush=True)
        completed = preview.run(sync_command, cwd=repo, allow_failure=True)
        if completed.returncode == 0:
            break

        output = completed.stdout or ""
        if OPERATION_IN_PROGRESS not in output.lower() or attempt == SYNC_ATTEMPTS:
            print(output, file=sys.stderr, flush=True)
            raise SystemExit(completed.returncode)

        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining <= 0:
            print(output, file=sys.stderr, flush=True)
            raise SystemExit(completed.returncode)

        print(
            f"{app_name}: another Argo CD operation is running; "
            f"waiting before sync retry {attempt + 1}/{SYNC_ATTEMPTS}.",
            flush=True,
        )
        wait_command = [
            "argocd",
            *base_args,
            "app",
            "wait",
            app_name,
            "--operation",
            "--timeout",
            str(remaining),
        ]
        print(f"Running: {' '.join(wait_command)}", flush=True)
        wait_result = preview.run(wait_command, cwd=repo, allow_failure=True)
        if wait_result.returncode != 0:
            print(
                f"{app_name}: the previous operation did not complete cleanly; "
                "retrying the approved sync while time remains.",
                file=sys.stderr,
                flush=True,
            )

    remaining = max(0, math.ceil(deadline - time.monotonic()))
    if remaining <= 0:
        print(f"{app_name}: timed out before the approved sync could be verified.", file=sys.stderr)
        raise SystemExit(1)

    wait_command = [
        "argocd",
        *base_args,
        "app",
        "wait",
        app_name,
        "--sync",
        "--health",
        "--operation",
        "--timeout",
        str(remaining),
    ]
    print(f"Running: {' '.join(wait_command)}", flush=True)
    preview.run(wait_command, cwd=repo)


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
        print("Application deletions require manual cleanup: " + ", ".join(removed_apps))

    if selected_apps and not args.dry_run:
        available, unavailable_reason = preview.argo_available()
        if not available:
            print(unavailable_reason, file=sys.stderr)
            return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_manifest in output_dir.glob("*.yaml"):
        old_manifest.unlink()

    generated_apps: list[tuple[str, Path]] = []
    metadata_apps: list[dict[str, Any]] = []
    for app in selected_apps:
        source_path = repo / app.manifest_path
        source_bytes = source_path.read_bytes()
        source_text = source_bytes.decode("utf-8")
        document = preview.load_yaml_document(source_text)
        if not document:
            raise ValueError(f"Unable to parse Application manifest: {app.manifest_path}")

        app_name = application_name(document)
        output_path = output_dir / f"{app_name}.yaml"
        output_path.write_bytes(source_bytes)
        generated_apps.append((app_name, output_path))
        metadata_apps.append(
            {
                "name": app_name,
                "namespace": app.namespace or "-",
                "reasons": sorted(reasons[app_name]),
            }
        )

        reason_text = ", ".join(sorted(reasons[app_name]))
        print(f"{app_name}: {reason_text}; using merged Application source revisions")

    metadata = {
        "prNumber": args.pr_number or "",
        "mergeSha": args.head,
        "applications": metadata_apps,
        "deferredDeletions": removed_apps,
    }
    Path(args.metadata_output).write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )

    if not generated_apps:
        print("No new or changed Applications need to be deployed.")
        return 0

    if not args.dry_run:
        for app_name, manifest_path in generated_apps:
            deploy_application(
                preview,
                repo=repo,
                app_name=app_name,
                manifest_path=manifest_path,
                timeout=args.timeout,
            )

    action = "Prepared" if args.dry_run else "Deployed"
    print(f"{action} {len(selected_apps)} Application(s) for PR #{args.pr_number or 'unknown'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
