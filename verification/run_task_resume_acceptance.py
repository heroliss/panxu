"""Real loopback HTTP acceptance of durable investigation and model handoff.

All fixture files and application data live in TemporaryDirectory. The report
contains fixed labels, booleans, counts and hashes, never fixture paths, file
names, model dialogue or credentials. No UI, external provider or real action
executor is used. Native continuation canaries are synthetic test values.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diskpilot.server import create_server
from tests.prompt_fixture import parse_host_data


MODEL_A = "resume-fixture-a"
MODEL_B = "resume-fixture-b"
PRIVATE_A = "synthetic-native-continuation-fixture-a"
PRIVATE_B = "synthetic-native-continuation-fixture-b"
FILE_COUNT = 5001


def sha(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fixture_hash(root):
    rows = [(p.name, sha(p.read_bytes())) for p in sorted(root.iterdir()) if p.is_file()]
    return {"file_count": len(rows), "sha256": sha(rows)}


@contextmanager
def serve(server):
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        if hasattr(server, "app"):
            server.app.close()
        if thread.is_alive():
            raise RuntimeError("loopback_server_did_not_close")


class Client:
    def __init__(self, server):
        self.base = f"http://127.0.0.1:{server.server_port}"
        self.opener = build_opener(ProxyHandler({}))
        self.csrf = self.request("/api/bootstrap?compact=1")["csrf"]

    def request(self, path, body=None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Origin": self.base,
                            "X-CSRF-Token": self.csrf})
        try:
            with self.opener.open(Request(self.base + path, data=data, headers=headers), timeout=60) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            with exc:
                value = json.loads(exc.read())
            raise RuntimeError(f"http_{exc.code}_{value.get('code', 'unknown')}") from None

    def wait(self, job_id, timeout=240):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.request("/api/jobs/" + job_id)
            if job["status"] != "running":
                if job["status"] != "completed":
                    raise RuntimeError("agent_job_" + job["status"])
                return job["result"]["task"]
            time.sleep(0.08)
        raise TimeoutError("agent_job_timeout")


class FixtureModel:
    """Progress derives from public context and actual preceding tool results."""
    def __init__(self, goal):
        self.goal = goal
        self.requests = []
        self.tools = Counter()
        self.errors = []
        self.authentication_headers = 0
        self.first_b_context = None
        self.first_b_fresh = False
        self.old_native_in_b = False
        self.native_a_reused_within_a = False
        self.restored_fact_ids = []
        self.read_old_facts = False
        self.saved_scan_id = None

    def answer(self, payload):
        messages = payload["messages"]
        context = parse_host_data(messages[1]["content"])
        model = payload["model"]
        if model not in (MODEL_A, MODEL_B) or context["goal"] != self.goal:
            raise ValueError("fixture_context_changed")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.requests.append({"model": model, "sha256": sha(payload)})
        if model == MODEL_A and PRIVATE_A in serialized:
            self.native_a_reused_within_a = True
        if model == MODEL_B:
            self.old_native_in_b |= PRIVATE_A in serialized
            if self.first_b_context is None:
                self.first_b_context = context
                self.first_b_fresh = len(messages) == 2

        results = []
        for observation in context.get("observations", []):
            result = observation.get("result")
            if isinstance(result, dict):
                results.append((observation.get("tool"), result))
        for position, message in enumerate(messages):
            if message.get("role") == "tool":
                result = parse_host_data(message["content"])
                if "error" in result:
                    raise ValueError("fixture_tool_result_error")
                call_id = message["tool_call_id"]
                name = next((call["function"]["name"] for previous in messages[:position]
                             for call in previous.get("tool_calls", []) if call["id"] == call_id), None)
                results.append((name, result))
        for name, result in results:
            if name == "read_facts" and model == MODEL_B:
                # Large tool results may be compacted into persisted observations
                # before the next request. Their evidence IDs must still survive.
                found = {item["id"] for item in result.get("items", [])}
                self.read_old_facts |= bool(found.intersection(self.restored_fact_ids))

        last_tool = results[-1][0] if results else None
        if model == MODEL_B and not self.tools["read_facts"] and self.restored_fact_ids:
            name, args = "read_facts", {"ids": self.restored_fact_ids[:2]}
        else:
            scans = list(context.get("scans", []))
            scans += [result for _, result in results if result.get("scan_id")]
            scan = scans[-1] if scans else None
            if not scan:
                name, args = "survey_scope", {"scope_id": context["scopes"][0]["id"]}
            elif (not scan.get("coverage", {}).get("complete", False)
                  or scan.get("published_count", 0) < scan.get("coverage", {}).get("observed_entries", 0) + 1):
                name, args = "continue_scan", {"scan_id": scan["scan_id"], "max_entries": 512}
            elif last_tool == "checkpoint_task":
                name, args = "present_findings", {
                    "summary": "测试目录的元数据采集已完成。保留选择和历史事实仍可继续使用；尚未逐项评估文件用途，实际文件未改变。",
                    "needs_input": False}
            else:
                name, args = "checkpoint_task", {
                    "summary": "目录元数据已采集，保留选择已保存。",
                    "next_steps": ["如需整理，继续逐项调查用途并提出可审阅建议。"],
                    "unresolved": ["元数据采集完成不表示每个文件的用途已分析。"]}
        self.tools[name] += 1
        call_id = f"fixture_{len(self.requests)}_{name}"
        return {"choices": [{"message": {
            "role": "assistant", "content": None,
            "reasoning_content": PRIVATE_A if model == MODEL_A else PRIVATE_B,
            "tool_calls": [{"id": call_id, "type": "function", "function": {
                "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}

    def server(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.path != "/v1/chat/completions" or not 0 < length <= 192 * 1024:
                        raise ValueError("unexpected_fixture_request")
                    fixture.authentication_headers += int(bool(self.headers.get("Authorization")))
                    value = fixture.answer(json.loads(self.rfile.read(length)))
                    status = 200
                except Exception as exc:
                    fixture.errors.append({"type_sha256": sha(type(exc).__name__), "message_sha256": sha(str(exc))})
                    value, status = {"error": {"message": "fixture_request_failed"}}, 500
                raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def run(goal):
    report = {"schema": "panxu.task-resume-acceptance.v1", "passed": False,
              "mode": "simulation", "actual_reclaimed_bytes": 0,
              "external_provider_used": False, "real_credentials_used": False,
              "checks": {}, "counts": {}, "stage": "fixture"}
    source_before = sha(Path(__file__).read_bytes())
    connections = Counter()
    model = FixtureModel(goal)
    allowed_ports = set()
    original_connection = socket.create_connection

    def loopback_only(address, *args, **kwargs):
        host, port = address
        try:
            allowed = ipaddress.ip_address(host).is_loopback and port in allowed_ports
        except ValueError:
            allowed = False
        connections["loopback" if allowed else "external_attempts"] += 1
        if not allowed:
            raise AssertionError("acceptance_may_only_connect_to_owned_loopback_servers")
        return original_connection(address, *args, **kwargs)

    before = after = None
    try:
        with tempfile.TemporaryDirectory(prefix="panxu-task-resume-") as temporary:
            base = Path(temporary)
            scope, data_dir = base / "fixture", base / "application-data"
            scope.mkdir()
            for number in range(FILE_COUNT):
                (scope / f"fixture-{number:05d}.bin").write_bytes(f"unchanged fixture {number:05d}\n".encode())
            before = fixture_hash(scope)
            with serve(model.server()) as provider, patch("socket.create_connection", side_effect=loopback_only):
                allowed_ports.add(provider.server_port)
                settings = {"provider": "custom", "protocol": "chat_completions", "reasoning_effort": "default",
                            "base_url": f"http://127.0.0.1:{provider.server_port}/v1", "model": MODEL_A, "api_key": ""}
                report["stage"] = "initial_turn"
                with serve(create_server(data_dir)) as app:
                    allowed_ports.add(app.server_port)
                    client = Client(app)
                    client.request("/api/settings", settings)
                    created = client.request("/api/tasks", {"goal": goal, "path": str(scope)})
                    task_id = created["task"]["id"]
                    first = client.wait(created["job_id"])
                    report["checks"]["initial_turn_exhausted_budget"] = first["status"] == "budget_exhausted"
                    report["checks"]["initial_scan_incomplete"] = bool(first["scans"]) and not first["scans"][0]["coverage"]["complete"]
                    model.saved_scan_id = first["scans"][0]["scan_id"]
                    rows = client.request("/api/files?limit=50")["items"]
                    kept = next(item for item in rows if item["kind"] == "file")
                    feedback = client.request(f"/api/tasks/{task_id}/feedback", {
                        "keep_ids": [kept["id"]], "message": "保留这份测试文件，继续调查其他内容。"})
                    before_restart = client.wait(feedback["job_id"])
                    old_facts = client.request(f"/api/tasks/{task_id}/facts?limit=5")
                    model.restored_fact_ids = [item["id"] for item in old_facts["items"]]
                    old_facts_sha = sha(old_facts["items"])
                    old_usage = before_restart["usage"]["model_requests"]
                    old_observed = before_restart["scans"][0]["coverage"]["observed_entries"]
                    report["counts"]["facts_before_restart"] = old_facts["total"]
                    report["counts"]["observed_before_restart"] = old_observed
                    report["checks"]["restart_mid_scan"] = not before_restart["scans"][0]["coverage"]["complete"]
                    report["checks"]["handoff_before_restart"] = bool(before_restart["handoff"]["summary"])

                report["stage"] = "reopen_and_model_switch"
                with serve(create_server(data_dir)) as app:
                    allowed_ports.add(app.server_port)
                    client = Client(app)
                    restored = client.request(f"/api/tasks/{task_id}")["task"]
                    facts = client.request(f"/api/tasks/{task_id}/facts?" + urlencode({"ids": ",".join(model.restored_fact_ids), "limit": 5}))
                    report["checks"]["old_facts_readable_after_reopen"] = sha(facts["items"]) == old_facts_sha
                    report["checks"]["usage_survived_reopen"] = restored["usage"]["model_requests"] == old_usage
                    report["checks"]["same_scan_after_reopen"] = restored["scans"][0]["scan_id"] == model.saved_scan_id
                    client.request("/api/settings", {"model": MODEL_B})
                    continuations = 0
                    task = restored
                    while task["status"] != "completed" and continuations < 15:
                        continued = client.request(f"/api/tasks/{task_id}/continue", {})
                        task = client.wait(continued["job_id"])
                        continuations += 1
                        if task["status"] not in ("completed", "budget_exhausted"):
                            raise RuntimeError("unexpected_task_status_" + task["status"])
                    public = client.request(f"/api/tasks/{task_id}")["task"]
                    health = client.request("/api/health")
                    history = client.request("/api/history")["history"]
                    final_facts = client.request(f"/api/tasks/{task_id}/facts?limit=5")
                    scan = public["scans"][0]
                    first_b = model.first_b_context or {}
                    choices = first_b.get("user_choices", [])
                    report["checks"].update({
                        "task_completed": public["status"] == "completed",
                        "scan_complete": scan["coverage"]["complete"] is True,
                        "flat_fixture_fully_observed": scan["coverage"]["observed_entries"] == FILE_COUNT and scan["aggregate"]["file_count"] == FILE_COUNT,
                        "all_observations_published": scan["published_count"] == FILE_COUNT + 1,
                        "scan_remains_observation_only": scan["coverage"]["snapshot"] is False and scan["coverage"]["can_delete_whole_directory"] is False,
                        "same_scan_resumed": scan["scan_id"] == model.saved_scan_id,
                        "scan_made_forward_progress": scan["coverage"]["observed_entries"] > old_observed,
                        "choice_in_new_model_context": any(choice.get("id") == kept["id"] and choice.get("choice") == "keep" for choice in choices),
                        "goal_in_new_model_context": first_b.get("goal") == goal,
                        "old_facts_in_new_model_context": bool(set(model.restored_fact_ids).intersection(item["id"] for item in first_b.get("facts", {}).get("items", []))),
                        "new_model_read_saved_fact": model.read_old_facts,
                        "new_model_started_fresh_transcript": model.first_b_fresh,
                        "new_model_saw_connection_change": first_b.get("connection_changed") is True,
                        "old_native_not_replayed_to_new_model": not model.old_native_in_b,
                        "native_continuity_exercised_before_switch": model.native_a_reused_within_a,
                        "handoff_uses_new_model": public["handoff"]["model"]["model"] == MODEL_B,
                        "usage_accumulated": public["usage"]["model_requests"] > old_usage,
                        "facts_accumulated": final_facts["total"] > old_facts["total"],
                        "checkpoint_executed": model.tools["checkpoint_task"] >= 1,
                        "findings_executed": model.tools["present_findings"] == 1,
                        "no_tool_failures": public["usage"]["tool_failures"] == 0,
                        "simulation_only": health["mode"] == "simulation" and health["live_executor_available"] is False,
                        "no_action_batches": not history,
                    })
                    report["counts"].update({"continue_requests_after_reopen": continuations, "task_turns": public["turn"],
                        "facts_after_resume": final_facts["total"], "observed_after_resume": scan["coverage"]["observed_entries"],
                        "published_observations": scan["published_count"], "scan_replay_work": scan["coverage"]["replay_work"],
                        "scan_work_total": scan["coverage"]["work_total"], "usage_model_requests": public["usage"]["model_requests"],
                        "usage_tool_calls": public["usage"]["tool_calls"], "action_batches": len(history)})
                    report["task_state_sha256"] = sha(public)
                # Close SQLite and the Windows data lease before reading every
                # persisted byte, including the formerly locked lease file.
                report["checks"]["private_continuation_not_persisted"] = all(
                    PRIVATE_A.encode() not in path.read_bytes() and PRIVATE_B.encode() not in path.read_bytes()
                    for path in data_dir.rglob("*") if path.is_file())
            after = fixture_hash(scope)
            report["checks"]["fixture_sources_unchanged"] = before == after and before["file_count"] == FILE_COUNT
            report["fixture_before"] = before
            report["fixture_after"] = after
            report["stage"] = "finished"
    except Exception as exc:
        report["error_type_sha256"] = sha(type(exc).__name__)
        report["error_message_sha256"] = sha(str(exc))
    report["checks"].update({"no_external_connections": connections["external_attempts"] == 0,
                              "no_real_authentication": model.authentication_headers == 0,
                              "provider_no_errors": not model.errors,
                              "script_source_unchanged": source_before == sha(Path(__file__).read_bytes())})
    report["counts"].update({"provider_requests": len(model.requests), "provider_errors": len(model.errors),
                              "loopback_connections": connections["loopback"], "external_connection_attempts": connections["external_attempts"],
                              "model_a_requests": sum(row["model"] == MODEL_A for row in model.requests),
                              "model_b_requests": sum(row["model"] == MODEL_B for row in model.requests),
                              "survey_calls": model.tools["survey_scope"], "continue_scan_calls": model.tools["continue_scan"],
                              "checkpoint_calls": model.tools["checkpoint_task"], "saved_fact_reads": model.tools["read_facts"]})
    report["request_transcript_sha256"] = sha(model.requests)
    report["script_source_sha256"] = source_before
    report["goal_sha256"] = sha(goal)
    report["passed"] = report["stage"] == "finished" and all(report["checks"].values())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", default="调查这个测试目录的元数据，保留个人选择，分步继续并保存交接。")
    args = parser.parse_args()
    if not 1 <= len(args.goal) <= 1000:
        parser.error("goal must contain 1 to 1000 characters")
    started = time.monotonic()
    report = run(args.goal)
    report["counts"]["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    target = Path(__file__).resolve().parent / "task-resume-acceptance.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
