"""Persisted per-turn resource budgets, independent of organizing preferences."""
from __future__ import annotations

import json

from .simulation import AppError


DEFAULTS = dict(model_requests=6, tool_calls=12)
LIMITS = dict(model_requests=(4, 32), tool_calls=(6, 128))
_KEY = 'agent_budget_v1'


class AgentBudget:
    def __init__(self, state):
        self.state = state

    @staticmethod
    def _valid(values):
        return (isinstance(values, dict) and not set(values) - set(DEFAULTS)
                and all(type(value) is int and LIMITS[key][0] <= value <= LIMITS[key][1]
                        for key, value in values.items()))

    def get(self):
        with self.state.lock:
            row = self.state.db.execute('SELECT value FROM meta WHERE key=?', (_KEY,)).fetchone()
            try:
                values = json.loads(row[0]) if row else {}
            except (ValueError, TypeError):
                values = {}
            return {**DEFAULTS, **values} if self._valid(values) else dict(DEFAULTS)

    def save(self, payload):
        if not payload or not self._valid(payload):
            raise AppError('调查预算无效：模型响应4至32次，工具调用6至128次。', 'invalid_agent_budget')
        with self.state.lock, self.state.db:
            current = self.get()
            current.update(payload)
            self.state.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                                  (_KEY, json.dumps(current, separators=(',', ':'))))
            return dict(current)


def budget_hint(settings, *, model_requests_used=0, tool_calls_used=0):
    """Resource awareness, not a prescribed investigation path or success claim."""
    if not AgentBudget._valid(settings):
        raise AppError('调查预算无效。', 'invalid_agent_budget')
    if (type(model_requests_used) is not int or model_requests_used < 0 or
            type(tool_calls_used) is not int or tool_calls_used < 0):
        raise AppError('调查预算使用量无效。', 'invalid_agent_budget')
    values = {**DEFAULTS, **settings}
    remaining_requests = max(0, values['model_requests'] - model_requests_used)
    remaining_calls = max(0, values['tool_calls'] - tool_calls_used)
    if remaining_requests == 0 or remaining_calls == 0:
        guidance = '本轮资源已用完；保留已取得事实、结论、下一步与缺口，允许续接，不把未覆盖范围说成完成。'
    elif remaining_requests <= 2 or remaining_calls <= 2:
        guidance = '本轮资源接近用尽；按需要形成已有证据结论并保存下一步与缺口，后续可继续，不必重复采集。'
    else:
        guidance = '依据目标和已有证据自行选择检查顺序与深度；长任务使用异步能力，结果按引用读取，资源不足时保存检查点续接。'
    return dict(remaining_model_requests=remaining_requests, remaining_tool_calls=remaining_calls,
                guidance=guidance)


def finalization_due(settings, *, model_requests_used=0, tool_calls_used=0):
    """Prompt timely scoring/conclusions while declared tools remain available.

    Two replies/tool calls remaining is a resource-awareness hint, not a hidden
    action, item or tool restriction. Declared hard limits remain unchanged.
    """
    remaining = budget_hint(settings, model_requests_used=model_requests_used, tool_calls_used=tool_calls_used)
    return remaining['remaining_model_requests'] <= 2 or remaining['remaining_tool_calls'] <= 2
