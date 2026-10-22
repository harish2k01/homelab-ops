"""Preflight and tool caches must not turn missing comparisons into green checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


SCRIPTS = Path(__file__).resolve().parent
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if Path("C:/Program Files/Git/bin/bash.exe").exists() else None)
NODE = shutil.which("node")


class WorkflowSetup(unittest.TestCase):
    def setUp(self):
        workflow = SCRIPTS.parent / "workflows/gitops-pr-preview.yaml"
        self.steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["preview"]["steps"]

    def test_plan_precedes_heavy_tools_without_cluster_credentials(self):
        plan_index = next(i for i, step in enumerate(self.steps) if step.get("id") == "plan")
        plan = self.steps[plan_index]
        self.assertIn("--plan-only", plan["run"])
        self.assertFalse(any(key.startswith("ARGOCD_") for key in plan.get("env", {})))
        for index, step in enumerate(self.steps):
            if step.get("uses", "").startswith(("azure/setup-helm", "azure/setup-kubectl")) or step.get("name") in {"Install Argo CD CLI", "Install dyff", "Generate GitOps preview"}:
                self.assertGreater(index, plan_index)
                self.assertEqual(step["if"], "steps.plan.outputs.has_changes == 'true'")

    def test_checkouts_and_comparisons_use_the_resolved_revisions(self):
        checkouts = [step for step in self.steps if step.get("uses", "").startswith("actions/checkout")]
        self.assertEqual(checkouts[0]["with"]["ref"], "${{ steps.revisions.outputs.base }}")
        self.assertEqual(checkouts[1]["with"]["ref"], "${{ steps.revisions.outputs.head }}")
        for step in self.steps:
            if step.get("id") in {"plan", "render"}:
                self.assertEqual(step["env"]["GITHUB_BASE_SHA"], "${{ steps.revisions.outputs.base }}")
        publisher = next(step for step in self.steps if step.get("name") == "Publish preview result")
        self.assertEqual(publisher["env"]["GITOPS_BASE_SHA"], "${{ steps.revisions.outputs.base }}")

    @unittest.skipUnless(NODE, "Node is required to exercise the actual revision resolver")
    def test_revision_resolver_refreshes_base_and_rejects_obsolete_heads(self):
        script = next(step for step in self.steps if step.get("id") == "revisions")["with"]["script"]
        harness = r'''
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const outputs = {};
let baseReads = 0;
const github = {rest: {
  pulls: {get: async () => ({data: {state: input.state, head: {sha: input.head}, base: {ref: 'main'}}})},
  git: {getRef: async ({ref}) => {baseReads++; if (ref !== 'heads/main') throw new Error('wrong ref'); return {data: {object: {sha: 'current-base'}}}; }},
}};
const context = {repo: {owner: 'owner', repo: 'ops'}, payload: {pull_request: {number: 1, head: {sha: 'event-head'}, base: {sha: 'old-base'}}}};
const run = new (Object.getPrototypeOf(async function () {}).constructor)('github', 'context', 'core', input.script);
run(github, context, {setOutput: (key, value) => {outputs[key] = value;}})
  .then(() => console.log(JSON.stringify({outputs, baseReads})))
  .catch(error => console.log(JSON.stringify({error: error.message, baseReads})));
'''
        for state, head, success in (("open", "event-head", True), ("open", "new-head", False), ("closed", "event-head", False)):
            with self.subTest(state=state, head=head):
                result = subprocess.run([NODE, "-e", harness], input=json.dumps({"script": script, "state": state, "head": head}),
                                        text=True, capture_output=True, check=True)
                data = json.loads(result.stdout)
                if success:
                    self.assertEqual(data, {"outputs": {"base": "current-base", "head": "event-head"}, "baseReads": 1})
                else:
                    self.assertIn("Superseded preview", data["error"])
                    self.assertEqual(data["baseReads"], 0)

    @unittest.skipUnless(BASH, "Bash is required to execute the actual workflow completion gate")
    def test_final_gate_accepts_noop_but_rejects_missing_or_failed_comparisons(self):
        gate = next(step for step in self.steps if step.get("name") == "Require complete successful preview")["run"]
        for plan, changed, render, success in (("success", "false", "skipped", True),
                                               ("success", "true", "success", True),
                                               ("failure", "false", "skipped", False),
                                               ("success", "true", "failure", False),
                                               ("success", "true", "skipped", False),
                                               ("success", "", "skipped", False)):
            with self.subTest(plan=plan, changed=changed, render=render):
                env = {**os.environ, "PLAN_OUTCOME": plan, "HAS_CHANGES": changed, "RENDER_OUTCOME": render}
                result = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-c", gate], env=env, capture_output=True)
                self.assertEqual(result.returncode == 0, success)


@unittest.skipUnless(BASH, "Bash is required to exercise verified cached downloads")
class VerifiedDownloads(unittest.TestCase):
    def test_cache_hit_avoids_network_and_corrupt_cache_is_replaced(self):
        script = r'''
source "$1"
count="$2"; source_file="$3"; destination="$4"; checksum="$5"
curl() { printf x >> "$count"; cp "$source_file" "${@: -1}"; }
gitops_download_cached https://example.invalid/tool "$checksum" "$destination"
gitops_download_cached https://example.invalid/tool "$checksum" "$destination"
[ "$(cat "$count")" = x ]
printf corrupted > "$destination"
gitops_download_cached https://example.invalid/tool "$checksum" "$destination"
[ "$(cat "$count")" = xx ]
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "verified-tool"
            source.write_bytes(b"verified payload")
            destination = root / "cache/tool"
            result = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-c", script, "test",
                                     (SCRIPTS / "gitops-download.sh").as_posix(), (root / "calls").as_posix(),
                                     source.as_posix(), destination.as_posix(), hashlib.sha256(source.read_bytes()).hexdigest()], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_failed_checksum_never_populates_the_cache(self):
        script = r'''
source "$1"
curl() { printf corrupted > "${@: -1}"; }
gitops_download_cached https://example.invalid/tool "$2" "$3"
'''
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cache/tool"
            result = subprocess.run([BASH, "--noprofile", "--norc", "-e", "-c", script, "test",
                                     (SCRIPTS / "gitops-download.sh").as_posix(), hashlib.sha256(b"verified payload").hexdigest(),
                                     destination.as_posix()], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.glob("*.download.*")), [])


if __name__ == "__main__":
    unittest.main()
