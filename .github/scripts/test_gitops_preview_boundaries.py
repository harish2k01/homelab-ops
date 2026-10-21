"""Preview-only CI must not acquire a deployment execution path."""
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml
import gitops as g


class PreviewBoundaries(unittest.TestCase):
    def test_argo_rejects_mutations_before_starting_a_process(self):
        for command in ("create", "sync", "delete", "patch", "terminate-op", "set"):
            with self.subTest(command=command), patch.object(g, "run") as run:
                with self.assertRaisesRegex(g.GitOpsError, "Only read-only"):
                    g.argo("app", command, "example")
                run.assert_not_called()

    def test_readonly_access_still_reaches_argo(self):
        with patch.object(g, "run", return_value="live manifests") as run:
            self.assertEqual(g.argo("app", "manifests", "example", "--source", "live"), "live manifests")
        self.assertEqual(run.call_args.args[0][-5:], ["app", "manifests", "example", "--source", "live"])

    def test_gitops_workflows_have_no_environment_or_deployment_token(self):
        workflows = Path(__file__).parents[1] / "workflows"
        self.assertFalse((workflows / "gitops-reconcile.yaml").exists())
        for file in workflows.glob("gitops*.yaml"):
            with self.subTest(workflow=file.name):
                text = file.read_text()
                self.assertNotIn("ARGOCD_DEPLOY_AUTH_TOKEN", text)
                for job in yaml.safe_load(text)["jobs"].values():
                    self.assertNotIn("environment", job)


if __name__ == "__main__":
    unittest.main()
