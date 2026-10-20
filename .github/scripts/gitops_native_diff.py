"""Argo CD 3.4.6 server-side dry-run prediction over unary gRPC-Web.

Only existing, managed resources belong here. Lifecycle decisions
and publication redaction remain the caller's responsibility. The server uses
the live Application's comparison policy, so changed policies fail explicitly.
Proto field numbers are pinned to Argo CD v3.4.6 application.proto and
pkg/apis/application/v1alpha1/generated.proto. No protobuf runtime is needed.
"""
import copy
import json
import os
import ssl
import struct
import time
from urllib import error, parse, request

MAX_MESSAGE = 32 * 1024 * 1024
MAX_BATCH = 1024 * 1024
RPC = "/application.ApplicationService/ServerSideDiff"


class NativeDiffError(RuntimeError):
    pass


def _varint(number):
    result = bytearray()
    while number > 127:
        result.append((number & 127) | 128)
        number >>= 7
    result.append(number)
    return bytes(result)


def _field(number, value):
    value = value.encode() if isinstance(value, str) else value
    return _varint(number * 8 + 2) + _varint(len(value)) + value


def _read_varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise NativeDiffError("Truncated native diff protobuf response")
        part = data[position]
        position += 1
        value |= (part & 127) << shift
        if not part & 128:
            return value, position
    raise NativeDiffError("Invalid native diff protobuf varint")


def _fields(data):
    position = 0
    while position < len(data):
        tag, position = _read_varint(data, position)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise NativeDiffError("Invalid native diff protobuf field")
        if wire == 0:
            value, position = _read_varint(data, position)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, position = data[position:position + size], position + size
        elif wire == 2:
            size, position = _read_varint(data, position)
            value, position = data[position:position + size], position + size
            if len(value) != size:
                raise NativeDiffError("Truncated native diff protobuf field")
        else:
            raise NativeDiffError("Unsupported native diff protobuf wire type")
        if position > len(data):
            raise NativeDiffError("Truncated native diff protobuf field")
        yield number, value


def _unframe(data, headers):
    position, messages, trailers = 0, [], {}
    while position < len(data):
        if len(data) - position < 5:
            raise NativeDiffError("Truncated native diff gRPC-Web frame")
        flag, size = data[position], struct.unpack(">I", data[position + 1:position + 5])[0]
        position += 5
        payload, position = data[position:position + size], position + size
        if len(payload) != size:
            raise NativeDiffError("Truncated native diff gRPC-Web payload")
        if flag == 128:
            for line in payload.decode("ascii", errors="replace").splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    trailers[key.lower()] = value.strip()
        elif flag == 0:
            messages.append(payload)
        else:
            raise NativeDiffError("Unsupported compressed native diff response")
    status = trailers.get("grpc-status", headers.get("grpc-status"))
    # Do not expose grpc-message: validation errors may quote manifest contents.
    if status != "0":
        raise NativeDiffError(f"Argo native diff failed (gRPC status {status or 'missing'})")
    if len(messages) != 1:
        raise NativeDiffError("Expected one native diff response message")
    return messages[0]


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NativeDiffError("Argo native diff endpoint redirected; configure its direct API address")


def _rpc(payload, *, server=None, token=None, timeout=90):
    server = server or os.getenv("ARGOCD_SERVER", "")
    if not server:
        raise NativeDiffError("ARGOCD_SERVER is required for native cluster comparison")
    if "://" not in server:
        server = ("http://" if os.getenv("ARGOCD_PLAINTEXT", "").lower() == "true" else "https://") + server
    endpoint = parse.urlsplit(server)
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise NativeDiffError("Invalid Argo native diff API address")
    root = os.getenv("ARGOCD_GRPC_WEB_ROOT_PATH", "").strip("/")
    url = server.rstrip("/") + ("/" + root if root else "") + RPC
    token = token if token is not None else os.getenv("ARGOCD_AUTH_TOKEN", "")
    headers = {"Content-Type": "application/grpc-web+proto", "Accept": "application/grpc-web+proto", "X-Grpc-Web": "1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    context = ssl.create_default_context()
    if os.getenv("ARGOCD_INSECURE", "").lower() == "true":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    opener = request.build_opener(_NoRedirect(), request.HTTPSHandler(context=context))
    frame = b"\x00" + struct.pack(">I", len(payload)) + payload
    if len(frame) > MAX_MESSAGE:
        raise NativeDiffError("Native diff request exceeds its size limit")
    for attempt in range(3):
        try:
            with opener.open(request.Request(url, data=frame, headers=headers, method="POST"), timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "application/grpc-web" not in content_type:
                    raise NativeDiffError("Argo endpoint did not return a gRPC-Web response")
                body = response.read(MAX_MESSAGE + 1)
                if len(body) > MAX_MESSAGE:
                    raise NativeDiffError("Native diff response exceeds its size limit")
                return _unframe(body, response.headers)
        except error.HTTPError as exc:
            if exc.code not in {429, 502, 503, 504} or attempt == 2:
                raise NativeDiffError(f"Argo native diff HTTP request failed ({exc.code})") from None
        except (error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise NativeDiffError("Argo native diff endpoint unavailable or request timed out") from None
        time.sleep(attempt + 1)
    raise NativeDiffError("Argo native diff request failed")


def _key(item):
    version = item.get("apiVersion", "")
    return (version.split("/")[0] if "/" in version else "", item.get("kind", ""),
            item.get("metadata", {}).get("namespace", ""), item.get("metadata", {}).get("name", ""))


def predict(live_app, proposed_app, live_resources, target_resources, *, server=None, token=None):
    """Return (normalized_live, predicted_live) for aligned existing resources.

    The API performs Kubernetes server-side dry-run using Argo's field manager,
    global normalizers and live Application ignores. This is a comparison
    prediction; hooks, controller side effects and admission changes after this
    observation cannot be forecast. No Application definition is updated.
    """
    for field in ("destination", "ignoreDifferences", "project"):
        if live_app.get("spec", {}).get(field) != proposed_app.get("spec", {}).get(field):
            raise NativeDiffError(f"Native comparison requires unchanged Application {field}; review that configuration change separately")
    annotation = "argocd.argoproj.io/compare-options"
    if (live_app.get("metadata", {}).get("annotations") or {}).get(annotation) != (proposed_app.get("metadata", {}).get("annotations") or {}).get(annotation):
        raise NativeDiffError("Native comparison requires unchanged Application comparison options")
    old, new = {_key(item): item for item in live_resources}, {_key(item): item for item in target_resources}
    if old.keys() != new.keys() or len(old) != len(live_resources) or len(new) != len(target_resources):
        raise NativeDiffError("Native comparison requires unique aligned existing resource identities")
    prefix = b"".join(_field(field, value) for field, value in (
        (1, live_app["metadata"]["name"]), (2, live_app["metadata"].get("namespace", "argocd")),
        (3, live_app.get("spec", {}).get("project", "default"))))
    batches, batch, size = [], [], len(prefix)
    for key in sorted(old):
        target = copy.deepcopy(new[key])
        tracking = (old[key].get("metadata", {}).get("annotations") or {}).get("argocd.argoproj.io/tracking-id")
        if tracking:
            metadata = target.setdefault("metadata", {})
            metadata["annotations"] = metadata.get("annotations") or {}
            metadata["annotations"]["argocd.argoproj.io/tracking-id"] = tracking
        live_json = json.dumps(old[key], separators=(",", ":"))
        target_json = json.dumps(target, separators=(",", ":"))
        resource = b"".join(_field(index, value) for index, value in enumerate(key, 1)) + _field(6, live_json)
        pair = _field(4, resource) + _field(5, target_json)
        if batch and size + len(pair) > MAX_BATCH:
            batches.append(batch)
            batch, size = [], len(prefix)
        batch.append(pair)
        size += len(pair)
    if batch:
        batches.append(batch)
    normalized, predicted, seen = [], [], set()
    for batch in batches:
        response = _rpc(prefix + b"".join(batch), server=server, token=token)
        for number, value in _fields(response):
            if number != 1:
                continue
            fields = dict(_fields(value))
            try:
                key = tuple(fields.get(index, b"").decode() for index in (1, 2, 3, 4))
                # 3.4.6 places the normalized/predicted states in liveState6/
                # targetState5; accept their explicit equivalents for compatibility.
                before = json.loads(fields.get(9) or fields[6])
                after = json.loads(fields.get(10) or fields[5])
            except (KeyError, ValueError, UnicodeError, TypeError, AttributeError):
                raise NativeDiffError("Invalid native diff resource response") from None
            if key not in old or key in seen or not isinstance(before, dict) or not isinstance(after, dict):
                raise NativeDiffError("Native diff returned unexpected or missing resource states")
            if _key(before) != key or _key(after) != key:
                raise NativeDiffError("Native diff returned mismatched resource identities")
            normalized.append(before)
            predicted.append(after)
            seen.add(key)
    if seen != old.keys():
        raise NativeDiffError("Native diff omitted managed resource results")
    return normalized, predicted
