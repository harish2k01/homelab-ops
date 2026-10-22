import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml
import gitops as g


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


preview = module("preview", "gitops-pr-preview.py")
URL = "https://github.com/example/ops"


def app(name="demo", path="manifests/demo", **spec):
    return {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
            "metadata": {"name": name, "namespace": "argocd"},
            "spec": {"project": "default", "source": {"repoURL": URL, "targetRevision": "main", "path": path},
                     "destination": {"server": "https://kubernetes.default.svc", "namespace": "demo"}, **spec}}


def resource(name="demo", **extra):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": {"version": "1"}, **extra}


def entry(name="demo", **extra):
    document = app(name)
    wrapped = {"document": document, "path": f"argocd-apps/{name}.yaml"}
    return {"name": name, "before": wrapped, "after": wrapped, "action": "update", "wave": 0,
            "dependencies": [], "notices": [], "deletion": None, **extra}


def plan_data(entries):
    return {"base": "a" * 40, "head": "b" * 40, "repository": URL, "applications": entries, "unmapped": []}


def fixture_discovery(registry, cluster):
    registry.register(cluster, "", "ConfigMap", True, "fixture API discovery")


class KnownScopes(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.scope_patch = patch.object(g.ScopeRegistry, "discover", fixture_discovery)
        self.scope_patch.start()
        self.addCleanup(self.scope_patch.stop)


class Repository(KnownScopes):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("argocd-apps/nested/demo.yaml", yaml.safe_dump(app()))
        self.write("manifests/demo/config.yaml", yaml.safe_dump(resource()))
        self.base = self.commit()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return g.git(self.root, *args)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def plan(self):
        return g.plan(self.root, self.base, self.commit(), URL)

    def test_nested_apps_and_values_only_changes_use_same_planner(self):
        self.write("manifests/demo/config.yaml", yaml.safe_dump(resource(data={"version": "2"})))
        data = self.plan()
        self.assertEqual([item["name"] for item in data["applications"]], ["demo"])
        self.assertEqual(data["unmapped"], [])

    def test_old_source_paths_still_map_after_spec_changes(self):
        self.write("argocd-apps/nested/demo.yaml", yaml.safe_dump(app(path="manifests/new")))
        self.write("manifests/demo/config.yaml", yaml.safe_dump(resource(data={"version": "2"})))
        self.write("manifests/new/config.yaml", yaml.safe_dump(resource()))
        self.assertEqual(self.plan()["unmapped"], [])

    def test_docs_only_has_no_deployment_changes(self):
        self.write("README.md", "Documentation")
        self.assertEqual(self.plan()["applications"], [])

    def test_empty_policy_does_not_redeploy_every_declared_app(self):
        self.write(g.POLICY, "dependencies: {}\ndeletions: {}\n")
        self.assertEqual(self.plan()["applications"], [])

    def test_shared_kustomize_base_maps_to_its_consumer(self):
        self.write("manifests/demo/kustomization.yaml", "resources: [../shared]\n")
        self.write("manifests/shared/kustomization.yaml", "resources: [config.yaml]\n")
        self.write("manifests/shared/config.yaml", yaml.safe_dump(resource()))
        self.base = self.commit()
        self.write("manifests/shared/config.yaml", yaml.safe_dump(resource(data={"version": "2"})))
        data = self.plan()
        self.assertEqual([item["name"] for item in data["applications"]], ["demo"])
        self.assertEqual(data["unmapped"], [])

    def test_proposed_merge_preserves_newer_base_applications(self):
        self.git("switch", "-c", "feature")
        self.write("manifests/demo/config.yaml", yaml.safe_dump(resource(data={"version": "2"})))
        head = self.commit()
        self.git("switch", "main")
        self.write("argocd-apps/newer.yaml", yaml.safe_dump(app("newer", path="manifests/newer")))
        base = self.commit()
        proposed = g.merge_revision(self.root, base, head)
        data = g.plan(self.root, base, proposed, URL)
        self.assertEqual([item["name"] for item in data["applications"]], ["demo"])

    def test_conflicting_merge_is_rejected(self):
        self.git("switch", "-c", "feature")
        self.write("manifests/demo/config.yaml", "feature")
        head = self.commit()
        self.git("switch", "main")
        self.write("manifests/demo/config.yaml", "main")
        base = self.commit()
        with self.assertRaises(g.GitOpsError):
            g.merge_revision(self.root, base, head)

    def test_unmapped_files_fail_explicitly(self):
        self.write("infra/unowned.yaml", yaml.safe_dump(resource()))
        self.assertEqual(self.plan()["unmapped"], ["infra/unowned.yaml"])

    def test_deletion_requires_explicit_decision(self):
        (self.root / "argocd-apps/nested/demo.yaml").unlink()
        removed = self.plan()["applications"][0]
        self.assertEqual(removed["action"], "delete")
        self.assertIn("BLOCKED:", removed["notices"][0])

    def test_rename_keeps_old_and_new_identities(self):
        self.write("argocd-apps/nested/demo.yaml", yaml.safe_dump(app("renamed")))
        result = self.plan()["applications"]
        self.assertEqual({item["name"]: item["action"] for item in result}, {"demo": "delete", "renamed": "create"})

    def test_duplicate_applications_fail(self):
        self.write("argocd-apps/duplicate.yaml", yaml.safe_dump(app()))
        with self.assertRaisesRegex(g.GitOpsError, "Duplicate"):
            self.plan()

    def test_unknown_objects_fail_instead_of_disappearing(self):
        self.write("argocd-apps/appset.yaml", "apiVersion: argoproj.io/v1alpha1\nkind: ApplicationSet\n")
        with self.assertRaisesRegex(g.GitOpsError, "Unsupported object"):
            self.plan()

    def test_no_thirty_app_truncation(self):
        for index in range(35):
            self.write(f"argocd-apps/app-{index}.yaml", yaml.safe_dump(app(f"app-{index}")))
        self.assertEqual(len(self.plan()["applications"]), 35)

    def test_dependency_cycle_fails(self):
        self.write("argocd-apps/other.yaml", yaml.safe_dump(app("other")))
        self.write(g.POLICY, "dependencies:\n  demo: [other]\n  other: [demo]\n")
        with self.assertRaisesRegex(g.GitOpsError, "cycle"):
            self.plan()

    def test_shared_values_select_all_consumers(self):
        for name in ("demo", "other"):
            document = app(name, sources=[{"repoURL": "https://charts.example", "chart": "demo", "targetRevision": "1.0.0",
                                         "helm": {"valueFiles": ["$values/charts/shared.yaml"]}},
                                        {"repoURL": URL, "targetRevision": "main", "ref": "values"}])
            document["spec"].pop("source")
            self.write(f"argocd-apps/{name}.yaml", yaml.safe_dump(document))
        (self.root / "argocd-apps/nested/demo.yaml").unlink()
        self.write("charts/shared.yaml", "enabled: true\n")
        self.base = self.commit()
        self.write("charts/shared.yaml", "enabled: false\n")
        self.assertEqual({item["name"] for item in self.plan()["applications"]}, {"demo", "other"})

    def test_renderer_uses_exact_snapshot_not_worktree(self):
        self.write("manifests/demo/config.yaml", yaml.safe_dump(resource(data={"version": "uncommitted"})))
        with tempfile.TemporaryDirectory() as directory:
            rendered = g.Renderer(self.root, self.base, URL, Path(directory)).render({"document": app()})
        self.assertEqual(rendered[0]["data"]["version"], "1")


class Rendering(KnownScopes):
    def test_chart_upgrade_reaches_helm_and_records_digest(self):
        commands = []
        def execute(args, **kwargs):
            commands.append(args)
            if args[:2] == ["helm", "pull"]:
                (Path(args[args.index("--destination") + 1]) / "demo.tgz").write_bytes(b"chart")
                return ""
            return yaml.safe_dump(resource(data={"version": "2.0.0"}))
        document = app(source={"repoURL": "https://charts.example", "chart": "demo", "targetRevision": "2.0.0"})
        with tempfile.TemporaryDirectory() as directory, patch.object(g, "run", side_effect=execute), patch.object(g, "cluster_capabilities", return_value=("v1.35.6", ("v1",))):
            renderer = g.Renderer(Path(directory), "sha", URL, Path(directory))
            result = renderer.render({"document": document})
            self.assertEqual(result[0]["data"]["version"], "2.0.0")
            self.assertEqual(commands[0][commands[0].index("--version") + 1], "2.0.0")
            self.assertEqual(renderer.identities[0]["sha256"], g.hashlib.sha256(b"chart").hexdigest())

    def test_unknown_source_options_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer = g.Renderer(Path(directory), "sha", URL, Path(directory))
            with self.assertRaisesRegex(g.GitOpsError, "Unsupported source"):
                renderer.render({"document": app(source={"plugin": {"name": "custom"}})})

    def test_path_traversal_rejected(self):
        with self.assertRaises(g.GitOpsError):
            g.inside(Path(tempfile.gettempdir()) / "repo", "../../credentials")

    def test_secret_content_is_redacted_but_change_is_visible(self):
        old = resource(kind="Secret", data={"password": "old-password"})
        new = resource(kind="Secret", data={"password": "new-password"})
        result = g.manifest_diff([old], [new], "base", "head")
        self.assertIn("Secret content changed", result)
        self.assertNotIn("password", result)

    def test_application_inline_inputs_are_not_published(self):
        document = app(source={"helm": {"values": "password: sensitive"}})
        self.assertNotIn("sensitive", json.dumps(g.public_resources([document])))

    def test_crd_plain_equals_enum_is_a_string(self):
        items = g.resources("apiVersion: example.io/v1\nkind: Widget\nmetadata: {name: demo}\nspec:\n  operators:\n    - =\n    - '!='\n")
        self.assertEqual(items[0]["spec"]["operators"], ["=", "!="])

    def test_invalid_rendered_object_fails(self):
        with self.assertRaises(g.GitOpsError):
            g.resources("hello: world")

    def test_dyff_shows_changed_field_without_unchanged_manifest(self):
        old, new = resource(), resource(data={"version": "2"})
        text = g.manifest_diff([old], [new], "base", "proposed")
        self.assertIn("ConfigMap demo (modified)", text)
        self.assertIn("version", text)
        self.assertNotIn("apiVersion:", text)

    def test_additions_and_deletions_show_resource_fields(self):
        text = g.manifest_diff([resource("removed", data={"setting": "old"})],
                               [resource("added", data={"setting": "new"})], "base", "proposed")
        self.assertIn("+ ADDED ConfigMap added", text)
        self.assertIn("- DELETED ConfigMap removed", text)
        self.assertIn("+ data:\n+   setting: new", text)
        self.assertIn("- data:\n-   setting: old", text)
        self.assertIn("+ apiVersion: v1", text)

    def test_added_and_deleted_resources_redact_secrets_and_runtime_metadata(self):
        secret = resource(kind="Secret", data={"password": "private-value"})
        secret["stringData"] = {"token": "private-token"}
        secret["metadata"].update(uid="runtime-uid", resourceVersion="123", managedFields=[{}])
        secret["status"] = {"detail": "runtime-status"}
        for before, after, prefix in (([], [secret], "+"), ([secret], [], "-")):
            with self.subTest(prefix=prefix):
                text = g.manifest_diff(before, after, "live", "sync")
                self.assertIn(f"{prefix} data:\n{prefix}   password: <redacted>", text)
                self.assertIn(f"{prefix} stringData:\n{prefix}   token: <redacted>", text)
                for hidden in ("private-value", "private-token", "runtime-uid", "resourceVersion", "managedFields", "runtime-status"):
                    self.assertNotIn(hidden, text)

    def test_null_secret_data_and_annotations_are_supported(self):
        document = resource(kind="Secret", data=None)
        document["metadata"]["annotations"] = None
        self.assertEqual(g.public_resources([document])[0]["data"], {})


class Preview(KnownScopes):
    def test_render_error_is_failed_result_not_green_no_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g.Renderer, "render", side_effect=g.GitOpsError("download timeout")):
            rows = preview.evaluate(Path(directory), plan_data([entry()]), Path(directory), offline=True)
        self.assertEqual(rows[0]["result"], "FAILED")
        self.assertIn("download timeout", rows[0]["error"])

    def test_proposed_collision_marks_both_apps_failed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(g.Renderer, "render", return_value=[resource()]):
            rows = preview.evaluate(Path(directory), plan_data([entry("one"), entry("two")]), Path(directory), offline=True)
        self.assertTrue(all(row["result"] == "FAILED" for row in rows))

    def test_live_read_failure_is_failed_not_empty_cluster(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(preview, "inventory", return_value={"demo": app()}), patch.object(g.Renderer, "render", return_value=[resource()]), patch.object(g, "argo", side_effect=g.GitOpsError("permission denied")):
            rows = preview.evaluate(Path(directory), plan_data([entry()]), Path(directory))
        self.assertEqual(rows[0]["result"], "FAILED")


def crd(scope="Cluster", group="example.io", kind="Widget"):
    return {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
            "metadata": {"name": "widgets." + group},
            "spec": {"group": group, "scope": scope, "names": {"kind": kind, "plural": "widgets"},
                     "versions": [{"name": "v1", "served": True, "storage": True}]}}


def custom_resource():
    return {"apiVersion": "example.io/v1", "kind": "Widget", "metadata": {"name": "sample"}}


class DynamicScopes(unittest.TestCase):
    def test_same_chart_crd_and_instance_both_scopes(self):
        for scope in ("Cluster", "Namespaced"):
            with self.subTest(scope=scope), tempfile.TemporaryDirectory() as directory:
                document = app(source={"repoURL": "https://charts.example", "chart": "demo", "targetRevision": "1.0.0"})
                text = yaml.safe_dump_all([custom_resource(), crd(scope)])
                with patch.object(g.Renderer, "helm", return_value=text):
                    rendered = g.Renderer(Path(directory), "sha", URL, Path(directory)).render({"document": document})
                widget = next(item for item in rendered if item["kind"] == "Widget")
                self.assertEqual(widget["metadata"].get("namespace"), "demo" if scope == "Namespaced" else None)

    def test_cross_application_new_crd_is_order_independent(self):
        for scope in ("Cluster", "Namespaced"):
            for names in (("consumer", "provider"), ("provider", "consumer")):
                with self.subTest(scope=scope, names=names), tempfile.TemporaryDirectory() as directory:
                    def render(renderer, wrapped, **kwargs):
                        return [crd(scope)] if wrapped["document"]["metadata"]["name"] == "provider" else [custom_resource()]
                    with patch.object(g.Renderer, "render", render):
                        rows = preview.evaluate(Path(directory), plan_data([entry(name) for name in names]), Path(directory), offline=True)
                    self.assertFalse(any(row["result"] == "FAILED" for row in rows), rows)

    def test_existing_crd_without_instances_is_read_from_its_owner(self):
        provider = app("provider")
        provider["status"] = {"resources": [{"group": "apiextensions.k8s.io", "kind": "CustomResourceDefinition", "name": "widgets.example.io"}]}
        def argo(*args, **kwargs):
            self.assertEqual(args[:3], ("app", "get-resource", "provider"))
            return json.dumps(crd())
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GITOPS_DISCOVERY": "false"}), patch.object(preview, "inventory", return_value={"provider": provider}), patch.object(g, "argo", side_effect=argo), patch.object(g.Renderer, "render", return_value=[custom_resource()]):
            rows = preview.evaluate(Path(directory), plan_data([entry("consumer")]), Path(directory))
        self.assertNotEqual(rows[0]["result"], "FAILED", rows)

    def test_unknown_scope_fails_without_guessing(self):
        registry = g.ScopeRegistry()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(g.GitOpsError, "Unknown resource scope"):
            registry.normalize([custom_resource()], app())

    def test_inventory_is_isolated_per_cluster(self):
        document = app()
        document["status"] = {"resources": [{"group": "example.io", "kind": "Widget", "name": "existing"}]}
        registry = g.ScopeRegistry()
        registry.inventory({"demo": document})
        self.assertNotIn("namespace", registry.normalize([custom_resource()], document)[0]["metadata"])
        other = app(destination={"server": "https://other", "namespace": "demo"})
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(g.GitOpsError, "Unknown resource scope"):
            registry.normalize([custom_resource()], other)

    def test_scope_conflict_from_crd_and_live_inventory_fails(self):
        registry = g.ScopeRegistry()
        cluster = g.cluster_key(app())
        registry.register(cluster, "example.io", "Widget", False, "inventory")
        with self.assertRaisesRegex(g.GitOpsError, "Conflicting resource scope"):
            registry.crds(cluster, [crd("Namespaced")], "proposed")
        registry.register(cluster, "apps", "Deployment", True, "API discovery")
        with self.assertRaisesRegex(g.GitOpsError, "Conflicting resource scope"):
            registry.crds(cluster, [crd("Cluster", "apps", "Deployment")], "proposed")

    def test_namespace_and_api_versions_share_ownership_identity(self):
        registry = g.ScopeRegistry()
        registry.crds(g.cluster_key(app()), [crd("Namespaced")], "proposed")
        first, second = custom_resource(), custom_resource()
        second["apiVersion"] = "example.io/v2"
        second["metadata"]["namespace"] = "demo"
        result = registry.normalize([first, second], app())
        self.assertEqual(len(result), 1)
        self.assertEqual(preview.ownership_key(first, app(), registry), preview.ownership_key(second, app(), registry))

    def test_api_discovery_uses_kind_not_plural_and_ignores_subresources(self):
        cluster = g.cluster_key(app())
        def execute(command, **kwargs):
            if "config" in command:
                return json.dumps({"clusters": [{"cluster": {"server": cluster}}]})
            responses = {"/api": {"versions": ["v1"]}, "/apis": {"groups": [{"versions": [{"groupVersion": "example.io/v1"}]}]},
                         "/api/v1": {"groupVersion": "v1", "resources": [{"name": "configmaps", "kind": "ConfigMap", "namespaced": True}]},
                         "/apis/example.io/v1": {"groupVersion": "example.io/v1", "resources": [
                             {"name": "widgets", "kind": "Widget", "namespaced": False},
                             {"name": "widgets/status", "kind": "Widget", "namespaced": True}]}}
            return json.dumps(responses[command[-1]])
        registry = g.ScopeRegistry()
        with patch.dict(os.environ, {"GITOPS_DISCOVERY_KUBECONFIG": "/readonly/config"}, clear=True), patch.object(g, "run", side_effect=execute) as execute_mock:
            self.assertFalse(registry.namespaced(cluster, "example.io", "Widget"))
            self.assertTrue(registry.namespaced(cluster, "", "ConfigMap"))
        self.assertTrue(all("--kubeconfig" in call.args[0] for call in execute_mock.call_args_list))

    def test_external_api_alias_requires_matching_live_uid(self):
        cluster = g.cluster_key(app())
        anchor = crd()
        anchor["metadata"]["uid"] = "live-cluster-unique-id"
        for actual_uid, accepted in (("live-cluster-unique-id", True), ("another-cluster", False)):
            with self.subTest(actual_uid=actual_uid):
                registry = g.ScopeRegistry()
                registry.crds(cluster, [anchor], "live CRD", live=True)
                def execute(command, **kwargs):
                    if "config" in command:
                        return json.dumps({"clusters": [{"cluster": {"server": "https://external-api"}}]})
                    if command[-1].startswith("/apis/apiextensions.k8s.io/"):
                        return json.dumps({"metadata": {"uid": actual_uid}})
                    return json.dumps({"versions": []})
                with patch.dict(os.environ, {"GITOPS_DISCOVERY_KUBECONFIG": "/readonly/config"}, clear=True), patch.object(g, "run", side_effect=execute):
                    if accepted:
                        registry.discover(cluster)
                    else:
                        with self.assertRaisesRegex(g.GitOpsError, "destination mismatch"):
                            registry.discover(cluster)

    def test_in_cluster_discovery_needs_no_kubeconfig(self):
        def execute(command, **kwargs):
            self.assertNotIn("config", command)
            return json.dumps({"versions": []})
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}, clear=True), patch.object(g, "run", side_effect=execute):
            g.ScopeRegistry().discover(g.cluster_key(app()))

    def test_discovery_refuses_a_different_cluster(self):
        with patch.dict(os.environ, {"GITOPS_DISCOVERY_KUBECONFIG": "/readonly/config"}, clear=True), patch.object(g, "run", return_value=json.dumps({"clusters": [{"cluster": {"server": "https://wrong"}}]})):
            with self.assertRaisesRegex(g.GitOpsError, "destination mismatch"):
                g.ScopeRegistry().namespaced(g.cluster_key(app()), "example.io", "Widget")

    def test_list_unwraps_crds_and_cluster_namespace_is_rejected(self):
        registry = g.ScopeRegistry()
        items = g.resources(yaml.safe_dump({"apiVersion": "v1", "kind": "List", "items": [crd(), custom_resource()]}))
        registry.crds(g.cluster_key(app()), items, "proposed")
        items[1]["metadata"]["namespace"] = "invalid"
        with self.assertRaisesRegex(g.GitOpsError, "Cluster-scoped resource"):
            registry.normalize(items, app())



if __name__ == "__main__":
    unittest.main()
