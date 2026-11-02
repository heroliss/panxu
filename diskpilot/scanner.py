"""Bounded, metadata-only directory inspection for DiskPilot.

This module never reads file contents and exposes no mutation operations.  A
signature describes observed metadata, not a content hash or a filesystem
snapshot.  Callers must revalidate a plan before acting on it.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX_OUTPUT_ITEMS = 2000
_CREDENTIAL_NAMES = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".git-credentials",
    ".pypirc", ".npmrc", ".netrc", "credentials", "credentials.json", "secrets.json", ".env",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
}
_SYSTEM_NAMES = {"windows", "program files", "program files (x86)"}
_PERSONAL_LOCATIONS = {"desktop", "documents", "downloads", "pictures", "videos", "music", "onedrive"}
_PERSONAL_EXTENSIONS = {".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
                        ".ppt", ".pptx", ".csv", ".tsv", ".jpg", ".jpeg", ".png",
                        ".gif", ".webp", ".heic", ".svg", ".mp4", ".mov", ".mp3", ".wav"}


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _parts(path: str) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(path).parts)


def _identity(info: os.stat_result, path: str) -> str:
    """Python's Windows stat uses the volume/file index as dev/ino.

    A zero inode is treated as unavailable, never as a shared identity.
    """
    if info.st_ino:
        return f"{info.st_dev:x}:{info.st_ino:x}"
    return "path:" + os.path.normcase(path)


def _allocated(info: os.stat_result) -> int | None:
    # st_blocks is allocation data on supported POSIX filesystems.  Windows
    # needs a separately verified, no-follow allocation query; do not invent it.
    blocks = getattr(info, "st_blocks", None)
    return int(blocks) * 512 if blocks is not None and blocks >= 0 else None


def _digest(parts: list[str]) -> str:
    value = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8", errors="surrogatepass")
        value.update(len(data).to_bytes(8, "big"))
        value.update(data)
    return value.hexdigest()


def _metadata_signature(path: str, info: os.stat_result) -> str:
    return "metadata-sha256:" + _digest([
        os.path.normcase(path), _identity(info, path), str(info.st_mode),
        str(info.st_size), str(info.st_mtime_ns), str(info.st_ctime_ns),
        str(info.st_nlink), str(getattr(info, "st_file_attributes", 0)),
    ])


def _utc_timestamp_ns(value: Any) -> str | None:
    """Render a stat nanosecond value without floating point or JS integers.

    The displayed precision is microseconds, not a claim about filesystem
    precision. Windows ctime is deliberately never used as creation time.
    """
    if type(value) is not int:
        return None
    try:
        seconds, remainder = divmod(value, 1_000_000_000)
        stamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=seconds, microseconds=remainder // 1000)
        return stamp.isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        return None


def _checked_root(raw_root: str) -> tuple[str, os.stat_result]:
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise ValueError("root 必须是非空目录路径")
    if "\x00" in raw_root:
        raise ValueError("路径包含空字符")

    # Inspect the uncollapsed chain too: link/../safe must not hide a link.
    expanded = os.path.expanduser(raw_root)
    drive, drive_tail = os.path.splitdrive(expanded)
    if drive and not drive_tail.startswith(("/", "\\")):
        raise ValueError("请使用包含盘符根分隔符的完整路径，例如 C:\\；不接受盘符相对路径")
    if os.path.isabs(expanded):
        chain_path = expanded
    else:
        chain_path = os.path.join(os.getcwd(), expanded)
    chain = Path(chain_path)
    current = chain.anchor
    if current:
        anchor_info = os.lstat(current)
        if _is_reparse(anchor_info):
            raise ValueError(f"拒绝扫描：根目录祖先是链接/重解析点：{current}")
    for component in chain.parts[1:] if chain.anchor else chain.parts:
        if component in {"", "."}:
            continue
        if component == "..":
            current = os.path.dirname(current)
        else:
            current = os.path.join(current, component)
        info = os.lstat(current)
        if _is_reparse(info):
            raise ValueError(f"拒绝扫描：根目录或祖先是链接/重解析点：{current}")
        if not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(f"路径不是目录：{current}")
    root = os.path.abspath(expanded)
    info = os.lstat(root)
    if _is_reparse(info):
        raise ValueError(f"拒绝扫描：根目录是链接/重解析点：{root}")
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"路径不是目录：{root}")
    return root, info


def _marker_exists(path: str) -> bool:
    try:
        os.lstat(path)
        return True
    except OSError:
        return False


def _project_at(path: str, names: set[str] | None = None) -> str | None:
    if names is None:
        git = _marker_exists(os.path.join(path, ".git"))
        unity = all(_marker_exists(os.path.join(path, name))
                    for name in ("Assets", "ProjectSettings"))
    else:
        git = ".git" in names
        unity = {"assets", "projectsettings"}.issubset(names)
    return "Unity" if unity else ("Git" if git else None)


def _ancestor_project(root: str) -> str | None:
    # Only named marker metadata is inspected outside the requested subtree.
    current = root
    while True:
        project = _project_at(current)
        if project:
            return project
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _classification(path: str, project: str | None) -> dict[str, Any]:
    parts = _parts(path)
    names = set(parts)
    name = os.path.basename(path).casefold()
    drive, tail = os.path.splitdrive(path)
    is_volume_root = (bool(drive) and tail in {os.sep, "/", "\\"}) or path == os.path.abspath(os.sep)
    if is_volume_root or names.intersection(_SYSTEM_NAMES):
        owner, category = "Windows / 已安装应用", "system"
        reason = "系统或已安装应用目录；不能按普通垃圾处理"
    elif names.intersection(_CREDENTIAL_NAMES) or name.startswith(".env.") or name.endswith((".pem", ".pfx", ".key")):
        owner, category = "凭据与密钥", "credentials"
        reason = "路径或文件名表明可能包含凭据、密钥或环境配置"
    elif ".hyperframes-cache" in names:
        owner, category = "Hyperframes", "project"
        reason = "Hyperframes 工作数据受到保护，不能仅因名称含 cache 就清理"
    elif project or ".git" in names:
        owner, category = f"{project or 'Git'} 项目", "project"
        reason = "检测到项目标记或版本库数据；资源、构建产物可能参与项目工作"
    else:
        cache_prefixes = (("appdata", "local", "npm-cache"),
                          ("appdata", "local", "pip", "cache"),
                          ("appdata", "local", "nvidia", "dxcache"),
                          ("appdata", "local", "nvidia", "glcache"),
                          ("appdata", "local", "d3dscache"))
        known_cache = any(parts[index:index + len(prefix)] == prefix
                          for prefix in cache_prefixes
                          for index in range(len(parts) - len(prefix) + 1))
        # A redirected LocalAppData can have a different directory spelling.
        known_cache = known_cache or _verified_managed_cache(path)
        if known_cache:
            return {
                "owner": "应用缓存", "category": "cache", "risk": "low",
                "recommendation": "可在相关应用退出后加入模拟清理计划",
                "reason": "路径符合已知可重建应用缓存；不依据 Temp/cache 名称单独判断",
                "impact": "实际清理后应用可能重新下载或重新生成缓存；本工具仅模拟",
            }
        installer = name.endswith((".msi", ".exe", ".msix", ".msixbundle", ".unitypackage"))
        temp = bool(names.intersection({"temp", "tmp"}))
        return {
            "owner": "用户文件", "category": "installer" if installer else ("temporary" if temp else "other"),
            "risk": "review", "recommendation": "人工确认用途后再加入模拟计划",
            "reason": "可能是安装包或独立程序，不能仅凭扩展名判断无用" if installer else (
                "位于临时名称目录，但无法确定文件已停止使用或没有独立用途" if temp else "仅有文件元数据，无法证明内容已经无用"
            ),
            "impact": "可能影响未完成任务、项目引用或个人资料；本工具仅模拟",
        }
    return {
        "owner": owner, "category": category, "risk": "protected",
        "recommendation": "保留；需要时使用应用或系统提供的管理入口",
        "reason": reason, "impact": "处理可能破坏系统、应用、项目或账户访问",
    }


def _verified_managed_cache(path: str) -> bool:
    """Require a cache rule under the current user's managed LocalAppData.

    Merely spelling AppData/Local inside Downloads (or another arbitrary
    directory) is insufficient.  No custom path is trusted by this rule.
    """
    configured = os.environ.get("LOCALAPPDATA", "")
    if not configured:
        profile = os.environ.get("USERPROFILE", "")
        configured = os.path.join(profile, "AppData", "Local") if profile else ""
    if not configured or not os.path.isabs(configured):
        return False
    managed = os.path.normcase(os.path.abspath(configured))
    candidate = os.path.normcase(os.path.abspath(path))
    try:
        if os.path.commonpath((managed, candidate)) != managed:
            return False
        relative = Path(os.path.relpath(candidate, managed)).parts
    except ValueError:
        return False
    relative = tuple(part.casefold() for part in relative)
    prefixes = (("npm-cache",), ("pip", "cache"), ("nvidia", "dxcache"),
                ("nvidia", "glcache"), ("d3dscache",))
    return any(relative[:len(prefix)] == prefix for prefix in prefixes)


def _decision_context(path: str, classification: dict[str, Any]) -> dict[str, Any]:
    """Advisory ownership evidence, never authorization to execute an action."""
    category, risk = classification.get("category"), classification.get("risk")
    if category == "system" and risk == "protected":
        ownership, source, personal, regenerable = "system", "protected_system_path", False, False
    elif risk == "protected":
        ownership, source, personal, regenerable = "personal", "protected_project_or_credentials", True, False
    elif category == "cache" and risk == "low" and _verified_managed_cache(path):
        ownership, source, personal, regenerable = "application", "managed_cache_path", False, True
    elif category == "cache":
        ownership, source, personal, regenerable = "unknown", "unverified_cache_location", True, False
    else:
        personal_location = bool(set(_parts(path)).intersection(_PERSONAL_LOCATIONS))
        personal_type = Path(path).suffix.casefold() in _PERSONAL_EXTENSIONS
        ownership = "personal" if personal_location or personal_type else "unknown"
        source = "personal_location" if personal_location else ("personal_file_type" if personal_type else "unproven_file_metadata")
        personal, regenerable = True, False
    return {
        "ownership": ownership, "personal_choice_required": personal,
        "regenerable": regenerable, "classification_source": source,
        "auto_select": ownership == "application" and regenerable and not personal and risk != "protected",
        "advisory_only": True,
    }


def metadata_item(path: str, info: os.stat_result, *, root: str, depth: int,
                  parent_id: str | None, project: str | None, is_dir: bool) -> dict[str, Any]:
    """Shared metadata/classification record, with no permission to act yet.

    Both the bounded tree scanner and the durable observation index use this
    constructor. Completeness and signatures must be supplied by the collector.
    """
    classification = _classification(path, project)
    decision = _decision_context(path, classification)
    item = {
        "id": "scan-" + _digest([os.path.normcase(path)])[:24],
        "kind": "directory" if is_dir else "file", "name": os.path.basename(path) or path,
        "path": path, **classification, "size_bytes": 0 if is_dir else info.st_size,
        "allocated_bytes": None if is_dir else _allocated(info), "actions": [],
        "signature": "", "details": {
            "depth": depth, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "timestamps": {
                "created_at": _utc_timestamp_ns(getattr(info, "st_birthtime_ns", None)),
                "modified_at": _utc_timestamp_ns(info.st_mtime_ns),
                "accessed_at": _utc_timestamp_ns(getattr(info, "st_atime_ns", None)),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "scope": "entry_only",
            },
            "file_identity": _identity(info, path), "link_count": info.st_nlink,
            "is_hardlink": not is_dir and info.st_nlink > 1,
            "signature_kind": "metadata_only", "root": root,
            "scan_only": True, "parent_id": parent_id,
            "decision_context": decision, "auto_select": decision["auto_select"],
            "verified_cache": decision["classification_source"] == "managed_cache_path",
        },
        "evidence": [classification["reason"], "仅检查名称和元数据；没有读取文件内容"],
    }
    if decision["classification_source"] == "unverified_cache_location":
        item["reason"] = "名称形态类似应用缓存，但不在已核实的当前用户受管缓存根内"
        item["recommendation"] = "必须由用户核实用途并选择；不能按缓存规则自动推荐"
        item["impact"] = "自定义或仿造缓存目录可能保存个人资料；用途尚未证明"
        item["evidence"][0] = item["reason"]
    return item


@dataclass
class _Node:
    item: dict[str, Any]
    depth: int
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    entities: dict[str, tuple[int, int | None]] = field(default_factory=dict)
    hardlink_counts: dict[str, int] = field(default_factory=dict)
    hardlink_totals: dict[str, int] = field(default_factory=dict)
    logical: int = 0
    files: int = 0
    directories: int = 0
    protected: int = 0
    skipped: int = 0
    errors: int = 0
    complete: bool = True
    markers: list[str] = field(default_factory=list)


def scan_directory(root: str, *, max_entries: int = 25000, max_depth: int = 12,
                   cancel=None, progress=None) -> dict[str, Any]:
    """Scan one directory without following reparse points or reading contents.

    ``cancel`` may be a callable or an object with ``is_set()``. ``progress``
    receives dictionaries. Limits, inaccessible paths and cancellation produce
    partial results; no partial directory can be selected as a whole.
    """
    warnings: list[str] = []
    summary: dict[str, Any] = {
        "root": root, "status": "error", "partial": True, "cancelled": False,
        "scanned_entries": 0, "file_count": 0, "directory_count": 0,
        "logical_bytes": 0, "unique_logical_bytes": 0, "allocated_bytes": None,
        "hardlink_paths": 0, "hardlink_duplicate_bytes": 0, "reparse_skipped": 0,
        "error_count": 0, "output_items": 0, "output_truncated": False,
        "max_entries": max_entries, "max_depth": max_depth,
        "signature_kind": "metadata_only", "reads_file_contents": False,
    }
    if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
        return {"items": [], "summary": summary, "warnings": ["max_entries 必须是正整数"]}
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        return {"items": [], "summary": summary, "warnings": ["max_depth 必须是非负整数"]}
    try:
        absolute_root, root_info = _checked_root(root)
    except (OSError, ValueError) as exc:
        summary["error_count"] = 1
        return {"items": [], "summary": summary,
                "warnings": [f"无法扫描根目录：{type(exc).__name__}: {exc}"]}
    summary["root"] = absolute_root
    nodes: list[_Node] = []
    global_entities: dict[str, tuple[int, int | None]] = {}
    limit_hit = False
    callback_failed = False
    last_progress = 0.0

    def cancelled() -> bool:
        if summary["cancelled"]:
            return True
        if cancel is None:
            return False
        try:
            answer = cancel() if callable(cancel) else cancel.is_set()
        except Exception as exc:
            warnings.append(f"取消信号读取失败，已停止扫描：{type(exc).__name__}: {exc}")
            answer = True
        if answer:
            summary["cancelled"] = True
        return bool(answer)

    def notify(path: str, force: bool = False) -> None:
        nonlocal last_progress, callback_failed
        if progress is None or callback_failed:
            return
        now = time.monotonic()
        if not force and now - last_progress < 0.1:
            return
        last_progress = now
        try:
            progress({**summary, "current_path": path, "phase": "scanning",
                      "status": "running", "unique_logical_bytes": sum(x[0] for x in global_entities.values())})
        except Exception as exc:
            callback_failed = True
            warnings.append(f"进度回调失败，扫描继续：{type(exc).__name__}: {exc}")

    def make_node(path: str, info: os.stat_result, depth: int,
                  parent: _Node | None, project: str | None, is_dir: bool) -> _Node:
        item = metadata_item(path, info, root=absolute_root, depth=depth,
                             parent_id=parent.item["id"] if parent else None,
                             project=project, is_dir=is_dir)
        node = _Node(item, depth, parent)
        node.protected = int(item["risk"] == "protected")
        if parent:
            parent.children.append(node)
        nodes.append(node)
        return node

    def refresh_classification(node: _Node, path: str, project: str) -> None:
        node.item.update(_classification(path, project))
        decision = _decision_context(path, node.item)
        node.item["details"].update(decision_context=decision, auto_select=decision["auto_select"],
                                     verified_cache=decision["classification_source"] == "managed_cache_path")
        node.item["evidence"][0] = node.item["reason"]
        node.protected = int(node.item["risk"] == "protected")

    def walk(path: str, info: os.stat_result, depth: int,
             parent: _Node | None, project: str | None) -> _Node:
        nonlocal limit_hit
        node = make_node(path, info, depth, parent, project, True)
        summary["directory_count"] += 1
        node.directories = 1
        entries: list[os.DirEntry[str]] = []
        if cancelled():
            node.complete = False
            node.markers.append("cancelled")
        elif depth >= max_depth:
            node.complete = False
            node.markers.append("depth-limit")
            warnings.append(f"达到深度限制，未展开：{path}")
        else:
            try:
                # Recheck the entire chain immediately before directory access.
                _checked_root(path)
                # Probe named markers even if limits later truncate listing,
                # but only after the no-reparse ancestor check.
                detected = project or _project_at(path)
                if detected and not project:
                    project = detected
                    refresh_classification(node, path, project)
                remaining = max_entries - summary["scanned_entries"]
                with os.scandir(path) as iterator:
                    for entry in iterator:
                        if cancelled():
                            node.complete = False
                            node.markers.append("cancelled")
                            break
                        if len(entries) >= remaining:
                            limit_hit = True
                            node.complete = False
                            node.markers.append("entry-limit")
                            break
                        entries.append(entry)
            except (OSError, ValueError) as exc:
                node.complete = False
                node.errors += 1
                summary["error_count"] += 1
                warnings.append(f"目录读取失败：{path}：{type(exc).__name__}: {exc}")
                node.markers.append(f"error:{type(exc).__name__}")
            detected = _project_at(path, {entry.name.casefold() for entry in entries})
            if detected and not project:
                project = detected
                refresh_classification(node, path, project)
            for entry in sorted(entries, key=lambda value: (value.name.casefold(), value.name)):
                if cancelled() or summary["scanned_entries"] >= max_entries:
                    node.complete = False
                    node.markers.append("cancelled" if summary["cancelled"] else "entry-limit")
                    limit_hit = limit_hit or not summary["cancelled"]
                    break
                summary["scanned_entries"] += 1
                child_path = os.path.join(path, entry.name)
                notify(child_path)
                try:
                    # On Windows DirEntry.stat may report zero st_ino/st_dev/
                    # st_nlink.  lstat is still no-follow and obtains the real
                    # file identity needed for hardlink accounting.
                    child_info = os.lstat(child_path)
                    if _is_reparse(child_info):
                        node.skipped += 1
                        summary["reparse_skipped"] += 1
                        node.complete = False
                        node.markers.append("reparse:" + entry.name)
                        warnings.append(f"已跳过链接/重解析点：{child_path}")
                        continue
                    if stat.S_ISDIR(child_info.st_mode):
                        child = walk(child_path, child_info, depth + 1, node, project)
                    elif stat.S_ISREG(child_info.st_mode):
                        child = make_node(child_path, child_info, depth + 1, node, project, False)
                        identity = _identity(child_info, child_path)
                        child.entities[identity] = (child_info.st_size, _allocated(child_info))
                        child.logical = child_info.st_size
                        child.files = 1
                        child.item["signature"] = _metadata_signature(child_path, child_info)
                        child.item["details"].update({"complete": True, "subtree_complete": True,
                            "allocated_known": child.item["allocated_bytes"] is not None,
                            "can_simulate_delete": child.item["risk"] != "protected"})
                        if child.item["risk"] != "protected":
                            child.item["actions"] = ["delete"]
                        summary["file_count"] += 1
                        summary["logical_bytes"] += child_info.st_size
                        if child_info.st_nlink > 1:
                            child.hardlink_counts[identity] = 1
                            child.hardlink_totals[identity] = child_info.st_nlink
                            summary["hardlink_paths"] += 1
                            child.item["evidence"].append("文件存在多个硬链接；删除本路径不一定释放实体空间")
                        prior = global_entities.get(identity)
                        if prior is not None and prior != child.entities[identity]:
                            node.complete = False
                            node.markers.append("entity-changed:" + identity)
                            warnings.append(f"同一文件实体的元数据在扫描期间变化：{child_path}")
                        global_entities[identity] = child.entities[identity]
                    else:
                        node.complete = False
                        node.markers.append("special:" + entry.name)
                        warnings.append(f"已跳过非普通文件：{child_path}")
                        continue
                    node.entities.update(child.entities)
                    for identity, count in child.hardlink_counts.items():
                        node.hardlink_counts[identity] = node.hardlink_counts.get(identity, 0) + count
                        node.hardlink_totals[identity] = max(node.hardlink_totals.get(identity, 0), child.hardlink_totals[identity])
                    node.logical += child.logical
                    node.files += child.files
                    node.directories += child.directories
                    node.protected += child.protected
                    node.skipped += child.skipped
                    node.errors += child.errors
                    node.complete = node.complete and child.complete
                except OSError as exc:
                    node.complete = False
                    node.errors += 1
                    summary["error_count"] += 1
                    warnings.append(f"条目读取失败：{child_path}：{type(exc).__name__}: {exc}")
                    node.markers.append(f"entry-error:{entry.name}:{type(exc).__name__}")
        try:
            after = os.lstat(path)
            if _is_reparse(after) or _identity(after, path) != _identity(info, path) or after.st_mtime_ns != info.st_mtime_ns:
                node.complete = False
                node.markers.append("directory-changed")
                warnings.append(f"目录在扫描期间变化，聚合结果不完整：{path}")
        except OSError as exc:
            node.complete = False
            node.errors += 1
            summary["error_count"] += 1
            node.markers.append(f"post-check:{type(exc).__name__}")
            warnings.append(f"目录复核失败：{path}：{type(exc).__name__}: {exc}")
        unique_size = sum(value[0] for value in node.entities.values())
        allocation = [value[1] for value in node.entities.values()]
        observed_allocated = sum(allocation) if all(value is not None for value in allocation) else None
        allocated = observed_allocated if node.complete else None
        node.item["size_bytes"] = unique_size
        node.item["allocated_bytes"] = allocated
        can_delete = node.complete and node.protected == 0
        node.item["details"].update({
            "complete": node.complete, "subtree_complete": node.complete,
            "can_simulate_delete": can_delete, "logical_bytes_all_paths": node.logical,
            "unique_logical_bytes": unique_size, "hardlink_duplicate_bytes": node.logical - unique_size,
            "unique_file_entities": len(node.entities), "file_count": node.files,
            "directory_count": node.directories, "protected_entries": node.protected,
            "reparse_skipped": node.skipped, "error_count": node.errors,
            "allocated_known": allocated is not None,
            "observed_allocated_bytes": observed_allocated,
            "hardlink_entities": [
                {"file_identity": identity, "size_bytes": node.entities[identity][0],
                 "observed_links": node.hardlink_counts[identity],
                 "link_count": node.hardlink_totals[identity]}
                for identity in sorted(node.hardlink_counts)[:128]
            ],
            "hardlink_entities_complete": len(node.hardlink_counts) <= 128,
            "has_external_hardlinks": any(node.hardlink_totals[identity] > count
                                          for identity, count in node.hardlink_counts.items()),
        })
        if can_delete:
            node.item["actions"] = ["delete"]
        elif not node.complete:
            node.item["recommendation"] = "扫描不完整；不能整目录加入模拟清理，可查看已确认的单项"
            node.item["evidence"].append("包含未扫描、失败或已跳过的条目，禁止整目录模拟清理")
        elif node.item["risk"] != "protected":
            node.item["recommendation"] = "包含受保护条目；不能整目录加入模拟清理"
            node.item["evidence"].append("子树包含受保护文件或项目")
        decision = dict(node.item["details"]["decision_context"])
        child_contexts = [child.item["details"]["decision_context"] for child in node.children]
        child_owners = {context["ownership"] for context in child_contexts}
        child_personal = any(context["personal_choice_required"] or context["ownership"] in {"personal", "unknown"}
                             for context in child_contexts)
        if child_personal:
            # A known cache container cannot promote a personal/unknown child
            # into application-owned data.  Mixed ownership stays unknown.
            owners = child_owners | {decision["ownership"]}
            decision["ownership"] = "personal" if owners == {"personal"} else "unknown"
            decision["classification_source"] = "conservative_directory_subtree"
        decision["personal_choice_required"] = bool(
            decision["personal_choice_required"] or child_personal or not node.complete
            or decision["ownership"] in {"personal", "unknown"})
        decision["regenerable"] = bool(
            decision["regenerable"] and node.complete and not node.protected
            and all(context["regenerable"] for context in child_contexts))
        decision["auto_select"] = bool(
            can_delete and decision["ownership"] == "application" and decision["regenerable"]
            and not decision["personal_choice_required"])
        if decision["auto_select"]:
            decision["classification_source"] = "managed_cache_subtree"
        node.item["details"].update(
            decision_context=decision, auto_select=decision["auto_select"],
            verified_cache=decision["auto_select"], contains_personal_or_unknown=child_personal
            or decision["ownership"] in {"personal", "unknown"} or not node.complete)
        content = [os.path.normcase(path), _metadata_signature(path, info)]
        content.extend(child.item["signature"] for child in node.children)
        content.extend(sorted(node.markers))
        node.item["signature"] = ("subtree-sha256:" if node.complete else "partial-subtree-sha256:") + _digest(content)
        return node

    notify(absolute_root, True)
    tree = walk(absolute_root, root_info, 0, None, _ancestor_project(absolute_root))
    if limit_hit:
        warnings.append(f"达到条目限制 {max_entries}；未扫描部分不计入大小")
    summary.update({
        "partial": not tree.complete, "status": "cancelled" if summary["cancelled"] else ("partial" if not tree.complete else "completed"),
        "unique_logical_bytes": tree.item["size_bytes"],
        "allocated_bytes": tree.item["allocated_bytes"],
        "hardlink_duplicate_bytes": tree.logical - tree.item["size_bytes"],
        "hardlink_entity_count": len(tree.hardlink_counts),
        "unobserved_hardlink_count": sum(max(0, tree.hardlink_totals[identity] - count)
                                        for identity, count in tree.hardlink_counts.items()),
    })
    for node in nodes:
        if node.item["kind"] == "file" and node.item["details"]["is_hardlink"]:
            identity = node.item["details"]["file_identity"]
            observed = tree.hardlink_counts.get(identity, 1)
            node.item["details"]["observed_links_in_scan"] = observed
            node.item["details"]["unobserved_link_count"] = max(0, node.item["details"]["link_count"] - observed)
            node.item["details"]["has_external_hardlinks"] = node.item["details"]["unobserved_link_count"] > 0

    # Keep all paths in small scans.  For large scans keep useful upper-level
    # directories and the largest files, always including their parent chain.
    selected: dict[str, _Node] = {}

    def include(node: _Node) -> None:
        chain: list[_Node] = []
        current: _Node | None = node
        while current and current.item["id"] not in selected:
            chain.append(current)
            current = current.parent
        if len(selected) + len(chain) <= _MAX_OUTPUT_ITEMS:
            for ancestor in reversed(chain):
                selected[ancestor.item["id"]] = ancestor

    if len(nodes) <= _MAX_OUTPUT_ITEMS:
        selected = {node.item["id"]: node for node in nodes}
    else:
        include(tree)
        upper = sorted((node for node in nodes if node.item["kind"] == "directory" and node.depth <= 2),
                       key=lambda node: (node.depth, -node.item["size_bytes"], node.item["path"]))
        for node in upper[:500]:
            include(node)
        candidates = sorted((node for node in nodes if node is not tree),
                            key=lambda node: (-node.item["size_bytes"], node.depth, node.item["path"]))
        for node in candidates:
            include(node)
    for node in selected.values():
        if node.item["kind"] == "directory":
            node.item["details"]["visible_child_count"] = sum(child.item["id"] in selected for child in node.children)
            node.item["details"]["omitted_direct_children"] = sum(child.item["id"] not in selected for child in node.children)
    items = [node.item for node in selected.values()]
    summary["output_items"] = len(items)
    summary["output_truncated"] = len(items) < len(nodes)
    summary["omitted_items"] = len(nodes) - len(items)
    if summary["output_truncated"]:
        warnings.append(f"展示已收敛为 {len(items)} 项；其余 {len(nodes) - len(items)} 项已计入聚合大小")
    if progress is not None and not callback_failed:
        try:
            progress({**summary, "current_path": absolute_root, "phase": "finished"})
        except Exception as exc:
            warnings.append(f"最终进度回调失败：{type(exc).__name__}: {exc}")
    return {"items": items, "summary": summary, "warnings": warnings}


def validate_item_snapshot(item: dict[str, Any]) -> bool:
    """Revalidate catalog metadata before preparing a simulation plan.

    This is deliberately conservative: no content reads, no link following,
    and no acceptance of a directory whose bounded rescan is incomplete.
    It does not replace another preflight immediately before execution.
    """
    if not isinstance(item, dict):
        return False
    path = item.get("path")
    if not isinstance(path, str) or not os.path.isabs(path):
        return False
    try:
        if item.get("kind") == "file":
            _checked_root(os.path.dirname(path))
            info = os.lstat(path)
            if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
                return False
            current = _classification(path, _ancestor_project(os.path.dirname(path)))
            return (current["risk"] == item.get("risk")
                    and _metadata_signature(path, info) == item.get("signature"))
        if item.get("kind") == "directory":
            # Refuse a previously incomplete snapshot even if its same partial
            # prefix could happen to reproduce on the next limited scan.
            details = item.get("details", {})
            if not isinstance(details, dict):
                return False
            if details.get("complete") is False or details.get("subtree_complete") is False:
                return False
            result = scan_directory(path, max_entries=10000, max_depth=32)
            if result["summary"]["status"] != "completed" or not result["items"]:
                return False
            current = result["items"][0]
            return (current["signature"] == item.get("signature")
                    and current["risk"] == item.get("risk")
                    and current["details"]["can_simulate_delete"] == details.get("can_simulate_delete"))
    except (OSError, ValueError, TypeError):
        return False
    return False
