"""Durable analysis allowances and semantic progress; never action authority."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
from datetime import datetime, timezone


DEFAULTS = dict(model_requests=96, tool_calls=256, elapsed_minutes=30,
                total_tokens=1_000_000, no_progress_requests=6, consecutive_failures=3)
LIMITS = dict(model_requests=(6, 1024), tool_calls=(12, 4096), elapsed_minutes=(1, 240),
              total_tokens=(1000, 20_000_000), no_progress_requests=(3, 30), consecutive_failures=(2, 12))
_EPHEMERAL = frozenset(('at', 'created_at', 'updated_at', 'generated_at', 'observed_at', 'completed_at',
    'started_at', 'duration_ms', 'elapsed_ms', 'fact_id', 'advice_id', 'inspection_id', 'request_id',
    'recorded_count', 'total_advice_count', 'job_id', 'snapshot_id', 'scan_id', 'cursor', 'next_cursor',
    'evidence_ids', 'fact_ids', 'requested_fact_ids'))
_BOOKKEEPING = frozenset(('checkpoint_task', 'present_findings', 'ask_user'))


def valid(values):
    return (isinstance(values, dict) and not set(values) - set(DEFAULTS)
            and all(type(value) is int and LIMITS[name][0] <= value <= LIMITS[name][1]
                    for name, value in values.items()))


def begin(task, limits):
    """Only an explicit user start can renew a finished/exhausted allowance.

    Automatic checkpoints never call this function. Pause, process restart and
    connection recovery retain the same consumed resource allowance.
    """
    prior = task.get('analysis_session')
    if not isinstance(prior, dict) or prior.get('status') in ('completed', 'awaiting_review', 'budget_exhausted'):
        if isinstance(prior, dict):
            task['analysis_session_history'] = (task.get('analysis_session_history', []) + [public(prior)])[-5:]
        prior = dict(id=secrets.token_hex(12), created_at=datetime.now(timezone.utc).isoformat(),
            limits=copy.deepcopy(limits), used=dict(model_requests=0, tool_calls=0, input_tokens=None,
                output_tokens=None, elapsed_ms=0, reported_usage_calls=0, complete_token_usage_calls=0),
            checkpoints=0, meaningful_results=0, no_progress_requests=0, consecutive_failures=0,
            fingerprints=[], status='investigating', stop_reason=None)
        task['analysis_session'] = prior
    prior.update(status='investigating', stop_reason=None)
    # A user has explicitly resumed after providing feedback or fixing a
    # connection; retry-loop counters restart, resource consumption does not.
    prior['no_progress_requests'] = 0
    prior['consecutive_failures'] = 0
    return prior


def public(session):
    if not isinstance(session, dict):
        return None
    result = {name: copy.deepcopy(session.get(name)) for name in
        ('id', 'created_at', 'status', 'limits', 'used', 'checkpoints', 'meaningful_results',
         'no_progress_requests', 'consecutive_failures', 'stop_reason')}
    used = result.get('used') or {}
    result['token_usage_complete'] = (used.get('model_requests', 0) > 0
        and used.get('complete_token_usage_calls', 0) == used.get('model_requests'))
    result['cost_estimate'] = None  # No provider pricing is guessed.
    return result


def stop_reason(session):
    used, limits = session['used'], session['limits']
    if used['elapsed_ms'] >= limits['elapsed_minutes'] * 60_000:
        return 'session_time'
    if used['model_requests'] >= limits['model_requests']:
        return 'session_requests'
    if used['tool_calls'] >= limits['tool_calls']:
        return 'session_tools'
    # Partial provider reports are a lower bound, not fabricated complete usage.
    if sum(used.get(name) or 0 for name in ('input_tokens', 'output_tokens')) >= limits['total_tokens']:
        return 'session_tokens'
    if session['consecutive_failures'] >= limits['consecutive_failures']:
        return 'repeated_failures'
    if session['no_progress_requests'] >= limits['no_progress_requests']:
        return 'no_progress'
    return None


def account_tokens(session, raw):
    if not isinstance(raw, dict):
        return
    reported = 0
    for name, aliases in (('input_tokens', ('input_tokens', 'prompt_tokens', 'promptTokenCount')),
                          ('output_tokens', ('output_tokens', 'completion_tokens', 'candidatesTokenCount'))):
        value = next((raw[k] for k in aliases if type(raw.get(k)) is int and raw[k] >= 0), None)
        if value is not None:
            session['used'][name] = (session['used'][name] or 0) + value
            reported += 1
    session['used']['reported_usage_calls'] += bool(reported)
    session['used']['complete_token_usage_calls'] += reported == 2


def _semantic(value):
    if isinstance(value, dict):
        return {key: _semantic(item) for key, item in value.items() if key not in _EPHEMERAL
                and not (key == 'id' and isinstance(item, str) and item.startswith(('fact-', 'work-')))}
    if isinstance(value, list):
        items = [_semantic(item) for item in value]
        if items and all(isinstance(item, dict) and ('id' in item or 'path' in item) for item in items):
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))
        return items
    return value


def observe(session, name, arguments, result):
    """A new fact ID or timestamp cannot turn the same read into progress."""
    if 'error' in result:
        session['consecutive_failures'] += 1
        return False
    session['consecutive_failures'] = 0
    if name in _BOOKKEEPING:
        return False
    content = dict(tool=name, result=_semantic(result))
    if name == 'read_facts':
        # Repeated collection can add a new durable fact wrapper for identical
        # data; a larger wrapper-index count is not new decision evidence.
        for key in ('total', 'has_more', 'requested_fact_ids'):
            content['result'].pop(key, None)
    if name in ('record_advice', 'record_specialist_suggestions', 'draft_user_tool'):
        content['saved_content'] = _semantic(arguments)
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode()).hexdigest()
    if fingerprint in session['fingerprints']:
        return False
    session['fingerprints'] = (session['fingerprints'] + [fingerprint])[-1024:]
    session['meaningful_results'] += 1
    return True
