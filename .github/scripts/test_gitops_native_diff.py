import copy
import json
import struct
import unittest
from unittest.mock import MagicMock, patch
from urllib import error

import gitops_native_diff as native


def resource(name="config"):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": "test"}, "data": {"key": "value"}}


def response(before, after):
    key = native._key(before)
    item = b"".join(native._field(i, value) for i, value in enumerate(key, 1))
    item += native._field(6, json.dumps(before)) + native._field(5, json.dumps(after))
    return native._field(1, item)


class NativeDiffTests(unittest.TestCase):
    def setUp(self):
        self.app = {"metadata": {"name": "example", "namespace": "argocd"},
                    "spec": {"destination": {"server": "cluster", "namespace": "test"}, "project": "default"}}

    def test_pinned_proto_roundtrip_and_unknown_fields(self):
        data = native._field(1, "hello") + native._varint(12 * 8) + native._varint(150) + native._field(90, "future")
        self.assertEqual(list(native._fields(data)), [(1, b"hello"), (12, 150), (90, b"future")])

    def test_frame_trailers_and_no_manifest_error_disclosure(self):
        message = native._field(1, b"item")
        trailer = b"grpc-status: 0\r\n"
        frames = b"\0" + struct.pack(">I", len(message)) + message + b"\x80" + struct.pack(">I", len(trailer)) + trailer
        self.assertEqual(native._unframe(frames, {}), message)
        failure = b"grpc-status: 7\r\ngrpc-message: secretpassword\r\n"
        with self.assertRaisesRegex(native.NativeDiffError, "status 7") as caught:
            native._unframe(b"\x80" + struct.pack(">I", len(failure)) + failure, {})
        self.assertNotIn("secretpassword", str(caught.exception))

    def test_truncated_and_compressed_frames_fail(self):
        for payload in (b"\0", b"\0\0\0\0\x02x", b"\x01\0\0\0\0"):
            with self.assertRaises(native.NativeDiffError):
                native._unframe(payload, {})
        with self.assertRaises(native.NativeDiffError):
            list(native._fields(b"\x0a\x04x"))

    def test_predicted_objects_not_raw_desired_are_returned(self):
        before = resource()
        before["metadata"]["labels"] = {"defaulted": "preserve"}
        after = copy.deepcopy(before)
        after["data"]["key"] = "new"
        target = resource()
        target["data"]["key"] = "new"
        with patch.object(native, "_rpc", return_value=response(before, after)):
            result = native.predict(self.app, self.app, [before], [target])
        self.assertEqual(result, ([before], [after]))

    def test_changed_policy_rejected_before_network(self):
        for field in ("destination", "project", "ignoreDifferences"):
            proposed = copy.deepcopy(self.app)
            proposed["spec"][field] = "changed"
            with patch.object(native, "_rpc") as rpc:
                with self.assertRaisesRegex(native.NativeDiffError, field):
                    native.predict(self.app, proposed, [resource()], [resource()])
                rpc.assert_not_called()

    def test_null_annotations_preserve_tracking_without_mutating_inputs(self):
        before, target = resource(), resource()
        tracking = "example:/ConfigMap:test/config"
        before["metadata"]["annotations"] = {"argocd.argoproj.io/tracking-id": tracking}
        target["metadata"]["annotations"] = None
        app = copy.deepcopy(self.app)
        app["metadata"]["annotations"] = None
        with patch.object(native, "_rpc", return_value=response(before, target)) as rpc:
            native.predict(app, self.app, [before], [target])
        sent = json.loads(next(value for number, value in native._fields(rpc.call_args.args[0]) if number == 5))
        self.assertEqual(sent["metadata"]["annotations"]["argocd.argoproj.io/tracking-id"], tracking)
        self.assertIsNone(target["metadata"]["annotations"])
        self.assertIsNone(app["metadata"]["annotations"])

    def test_null_live_resource_annotations_are_supported(self):
        before, target = resource(), resource()
        before["metadata"]["annotations"] = None
        with patch.object(native, "_rpc", return_value=response(before, target)):
            self.assertEqual(native.predict(self.app, self.app, [before], [target]), ([before], [target]))

    def test_unaligned_and_duplicate_inputs_rejected(self):
        for before, after in (([resource()], []), ([resource(), resource()], [resource()])):
            with self.assertRaises(native.NativeDiffError):
                native.predict(self.app, self.app, before, after)

    def test_server_masked_secret_states_are_preserved_without_disclosure(self):
        before = resource()
        before["kind"] = "Secret"
        before.pop("data")
        before["data"] = {"credential": "++++"}
        after = copy.deepcopy(before)
        after["data"]["credential"] = "++++++++"
        desired = copy.deepcopy(before)
        desired["data"]["credential"] = "private-request-value"
        with patch.object(native, "_rpc", return_value=response(before, after)):
            result = native.predict(self.app, self.app, [before], [desired])
        self.assertEqual(result, ([before], [after]))
        self.assertNotIn("private-request-value", json.dumps(result))

    def test_missing_and_wrong_identity_response_rejected(self):
        for payload in (b"", response(resource("different"), resource("different"))):
            with patch.object(native, "_rpc", return_value=payload):
                with self.assertRaises(native.NativeDiffError):
                    native.predict(self.app, self.app, [resource()], [resource()])

    def test_no_resources_requires_no_transport(self):
        with patch.object(native, "_rpc") as rpc:
            self.assertEqual(native.predict(self.app, self.app, [], []), ([], []))
            rpc.assert_not_called()

    def test_transport_uses_post_body_and_authorization_not_url(self):
        message = native._field(2, b"result")
        trailer = b"grpc-status: 0\r\n"
        frames = b"\0" + struct.pack(">I", len(message)) + message + b"\x80" + struct.pack(">I", len(trailer)) + trailer
        opener = MagicMock()
        response_mock = opener.open.return_value.__enter__.return_value
        response_mock.headers = {"Content-Type": "application/grpc-web+proto"}
        response_mock.read.return_value = frames
        with patch.dict(native.os.environ, {"ARGOCD_GRPC_WEB_ROOT_PATH": "argo"}), patch.object(native.request, "build_opener", return_value=opener):
            result = native._rpc(b"private-manifest", server="https://example.invalid", token="private-token")
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(sent.full_url, "https://example.invalid/argo" + native.RPC)
        self.assertNotIn("private", sent.full_url)
        self.assertEqual(sent.get_header("Authorization"), "Bearer private-token")
        self.assertEqual(sent.data[5:], b"private-manifest")
        self.assertEqual(result, message)

    def test_transport_error_does_not_quote_server_error_or_request(self):
        opener = MagicMock()
        opener.open.side_effect = error.HTTPError("https://example.invalid/private-token", 403, "private-manifest", {}, None)
        with patch.object(native.request, "build_opener", return_value=opener):
            with self.assertRaises(native.NativeDiffError) as caught:
                native._rpc(b"private-manifest", server="https://example.invalid", token="private-token")
        self.assertEqual(str(caught.exception), "Argo native diff HTTP request failed (403)")
        self.assertEqual(opener.open.call_count, 1)

    def test_transient_read_only_request_is_bounded(self):
        opener = MagicMock()
        opener.open.side_effect = error.URLError("private detail")
        with patch.object(native.request, "build_opener", return_value=opener), patch.object(native.time, "sleep"):
            with self.assertRaises(native.NativeDiffError):
                native._rpc(b"private-manifest", server="https://example.invalid", token="private-token")
        self.assertEqual(opener.open.call_count, 3)


if __name__ == "__main__":
    unittest.main()
