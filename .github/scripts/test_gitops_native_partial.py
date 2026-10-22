"""Native prediction isolates invalid resources without inventing sync results."""
import copy
import json
import struct
import unittest
from unittest.mock import MagicMock, patch

import gitops_native_diff as native


def resource(name, **fields):
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": "demo"}, "data": {"setting": "value"}, **fields}


def application():
    return {"metadata": {"name": "demo", "namespace": "argocd"}, "spec": {"project": "default"}}


def targets(payload):
    return [json.loads(value) for number, value in native._fields(payload) if number == 5]


def response(before, after):
    result = b""
    for left, right in zip(before, after):
        item = b"".join(native._field(index, value) for index, value in enumerate(native._key(left), 1))
        item += native._field(6, json.dumps(left)) + native._field(5, json.dumps(right))
        result += native._field(1, item)
    return result


def classified(message, status="2"):
    trailer = f"grpc-status: {status}\r\ngrpc-message: {native.parse.quote(message)}\r\n".encode()
    try:
        native._unframe(b"\x80" + struct.pack(">I", len(trailer)) + trailer, {})
    except native.NativeDiffError as exc:
        return exc
    raise AssertionError("Expected classified failure")


class PartialNativePrediction(unittest.TestCase):
    def test_immutable_pvc_is_not_mislabeled_as_rbac_or_disclosed(self):
        exc = classified('PersistentVolumeClaim "private-name" is invalid: spec: Forbidden: spec is immutable after creation; private-secret')
        self.assertEqual(exc.category, "immutable")
        self.assertTrue(exc.isolatable)
        self.assertEqual(exc.code, "2")
        self.assertNotIn("private", str(exc))

    def test_rbac_authentication_and_unknown_errors_do_not_allow_fanout(self):
        cases = [
            ('ClusterRole "private-role" is forbidden: User "private" cannot patch resource "clusterroles"', "2", "rbac"),
            ('private failure', "16", "authentication"),
            ('private failure', "7", "authorization"),
            ('private failure', "8", "capacity"),
            ('private failure', "14", "transport"),
            ('private failure', "2", "server"),
        ]
        for message, status, category in cases:
            with self.subTest(category=category):
                exc = classified(message, status)
                self.assertEqual(exc.category, category)
                self.assertFalse(exc.isolatable)
                self.assertNotIn("private", str(exc))

    def test_validation_and_admission_have_safe_actionable_categories(self):
        for message, category in (("Deployment private is invalid: spec.foo: Invalid value: private-value", "validation"),
                                  ('admission webhook "private" denied the request: private-content', "admission"),
                                  ('pods "private" is forbidden: exceeded quota: private', "admission")):
            with self.subTest(category=category):
                exc = classified(message)
                self.assertEqual(exc.category, category)
                self.assertTrue(exc.isolatable)
                self.assertNotIn("private", str(exc))

    def test_one_invalid_resource_preserves_normalized_predictions_for_others(self):
        before = [resource("a"), resource("b"), resource("c"), resource("d")]
        desired = copy.deepcopy(before)
        for item in desired:
            item["data"]["setting"] = "changed"
        by_name = {item["metadata"]["name"]: item for item in before}

        def rpc(payload, **kwargs):
            requested = targets(payload)
            if any(item["metadata"]["name"] == "b" for item in requested):
                raise classified("spec: Forbidden: field is immutable; private-value")
            normalized, predicted = [], []
            for item in requested:
                left = copy.deepcopy(by_name[item["metadata"]["name"]])
                left["metadata"]["labels"] = {"server-default": "preserved"}
                right = copy.deepcopy(item)
                right["metadata"]["labels"] = {"server-default": "preserved"}
                normalized.append(left)
                predicted.append(right)
            return response(normalized, predicted)

        with patch.object(native, "_rpc", side_effect=rpc) as transport:
            result = native.predict_resources(application(), application(), before, desired)
        self.assertFalse(result["complete"])
        self.assertEqual([item["metadata"]["name"] for item in result["after"]], ["a", "c", "d"])
        self.assertTrue(all(item["metadata"]["labels"]["server-default"] == "preserved" for item in result["after"]))
        self.assertEqual(result["blockers"][0]["identity"], ["", "ConfigMap", "demo", "b"])
        self.assertEqual(result["blockers"][0]["category"], "immutable")
        self.assertNotIn("private-value", json.dumps(result))
        self.assertEqual(transport.call_count, 5)

    def test_isolation_request_budget_does_not_blame_unchecked_resources(self):
        items = [resource("a"), resource("b"), resource("c"), resource("d")]
        with patch.object(native, "_rpc", side_effect=classified("field is immutable")) as transport:
            result = native.predict_resources(application(), application(), items, items, max_extra_requests=2)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual(result["before"], [])
        self.assertEqual(len(result["blockers"]), 4)
        self.assertIn("isolation-budget", {item["category"] for item in result["blockers"]})
        self.assertFalse(result["complete"])

    def test_time_budget_ends_before_starting_more_requests(self):
        items = [resource("a"), resource("b")]
        with patch.object(native, "_rpc") as transport:
            result = native.predict_resources(application(), application(), items, items, time_budget=0)
        transport.assert_not_called()
        self.assertEqual({item["category"] for item in result["blockers"]}, {"time-budget"})
        self.assertFalse(result["complete"])

    def test_transport_retries_share_one_total_deadline(self):
        opener = MagicMock()
        opener.open.side_effect = native.error.URLError("private-network-detail")
        # Deadline=13; attempts have 3s, then 2s, then no remaining budget.
        with patch.object(native.request, "build_opener", return_value=opener), \
                patch.object(native.time, "monotonic", side_effect=[10, 10, 11, 11, 13, 13]), \
                patch.object(native.time, "sleep") as sleep:
            with self.assertRaisesRegex(native.NativeDiffError, "time budget exhausted") as caught:
                native._rpc(b"private-manifest", server="http://example.invalid", token="private-token", timeout=3)
        self.assertEqual(caught.exception.category, "transport")
        self.assertFalse(caught.exception.isolatable)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual([call.kwargs["timeout"] for call in opener.open.call_args_list], [3, 2])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 0])

    def test_global_failures_raise_once_without_resource_fanout(self):
        for exc in (native.NativeDiffError("Backend unavailable", category="transport"),
                    native.NativeDiffError("Malformed response", category="protocol"),
                    classified("private failure", "7")):
            with self.subTest(category=exc.category), patch.object(native, "_rpc", side_effect=exc) as transport:
                with self.assertRaises(native.NativeDiffError) as caught:
                    native.predict_resources(application(), application(), [resource("a"), resource("b")], [resource("a"), resource("b")])
                self.assertIs(caught.exception, exc)
                self.assertEqual(transport.call_count, 1)

    def test_response_for_a_different_batch_cannot_be_accepted(self):
        with patch.object(native, "_rpc", return_value=response([resource("wrong")], [resource("wrong")])) as transport:
            with self.assertRaisesRegex(native.NativeDiffError, "unexpected"):
                native.predict_resources(application(), application(), [resource("a")], [resource("a")])
        self.assertEqual(transport.call_count, 1)

    def test_malformed_resource_response_is_a_protocol_error_without_fanout(self):
        before, after = resource("a"), resource("a")
        after["metadata"] = None
        for payload in (response([before], [after]), native._varint(1 * 8) + native._varint(1)):
            with self.subTest(payload=payload), patch.object(native, "_rpc", return_value=payload) as transport:
                with self.assertRaisesRegex(native.NativeDiffError, "Invalid native diff resource response") as caught:
                    native.predict_resources(application(), application(), [before], [before])
                self.assertEqual(caught.exception.category, "protocol")
                self.assertEqual(transport.call_count, 1)

    def test_strict_interface_rejects_partial_prediction(self):
        with patch.object(native, "_rpc", side_effect=classified("field is immutable")):
            with self.assertRaisesRegex(native.NativeDiffError, "incomplete"):
                native.predict(application(), application(), [resource("a")], [resource("a")])

    def test_oversized_resource_is_a_blocker_without_sending_a_request(self):
        item = resource("big")
        with patch.object(native, "MAX_MESSAGE", 20), patch.object(native, "_rpc") as transport:
            result = native.predict_resources(application(), application(), [item], [item])
        transport.assert_not_called()
        self.assertEqual(result["blockers"][0]["category"], "size")
        self.assertEqual(result["after"], [])


if __name__ == "__main__":
    unittest.main()
