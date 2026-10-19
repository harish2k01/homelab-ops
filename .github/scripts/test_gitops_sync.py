"""Lifecycle and normalized comparison used by the live sync preview."""
import copy
import unittest
from unittest.mock import patch

import gitops as g


def resource(name="demo", **annotations):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
        "name": name, "namespace": "demo", "annotations": annotations}, "data": {"setting": "same"}}


def application(prune=True):
    return {"metadata": {"name": "demo"}, "spec": {"syncPolicy": {"automated": {"prune": prune}}}}


class SyncPredictionTests(unittest.TestCase):
    def test_server_default_is_not_reported_as_a_removed_field(self):
        live = resource()
        live["metadata"]["labels"] = {"injected-default": "kept"}
        desired = resource()
        with patch("gitops_native_diff.predict", return_value=([live], [live])) as native:
            before, after, notices = g.predict_sync(application(), application(), [live], [desired])
        native.assert_called_once()
        self.assertEqual(g.change_counts(before, after), {"added": 0, "deleted": 0, "modified": 0})
        self.assertEqual(notices, [])

    def test_pruning_disabled_preserves_removed_manifest(self):
        live = [resource(), resource("removed-from-chart")]
        before, after, notices = g.predict_sync(application(), application(False), live, live[:1])
        self.assertEqual(g.change_counts(before, after)["deleted"], 0)
        self.assertIn("pruning is disabled", notices[0])

    def test_pruning_enabled_removes_obsolete_resource(self):
        live = [resource(), resource("removed-from-chart")]
        before, after, notices = g.predict_sync(application(), application(), live, live[:1])
        self.assertEqual(g.change_counts(before, after)["deleted"], 1)
        self.assertEqual(notices, [])

    def test_resource_prune_false_or_confirmation_preserves_it(self):
        for option in ("Prune=false", "Prune=confirm"):
            with self.subTest(option=option):
                live = resource(**{"argocd.argoproj.io/sync-options": option})
                before, after, notices = g.predict_sync(application(), application(), [live], [])
                self.assertEqual(g.change_counts(before, after)["deleted"], 0)
                self.assertTrue(notices)

    def test_delete_false_survives_application_cascade(self):
        keep = resource("keep", **{"argocd.argoproj.io/sync-options": "Delete=false"})
        before, after, notices = g.predict_sync(application(), None, [keep, resource("delete")], [])
        self.assertEqual(g.change_counts(before, after)["deleted"], 1)
        self.assertEqual(after, [keep])
        self.assertIn("Retained ConfigMap demo/keep", notices[0])

    def test_native_failure_is_not_a_noop(self):
        desired = resource()
        desired["data"]["setting"] = "changed"
        import gitops_native_diff as native
        with patch.object(native, "predict", side_effect=native.NativeDiffError("Argo endpoint unavailable")):
            with self.assertRaisesRegex(g.GitOpsError, "Argo endpoint unavailable"):
                g.predict_sync(application(), application(), [resource()], [desired])

    def test_hook_is_a_sync_task_not_a_persistent_resource_modification(self):
        hook = resource(**{"argocd.argoproj.io/hook": "PreSync"})
        before, after, notices = g.predict_sync(application(), application(), [hook], [copy.deepcopy(hook)])
        self.assertEqual(before, [])
        self.assertEqual(after, [])
        self.assertIn("runs during sync", notices[0])

    def test_added_hook_is_identified_as_a_sync_task(self):
        hook = resource(**{"helm.sh/hook": "pre-install,pre-upgrade"})
        before, after, _ = g.predict_sync(None, application(), [], [hook])
        self.assertEqual(g.change_counts(before, after)["added"], 1)
        self.assertIn("+ ADDED ConfigMap demo/demo (sync hook)", g.manifest_diff(before, after, "live", "sync"))
