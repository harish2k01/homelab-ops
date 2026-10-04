"""Expose numeric leaves and the full cached smartctl -x JSON report.

Only cached files are read by this unprivileged process. Device reads happen in
the existing privileged exporter container. Values remain vendor-specific.
"""
import json
import math
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

DATA = Path(os.environ.get("SMARTCTL_DATA", "/data"))


def labels(**values):
    def escape(value):
        return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    return ",".join(f'{key}="{escape(value)}"' for key, value in values.items())


def leaves(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, f"{path}[{index}]")
    elif isinstance(value, (int, float)) and math.isfinite(value):
        yield path, int(value) if isinstance(value, bool) else value
    elif isinstance(value, str):
        yield path, value


def devices():
    try:
        return sorted({Path(line).name for line in (DATA / "active").read_text().splitlines() if line})
    except OSError:
        return []


def metrics():
    lines = [
        "# HELP smartctl_extended_value Numeric value from a cached smartctl -x JSON report; path and units are vendor-specific.",
        "# TYPE smartctl_extended_value gauge",
        "# HELP smartctl_extended_text Text field from a cached smartctl -x JSON report.",
        "# TYPE smartctl_extended_text gauge",
        "# HELP smartctl_extended_collection_success Report parsed and smartctl device access succeeded.",
        "# TYPE smartctl_extended_collection_success gauge",
        "# HELP smartctl_extended_collection_timestamp_seconds Successful report file modification time.",
        "# TYPE smartctl_extended_collection_timestamp_seconds gauge",
    ]
    for device in devices():
        try:
            file = DATA / f"{device}.json"
            report = json.loads(file.read_text())
            if not isinstance(report, dict) or not isinstance(report.get("smartctl"), dict):
                raise ValueError("Not a smartctl report")
            # Exit bits 0/1 mean command syntax/device access failure; higher
            # bits report disk problems and must remain visible as valid data.
            success = int(not (int(report["smartctl"].get("exit_status", 0)) & 3))
            lines.append(f"smartctl_extended_collection_success{{{labels(device=device)}}} {success}")
            if not success:
                continue
            lines.append(f"smartctl_extended_collection_timestamp_seconds{{{labels(device=device)}}} {file.stat().st_mtime}")
            for section, value in report.items():
                if section in {"smartctl", "json_format_version", "local_time", "device"}:
                    continue
                for field, number in leaves(value):
                    if isinstance(number, str):
                        # Raw-value display strings often change every scrape
                        # and would create a new label series each time. Their
                        # numeric raw values are already exported; retain the
                        # exact display strings in the full JSON endpoint.
                        if field.endswith(".raw.string"):
                            continue
                        lines.append(f"smartctl_extended_text{{{labels(device=device, section=section, field=field or 'value', text=number)}}} 1")
                    else:
                        lines.append(f"smartctl_extended_value{{{labels(device=device, section=section, field=field or 'value')}}} {number}")
        except (OSError, ValueError, TypeError, OverflowError):
            lines.append(f"smartctl_extended_collection_success{{{labels(device=device)}}} 0")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/healthz":
            content, mime = b"ok\n", "text/plain"
        elif path == "/metrics":
            content, mime = metrics().encode(), "text/plain; version=0.0.4; charset=utf-8"
        elif path.startswith("/reports/"):
            name = path[len("/reports/"):]
            if not re.fullmatch(r"[a-zA-Z0-9_-]+\.json", name) or name[:-5] not in devices():
                self.send_error(404)
                return
            try:
                content, mime = (DATA / name).read_bytes(), "application/json"
            except OSError:
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9634), Handler).serve_forever()
