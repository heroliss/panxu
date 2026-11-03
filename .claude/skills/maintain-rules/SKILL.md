---
name: maintain-rules
description: Add or change a 盘序 cleanup rule, knowledge entry or detection pattern, or fix a false positive in the check-up / 我的文件. Use when the user reports a folder that should (not) be cleaned, a wrong suggestion, a missed cache, or output of tools/real_check.py.
---

# 维护盘序的规则

按 `docs/RULES.md` 的六步做，不要跳步。先读 `AGENTS.md` 和 `docs/RULES.md`。

1. **确认事实**：用户给的路径里是什么、删了会怎样。不确定就只在 `panxu/knowledge.py` 加说明，不要做成清理规则。
2. **改对地方**：清理规则 `panxu/junk.py`；说明 `panxu/knowledge.py`；我的文件 `panxu/insights.py`；自动勾选 `panxu/checkup.py`；程序匹配 `panxu/apps.py`。不能撤销、或可能含用户自己东西的规则加进 `checkup.BOLD_EXCLUDED_RULES`。
3. **演练样例**：在 `panxu/sandbox.py` 的 `_layout()` 加要清理的和必须保留的假文件。
4. **测试**：修误报先写能重现的测试（只用临时目录夹具）。然后运行
   `python -W error::ResourceWarning -m unittest discover -s tests -q`、`node tests/test_ui.cjs`、`python tools/sandbox_e2e.py`。
5. **真实电脑只读检查**（只在 Windows 上，且用户同意时）：`python tools/real_check.py --compare real-check-base.json`；报告里不能出现「严重」，变化要能解释。报告含用户路径，不要提交。
6. **功能分支提交并开 PR**：写清改了什么、依据、第 4、5 步的结果。不要直接合并。

原则：宁可漏掉，不要删错。模型的判断不能扩大处理范围；个人文件只由用户决定。
