"""User-facing advice, with deterministic protection and personal-choice gates.

Recommendations never change an item's original actions/risk or execute anything.
Positive decision evidence is collector-owned, never inferred from age/memory/name.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import fnmatch
import re

_ACTION = {"file": "delete", "directory": "delete", "startup": "disable_startup",
           "service": "stop_service", "process": "stop_process", "software": "uninstall"}
_GROUPS = (("recommended", "建议处理"), ("personal", "你的文件，等你选择"),
           ("keep", "建议保留"), ("review", "还需核实"))
_IMPACTS = {
    "file": "处理前仍需确认留存需求；本版本只做模拟。",
    "directory": "处理前仍需确认目录用途；本版本只做模拟。",
    "startup": "处理后相关功能不会随登录自动就绪，仍可手动打开；本版本只做模拟。",
    "service": "停止会影响依赖它的后台功能，部分服务可能自动重启；本版本只做模拟。",
    "process": "停止可能中断任务或丢失未保存工作；本版本只做模拟。",
    "software": "正式处理需要软件的正规卸载入口，可能移除插件或配置；本版本只做模拟。",
}
_PERSONAL_CATEGORY = {"personal", "installer", "project", "other", "temporary",
                      "个人资料", "个人作品", "项目资料", "安装包", "临时工作资料"}
_KEEP_CATEGORY = {"system", "credentials", "privacy", "系统组件", "隐私与账号"}


def _details(item):
    return item.get("details") if isinstance(item.get("details"), dict) else {}


def _action(item):
    expected = _ACTION.get(item.get("kind"))
    actions = item.get("actions")
    return expected if isinstance(actions, list) and expected in actions else None


def _managed_cache(item):
    """Accept only a current collector-verified managed root, never its spelling."""
    details = _details(item)
    context = details.get("decision_context")
    return bool(details.get("verified_cache") is True and isinstance(context, dict)
                and context.get("ownership") == "application"
                and context.get("personal_choice_required") is False
                and context.get("regenerable") is True and context.get("auto_select") is True
                and context.get("classification_source") in ("managed_cache_path", "managed_cache_subtree"))


def _is_personal(item):
    if item.get("kind") not in ("file", "directory"):
        return False
    details = _details(item)
    if any(details.get(k) is True for k in ("user_created", "user_downloaded", "personal")):
        return True
    category = item.get("category")
    if category in _PERSONAL_CATEGORY:
        return True
    parts = str(item.get("path", "")).replace("\\", "/").casefold().split("/")
    personal_names = {"downloads", "download", "documents", "desktop", "pictures", "videos",
                      "music", "下载", "文档", "桌面", "图片", "视频", "作品", "演练下载", "演练作品"}
    if any(p in personal_names for p in parts):
        return True
    context = details.get("decision_context")
    if isinstance(context, dict):
        if context.get("personal_choice_required") is True:
            return True
        if context.get("ownership") == "application" and context.get("personal_choice_required") is False:
            return False
    # Unknown user files are personal, even if named *.tmp or cache.zip.
    return not _managed_cache(item)


def _evidence_flags(item):
    """Explicit local evidence that may support a model's non-personal decision."""
    d, kind = _details(item), item.get("kind")
    # Files use the verified-cache rule or personal-choice gate. A model cannot
    # reopen an occupied/incomplete-cache decision through a generic evidence bit.
    if kind in ("file", "directory") or (kind == "process" and d.get("unsaved_work") is True):
        return []
    if d.get("verified_unnecessary") is True:
        return ["verified_unnecessary"]
    if kind == "startup" and d.get("optional") is True and d.get("purpose_verified") is True:
        return ["optional_startup", "purpose_verified"]
    if (kind == "service" and d.get("optional") is True
            and d.get("dependency_check_complete") is True and d.get("has_dependents") is False):
        return ["optional_service", "no_dependents_verified"]
    if kind == "process" and d.get("disposable_session") is True and d.get("unsaved_work") is False:
        return ["disposable_session", "no_unsaved_work_verified"]
    if kind == "software":
        if d.get("unused_confirmed") is True or d.get("user_confirmed_unused") is True:
            return ["unused_confirmed"]
        if d.get("redundant") is True and d.get("replacement_verified") is True:
            return ["redundant_version", "replacement_verified"]
    return []


def _advice(group, reason, impact, action=None, *, origin="rule", auto=False):
    return {"group": group, "title": dict(_GROUPS)[group], "reason": reason,
            "impact": impact, "auto_select": bool(auto and group == "recommended"),
            "suggested_action": action, "origin": origin, "needs_user_choice": group == "personal"}


def _provenance(value=None):
    """Expose only generation facts supplied by the host, never current settings.

    Older callers may return model advice without metadata. That stays unknown;
    attributing it to whichever profile happens to be selected now is false.
    """
    unknown = dict(source='unknown', provider=None, model=None, protocol=None, generated_at=None)
    if not isinstance(value, dict) or value.get('source') not in ('llm', 'local'):
        return unknown
    stamp = value.get('generated_at')
    try:
        parsed = datetime.fromisoformat(stamp) if isinstance(stamp, str) and len(stamp) <= 50 else None
        if parsed is None or parsed.tzinfo is None:
            return unknown
        stamp = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return unknown
    fields = {}
    for field, limit in (('provider', 80), ('model', 160), ('protocol', 80)):
        text = value.get(field)
        if value['source'] == 'llm' and (not isinstance(text, str) or not text.strip() or len(text) > limit
                                        or any(ord(char) < 32 for char in text)):
            return unknown
        fields[field] = text if value['source'] == 'llm' else None
    return dict(source=value['source'], generated_at=stamp, **fields)


def _protected_by_pattern(item, patterns):
    path = str(item.get("path", "")).replace("\\", "/").casefold()
    return any(isinstance(p, str) and fnmatch.fnmatchcase(path, p.replace("\\", "/").casefold())
               for p in patterns)


def _base_advice(item, protect_patterns=()):
    d, action = _details(item), _action(item)
    context = d.get("decision_context") if isinstance(d.get("decision_context"), dict) else {}
    kind = item.get("kind")
    impact = _IMPACTS.get(kind, "用途未确认；本版本只做模拟。")
    if item.get("simulated") is True:
        return _advice("keep", "这项已在模拟记录中处理，无需重复加入。", "模拟记录不等于真实系统已改变。")
    if (item.get("risk") == "protected" or item.get("category") in _KEEP_CATEGORY or context.get("ownership") == "system"
            or d.get("user_protected") is True or _protected_by_pattern(item, protect_patterns)):
        return _advice("keep", "这是系统、账户或受保护内容，建议保留。", "处理可能影响系统、账户或项目资料。")
    if action is None:
        return _advice("keep", "目前没有已支持的处理方式，先保留。", "本工具不会尝试其他操作。")
    if _is_personal(item):
        return _advice("personal", "这是你的下载、创建内容或用途未知的文件，是否留用由你最后选择。", impact, action)
    if kind in ("file", "directory") and _managed_cache(item):
        if any(d.get(k) is True for k in ("in_use", "locked", "active", "is_reparse_point", "is_symlink")):
            return _advice("review", "这是应用缓存，但仍可能被占用或包含链接，暂不默认勾选。", impact, action)
        if kind == "directory" and (d.get("complete") is not True or d.get("subtree_complete") is not True):
            return _advice("review", "缓存目录尚未完整核查，暂不整目录选择。", impact, action)
        return _advice("recommended", "这是已确认可重新生成的应用缓存，可以优先处理。",
                       "下次使用可能重新下载或编译，首次启动会慢一些；本版本只做模拟。", action, auto=True)
    return _advice("review", "还缺少用途、依赖或使用需求的依据；不会仅因占用大或时间久就建议处理。", impact, action)


def _representative_items(items, limit=100, protect_patterns=()):
    candidates = [i for i in items if _base_advice(i, protect_patterns)["group"] != "keep"]
    buckets = {}
    for item in candidates:
        buckets.setdefault((item.get("kind"), _base_advice(item, protect_patterns)["group"]), []).append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda i: i.get("size_bytes", 0) if type(i.get("size_bytes")) is int else 0, reverse=True)
    chosen = []
    while buckets and len(chosen) < limit:
        for key in list(buckets):
            if len(chosen) == limit:
                break
            chosen.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    return chosen, len(candidates)


def _coalesce_auto_selection(items):
    """Use complete verified cache roots instead of selecting all descendants."""
    by_id = {i.get("id"): i for i in items}
    roots = {i.get("id") for i in items if i.get("kind") == "directory"
             and i["advice"]["auto_select"] and _managed_cache(i)
             and _details(i).get("complete") is True and _details(i).get("subtree_complete") is True}
    for item in items:
        if not item["advice"]["auto_select"]:
            continue
        ancestor = _details(item).get("parent_id")
        seen = {item.get("id")}
        while isinstance(ancestor, str) and ancestor not in seen and len(seen) <= 128:
            if ancestor in roots:
                item["advice"]["auto_select"] = False
                item["advice"]["reason"] += " 已由上级完整缓存目录统一处理，无需重复选择。"
                break
            seen.add(ancestor)
            parent = by_id.get(ancestor)
            ancestor = _details(parent).get("parent_id") if parent else None


def _purpose_title(item):
    name = "".join(c for c in str(item.get("name", "未命名项目")) if ord(c) >= 32)[:120]
    if _managed_cache(item):
        path = str(item.get("path", "")).replace("\\", "/").casefold()
        if "/pip/cache" in path:
            return "Python 下载和构建缓存"
        if "/npm-cache" in path:
            return "Node.js 下载缓存"
        if any(p in path for p in ("/nvidia/dxcache", "/nvidia/glcache", "/d3dscache")):
            return "图形着色器缓存"
        return "应用可再生成缓存"
    prefixes = {"startup": "随开机启动的 ", "service": "后台服务：", "process": "正在运行的 ",
                "software": "已安装的 "}
    return prefixes.get(item.get("kind"), "") + name


def _purpose_description(item):
    if _managed_cache(item):
        path = str(item.get("path", "")).replace("\\", "/").casefold()
        if "/pip/cache" in path:
            return "安装或更新 Python 工具时保存的下载和构建副本，之后需要时可以重新获取。"
        if "/npm-cache" in path:
            return "JavaScript 开发工具保存的软件包下载副本，用于加快以后安装软件包的速度。"
        if any(p in path for p in ("/nvidia/dxcache", "/nvidia/glcache", "/d3dscache")):
            return "显卡为游戏和图形应用保存的计算结果，用来缩短后续加载和编译时间。"
        return "应用为了加快运行而保存的临时副本，可以在需要时重新生成。"
    kind = item.get("kind")
    descriptions = {
        "startup": "让相关应用在登录 Windows 后自动打开；它与应用本身是两个不同的设置。",
        "service": "在后台为 Windows 或其他应用提供功能，是否需要取决于用途和依赖关系。",
        "process": "当前正在运行的应用或后台任务；关闭它可能中断正在进行的工作。",
        "software": "电脑中已安装的软件，清理前需要了解它的用途以及是否仍在使用。",
        "directory": "包含其他文件的文件夹，整组处理会影响里面的内容。",
        "file": "这是已检查到的文件，仅凭名称和大小还不能确定你是否需要保留。",
    }
    return descriptions.get(kind, "目前还不能可靠确认它的具体用途。")


def _validated_suggestions(value, items):
    """Reject a complete malformed/out-of-scope batch, rather than half-apply it."""
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("invalid_recommendations")
    scoped = {i.get("id"): i for i in items}
    seen, accepted = set(), []
    for row in value:
        expected = {"id", "recommend", "suggested_action", "reason", "impact"}
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError("invalid_recommendations")
        key = row["id"]
        if not isinstance(key, str) or key not in scoped or key in seen or type(row["recommend"]) is not bool:
            raise ValueError("invalid_recommendations")
        seen.add(key)
        action = row["suggested_action"]
        if action is not None and action != _action(scoped[key]):
            raise ValueError("invalid_recommendations")
        if row["recommend"] and action is None:
            raise ValueError("invalid_recommendations")
        for field in ("reason", "impact"):
            text = row[field]
            if (not isinstance(text, str) or not text.strip() or len(text) > 280
                    or any(ord(c) < 32 for c in text)
                    or re.search(r"已(?:经)?(?:成功)?(?:删除|卸载|停止|禁用|清理|移动)", text)):
                raise ValueError("invalid_recommendations")
        accepted.append(copy.deepcopy(row))
    return accepted


def build_recommendations(items, assistant=None, *, use_model=False):
    """Add advice without changing source fields or making any real changes."""
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise ValueError("库存必须是项目列表。")
    patterns = []
    if assistant is not None and callable(getattr(assistant, "public_settings", None)):
        try:
            patterns = assistant.public_settings().get("protect_patterns", [])
        except Exception:
            pass
    result = copy.deepcopy(items)
    local_provenance = dict(source='local', provider=None, model=None, protocol=None,
                            generated_at=datetime.now(timezone.utc).isoformat())
    for item in result:
        item["advice"] = _base_advice(item, patterns)
        item["advice"]["provenance"] = copy.deepcopy(local_provenance)
    model_mode, analyzed_count, candidate_count, incomplete = "not_requested", 0, 0, False
    warnings = []
    if use_model:
        model_mode = "local"
        sample, candidate_count = _representative_items(result, protect_patterns=patterns)
        incomplete = candidate_count > len(sample)
        if assistant is None:
            warnings.append("尚未连接模型，本次使用本地规则，没有完成 AI 分析。")
        else:
            try:
                response = assistant.recommend(result)
                if not isinstance(response, dict) or response.get("mode") not in ("llm", "local"):
                    raise ValueError("invalid_recommendations")
                if response["mode"] == "llm":
                    suggestions = _validated_suggestions(response.get("recommendations"), sample)
                    provenance = _provenance(response.get('provenance'))
                    if provenance['source'] == 'local':
                        provenance = _provenance()  # contradictory model metadata is not a local rule
                    model_mode = "llm"
                    analyzed_count = min(len(sample), max(0, int(response.get("analyzed_count", len(sample)))))
                    by_id = {i.get("id"): i for i in result}
                    for row in suggestions:
                        item = by_id[row["id"]]
                        base = item["advice"]
                        if base["group"] == "keep":
                            continue
                        impact = row["impact"] + " " + _IMPACTS.get(item.get("kind"), "本版本只做模拟。")
                        if base["group"] == "personal":
                            item["advice"] = _advice("personal", row["reason"], impact,
                                                     row["suggested_action"], origin="model")
                        elif base["group"] == "recommended":
                            group = "recommended" if row["recommend"] else "review"
                            item["advice"] = _advice(group, row["reason"], impact,
                                                     row["suggested_action"], origin="model", auto=row["recommend"])
                        elif row["recommend"] and _evidence_flags(item):
                            item["advice"] = _advice("recommended", row["reason"], impact,
                                                     row["suggested_action"], origin="model", auto=True)
                        else:
                            reason = row["reason"]
                            if row["recommend"]:
                                reason += " 目前缺少足够的本机依据，暂不默认选择。"
                            item["advice"] = _advice("review", reason, impact,
                                                     row["suggested_action"], origin="model")
                        item["advice"]["provenance"] = copy.deepcopy(provenance)
                else:
                    warnings.append("模型未配置、请求失败或建议未通过校验，保留本地规则结果。")
            except Exception:
                # Never expose provider exception text, credentials or model body.
                warnings.append("模型建议不可用，保留本地规则结果，没有扩大处理范围。")
                model_mode = "local"
                analyzed_count = 0
        incomplete = candidate_count > analyzed_count
    _coalesce_auto_selection(result)
    for item in result:
        item["advice"]["title"] = _purpose_title(item)
        item["advice"]["purpose"] = _purpose_description(item)
    groups = [{"id": key, "title": title, "count": sum(i["advice"]["group"] == key for i in result),
               "ids": [i.get("id") for i in result if i["advice"]["group"] == key]}
              for key, title in _GROUPS]
    summary = {"total_count": len(result), **{g["id"] + "_count": g["count"] for g in groups},
               "auto_selected_count": sum(i["advice"]["auto_select"] for i in result),
               "model_mode": model_mode, "model_analyzed_count": analyzed_count,
               "model_candidate_count": candidate_count, "coverage_incomplete": incomplete,
               "warnings": warnings}
    if incomplete:
        if model_mode == "llm":
            warnings.append(f"模型为 {analyzed_count}/{candidate_count} 个候选给出了通过校验的建议，其余保留本地判断。")
        else:
            warnings.append("模型尚未完成候选项目的分析，本次保留本地判断。")
    return {"items": result, "groups": groups, "summary": summary}
