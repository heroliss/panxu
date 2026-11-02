"""Win11 acceptance of the simulation boundary, using disposable local data.

The report deliberately contains only fixed labels, counts, SHA-256 digests and
booleans.  No collected names, paths, commands, registry values or credentials
are written to it.  The only process this script may stop is its own helper.
"""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diskpilot.llm import ModelAssistant
from diskpilot import __version__
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot import windows
from tests.prompt_fixture import parse_host_data


def sha(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fixture_hash(root: Path) -> dict:
    # This function is called exclusively with the directory created below.
    files = sorted(path for path in root.rglob("*") if path.is_file())
    rows = [(path.relative_to(root).as_posix(), sha(path.read_bytes())) for path in files]
    return {"file_count": len(rows), "content_sha256": sha(rows)}


def collect(collector) -> tuple[list[dict], dict]:
    try:
        rows, warnings = collector()
        return rows, {"available": bool(rows), "count": len(rows),
                      "warning_count": len(warnings), "collector_failed": False}
    except Exception as exc:
        return [], {"available": False, "count": 0, "warning_count": 0,
                    "collector_failed": True, "error_type_sha256": sha(type(exc).__name__)}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from strings(child)


def model_acceptance(data_dir: Path, fixture_item: dict) -> dict:
    """Exercise the actual urllib transport against a loopback-only server."""
    requests: list[dict] = []
    auth_matches: list[bool] = []
    handler_errors: list[bool] = []
    dummy_key = "acceptance-dummy-key-" + secrets.token_hex(16)
    bearer_canary = "acceptance-reflected-canary-" + secrets.token_hex(8)
    private_canary = "acceptance-private-metadata-" + secrets.token_hex(8)
    call_id = "acceptance_summary_1"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/v1/chat/completions" or not 0 < length <= 192 * 1024:
                    raise ValueError("unexpected_mock_request")
                payload = json.loads(self.rfile.read(length))
                requests.append(payload)
                auth_matches.append(self.headers.get("Authorization") == "Bearer " + dummy_key)
                if len(requests) == 1:
                    message = {"role": "assistant", "content": None, "tool_calls": [
                        {"id": call_id, "type": "function", "function": {
                            "name": "summarize_inventory", "arguments": "{}"}}
                    ]}
                elif len(requests) == 2:
                    message = {"role": "assistant", "content":
                               "只读模拟已核查。" + dummy_key + " Bearer " + bearer_canary}
                else:
                    raise ValueError("unexpected_extra_round")
                response = json.dumps({"choices": [{"message": message}]}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except Exception:
                handler_errors.append(True)
                self.send_response(500)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection_counts = {"loopback": 0, "external": 0}
    original_connection = socket.create_connection

    def loopback_only(address, *args, **kwargs):
        host, port = address
        try:
            allowed = ipaddress.ip_address(host).is_loopback and port == server.server_port
        except ValueError:
            allowed = False
        connection_counts["loopback" if allowed else "external"] += 1
        if not allowed:
            raise AssertionError("acceptance transport may only contact its own loopback server")
        return original_connection(address, *args, **kwargs)

    item = dict(fixture_item)
    item["owner"] = private_canary
    item["details"] = {**item.get("details", {}), "command_line": private_canary,
                       "private_canary": private_canary}
    item["evidence"] = [private_canary, fixture_item["path"]]
    before_sha = sha(item)
    try:
        assistant = ModelAssistant(data_dir)
        public = assistant.save_settings({"base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                          "model": "local-acceptance-mock", "api_key": dummy_key})
        with patch("socket.create_connection", side_effect=loopback_only):
            answer = assistant.analyze("说明所选测试文件的模拟结果。", [item], [item["id"]])
        rendered = json.dumps(answer, ensure_ascii=False)
        public_rendered = json.dumps(public, ensure_ascii=False)
        sent_strings = list(strings(requests))
        path = fixture_item["path"]
        path_forms = {path, path.replace("\\", "/"), json.dumps(path)[1:-1]}
        full_path_absent = all(not any(form in value for form in path_forms) for value in sent_strings)
        private_details_absent = all(private_canary not in value for value in sent_strings)
        key_absent_from_bodies = all(dummy_key not in value for value in sent_strings)
        tools = []
        if len(requests) >= 2:
            tools = [message for message in requests[1].get("messages", []) if message.get("role") == "tool"]
        tool_summary_matches = False
        if len(tools) == 1:
            content = parse_host_data(tools[0]["content"])
            tool_summary_matches = (tools[0].get("tool_call_id") == call_id
                                    and content.get("count") == 1
                                    and content.get("simulation_only") is True)
        stored_plaintext_absent = all(dummy_key not in file.read_text(encoding="utf-8")
                                      for file in data_dir.glob("*.json"))
        report = {
            "skipped": False, "request_count": len(requests),
            "loopback_connection_count": connection_counts["loopback"],
            "external_connection_attempt_count": connection_counts["external"],
            "handler_error_count": len(handler_errors),
            "request_body_sha256": [sha(request) for request in requests],
            "response_sha256": sha(answer),
            "two_real_http_requests": len(requests) == 2 and connection_counts["loopback"] == 2,
            "summary_tool_result_received": tool_summary_matches,
            "tool_trace_count": sum(event.get("event") == "tool" for event in answer.get("trace", [])),
            "model_round_count": sum(event.get("event") == "model_round" for event in answer.get("trace", [])),
            "model_mode_confirmed": answer.get("mode") == "llm",
            "paths_disabled_by_default": public.get("send_paths") is False,
            "full_paths_absent_from_requests": full_path_absent,
            "private_details_absent_from_requests": private_details_absent,
            "key_absent_from_request_bodies": key_absent_from_bodies,
            "dummy_auth_only_to_mock": bool(auth_matches) and all(auth_matches),
            "secret_absent_from_answer": dummy_key not in rendered and bearer_canary not in rendered,
            "secret_absent_from_public_settings": dummy_key not in public_rendered,
            "secret_absent_from_plaintext_storage": stored_plaintext_absent,
            "inventory_unchanged": before_sha == sha(item),
        }
        report["passed"] = all(report[key] for key in (
            "two_real_http_requests", "summary_tool_result_received", "model_mode_confirmed",
            "paths_disabled_by_default", "full_paths_absent_from_requests",
            "private_details_absent_from_requests", "key_absent_from_request_bodies",
            "dummy_auth_only_to_mock", "secret_absent_from_answer",
            "secret_absent_from_public_settings", "secret_absent_from_plaintext_storage",
            "inventory_unchanged")) and report["external_connection_attempt_count"] == 0 and not handler_errors
        return report
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def select_optional(rows: list[dict], kind: str, helper) -> dict | None:
    candidates = [item for item in rows if item.get("risk") != "protected" and item.get("actions")]
    if kind == "process":
        return next((item for item in candidates if item.get("details", {}).get("pid") == helper.pid), None)
    if kind == "service":
        candidates.sort(key=lambda item: item.get("details", {}).get("state") != "Running")
    return candidates[0] if candidates else None


def audit_selected(selected: dict[str, dict], helper) -> dict:
    """Compare only selected public collector signatures, retaining no names."""
    reports = {}
    collectors = {"startup": windows.startup_items, "service": windows.service_items,
                  "software": windows.software_items, "process": windows.process_items}
    for kind, before in selected.items():
        rows, meta = collect(collectors[kind])
        after = next((item for item in rows if item["id"] == before["id"]), None)
        entry = {"skipped": False, "post_count": meta["count"], "present_after": after is not None,
                 "identity_sha256": sha(before["id"]), "before_signature_sha256": sha(before["signature"]),
                 "after_signature_sha256": sha(after["signature"]) if after else sha([]),
                 "signature_unchanged": bool(after and after["signature"] == before["signature"])}
        if kind == "service":
            entry.update(
                start_mode_unchanged=bool(after and after["details"].get("start_mode") == before["details"].get("start_mode")),
                selected_service_state_unchanged=bool(after and after["details"].get("state") == before["details"].get("state")),
                state_change_observed=bool(after and after["details"].get("state") != before["details"].get("state")),
                before_state_sha256=sha(before["details"].get("state")),
                after_state_sha256=sha(after["details"].get("state") if after else []),
                external_change_attribution_not_inferred=True,
            )
        if kind == "process":
            entry["owned_helper_still_alive"] = helper.poll() is None
        reports[kind] = entry
    return reports


def run() -> dict:
    report = {"schema_version": 1, "application_version": __version__, "platform_windows": os.name == "nt",
              "python_major": sys.version_info.major, "python_minor": sys.version_info.minor,
              "simulation_only": True, "real_credentials_used": False,
              "platform_checks_skipped": os.name != "nt",
              "scanned_user_directories": False, "helper_created": False,
              "helper_cleanup_completed": False, "passed": False}
    helper = None
    state = None

    def close_state():
        nonlocal state
        if state is not None:
            state.close()
            state = None

    def stop_owned_helper():
        if helper is not None:
            if helper.poll() is None:
                helper.terminate()  # exclusively the Popen object created below
                try:
                    helper.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait(timeout=5)
            report["helper_cleanup_completed"] = helper.poll() is not None

    try:
        with tempfile.TemporaryDirectory(prefix="diskpilot-local-acceptance-") as temporary, ExitStack() as resources:
            # LIFO callbacks run before TemporaryDirectory removes its paths,
            # including if an assertion/collector fails while SQLite is open.
            resources.callback(close_state)
            base = Path(temporary)
            fixture = base / "fixture"
            fixture.mkdir()
            (fixture / "sample.bin").write_bytes(b"DiskPilot acceptance fixture\n" + secrets.token_bytes(128))
            (fixture / "nested").mkdir()
            (fixture / "nested" / "preserve.txt").write_bytes(b"Independent fixture content; preserve exactly.\n")
            before = fixture_hash(fixture)
            report["fixture_before"] = before
            scan = scan_directory(str(fixture))
            if scan["summary"]["status"] != "completed":
                raise RuntimeError("fixture_scan_incomplete")
            file_item = next(item for item in scan["items"] if item["kind"] == "file")
            helper = subprocess.Popen(
                [sys.executable, "-c", "import time; data=bytearray(16*1024*1024); time.sleep(180)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            resources.callback(stop_owned_helper)
            report["helper_created"] = True
            time.sleep(0.25)
            collectors = {"startup": windows.startup_items, "service": windows.service_items,
                          "software": windows.software_items, "process": windows.process_items}
            report["collectors"] = {}
            selected: dict[str, dict] = {}
            preflight_changes = 0
            state = SimulationState(base / "appdata")
            executed = None
            for attempt in range(2):
                selected.clear()
                for kind, collector in collectors.items():
                    rows, meta = collect(collector)
                    chosen = select_optional(rows, kind, helper)
                    meta.update(selected_count=int(chosen is not None), skipped=chosen is None,
                                no_eligible_candidate=chosen is None)
                    report["collectors"][kind] = meta
                    if chosen:
                        selected[kind] = chosen
                state.publish([file_item, *selected.values()], "local-acceptance")
                prepared = state.prepare([file_item["id"], *(item["id"] for item in selected.values())])
                try:
                    executed = state.execute(prepared["id"], system_validator=windows.validate_system_items)
                    break
                except AppError as exc:
                    if exc.code != "target_changed" or attempt:
                        raise
                    preflight_changes += 1
            if executed is None:
                raise RuntimeError("mixed_batch_not_executed")
            batch_id = executed["batch"]["id"]
            expected_count = 1 + len(selected)
            report["mixed_batch"] = {
                "selected_file_count": 1, "selected_system_kind_count": len(selected),
                "selected_item_count": expected_count, "batch_item_count": executed["batch"]["item_count"],
                "preflight_change_count": preflight_changes,
                "actual_reclaimed_bytes": executed["batch"]["actual_reclaimed_bytes"],
                "all_selected_simulated": executed["summary"]["simulated_items"] == expected_count,
                "system_validator_used": True,
                "helper_alive_after_simulated_stop": helper.poll() is None,
                "fixture_after_batch": fixture_hash(fixture),
            }
            report["post_batch_system"] = audit_selected(selected, helper)
            state.close()
            state = SimulationState(base / "appdata")
            persisted = state.view()
            persistence_ok = persisted["summary"]["simulated_items"] == expected_count and any(
                item["id"] == batch_id and item["status"] == "simulated" for item in persisted["history"])
            undone = state.undo(batch_id)
            undo_ok = undone["summary"]["simulated_items"] == 0 and any(
                item["id"] == batch_id and item["status"] == "undone" for item in undone["history"])
            reset_plan = state.prepare([file_item["id"]])
            state.execute(reset_plan["id"], system_validator=windows.validate_system_items)
            reset = state.reset()
            reset_ok = reset["summary"]["simulated_items"] == 0 and all(
                item["status"] == "undone" for item in reset["history"])
            state.close()
            state = SimulationState(base / "appdata")
            persisted_reset = state.view()
            report["journal"] = {
                "batch_persisted_after_reopen": persistence_ok, "batch_undo_confirmed": undo_ok,
                "reset_confirmed": reset_ok,
                "reset_persisted_after_reopen": persisted_reset["summary"]["simulated_items"] == 0,
                "history_count": len(persisted_reset["history"]),
                "actual_reclaimed_bytes": persisted_reset["summary"]["actual_reclaimed_bytes"],
            }
            report["model_loopback"] = model_acceptance(base / "model-appdata", file_item)
            report["post_all_system"] = audit_selected(selected, helper)
            report["fixture_after"] = fixture_hash(fixture)
            report["fixture_unchanged"] = report["fixture_before"] == report["fixture_after"]
            report["helper_alive_before_cleanup"] = helper.poll() is None
            unchanged = all(
                item["present_after"] and item["signature_unchanged"]
                and item.get("start_mode_unchanged", True)
                and item.get("selected_service_state_unchanged", True)
                and item.get("owned_helper_still_alive", True)
                for phase in ("post_batch_system", "post_all_system")
                for item in report[phase].values()
            )
            report["observed_system_items_unchanged"] = unchanged
            report["passed"] = bool(
                report["fixture_unchanged"] and unchanged
                and report["mixed_batch"]["all_selected_simulated"]
                and report["mixed_batch"]["actual_reclaimed_bytes"] == 0
                and all(report["journal"][key] for key in (
                    "batch_persisted_after_reopen", "batch_undo_confirmed",
                    "reset_confirmed", "reset_persisted_after_reopen"))
                and report["model_loopback"]["passed"] and report["helper_alive_before_cleanup"]
            )
            state.close()
            state = None
            # The TemporaryDirectory owns every path that is cleaned up here.
    except Exception as exc:
        report["failed_with_exception"] = True
        report["error_type_sha256"] = sha(type(exc).__name__)
        report["error_code_sha256"] = sha(getattr(exc, "code", "acceptance_failure"))
        report["passed"] = False
    finally:
        close_state()
        stop_owned_helper()
        report["passed"] = report["passed"] and report["helper_cleanup_completed"]
    return report


if __name__ == "__main__":
    result = run()
    output = Path(__file__).with_name("local-acceptance.json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
