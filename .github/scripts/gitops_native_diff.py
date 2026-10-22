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
import socket
import ssl
import struct
import time
from urllib import error, parse, request

MAX_MESSAGE = 32 * 1024 * 1024
MAX_BATCH = 1024 * 1024
MAX_EXTRA_REQUESTS = 8
PREDICTION_BUDGET = 180
RPC = "/application.ApplicationService/ServerSideDiff"


class NativeDiffError(RuntimeError):
    def __init__(self, message, *, category="protocol", code=None, isolatable=False, retryable=False):
        super().__init__(message)
        self.category = category
        self.code = code
        self.isolatable = isolatable
        self.retryable = retryable


def _server_failure(status, message):
    """Classify privately; server messages may embed complete Secret manifests."""
    suffix = f" (gRPC status {status or 'missing'})"
    if status == "16":
        return NativeDiffError("Argo native diff authentication failed; check the preview credential" + suffix,
                               category="authentication", code=status)
    if status == "7":
        return NativeDiffError("Argo native diff authorization failed; check preview Application access" + suffix,
                               category="authorization", code=status)
    if "attempting to grant rbac permissions" in message or (
            "cannot " in message and "resource" in message and "is forbidden" in message):
        return NativeDiffError("Argo native diff denied by Kubernetes RBAC; check argocd-server permissions" + suffix,
                               category="rbac", code=status)
    if any(marker in message for marker in (
            "field is immutable", "spec is immutable", "immutable after creation",
            "updates to statefulset spec for fields other than", "may not change once set")):
        return NativeDiffError("Existing resource has an immutable field change; preserve it or plan replacement or migration" + suffix,
                               category="immutable", code=status, isolatable=True)
    if any(marker in message for marker in ("denied the request", "violates podsecurity", "violates pod security", "exceeded quota")):
        return NativeDiffError("Admission policy or quota rejected the proposed resource; review its configured policies and limits" + suffix,
                               category="admission", code=status, isolatable=True)
    if any(marker in message for marker in ("is invalid:", "invalid value:", "required value", "unsupported value:")):
        return NativeDiffError("Kubernetes rejected the proposed resource fields; review the rendered resource and schema" + suffix,
                               category="validation", code=status, isolatable=True)
    if status == "8":
        return NativeDiffError("Argo native diff exceeded API capacity; check message limits and API memory" + suffix,
                               category="capacity", code=status)
    if status in {"4", "14"}:
        return NativeDiffError("Argo native diff backend is unavailable or exceeded its deadline; check API readiness and memory" + suffix,
                               category="transport", code=status, retryable=True)
    return NativeDiffError("Argo native diff failed; check the API server diagnostic logs" + suffix,
                           category="server", code=status)


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
        message = parse.unquote(trailers.get("grpc-message", headers.get("grpc-message", ""))).lower()
        raise _server_failure(status, message)
    if len(messages) != 1:
        raise NativeDiffError("Expected one native diff response message")
    return messages[0]


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NativeDiffError("Argo native diff endpoint redirected; configure its direct API address")


def _transport_failure(exc):
    reason = exc.reason if isinstance(exc, error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        message = "Argo native diff API hostname cannot be resolved; check runner DNS"
    elif isinstance(reason, ssl.SSLError):
        message = "Argo native diff TLS negotiation failed; check the direct API address and certificate settings"
    elif isinstance(reason, ConnectionRefusedError):
        message = "Argo native diff API connection was refused; check service endpoints and API readiness"
    elif isinstance(reason, TimeoutError):
        message = "Argo native diff request timed out; check API readiness and memory"
    else:
        message = "Argo native diff endpoint unavailable or request timed out; check API readiness and memory"
    return NativeDiffError(message, category="transport", retryable=True)


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
    deadline = time.monotonic() + timeout
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeDiffError("Argo native diff request time budget exhausted; check API readiness and memory",
                                  category="transport", retryable=True)
        try:
            with opener.open(request.Request(url, data=frame, headers=headers, method="POST"), timeout=remaining) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "application/grpc-web" not in content_type:
                    raise NativeDiffError("Argo endpoint did not return a gRPC-Web response")
                body = response.read(MAX_MESSAGE + 1)
                if len(body) > MAX_MESSAGE:
                    raise NativeDiffError("Native diff response exceeds its size limit")
                return _unframe(body, response.headers)
        except error.HTTPError as exc:
            if exc.code not in {429, 502, 503, 504} or attempt == 2:
                category = "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "transport"
                raise NativeDiffError(f"Argo native diff HTTP request failed ({exc.code})", category=category,
                                      code=str(exc.code), retryable=exc.code in {429, 502, 503, 504}) from None
        except (error.URLError, TimeoutError, OSError) as exc:
            if attempt == 2:
                raise _transport_failure(exc) from None
        time.sleep(min(attempt + 1, max(0, deadline - time.monotonic())))
    raise NativeDiffError("Argo native diff request failed")


def _key(item):
    version = item.get("apiVersion", "")
    return (version.split("/")[0] if "/" in version else "", item.get("kind", ""),
            item.get("metadata", {}).get("namespace", ""), item.get("metadata", {}).get("name", ""))


def predict_resources(live_app, proposed_app, live_resources, target_resources, *, server=None, token=None,
                      max_extra_requests=MAX_EXTRA_REQUESTS, time_budget=PREDICTION_BUDGET):
    """Return verified pairs and safe per-resource blockers for existing objects.

    The API performs Kubernetes server-side dry-run using Argo's field manager,
    global normalizers and live Application ignores. This is a comparison
    prediction; hooks, controller side effects and admission changes after this
    observation cannot be forecast. Deterministic object validation failures are
    isolated with serial, bounded batch splitting. Transport, authorization, and
    protocol failures are global errors and never trigger request fan-out.
    No Application definition is updated. Failed objects are never returned as
    predicted states: their rendered intent belongs in a separately labeled view.
    """
    for field in ("destination", "ignoreDifferences", "project"):
        if live_app.get("spec", {}).get(field) != proposed_app.get("spec", {}).get(field):
            raise NativeDiffError(f"Native comparison requires unchanged Application {field}; review that configuration change separately",
                                  category="policy")
    annotation = "argocd.argoproj.io/compare-options"
    if (live_app.get("metadata", {}).get("annotations") or {}).get(annotation) != (proposed_app.get("metadata", {}).get("annotations") or {}).get(annotation):
        raise NativeDiffError("Native comparison requires unchanged Application comparison options", category="policy")
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
        batch.append((key, pair))
        size += len(pair)
    if batch:
        batches.append(batch)
    normalized, predicted, seen, blockers = [], [], set(), []
    deadline = time.monotonic() + max(0, time_budget)
    extra_requests = 0

    def block(items, category, message):
        for key, _ in items:
            blockers.append({"identity": list(key), "category": category, "message": message})

    def verify(batch, *, extra=False):
        nonlocal extra_requests
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            block(batch, "time-budget", "Native prediction time budget exhausted; this resource is unverified")
            return
        if extra and extra_requests >= max(0, max_extra_requests):
            block(batch, "isolation-budget", "A resource in this batch failed validation; the isolation budget ended before individual verification")
            return
        payload = prefix + b"".join(pair for _, pair in batch)
        if len(payload) + 5 > MAX_MESSAGE:
            block(batch, "size", "Resource prediction exceeds the native request size limit; this resource is unverified")
            return
        if extra:
            extra_requests += 1
        try:
            response = _rpc(payload, server=server, token=token, timeout=min(90, remaining))
        except NativeDiffError as exc:
            if not exc.isolatable:
                raise
            if len(batch) == 1:
                block(batch, exc.category, str(exc))
            else:
                middle = len(batch) // 2
                verify(batch[:middle], extra=True)
                verify(batch[middle:], extra=True)
            return
        expected = {key for key, _ in batch}
        actual, batch_before, batch_after = set(), [], []
        for number, value in _fields(response):
            if number != 1:
                continue
            if not isinstance(value, bytes):
                raise NativeDiffError("Invalid native diff resource response")
            fields = dict(_fields(value))
            try:
                key = tuple(fields.get(index, b"").decode() for index in (1, 2, 3, 4))
                # 3.4.6 places the normalized/predicted states in liveState6/
                # targetState5; accept their explicit equivalents for compatibility.
                before = json.loads(fields.get(9) or fields[6])
                after = json.loads(fields.get(10) or fields[5])
            except (KeyError, ValueError, UnicodeError, TypeError, AttributeError):
                raise NativeDiffError("Invalid native diff resource response") from None
            if key not in expected or key in actual or key in seen or not isinstance(before, dict) or not isinstance(after, dict):
                raise NativeDiffError("Native diff returned unexpected or missing resource states")
            if not isinstance(before.get("metadata"), dict) or not isinstance(after.get("metadata"), dict):
                raise NativeDiffError("Invalid native diff resource response")
            if _key(before) != key or _key(after) != key:
                raise NativeDiffError("Native diff returned mismatched resource identities")
            batch_before.append(before)
            batch_after.append(after)
            actual.add(key)
        if actual != expected:
            raise NativeDiffError("Native diff omitted managed resource results")
        normalized.extend(batch_before)
        predicted.extend(batch_after)
        seen.update(actual)

    for batch in batches:
        verify(batch)
    if seen | {tuple(item["identity"]) for item in blockers} != old.keys():
        raise NativeDiffError("Native diff omitted managed resource results")
    return {"before": normalized, "after": predicted, "blockers": blockers, "complete": not blockers}


def predict(live_app, proposed_app, live_resources, target_resources, *, server=None, token=None):
    """Strict compatibility interface: incomplete predictions cannot look valid."""
    result = predict_resources(live_app, proposed_app, live_resources, target_resources, server=server, token=token)
    if not result["complete"]:
        raise NativeDiffError("Native comparison is incomplete; inspect per-resource blockers", category="incomplete")
    return result["before"], result["after"]
