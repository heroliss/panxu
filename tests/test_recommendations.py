import copy
import unittest

from diskpilot.recommendations import build_recommendations


def item(key="a", kind="file", **changes):
    value = {"id": key, "kind": kind, "name": "普通文件.bin", "path": r"E:\UserFiles\file.bin",
             "risk": "review", "category": "other", "actions": [{"file": "delete", "directory": "delete",
             "startup": "disable_startup", "service": "stop_service", "process": "stop_process",
             "software": "uninstall"}[kind]], "size_bytes": 100, "details": {}, "simulated": False}
    value.update(changes)
    return value


def cache(key="a", kind="file", **changes):
    d = {"verified_cache": True, "complete": True, "subtree_complete": True,
         "decision_context": {"ownership": "application", "personal_choice_required": False,
                              "regenerable": True, "auto_select": True,
                              "classification_source": "managed_cache_subtree" if kind == "directory" else "managed_cache_path"}}
    value = item(key, kind, category="cache", risk="low", path=r"C:\Users\example\AppData\Local\pip\Cache", details=d)
    value.update(changes)
    return value


def suggestion(key, action="delete", recommend=True):
    return {"id": key, "recommend": recommend, "suggested_action": action,
            "reason": "建议处理这项已经核实的可选功能。", "impact": "下次需要时可重新打开。"}


class StubAssistant:
    def __init__(self, rows=None, error=None, patterns=None):
        self.rows = rows or []
        self.error = error
        self.patterns = patterns or []
        self.calls = 0

    def public_settings(self):
        return {"protect_patterns": self.patterns}

    def recommend(self, items):
        self.calls += 1
        if self.error:
            raise self.error
        return {"mode": "llm", "recommendations": self.rows, "analyzed_count": len(self.rows)}


class RecommendationTests(unittest.TestCase):
    def test_original_fields_and_input_preserved(self):
        source = [cache(), item("b")]
        before = copy.deepcopy(source)
        result = build_recommendations(source)
        self.assertEqual(source, before)
        for original, enriched in zip(source, result["items"]):
            self.assertEqual({k: v for k, v in enriched.items() if k != "advice"}, original)
        self.assertEqual(result["summary"]["recommended_count"], 1)
        self.assertEqual(result["summary"]["personal_count"], 1)
        self.assertEqual(result["summary"]["model_mode"], "not_requested")

    def test_hard_protection_no_actions_and_processed_always_keep(self):
        values = [cache("p", risk="protected"), cache("n", actions=[]), cache("s", simulated=True),
                  cache("sys", category="system")]
        assistant = StubAssistant([suggestion("p")])
        result = build_recommendations(values, assistant, use_model=True)
        self.assertTrue(all(i["advice"]["group"] == "keep" for i in result["items"]))
        self.assertTrue(all(not i["advice"]["auto_select"] for i in result["items"]))

    def test_personal_download_created_unknown_never_auto_select(self):
        values = [item("unknown", name="cache.tmp"),
                  cache("download", path=r"C:\Users\example\Downloads\cache.zip"),
                  cache("created", details={**cache()["details"], "user_created": True})]
        assistant = StubAssistant([suggestion(i["id"]) for i in values])
        result = build_recommendations(values, assistant, use_model=True)
        for value in result["items"]:
            self.assertEqual(value["advice"]["group"], "personal")
            self.assertTrue(value["advice"]["needs_user_choice"])
            self.assertFalse(value["advice"]["auto_select"])
            self.assertEqual(value["advice"]["suggested_action"], "delete")

    def test_legacy_or_spoofed_cache_is_not_verified(self):
        values = [cache("legacy", details={"verified_cache": True}),
                  item("spoof", path=r"D:\Downloads\AppData\Local\pip\Cache", category="cache", risk="low"),
                  cache("wrong", details={**cache()["details"], "decision_context": {
                      "ownership": "unknown", "personal_choice_required": True, "regenerable": False,
                      "auto_select": False, "classification_source": "unverified_cache_location"}})]
        result = build_recommendations(values)
        self.assertEqual(result["summary"]["auto_selected_count"], 0)
        self.assertTrue(all(i["advice"]["needs_user_choice"] for i in result["items"]))

    def test_active_or_incomplete_verified_cache_not_auto_selected(self):
        values = [cache("active", details={**cache()["details"], "in_use": True, "verified_unnecessary": True}),
                  cache("partial", "directory", details={**cache(kind="directory")["details"], "subtree_complete": False})]
        result = build_recommendations(values, StubAssistant([suggestion("active"), suggestion("partial")]), use_model=True)
        self.assertTrue(all(i["advice"]["group"] == "review" for i in result["items"]))
        self.assertEqual(result["summary"]["auto_selected_count"], 0)

    def test_background_requires_model_and_positive_local_evidence(self):
        positive = item("startup", "startup", details={"optional": True, "purpose_verified": True})
        high_memory = item("process", "process", details={"memory_bytes": 999999999, "age_days": 1000})
        assistant = StubAssistant([suggestion("startup", "disable_startup"), suggestion("process", "stop_process")])
        local = build_recommendations([positive, high_memory], assistant)
        self.assertEqual(assistant.calls, 0)
        self.assertEqual(local["summary"]["recommended_count"], 0)
        result = build_recommendations([positive, high_memory], assistant, use_model=True)
        self.assertTrue(result["items"][0]["advice"]["auto_select"])
        self.assertFalse(result["items"][1]["advice"]["auto_select"])
        self.assertEqual(result["items"][1]["advice"]["group"], "review")
        self.assertIn("未保存", result["items"][1]["advice"]["impact"])

    def test_software_requires_confirmed_use_evidence(self):
        values = [item("known", "software", details={"unused_confirmed": True}),
                  item("old", "software", details={"version": "old", "last_used_days": 1000})]
        result = build_recommendations(values, StubAssistant([suggestion("known", "uninstall"), suggestion("old", "uninstall")]), use_model=True)
        self.assertEqual([i["advice"]["group"] for i in result["items"]], ["recommended", "review"])

    def test_unknown_id_or_action_rejects_entire_model_batch(self):
        for rows in [[suggestion("a"), suggestion("outside")], [suggestion("a", "stop_service")],
                     [{**suggestion("a"), "needs_user_choice": False}]]:
            result = build_recommendations([item()], StubAssistant(rows), use_model=True)
            self.assertEqual(result["items"][0]["advice"]["origin"], "rule")
            self.assertEqual(result["summary"]["model_mode"], "local")

    def test_model_failure_retains_rule_results_without_exception_text(self):
        result = build_recommendations([cache(), item("p")], StubAssistant(error=RuntimeError("Bearer private-secret")), use_model=True)
        self.assertEqual(result["summary"]["recommended_count"], 1)
        self.assertEqual(result["summary"]["personal_count"], 1)
        self.assertEqual(result["summary"]["model_mode"], "local")
        self.assertNotIn("private-secret", str(result))

    def test_model_caution_can_disable_cache_default_selection(self):
        result = build_recommendations([cache()], StubAssistant([suggestion("a", recommend=False)]), use_model=True)
        self.assertEqual(result["items"][0]["advice"]["group"], "review")
        self.assertFalse(result["items"][0]["advice"]["auto_select"])

    def test_protection_patterns_win_over_verified_cache_and_model(self):
        assistant = StubAssistant(patterns=[r"c:\users\*\appdata\local\pip\*"])
        result = build_recommendations([cache()], assistant, use_model=True)
        self.assertEqual(result["items"][0]["advice"]["group"], "keep")

    def test_complete_cache_root_covers_children_without_personal_choice(self):
        root = cache("root", "directory")
        child = cache("child", details={**cache()["details"], "parent_id": "root"})
        result = build_recommendations([child, root])
        self.assertEqual(result["summary"]["recommended_count"], 2)
        self.assertEqual(result["summary"]["auto_selected_count"], 1)
        self.assertFalse(result["items"][0]["advice"]["needs_user_choice"])
        self.assertIn("上级完整缓存", result["items"][0]["advice"]["reason"])
        self.assertEqual(result["items"][0]["advice"]["group"], "recommended")

    def test_titles_explain_purpose_and_unknown_uses_name(self):
        result = build_recommendations([cache(), item("x", name="我的归档.zip"), item("s", "startup", name="聊天工具")])
        self.assertEqual(result["items"][0]["advice"]["title"], "Python 下载和构建缓存")
        self.assertEqual(result["items"][1]["advice"]["title"], "我的归档.zip")
        self.assertIn("开机启动", result["items"][2]["advice"]["title"])

    def test_partial_model_coverage_is_explicit(self):
        values = [item(str(i)) for i in range(150)]
        result = build_recommendations(values, StubAssistant([suggestion("0")]), use_model=True)
        self.assertEqual(result["summary"]["model_analyzed_count"], 1)
        self.assertEqual(result["summary"]["model_candidate_count"], 150)
        self.assertTrue(result["summary"]["coverage_incomplete"])
        self.assertIn("1/150", " ".join(result["summary"]["warnings"]))


    def test_model_impact_score_is_retained_without_changing_cleanup_choice(self):
        for score in (1, 2, 3, 4, 5, None):
            result = build_recommendations([cache()], StubAssistant([
                dict(suggestion('a'), cleanup_rating=5, impact_rating=score)]), use_model=True)
            advice = result['items'][0]['advice']
            self.assertEqual(advice['impact_rating'], score)
            self.assertEqual(advice['cleanup_rating'], 5)
            self.assertTrue(advice['auto_select'])
        legacy = build_recommendations([cache()], StubAssistant([suggestion('a')]), use_model=True)
        self.assertIsNone(legacy['items'][0]['advice']['impact_rating'])

    def test_invalid_impact_score_rejects_model_suggestions_without_losing_local_rules(self):
        for invalid in (True, 0, 6, 2.5, '2'):
            result = build_recommendations([cache()], StubAssistant([
                dict(suggestion('a'), impact_rating=invalid)]), use_model=True)
            advice = result['items'][0]['advice']
            self.assertEqual(advice['origin'], 'rule')
            self.assertIsNone(advice['impact_rating'])
            self.assertTrue(result['summary']['warnings'])


if __name__ == "__main__":
    unittest.main()
