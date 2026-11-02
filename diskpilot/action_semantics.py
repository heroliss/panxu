"""Shared object/action meaning for lists, frozen plans and their validation."""
from collections import Counter

from .simulation import within


FILE_KINDS = frozenset(('file', 'directory'))
APP_KINDS = frozenset(('startup', 'service', 'process', 'software'))
KINDS = FILE_KINDS | APP_KINDS
DEFAULT_ACTIONS = dict(file='delete', directory='delete', startup='disable_startup',
                       service='stop_service', process='stop_process', software='uninstall')
ACTION_DETAILS = {
    'delete': ('删除文件或文件夹', '删除后原路径中的内容将不可继续使用，请保留仍需要的资料。'),
    'disable_startup': ('禁用开机启动', '相应功能不会随登录自动就绪；不会卸载程序或结束已运行进程。'),
    'stop_service': ('停止服务', '只停止当前运行的服务，可能影响依赖功能；不会禁用开机启动或修改启动方式。'),
    'stop_process': ('结束进程', '会结束当前这一进程，未保存内容可能丢失；不会禁用开机启动。'),
    'uninstall': ('卸载软件', '使用软件卸载语义，可能影响组件和配置；不同于直接删除安装目录。'),
}


def primary_action(item):
    """An explicit kind mapping, never an inference from score or actions[0]."""
    action = DEFAULT_ACTIONS.get(item.get('kind'))
    if action not in (item.get('actions') or []):
        return None
    if action == 'stop_service' and str(item.get('details', {}).get('state', '')).casefold() not in ('running', '正在运行'):
        return None
    return action


def action_details(action):
    label, effect = ACTION_DETAILS.get(action, ('暂无可执行操作', '请先核对对象当前状态与可用动作。'))
    return dict(action=action, action_label=label, action_effect=effect)


def unavailable_reason(item):
    if item.get('risk') == 'protected':
        return '此对象受保护，不能加入操作清单。'
    if item.get('kind') == 'service' and not primary_action(item):
        if str(item.get('details', {}).get('state', '')).casefold() == 'stopped':
            return '当前服务已停止，无需再次停止。'
        return '尚未确认服务正在运行，暂不能停止。'
    return '' if primary_action(item) else '当前没有受支持的操作入口。'


def action_groups(entries, returned_ids=None):
    totals = Counter(row.get('action') or primary_action(row) for row in entries)
    returned = totals if returned_ids is None else Counter(row.get('action') or primary_action(row)
        for row in entries if row['id'] in returned_ids)
    return [dict(action=action, label=ACTION_DETAILS[action][0], total=totals[action], returned=returned[action])
            for action in ACTION_DETAILS if totals[action]]


def in_task_scope(task, item, *, known=None):
    """Observed background context does not expand an analysis/action scope."""
    known = known if known is not None else set(task.get('known_ids', [])) | set(task.get('target_ids', []))
    if item.get('id') not in known:
        return False
    analysis = task.get('analysis')
    if analysis:
        return item['id'] in task.get('target_ids', []) or item.get('kind') in FILE_KINDS and any(
            within(item['path'], path) for path in task.get('action_directories', []))
    if task.get('scope_kind', 'filesystem') == 'filesystem':
        return item.get('kind') in FILE_KINDS and any(within(item['path'], root['path']) for root in task.get('roots', []))
    if task.get('scope_kind') == 'applications':
        kinds = APP_KINDS - {'software'} if task.get('preset') == 'background' else APP_KINDS
        return item.get('kind') in kinds
    return False
