"""Regression coverage for recovery and deployment boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from test_gitops import deploy, entry, g, plan_data


class DeploymentBoundaries(unittest.TestCase):
    def test_empty_plan_needs_no_cluster_access_and_records_no_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "argo") as argo:
            output = Path(directory)
            self.assertFalse(deploy.reconcile(output, plan_data([]), output, dry_run=True))
            argo.assert_not_called()
            self.assertEqual(json.loads((output / "results.json").read_text())["applications"], [])
            self.assertIn("No deployment changes", (output / "report.md").read_text())

    def test_noop_job_graph_skips_environment_but_still_reports_and_checks_cursor(self):
        workflow = Path(__file__).parents[1] / "workflows" / "gitops-reconcile.yaml"
        jobs = yaml.safe_load(workflow.read_text())["jobs"]
        self.assertNotIn("environment", jobs["plan"])
        self.assertIn("plan", jobs["deploy"]["needs"])
        self.assertEqual(jobs["deploy"]["if"], "needs.plan.outputs.has_changes == 'true'")
        self.assertIn("plan", jobs["report"]["needs"])
        cursor = next(step for step in jobs["report"]["steps"] if step.get("name") == "Verify cursor revision for no-op runs")
        self.assertEqual(cursor["if"], "needs.plan.outputs.has_changes == 'false'")

    def test_failed_replan_cannot_publish_success_from_an_earlier_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "report.md").write_text("synced and healthy", encoding="utf-8")
            (output / "results.json").write_text('{"success": true}', encoding="utf-8")
            args = ["apply", "--base", "a", "--head", "b", "--repo", "example/ops", "--output-dir", directory]
            with patch("sys.argv", args), patch.object(g, "plan", side_effect=g.GitOpsError("invalid policy")):
                self.assertEqual(deploy.main(), 1)
            self.assertIn("invalid policy", (output / "report.md").read_text())
            self.assertNotIn("synced and healthy", (output / "report.md").read_text())
            self.assertFalse((output / "results.json").exists())

    def test_explicit_retain_and_delete_policies_are_passed_to_argo(self):
        for policy, cascade in (("retain", "false"), ("delete", "true")):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as directory:
                calls = []

                def argo(*args, **kwargs):
                    calls.append(args)
                    return '[{"metadata": {"name": "demo"}}]' if args[:2] == ("app", "list") else ""

                with patch.object(g, "argo", side_effect=argo), patch.object(deploy, "current_main"):
                    result = deploy.reconcile(Path(directory), plan_data([entry(action="delete", deletion=policy)]), Path(directory))
                self.assertFalse(result)
                self.assertIn(("app", "delete", "demo", f"--cascade={cascade}", "--yes"), calls)
                self.assertTrue(any(call[:3] == ("app", "wait", "demo") and "--delete" in call for call in calls))

    def test_failed_wave_blocks_later_waves_while_same_wave_independent_app_finishes(self):
        calls = []

        def argo(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("app", "list"):
                return "[]"
            if args[:3] == ("app", "create", "provider"):
                raise g.GitOpsError("repository forbidden")
            return ""

        entries = [entry("provider", wave=0), entry("independent", wave=0), entry("later", wave=1)]
        health = {"sync": "Synced", "health": "Healthy"}
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "argo", side_effect=argo), patch.object(deploy, "current_main"), patch.object(deploy, "diagnostics", return_value=health):
            self.assertTrue(deploy.reconcile(Path(directory), plan_data(entries), Path(directory)))
            rows = json.loads((Path(directory) / "results.json").read_text())["applications"]
        self.assertEqual(rows[1]["result"], "synced and healthy")
        self.assertEqual(rows[2]["result"], "not attempted")
        self.assertIn("failed dependencies/waves", rows[2]["error"])
        self.assertFalse(any(call[:3] == ("app", "create", "later") for call in calls))


if __name__ == "__main__":
    unittest.main()
