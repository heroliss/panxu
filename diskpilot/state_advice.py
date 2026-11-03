"""Explicit state advice, separate from cleanup inclination and action authority.

Service runtime and service startup policy are independent facts. In particular,
Stopped never means Disabled. This module only interprets collected metadata;
it does not inspect the machine, enable anything, or add executor capabilities.
"""
from __future__ import annotations


RECOMMENDATIONS = ('keep_disabled', 'enable', 'disable', 'keep_enabled', 'keep_current', 'investigate')
SCOPES = ('runtime', 'login', 'service_startup')
BLOCKED_REASON = 'state_recommendation_no_cleanup_action'
_SCOPES = {'startup': ('login',), 'service': ('runtime', 'service_startup'), 'process': ('runtime',)}
_ACTIONS = {'startup': 'disable_startup', 'service': 'stop_service', 'process': 'stop_process'}
_LABELS = {
    ('startup', 'login'): {
        'keep_disabled': '保持关闭开机启动', 'enable': '建议开启开机启动', 'disable': '建议关闭开机启动',
        'keep_enabled': '保持开机启动', 'keep_current': '保持现状', 'investigate': '需进一步核实'},
    ('service', 'runtime'): {
        'keep_disabled': '保持停止', 'enable': '建议启动服务', 'disable': '建议停止服务',
        'keep_enabled': '保持运行', 'keep_current': '保持现状', 'investigate': '需进一步核实'},
    ('service', 'service_startup'): {
        'keep_disabled': '保持禁用服务启动', 'enable': '建议启用服务启动', 'disable': '建议禁用服务启动',
        'keep_enabled': '保持启用服务启动', 'keep_current': '保持现状', 'investigate': '需进一步核实'},
    ('process', 'runtime'): {
        'keep_disabled': '保持未运行', 'enable': '建议运行程序', 'disable': '建议结束进程',
        'keep_enabled': '保持运行', 'keep_current': '保持现状', 'investigate': '需进一步核实'},
}
_SCOPE_LABELS = {'runtime': '当前运行状态', 'login': '登录时启动', 'service_startup': '服务启动方式'}


def normalize(item, advice):
    """Validate optional model fields without inventing intent for old advice.

    Startup/process scopes are unambiguous defaults. Services must name their
    scope: a request to stop now cannot silently become a startup-policy change.
    """
    recommendation, scope = advice.get('state_recommendation'), advice.get('state_scope')
    if recommendation is None and scope is None:
        return dict(state_recommendation=None, state_scope=None)
    if recommendation not in RECOMMENDATIONS or item.get('kind') not in _SCOPES:
        raise ValueError('invalid_state_recommendation')
    if scope is None:
        scope = {'startup': 'login', 'process': 'runtime'}.get(item.get('kind'))
    if scope not in _SCOPES[item['kind']]:
        raise ValueError('invalid_state_scope')
    return dict(state_recommendation=recommendation, state_scope=scope)


def _text(value):
    return value.strip().casefold() if isinstance(value, str) else ''


def current_state(item, scope):
    """Report finite metadata evidence; registered startup commands stay unknown."""
    details = item.get('details') if isinstance(item.get('details'), dict) else {}
    kind = item.get('kind')
    state = 'unknown'
    if scope == 'service_startup' and kind == 'service':
        state = {'auto': 'automatic', 'automatic': 'automatic', '自动': 'automatic',
                 'manual': 'manual', 'demand': 'manual', '手动': 'manual',
                 'disabled': 'disabled', '禁用': 'disabled', '已禁用': 'disabled'}.get(_text(details.get('start_mode')), 'unknown')
        labels = {'automatic': '自动启动', 'manual': '手动启动', 'disabled': '已禁用', 'unknown': '启动方式未确认'}
    elif scope == 'login' and kind == 'startup':
        # A Run key or Startup folder entry alone does not reveal StartupApproved.
        for key in ('startup_enabled', 'enabled'):
            if type(details.get(key)) is bool:
                state = 'enabled' if details[key] else 'disabled'
                break
        if state == 'unknown':
            state = {'enabled': 'enabled', 'disabled': 'disabled', '已启用': 'enabled', '已禁用': 'disabled',
                     '已开启': 'enabled', '已关闭': 'disabled'}.get(_text(details.get('state')), 'unknown')
        labels = {'enabled': '已开启', 'disabled': '已关闭', 'unknown': '开机启动状态未确认'}
    elif scope == 'runtime' and kind in ('service', 'process'):
        state = {'running': 'running', '正在运行': 'running', 'stopped': 'stopped', '已停止': 'stopped',
                 'paused': 'paused', '已暂停': 'paused', 'start pending': 'pending', 'stop pending': 'pending',
                 '正在启动': 'pending', '正在停止': 'pending'}.get(_text(details.get('state')), 'unknown')
        labels = {'running': '正在运行', 'stopped': '已停止', 'paused': '已暂停', 'pending': '正在切换状态', 'unknown': '运行状态未确认'}
    else:
        labels = {'unknown': '状态未确认'}
    return dict(state=state, label=labels[state], known=state != 'unknown')


def _supported_action(item, action):
    if item.get('risk') == 'protected' or item.get('simulated') is True:
        return False
    if not action or action != _ACTIONS.get(item.get('kind')) or action not in (item.get('actions') or []):
        return False
    if item.get('kind') == 'service':
        return current_state(item, 'runtime')['state'] == 'running'
    if item.get('kind') == 'process':
        current = current_state(item, 'runtime')['state']
        if current == 'running':
            return True
        details = item.get('details') if isinstance(item.get('details'), dict) else {}
        # Retained process observations can predate the display-state field.
        # Their positive PID+creation-time identity still supports preparing an
        # exact action; the runtime validator must verify that identity afresh.
        return (current == 'unknown' and type(details.get('pid')) is int and details['pid'] > 0
                and type(details.get('create_time')) in (int, float) and details['create_time'] > 0)
    return current_state(item, 'login')['state'] != 'disabled'


def operation_allowed(item, advice, action=None, *, explicit_user_override=False):
    """Prevent state advice from being reinterpreted as the kind's cleanup action.

    A user's separate concrete action may override the recommendation, but its
    actual supported action/state must still be checked. Merely ticking a row is
    not an instruction to translate an enable/no-op recommendation into stop.
    """
    if explicit_user_override:
        return _supported_action(item, action)
    try:
        value = normalize(item, advice or {})
    except (ValueError, TypeError, AttributeError):
        return False
    if value['state_recommendation'] is None:
        return True  # Legacy action checks are deliberately unchanged.
    if value['state_recommendation'] != 'disable' or value['state_scope'] == 'service_startup':
        return False
    return _supported_action(item, action or _ACTIONS.get(item.get('kind')))


def present(item, advice, *, stale=False):
    """A compact UI projection, with current facts and advisory intent apart."""
    try:
        value = normalize(item, advice or {})
    except (ValueError, TypeError, AttributeError):
        return None
    recommendation, scope = value['state_recommendation'], value['state_scope']
    if recommendation is None:
        return None
    current = current_state(item, scope)
    enabled = current['state'] in ('enabled', 'running', 'automatic', 'manual')
    disabled = current['state'] in ('disabled', 'stopped')
    conflict = bool(recommendation == 'keep_disabled' and enabled or recommendation == 'keep_enabled' and disabled)
    actionable = not stale and not conflict and operation_allowed(item, value)
    label = _LABELS[(item['kind'], scope)][recommendation]
    if recommendation == 'keep_enabled' and scope == 'service_startup':
        label = {'automatic': '保持自动启动', 'manual': '保持手动启动'}.get(current['state'], label)
    if not current['known'] and recommendation in ('keep_disabled', 'keep_enabled'):
        label = '建议' + label + '（状态待核实）'
    if stale:
        reason = '建议对应的检查已变化，请刷新后核对。'
    elif conflict:
        reason = '建议与当前状态不一致，请重新核对。'
    elif not current['known'] and recommendation in ('keep_disabled', 'keep_enabled'):
        reason = '当前状态尚未确认，这只是维持状态的建议，不能据此认定已经开启或关闭。'
    elif recommendation in ('keep_disabled', 'keep_enabled', 'keep_current'):
        reason = '维持所述状态，无需清理操作。'
    elif recommendation == 'investigate':
        reason = '先补充用途或状态依据，再决定是否调整。'
    elif scope == 'service_startup':
        reason = '这是服务启动方式建议，当前没有对应的执行入口；停止当前服务不会改变启动方式。'
    elif recommendation == 'enable':
        reason = ('当前已是开启状态，无需再次开启。' if enabled else
                  '这是开启建议，当前没有对应的执行入口；不会转换成关闭或停止操作。')
    elif not actionable:
        reason = '当前状态不需要或不支持这项关闭操作，请先核对。'
    else:
        reason = None
    return dict(recommendation=recommendation, scope=scope, scope_label=_SCOPE_LABELS[scope], label=label,
                current_state=current['state'], current_label=current['label'], current_state_known=current['known'],
                actionable=actionable, state_conflict=conflict, stale=bool(stale), reason=reason)
