"""Small read-only progress projection, independent of public advice paging."""
from __future__ import annotations


def summarize_analysis(task, current, budget_defaults, *, coverage=None, stale_ids=()):
    """Count current suggestions without treating an open inventory as a target.

    Stored, completed suggestions are the progress unit. Model/tool budgets are
    separate resource counters, never an item-count limit or a percent estimate.
    """
    stale_ids = set(stale_ids)
    saved = []
    stale_count = 0
    for ident, advice in (task.get('advice') or {}).items():
        item = current.get(ident)
        frozen_tool = (task.get('scope_kind') == 'cleanup_tools' and item
                       and item.get('kind') == 'cleanup_tool')
        # A tool task describes a fixed diagnostic snapshot. Cache expiry or a
        # restart removes current execution eligibility, not the completed
        # analysis of that snapshot. File/application identity changes still
        # invalidate their progress through the normal signature check.
        if item and (ident not in stale_ids or frozen_tool) and advice.get('signature') == item.get('signature'):
            saved.append(advice)
            stale_count += bool(frozen_tool and ident in stale_ids)
    rating_counts = {str(value): 0 for value in range(1, 6)}
    skipped = no_advice = 0
    for advice in saved:
        conclusion = advice.get('conclusion')
        if conclusion == 'skip':
            skipped += 1
        elif conclusion == 'no_advice':
            no_advice += 1
        else:
            score = advice.get('cleanup_rating')
            if type(score) is int and 1 <= score <= 5:
                rating_counts[str(score)] += 1
    targets = task.get('target_ids')
    known_target = bool(task.get('analysis')) and isinstance(targets, list)
    total = len(targets) if known_target else None
    remaining = (coverage or {}).get('remaining_count') if known_target else None
    if total is not None and (type(remaining) is not int or remaining < 0):
        target_set = set(targets)
        remaining = max(0, total - sum(row.get('id') in target_set for row in saved))
    turn = task.get('turn_budget') or {}
    limits = turn.get('limits') or budget_defaults
    usage = turn.get('used') or {}
    resource_budget = {}
    for resource in ('model_requests', 'tool_calls'):
        used = usage.get(resource)
        resource_budget[resource] = dict(used=used if type(used) is int and used >= 0 else None,
                                         limit=limits.get(resource, budget_defaults[resource]))
    notice = task.get('investigation_notice') or {}
    if notice.get('code'):
        resource_budget['reason'] = notice['code']
    status = task.get('status')
    if status == 'investigating':
        phase = task.get('analysis_stage', 'analyzing')
        if phase not in ('collecting', 'analyzing', 'finalizing'):
            phase = 'analyzing'
    else:
        phase = {'budget_exhausted': 'limited', 'needs_input': 'waiting', 'needs_model': 'waiting',
                 'awaiting_review': 'completed', 'completed': 'completed', 'paused': 'paused',
                 'error': 'error', 'executing': 'executing'}.get(status, 'ready')
    return dict(target_count=total, saved_count=len(saved), rated_count=sum(rating_counts.values()),
                skipped_count=skipped, no_advice_count=no_advice, rating_counts=rating_counts,
                stale_count=stale_count,
                remaining_count=remaining, phase=phase, budget=resource_budget)
