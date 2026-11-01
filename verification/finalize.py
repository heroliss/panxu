"""Finalize frozen artifacts using current, explicitly scoped evidence.

This does not build the program, run tests, or infer visual acceptance from
screenshots. Archive preparation is a preview unless --apply is also supplied.
Previous ZIP files and userdata are never deleted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import struct
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "verification"


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def version() -> str:
    source = (ROOT / "diskpilot" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r"__version__\s*=\s*['\"](\d+\.\d+\.\d+)['\"]", source)
    if not match:
        raise RuntimeError("Cannot determine the current source version")
    return match.group(1)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def inside_project(path: Path) -> Path:
    absolute = path.resolve(strict=False)
    require(absolute != ROOT and absolute.is_relative_to(ROOT), "Output must stay inside the project")
    return absolute


def linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def archive_previous(*, apply: bool = False) -> dict:
    """Preview/preserve only the explicitly named previous package/evidence."""
    package_path = VERIFY / "package.json"
    require(package_path.is_file(), "Previous static package report is required to select archive destination")
    previous = json.loads(package_path.read_text(encoding="utf-8-sig"))
    previous_version = previous.get("application", {}).get("version", "")
    require(re.fullmatch(r"\d+\.\d+\.\d+", previous_version) is not None, "Previous version is invalid")
    require(previous_version != version(), "Current-version package must not be archived as previous evidence")
    destination = inside_project(VERIFY / ("previous-v" + previous_version.replace(".", "")))
    require(not destination.exists() or not linked(destination), "Archive destination cannot be a link")
    pairs = [
        (ROOT / "dist" / "PanXu", destination / "PanXu"),
        (VERIFY / "package.json", destination / "package.json"),
        (VERIFY / "acceptance.json", destination / "acceptance.json"),
        (VERIFY / "source-manifest.json", destination / "source-manifest.json"),
    ]
    plan = []
    for source, target in pairs:
        inside_project(source)
        inside_project(target)
        require(not linked(source), "Refusing to archive a linked source")
        require(not (source.exists() and target.exists()), "Previous archive target exists; nothing overwritten")
        plan.append({"source": source.relative_to(ROOT).as_posix(),
                     "target": target.relative_to(ROOT).as_posix(),
                     "source_exists": source.exists(), "already_archived": not source.exists() and target.exists()})
    package_path = VERIFY / "package.json"
    if package_path.exists():
        previous = json.loads(package_path.read_text(encoding="utf-8-sig"))
        require(previous.get("application", {}).get("version") != version(),
                "Current-version package must not be archived as previous evidence")
    result = {"applied": False, "plan": plan, "old_zip_files_preserved": True,
              "userdata_preserved_with_package": True, "deletes_anything": False}
    if apply:
        # Same-project renames preserve every existing file, including userdata.
        destination.mkdir(parents=True, exist_ok=True)
        for source, target in pairs:
            if source.exists():
                require(not target.exists(), "Archive target appeared; refusing to overwrite")
                source.rename(target)
        result["applied"] = True
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_health(url: str, expected_version: str) -> dict:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    try:
        loopback = host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    require(parsed.scheme in {"http", "https"} and loopback, "Health URL must use loopback HTTP")
    require(parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment,
            "Health URL must not contain credentials, query parameters or fragments")
    require(parsed.path == "/api/health", "Health URL must use /api/health")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(url, timeout=10) as response:
        require(response.status == 200, "Health endpoint did not return success")
        raw = response.read(65537)
    require(len(raw) <= 65536, "Health response is unexpectedly large")
    health = json.loads(raw)
    require(health.get("status") == "ok" and health.get("version") == expected_version,
            "Health does not identify the current running version")
    require(health.get("mode") == "simulation" and health.get("live_executor_available") is False,
            "Runtime simulation-only boundary is not confirmed")
    return {key: health[key] for key in ("status", "version", "mode", "live_executor_available")}


def screenshot_evidence() -> list[dict]:
    evidence = []
    marker = "native-v" + version().replace(".", "")
    for name in (marker + ".png", marker + "-files.png"):
        path = VERIFY / name
        require(path.is_file() and not linked(path), "Current native screenshot missing or linked: " + name)
        with path.open("rb") as stream:
            header = stream.read(24)
        require(len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR",
                "Native screenshot is not a PNG: " + name)
        width, height = struct.unpack(">II", header[16:24])
        require(width > 0 and height > 0 and path.stat().st_size > 1000, "Native screenshot is empty: " + name)
        evidence.append({"file": name, "bytes": path.stat().st_size, "sha256": sha(path),
                         "width": width, "height": height})
    return evidence


def source_snapshot(app_version: str) -> dict:
    sources = {ROOT / name for name in ("run.py", "README.txt", "build.ps1")}
    for folder, suffixes in (("diskpilot", {".py"}), ("tests", {".py"}), ("web", {".html", ".js", ".css"})):
        sources.update(path for path in (ROOT / folder).rglob("*") if path.is_file() and path.suffix in suffixes)
    sources.update(VERIFY.glob("*.py"))
    files = {}
    for path in sorted(sources):
        require(not linked(path), "Refusing a linked source file")
        files[path.relative_to(ROOT).as_posix()] = {"sha256": sha(path), "bytes": path.stat().st_size}
    return {"version": app_version, "source_file_count": len(files), "files": files,
            "scope": "Current frozen source and verification scripts; not reconstructed executable internals"}


def release_files(release: Path) -> list[Path]:
    require(release.is_dir() and not linked(release), "Release directory missing or linked")
    result = []
    forbidden_parts = {"userdata", "verification", "tests", ".git", ".venv"}
    forbidden_names = {"model_settings.json", "model-settings.json", "simulation.sqlite3",
                       "simulation.sqlite3-wal", "simulation.sqlite3-shm", ".env"}
    for directory, directories, names in os.walk(release, followlinks=False):
        base = Path(directory)
        for name in directories:
            require(not linked(base / name), "Linked directory found in release")
        for name in names:
            path = base / name
            relative = path.relative_to(release)
            require(not linked(path), "Linked file found in release")
            require(not any(part.casefold() in forbidden_parts for part in relative.parts)
                    and name.casefold() not in forbidden_names and not name.casefold().startswith(".env."),
                    "Private or test data found in release")
            result.append(path)
    return sorted(result)


def finalize(*, tests_run: int, health_url: str) -> dict:
    require(tests_run > 0, "--tests-run must be the count from the completed current test run")
    app_version = version()
    release = inside_project(ROOT / "dist" / "PanXu")
    archive = inside_project(ROOT / "dist" / f"PanXu-{app_version}-win-x64.zip")
    acceptance_path = inside_project(VERIFY / "acceptance.json")
    manifest_path = inside_project(VERIFY / "source-manifest.json")
    require(not archive.exists(), "Current-version ZIP exists; preserve it before finalizing again")
    require(not acceptance_path.exists() and not manifest_path.exists(),
            "Previous acceptance/manifest must be preserved before finalization")
    package_path = VERIFY / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8-sig"))
    require(package.get("status") == "passed" and package.get("application", {}).get("version") == app_version,
            "A successful current-version static package report is required")
    require(package.get("build", {}).get("requested_execution_level") == "asInvoker", "Package does not confirm asInvoker")
    require(package.get("application", {}).get("mode") == "simulation", "Package simulation boundary is not recorded")
    exe = release / "PanXu.exe"
    require(exe.is_file() and sha(exe) == package.get("package", {}).get("exe_sha256"), "Executable differs from static report")
    require((release / "README.txt").is_file() and sha(release / "README.txt") == sha(ROOT / "README.txt"),
            "Release README differs from current source")
    for name in ("index.html", "style.css", "app.js", "tasks.js"):
        require(sha(ROOT / "web" / name) == sha(release / "_internal" / "web" / name), "Packaged web asset differs from source")
    files = release_files(release)
    health = read_health(health_url, app_version)
    local_path = VERIFY / "local-acceptance.json"
    local = json.loads(local_path.read_text(encoding="utf-8-sig"))
    require(local.get("passed") is True and local.get("fixture_unchanged") is True
            and local.get("observed_system_items_unchanged") is True and local.get('application_version') == app_version,
            "Supplied local simulation acceptance report did not pass")
    mock = local.get("model_loopback", {})
    require(mock.get("passed") is True and mock.get("external_connection_attempt_count") == 0,
            "Loopback model acceptance or external-connection boundary did not pass")
    agent_path = VERIFY / 'agent-ui-acceptance.json'
    agent = json.loads(agent_path.read_text(encoding='utf-8-sig'))
    require(agent.get('passed') is True and agent.get('version') == app_version
            and agent.get('real_fixture_files_unchanged') is True and agent.get('actual_reclaimed_bytes') == 0,
            'Current agent task UI/loop acceptance did not pass')
    screenshots = screenshot_evidence()
    manifest = source_snapshot(app_version)
    # Exclusive creation preserves all existing archives. No existing evidence,
    # previous portable package or userdata is removed by finalization.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in files:
            output.write(path, (Path("PanXu") / path.relative_to(release)).as_posix())
    with zipfile.ZipFile(archive) as output:
        require(output.testzip() is None, "ZIP integrity check failed")
        archive_count = len(output.infolist())
    timestamp = datetime.now(timezone.utc).isoformat()
    runtime = {"status": "health_verified_screenshots_recorded", "verified_at_utc": timestamp,
               "health": health, "screenshots": screenshots, "visual_acceptance_asserted_by_finalizer": False,
               "scope": "Packaged loopback health and screenshot integrity; UI flows reviewed separately"}
    package["runtime_acceptance"] = runtime
    package["simulation_only"] = True
    package["real_provider_tested"] = False
    report = {
        "passed": True, "version": app_version, "simulation_only": True, "finalized_at_utc": timestamp,
        "verification_scope": "Current static package report, loopback health, supplied local acceptance and artifact integrity",
        "automated_checks": {"tests_run": tests_run, "executed_by_finalizer": False,
                             "count_source": "Caller supplied --tests-run after its completed current test run"},
        "native_windows": runtime,
        "local_acceptance": {"file": local_path.name, "sha256": sha(local_path), "reported_passed": True,
                             "fixture_unchanged": True, "observed_system_items_unchanged": True,
                             "package_version_attribution_verified": False},
        "real_provider_tested": False, "mock_provider_tool_loop_passed": True,
        "agent_ui_acceptance": {"file": agent_path.name, "sha256": sha(agent_path), "reported_passed": True},
        "source_manifest": {"file": manifest_path.name, "source_file_count": manifest["source_file_count"]},
        "release": {"exe_sha256": sha(exe), "zip_name": archive.name, "zip_bytes": archive.stat().st_size,
                    "zip_sha256": sha(archive), "zip_entries": archive_count},
        "limitations": ["No real executor exists in this build.",
                        "Real provider authentication and compatibility have not been tested.",
                        "Screenshot presence does not establish that each native UI flow passed.",
                        "Test count is supplied by the caller; the finalizer does not run or fabricate test results.",
                        "Local report integrity is checked without claiming packaged-version attribution.",
                        "Startup inventory is partial; desktop uninstall registrations do not establish all dependencies.",
                        "Windows allocated space is unknown; logical estimates are not reclaimed space."],
    }
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with manifest_path.open("x", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
        output.write("\n")
    report["source_manifest"]["sha256"] = sha(manifest_path)
    report["package_report_sha256"] = sha(package_path)
    with acceptance_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-run", type=int, help="Count from the completed current test run")
    parser.add_argument("--health-url", help="Current packaged process /api/health loopback URL")
    parser.add_argument("--archive-previous", action="store_true", help="Preview preservation of previous package/evidence")
    parser.add_argument("--apply", action="store_true", help="Apply archive preview after source freeze; never deletes/overwrites")
    args = parser.parse_args()
    if args.archive_previous:
        require(args.tests_run is None and args.health_url is None, "Archiving and finalization are separate steps")
        print(json.dumps(archive_previous(apply=args.apply), ensure_ascii=False, indent=2))
        return
    require(not args.apply, "--apply is only valid with --archive-previous")
    require(args.tests_run is not None and args.health_url is not None, "--tests-run and --health-url are required")
    report = finalize(tests_run=args.tests_run, health_url=args.health_url)
    print(json.dumps({"passed": report["passed"], "version": report["version"],
                      "tests_run": args.tests_run, **report["release"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
