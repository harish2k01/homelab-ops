"""Checks for the live-to-sync preview exposed in pull request comments."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
import gitops as g


spec = importlib.util.spec_from_file_location("preview_presentation", Path(__file__).with_name("gitops-pr-preview.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def resource(name, version="1"):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": "demo"},
            "data": {"version": version}}


def entry(resources, *, deletion=None):
    document = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {"name": "demo"},
                "spec": {"destination": {"server": "https://kubernetes.default.svc", "namespace": "demo"}}}
    side = {"document": document, "resources": resources}
    return {"name": "demo", "action": "delete" if deletion else "update", "notices": [],
            "deletion": deletion, "before": side, "after": None if deletion else copy.deepcopy(side)}


class Renderer:
    def __init__(self, *args):
        self.identities = []

    def render(self, app, **kwargs):
        return copy.deepcopy(app["resources"])


class LivePreviewTests(unittest.TestCase):
    def evaluate(self, app_entry, live, *, offline=False, failure=None):
        app = copy.deepcopy(app_entry["before"]["document"])
        app["status"] = {"resources": [{"group": "", "kind": "ConfigMap", "namespace": "demo", "name": "sample"}]}
        data = {"base": "base-sha", "head": "proposed-sha", "repository": "https://github.com/example/ops",
                "applications": [app_entry]}
        def diff(before, after, *labels):
            return "resource fields changed\n" if any(g.change_counts(before, after).values()) else ""
        with tempfile.TemporaryDirectory() as temp, patch.object(preview, "inventory", return_value={"demo": app}), \
                patch.object(g, "Renderer", Renderer), patch.object(g, "manifest_diff", side_effect=diff), \
                patch.object(g, "preview_sync", create=True,
                             side_effect=lambda live_app, proposed_doc, live, target: (live, target, [], [])), \
                patch.object(g, "argo", side_effect=failure, return_value=yaml.safe_dump_all(live)):
            return data, preview.evaluate(Path(temp), data, Path(temp), offline=offline)

    def test_counts_and_comment_describe_live_not_base(self):
        data, rows = self.evaluate(entry([resource("shared", "2"), resource("new")]),
                                   [resource("shared"), resource("old")])
        self.assertEqual(rows[0]["resourceCounts"], {"added": 1, "deleted": 1, "modified": 1})
        body = preview.render_comment(data, rows)
        self.assertIn("| `demo` | 1 | 1 | 1 | 0 | Changes after sync |", body)
        self.assertEqual(body.count("```diff"), 1)
        for unwanted in ("base-sha", "proposed-sha", "Revision", "PR head", "base → proposed", "Application configuration"):
            self.assertNotIn(unwanted, body)

    def test_unchanged_resources_have_no_empty_block(self):
        data, rows = self.evaluate(entry([resource("same")]), [resource("same")])
        body = preview.render_comment(data, rows)
        self.assertIn("No resource changes after sync", body)
        self.assertNotIn("```", body)

    def test_retained_deletion_does_not_claim_workload_removal(self):
        data, rows = self.evaluate(entry([resource("same")], deletion="retain"), [resource("same")])
        self.assertEqual(rows[0]["resourceCounts"]["deleted"], 0)
        self.assertIn("Application removed; resources retained", preview.render_comment(data, rows))

    def test_cascaded_deletion_counts_live_resources(self):
        _, rows = self.evaluate(entry([resource("old")], deletion="delete"), [resource("same"), resource("other")])
        self.assertEqual(rows[0]["resourceCounts"]["deleted"], 2)

    def test_failed_live_read_is_not_zero_changes(self):
        data, rows = self.evaluate(entry([resource("same")]), [], failure=g.GitOpsError("live read unavailable"))
        body = preview.render_comment(data, rows)
        self.assertIn("| `demo` | — | — | — | — | FAILED |", body)
        self.assertIn("live read unavailable", body)
        self.assertNotIn("```", body)

    def test_offline_never_claims_live_counts(self):
        data, rows = self.evaluate(entry([resource("same")]), [], offline=True)
        body = preview.render_comment(data, rows, offline=True)
        self.assertNotIn("resourceCounts", rows[0])
        self.assertIn("Live comparison unavailable", body)
        self.assertNotIn("| 0 | 0 | 0 |", body)

    def test_truncated_comment_keeps_full_diff_link_and_summary(self):
        rows = [{"name": f"app-{i}", "result": "Changes after sync", "resourceCounts": {"added": 1, "deleted": 2, "modified": 3},
                 "liveDiff": "x" * 20000, "error": "", "notices": []} for i in range(10)]
        body = preview.render_comment({}, rows)
        self.assertLess(len(body), 60000)
        self.assertIn("| `app-9` | 1 | 2 | 3 | 0 |", body)
        self.assertIn("Some resource details are omitted", body)
        self.assertIn("[Full diff]({{GITOPS_PREVIEW_ARTIFACT_URL}})", body)


if __name__ == "__main__":
    unittest.main()
