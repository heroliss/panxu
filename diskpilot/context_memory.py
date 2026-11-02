"""Bounded model working evidence over durable, already-collected facts.

This module never reads the inventory, scans a path or calls a provider. A
working window is a projection of observations, not a new permission boundary
or a claim that the task's full scope has been inspected.
"""
from __future__ import annotations

import copy
from datetime import datetime
import json

from .prompt_text import render_host_text


_FACT_FIELDS = ('tool', 'at', 'source', 'scope', 'freshness', 'requires_recheck_for_action')
_OBJECT_TOOLS = frozenset(('inspect_items', 'list_items', 'read_scan'))


def _size(value):
    return len(render_host_text(value).encode('utf-8'))


def _stamp(value):
    try:
        date = datetime.fromisoformat(value)
        return date.timestamp() if date.tzinfo else float('-inf')
    except (TypeError, ValueError, OverflowError, OSError):
        return float('-inf')


def _metadata(fact):
    return dict(fact_id=fact['id'], **{key: copy.deepcopy(fact[key]) for key in _FACT_FIELDS if key in fact})


def _source_partial(result):
    if not isinstance(result, dict):
        return False
    summary = result.get('summary')
    return bool(result.get('has_more') is True or result.get('complete') is False
                or result.get('partial') is True or result.get('truncated') is True
                or result.get('status') in ('partial', 'incomplete')
                or isinstance(summary, dict) and (summary.get('complete') is False
                    or summary.get('truncated') is True or summary.get('status') in ('partial', 'incomplete')))


def _window(records, total):
    represented = sum('result' in record for record in records)
    count = sum(len(record['result']['items']) for record in records
                if isinstance(record.get('result'), dict) and isinstance(record['result'].get('items'), list))
    return dict(items=records, omitted_count=max(0, total - represented), object_count=count,
                partial=represented < total or any(record.get('partial') is True for record in records),
                read_more='read_facts')


def _reference(fact, reason, *, scoped=True):
    value = _metadata(fact)
    if not scoped and 'scope' in value:
        value.pop('scope')
        value['scope_omitted'] = True
    value.update(result_omitted=True, reason=reason, partial=True)
    return value


def _observed_at(row):
    value = row.get('observed_at')
    if value is None and isinstance(row.get('timestamps'), dict):
        value = row['timestamps'].get('observed_at')
    return value if isinstance(value, str) and value else None


def _covers(complete, candidate):
    """A strict recursive subset; never combine conflicting or unknown values."""
    if type(complete) is not type(candidate):
        return False
    if isinstance(candidate, dict):
        return all(key in complete and _covers(complete[key], value) for key, value in candidate.items())
    if isinstance(candidate, list):
        return len(complete) == len(candidate) and all(_covers(a, b) for a, b in zip(complete, candidate))
    return complete == candidate


def _covered_by(row, retained):
    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not _observed_at(row):
        return None
    for complete, source in retained:
        if (isinstance(complete, dict) and complete.get('id') == row['id']
                and _observed_at(complete) == _observed_at(row)
                and _covers(complete.get('signature'), row.get('signature')) and _covers(complete, row)):
            return source
    return None


def build_working_evidence(facts, *, preferred_ids=(), budget_bytes=24000,
                          max_records=3, max_objects=16):
    """Keep complete rows and provenance within a measured readable-text budget.

    `omitted_count` counts facts without a retained result, including reference-
    only entries. `object_count` counts complete emitted items rows; covered
    duplicates reference an earlier full row, while different observation times,
    signatures or conflicting facts remain separate. Projection totals describe
    that observation's items array, never the whole inventory. Source totals,
    pagination, warnings and unknown/false/zero values remain unchanged.
    """
    if (not isinstance(facts, (list, tuple)) or type(budget_bytes) is not int or budget_bytes <= 0
            or type(max_records) is not int or not 0 <= max_records <= 20
            or type(max_objects) is not int or not 0 <= max_objects <= 100
            or not isinstance(preferred_ids, (list, tuple))
            or any(not isinstance(ident, str) for ident in preferred_ids)):
        raise ValueError('invalid_working_evidence_budget')
    preferred = {ident: index for index, ident in reversed(list(enumerate(preferred_ids)))}
    candidates, seen, invalid = [], set(), 0
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict) or not isinstance(fact.get('id'), str) or not fact['id']:
            invalid += 1
            continue
        if fact['id'] in seen:
            continue
        seen.add(fact['id'])
        candidates.append((index, fact))
    candidates.sort(key=lambda pair: (0 if pair[1]['id'] in preferred else 1,
        preferred.get(pair[1]['id'], 0), 0 if pair[1].get('tool') in _OBJECT_TOOLS else 1,
        -_stamp(pair[1].get('at')), pair[0]))
    total, records, object_count, retained = len(candidates) + invalid, [], 0, []
    if _size(_window([], total)) > budget_bytes:
        raise ValueError('working_evidence_budget_too_small')

    def fits(record):
        return _size(_window([*records, record], total)) <= budget_bytes

    for _, fact in candidates:
        if len(records) >= max_records:
            break
        result = fact.get('result')
        reason = '工作记忆预算不足；请用read_facts按fact_id回读完整内容。'
        if 'result' not in fact:
            reason = '本条只有事实引用；请用read_facts回读正文。'
        elif isinstance(result, dict) and result.get('truncated') is True and 'preview' in result:
            # A serialized preview cannot become new, seemingly complete facts.
            reason = '原观察只保留截断预览；请回读记录并重新查询具体对象。'
        else:
            entry = _metadata(fact)
            entry['partial'] = _source_partial(result)
            if isinstance(result, dict):
                entry['result'] = {key: copy.deepcopy(value) for key, value in result.items()
                                   if key != '_scope' and not (key == 'items' and isinstance(value, list))}
                if '_scope' in result and result['_scope'] != fact.get('scope'):
                    entry['result_scope'] = copy.deepcopy(result['_scope'])
            else:
                entry['result'] = copy.deepcopy(result)
            rows = result.get('items') if isinstance(result, dict) else None
            if isinstance(rows, list):
                available, references = [], []
                for row in rows:
                    covered = _covered_by(row, retained)
                    if covered is None:
                        available.append(row)
                    else:
                        references.append(dict(item_id=row['id'], fact_id=covered, observed_at=_observed_at(row)))
                kept = copy.deepcopy(available[:max(0, max_objects - object_count)])
                if references:
                    entry['row_references'] = references
                while True:
                    entry['result']['items'] = kept
                    omitted = len(rows) - len(kept)
                    entry['projection'] = dict(shown=len(kept), total=len(rows), omitted=omitted,
                                               partial=omitted > 0, scope='this_observation')
                    if references:
                        entry['projection'].update(deduplicated=len(references),
                            unrepresented=len(rows)-len(kept)-len(references))
                    entry['partial'] = _source_partial(result) or omitted > 0
                    if (kept or references or not rows) and fits(entry):
                        records.append(entry)
                        object_count += len(kept)
                        retained.extend((row, fact['id']) for row in kept)
                        break
                    if not kept:
                        entry = None
                        break
                    kept.pop()
                if entry is not None:
                    continue
            elif fits(entry):
                records.append(entry)
                continue
        for scoped in (True, False):
            reference = _reference(fact, reason, scoped=scoped)
            if fits(reference):
                records.append(reference)
                break
    value = _window(records, total)
    assert _size(value) <= budget_bytes
    return value


def model_visible_bytes(messages):
    """UTF-8 message-envelope estimate, excluding internal continuation copies.

    This is neither provider token usage nor exact native wire size. Public
    assistant/user prose stays byte-for-byte unchanged. Recognized host JSON
    and tool JSON use the same readable renderer as the sender; private usage,
    provenance and opaque native continuation are not counted a second time.
    """
    visible = []
    for message in messages:
        record = {key: copy.deepcopy(message[key]) for key in ('role', 'content', 'tool_calls', 'tool_call_id')
                  if key in message}
        content = record.get('content')
        if isinstance(content, str):
            try:
                value = json.loads(content)
            except (ValueError, TypeError):
                pass
            else:
                host = (record.get('role') == 'user' and isinstance(value, dict)
                        and isinstance(value.get('goal'), str)
                        and (str(value.get('_prompt_projection', '')).startswith('task-')
                             or value.get('simulation_only') is True and type(value.get('available_items')) is int))
                if record.get('role') == 'tool' or host:
                    record['content'] = render_host_text(value)
        visible.append(record)
    return len(json.dumps(visible, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
