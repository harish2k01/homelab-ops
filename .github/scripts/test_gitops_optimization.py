"""Regression cases for preview coverage, no-op setup and API load."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
import gitops as g
import test_gitops as fixtures

preview = fixtures.preview


class Renderer:
    calls = []

    def __init__(self, *args):
        self.identities = []

    def render(self, app, **kwargs):
        self.calls.append(app)
        if app.get("unavailable"):
            raise g.GitOpsError("old chart is unavailable")
        return copy.deepcopy(app["resources"])


def planned(before, after):
    item = fixtures.entry()
    item["before"] = {"document": fixtures.app(), "resources": before}
    item["after"] = {"document": fixtures.app(), "resources": after}
    return fixtures.plan_data([item])


def live_app(resources, name="demo"):
    app = fixtures.app(name)
    app["status"] = {"resources": [dict(zip(("group", "kind", "namespace", "name"), g.resource_key(item))) for item in resources]}
    return app


class OptimizedPreflight(unittest.TestCase):
    def run_plan(self, data, *, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, metadata = root / "comment.md", root / "github-output"
            output.write_text("stale successful output", encoding="utf-8")
            args = ["preview", "--base", "a", "--head", "b", "--repo", "example/ops", "--plan-only",
                    "--output", str(output), "--output-dir", str(root / "artifact")]
            with patch("sys.argv", args), patch.dict(os.environ, {"GITHUB_OUTPUT": str(metadata)}), \
                    patch.object(g, "merge_revision", return_value="merged"), \
                    patch.object(g, "plan", return_value=data, side_effect=failure), \
                    patch.object(preview, "evaluate") as evaluate, patch.object(g, "argo") as argo:
                code = preview.main()
                evaluate.assert_not_called()
                argo.assert_not_called()
            return code, output.read_text() if output.exists() else None, metadata.read_text(), (root / "artifact" / "summary.json").read_text()

    def test_docs_only_needs_no_cluster_or_render_tools(self):
        code, body, metadata, artifact = self.run_plan(fixtures.plan_data([]))
        self.assertEqual(code, 0)
        self.assertIn("No affected Applications", body)
        self.assertIn("has_changes=false", metadata)

    def test_affected_plan_does_not_publish_secrets_or_stale_success(self):
        data = fixtures.plan_data([fixtures.entry()])
        data["applications"][0]["after"]["document"]["spec"]["source"]["helm"] = {"values": "password: private-value"}
        code, body, metadata, artifact = self.run_plan(data)
        self.assertEqual(code, 0)
        self.assertIsNone(body)
        self.assertIn("has_changes=true", metadata)
        self.assertNotIn("private-value", artifact)

    def test_invalid_or_unmapped_plan_cannot_become_green_noop(self):
        for data, failure in ((fixtures.plan_data([]), g.GitOpsError("invalid policy")),
                              ({**fixtures.plan_data([]), "unmapped": ["manifests/unowned.yaml"]}, None)):
            with self.subTest(failure=failure):
                code, body, metadata, artifact = self.run_plan(data, failure=failure)
                self.assertEqual(code, 1)
                self.assertIn("Preview failed", body)
                self.assertIn("has_changes=false", metadata)


class EfficientScopeAndRendering(unittest.TestCase):
    def test_zero_instance_crd_is_read_lazily_only_for_its_group(self):
        definition = fixtures.crd()
        definition["metadata"]["uid"] = "trusted-uid"
        owner = fixtures.app("operator")
        owner["status"] = {"resources": [{"kind": "CustomResourceDefinition", "name": definition["metadata"]["name"]}]}
        registry = g.ScopeRegistry()
        registry.inventory({"operator": owner})
        with patch.dict(os.environ, {"GITOPS_DISCOVERY": "false"}, clear=True), \
                patch.object(g, "argo", return_value=json.dumps(definition)) as argo:
            self.assertFalse(registry.namespaced(g.cluster_key(owner), "example.io", "Widget"))
            self.assertFalse(registry.namespaced(g.cluster_key(owner), "example.io", "Widget"))
        argo.assert_called_once()
        self.assertEqual(argo.call_args.args[:3], ("app", "get-resource", "operator"))

    def test_external_api_uses_one_live_crd_uid_anchor(self):
        definition = fixtures.crd()
        definition["metadata"]["uid"] = "trusted-uid"
        owner = fixtures.app("operator")
        owner["status"] = {"resources": [{"kind": "CustomResourceDefinition", "name": definition["metadata"]["name"]}]}
        for actual_uid in ("trusted-uid", "wrong-cluster"):
            registry = g.ScopeRegistry()
            registry.inventory({"operator": owner})
            def execute(command, **kwargs):
                if "config" in command:
                    return json.dumps({"clusters": [{"cluster": {"server": "https://external-api"}}]})
                if "customresourcedefinitions/" in command[-1]:
                    return json.dumps({"metadata": {"uid": actual_uid}})
                return json.dumps({"versions": []})
            with patch.dict(os.environ, {"GITOPS_DISCOVERY_KUBECONFIG": "readonly"}, clear=True), \
                    patch.object(g, "argo", return_value=json.dumps(definition)) as argo, patch.object(g, "run", side_effect=execute):
                if actual_uid == "trusted-uid":
                    registry.discover(g.cluster_key(owner))
                else:
                    with self.assertRaisesRegex(g.GitOpsError, "destination mismatch"):
                        registry.discover(g.cluster_key(owner))
            argo.assert_called_once()

    def test_shared_chart_archive_downloads_once_but_version_bump_downloads_again(self):
        calls = []
        def execute(command, **kwargs):
            calls.append(command)
            if command[:2] == ["helm", "pull"]:
                (Path(command[command.index("--destination") + 1]) / "demo.tgz").write_bytes(b"chart")
                return ""
            return yaml.safe_dump(fixtures.resource())
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "run", side_effect=execute), \
                patch.object(g, "cluster_capabilities", return_value=("v1.35.6", ("v1",))):
            shared = {}
            for index, version in enumerate(("1.0.0", "1.0.0", "2.0.0")):
                root = Path(directory) / str(index)
                root.mkdir()
                renderer = g.Renderer(root, "sha", fixtures.URL, root, chart_cache=shared)
                renderer.render({"document": fixtures.app(source={"repoURL": "https://charts.example", "chart": "demo", "targetRevision": version})}, normalize=False)
        self.assertEqual(sum(command[:2] == ["helm", "pull"] for command in calls), 2)


class UsefulPartialPreview(unittest.TestCase):
    def evaluate(self, data, live, prediction, *, unrelated=None):
        inventory = {"demo": live_app(live)}
        if unrelated:
            inventory.update(unrelated)
        Renderer.calls = []
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, "inventory", return_value=inventory), \
                patch.object(g, "Renderer", Renderer), patch.object(g, "argo", return_value=yaml.safe_dump_all(live)) as argo, \
                patch("gitops_native_diff.predict_resources", return_value=prediction):
            rows = preview.evaluate(Path(directory), data, Path(directory))
        return rows, argo.call_args_list

    def test_valid_changes_survive_immutable_claim_template_blocker(self):
        before_config = {**fixtures.resource(), "metadata": {"name": "config", "namespace": "demo"}}
        after_config = copy.deepcopy(before_config)
        after_config["data"]["version"] = "2"
        stateful = {"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "logs", "namespace": "demo"},
                    "spec": {"volumeClaimTemplates": [{"metadata": {"name": "storage"}, "spec": {"resources": {"requests": {"storage": "5Gi"}}}}]}}
        proposed = copy.deepcopy(stateful)
        proposed["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] = "10Gi"
        data = planned([before_config, stateful], [after_config, proposed])
        blocker = {"identity": list(g.resource_key(stateful)), "category": "immutable", "message": "Immutable StatefulSet change"}
        rows, calls = self.evaluate(data, [before_config, stateful], {"before": [before_config], "after": [after_config], "blockers": [blocker], "complete": False})
        row = rows[0]
        self.assertEqual(row["resourceCounts"], {"added": 0, "deleted": 0, "modified": 1})
        self.assertIn("BLOCKED", row["result"])
        self.assertIn("ConfigMap demo/config", row["liveDiff"])
        self.assertNotIn("StatefulSet", row["liveDiff"])
        self.assertIn("5Gi", row["blockedIntentDiff"])
        self.assertIn("10Gi", row["blockedIntentDiff"])
        body = preview.render_comment(data, rows)
        self.assertIn("| 0 | 0 | 1 | 1 | BLOCKED", body)
        self.assertIn("Rendered intent", body)
        self.assertIn("not a validated sync result", body)

    def test_old_chart_failure_and_unrelated_crd_owner_do_not_block_live_preview(self):
        live = {**fixtures.resource(), "metadata": {"name": "config", "namespace": "demo"}}
        target = copy.deepcopy(live)
        target["data"]["version"] = "2"
        data = planned([live], [target])
        data["applications"][0]["before"]["unavailable"] = True
        unrelated = fixtures.app("unrelated")
        unrelated["status"] = {"resources": [{"group": "apiextensions.k8s.io", "kind": "CustomResourceDefinition", "name": "widgets.example.io"}]}
        rows, calls = self.evaluate(data, [live], {"before": [live], "after": [target], "blockers": [], "complete": True}, unrelated={"unrelated": unrelated})
        self.assertEqual(rows[0]["result"], "Changes after sync")
        self.assertEqual(len(Renderer.calls), 1)
        self.assertEqual([call.args[:3] for call in calls], [("app", "manifests", "demo")])

    def test_changed_application_policy_is_not_a_zero_resource_success(self):
        app = fixtures.app()
        proposed = copy.deepcopy(app)
        proposed["spec"]["project"] = "other-project"
        with patch("gitops_native_diff.predict_resources") as native:
            with self.assertRaisesRegex(g.GitOpsError, "project changed"):
                g.preview_sync(app, proposed, [fixtures.resource()], [fixtures.resource()])
        native.assert_not_called()

    def test_partial_blocker_keeps_the_required_check_failed(self):
        row = {"name": "demo", "result": "BLOCKED", "resourceCounts": {"added": 0, "deleted": 0, "modified": 1},
               "blockers": [{"identity": ["apps", "StatefulSet", "demo", "logs"], "category": "immutable", "message": "Immutable field"}]}
        with tempfile.TemporaryDirectory() as directory:
            args = ["preview", "--base", "a", "--head", "b", "--repo", "example/ops", "--output", str(Path(directory) / "comment.md"),
                    "--output-dir", str(Path(directory) / "artifact")]
            with patch("sys.argv", args), patch.object(g, "merge_revision", return_value="merged"), \
                    patch.object(g, "plan", return_value=fixtures.plan_data([fixtures.entry()])), \
                    patch.object(preview, "evaluate", return_value=[row]):
                self.assertEqual(preview.main(), 1)

    def test_replacement_strategy_is_not_misrepresented_as_update_prediction(self):
        app = fixtures.app()
        proposed = copy.deepcopy(app)
        proposed["spec"]["syncPolicy"] = {"syncOptions": ["Replace=true"]}
        before, after = fixtures.resource(), fixtures.resource(data={"version": "2"})
        with patch("gitops_native_diff.predict_resources") as native:
            old, new, notices, blockers = g.preview_sync(app, proposed, [before], [after])
        native.assert_not_called()
        self.assertEqual(old, [])
        self.assertEqual(new, [])
        self.assertEqual(blockers[0]["category"], "sync-strategy")

    def test_nullable_core_group_does_not_bypass_existing_ownership(self):
        resource = {**fixtures.resource(), "metadata": {"name": "config", "namespace": "demo"}}
        owner = fixtures.app("other")
        owner["status"] = {"resources": [{"group": None, "kind": "ConfigMap", "name": "config", "namespace": "demo"}]}
        rows, calls = self.evaluate(planned([], [resource]), [], {"before": [], "after": [], "blockers": [], "complete": True}, unrelated={"other": owner})
        self.assertEqual(rows[0]["result"], "FAILED")
        self.assertIn("tracked by other", rows[0]["error"])

    def test_sealed_payload_redaction_preserves_modified_signal(self):
        before = {"apiVersion": "bitnami.com/v1alpha1", "kind": "SealedSecret", "metadata": {"name": "credentials"},
                  "spec": {"encryptedData": {"password": "old-private-ciphertext"}, "data": "old-legacy-ciphertext"}}
        after = copy.deepcopy(before)
        after["spec"]["encryptedData"]["password"] = "new-private-ciphertext"
        after["spec"]["data"] = "new-legacy-ciphertext"
        for left, right in (([before], [after]), ([], [after]), ([before], [])):
            text = g.manifest_diff(left, right, "live", "sync")
            self.assertNotIn("ciphertext", text)
        self.assertIn("SealedSecret content changed", g.manifest_diff([before], [after], "live", "sync"))
        self.assertEqual(g.change_counts([before], [after])["modified"], 1)
        self.assertNotIn("ciphertext", json.dumps(g.public_resources([before, after])))


if __name__ == "__main__":
    unittest.main()
