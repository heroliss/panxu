"""Shared tool discovery and durable read-only work for model-led tasks."""
from __future__ import annotations

import copy
import os

from .dedup import run_duplicate_scan, validate_duplicate_request
from .simulation import AppError, canonical, within


def orchestration_definitions():
    ident = {'type': 'string', 'maxLength': 160}
    refs = {'type': 'array', 'items': ident, 'minItems': 1, 'maxItems': 8}
    page = {'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 40}}
    return [
        ('describe_cleanup_tool', '按需取得专项工具完整参数名、类型、枚举及结果协议；不采集或执行。', {'tool_id': ident}, ['tool_id']),
        ('run_cleanup_diagnostics', '在当前目录范围共享采集轻量诊断；覆盖工具适用性，不逐项深扫48个工具。宿主等待后台工作，不需要反复轮询。', {'scope_ids': refs, 'refresh': {'type': 'boolean'}}, []),
        ('read_cleanup_diagnostics', '按引用分页读取当前任务的诊断事实与缺口。', {'snapshot_id': ident, **page,
            'tool_ids': {'type': 'array', 'items': ident, 'minItems': 1, 'maxItems': 20}}, ['snapshot_id']),
        ('start_duplicate_scan', '用现有本地SHA256引擎查找授权目录内重复文件，保存结果后继续；不上传字节，不自动删除。宿主负责等待和显示进度。',
         {'scope_ids': refs, 'directory_ids': refs, 'max_entries': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
          'max_depth': {'type': 'integer', 'minimum': 1, 'maximum': 32}, 'max_bytes': {'type': 'integer', 'minimum': 1, 'maximum': 8 * 1024 ** 3}, 'refresh': {'type': 'boolean'}}, []),
        ('read_task_work', '读取本任务后台工作的状态；宿主通常已等到完成，无需重复轮询。', {'job_id': ident}, ['job_id']),
        ('read_duplicate_groups', '分页读取已核验重复组；文件成员再按group_id读取。', {'job_id': ident, **page}, ['job_id']),
        ('read_duplicate_members', '分页读取重复组内的已观察对象，可据此提出保留或清理建议。', {'job_id': ident, 'group_id': ident, **page}, ['job_id', 'group_id']),
        ('cancel_task_work', '取消本任务尚未完成的只读工作，保留已取得的部分事实。', {'job_id': ident}, ['job_id']),
        ('discover_user_tools', '搜索用户保存的清理工具及本任务草案，仅取得简短目录。', {'query': {'type': 'string', 'maxLength': 160}}, []),
        ('describe_user_tool', '取得用户工具的条件、参数与版本；实际目录由宿主关联，不能猜测新路径。', {'tool_id': ident, 'version': {'type': 'integer', 'minimum': 1}}, ['tool_id']),
        ('draft_user_tool', '把重复需求保存为待用户审阅的工具草案。文件根目录用scope_ids，规则只表达条件；不保存为正式工具或执行。',
         {'title': {'type': 'string', 'maxLength': 80}, 'description': {'type': 'string', 'maxLength': 600},
          'type': {'type': 'string', 'enum': ['files', 'preset']}, 'scope_ids': refs,
          'rules': {'type': 'object', 'properties': {'recursive': {'type': 'boolean'},
            'includes': {'type': 'array', 'items': {'type': 'string', 'maxLength': 240}, 'minItems': 1, 'maxItems': 20},
            'excludes': {'type': 'array', 'items': {'type': 'string', 'maxLength': 240}, 'maxItems': 20},
            'min_age_days': {'type': 'integer', 'minimum': 0, 'maximum': 36500},
            'min_size_bytes': {'type': 'integer', 'minimum': 0}, 'max_size_bytes': {'type': ['integer', 'null'], 'minimum': 0}}, 'additionalProperties': False},
          'builtin': {'type': 'object', 'properties': {'tool_id': ident, 'parameters': {'type': 'object', 'maxProperties': 12}}, 'required': ['tool_id'], 'additionalProperties': False},
          'recipe_id': ident, 'base_version': {'type': 'integer', 'minimum': 1}}, ['title', 'type']),
        ('inspect_user_tool', '只读试查本任务范围内的规则或预设，返回匹配摘要和运行引用，不批准执行。',
         {'tool_id': ident, 'version': {'type': 'integer', 'minimum': 1}, 'draft_id': ident}, []),
    ]


class Orchestration:
    def _describe_tool_binding(self, tool_id):
        result = self.cleanup_tools.describe(tool_id)
        for field in result.get('parameters', []):
            if field.get('format') != 'path':
                continue
            schema = result['input_schema']['properties'][field['name']]
            result['input_schema']['properties'][field['name']] = dict(anyOf=[schema,
                dict(type='object', properties={'scope_id': {'type': 'string', 'maxLength': 160}},
                     required=['scope_id'], additionalProperties=False)])
        result['path_binding'] = dict(value={'scope_id': '当前任务目录引用'},
            note='format:path参数可使用此对象；宿主解析已授权目录，不需知道或填写真实路径。')
        return result

    def _check_tool_read_scope(self, task, tool_id, parameters):
        fields = self.cleanup_tools.describe(tool_id).get('parameters', [])
        path_fields = {field['name'] for field in fields if field.get('format') == 'path'}
        resolved = copy.deepcopy(parameters)
        if isinstance(resolved, dict):
            for field, value in resolved.items():
                if isinstance(value, dict):
                    if field not in path_fields or set(value) != {'scope_id'}:
                        raise AppError('目录参数只能使用当前任务的scope_id引用。', 'invalid_work_scope')
                    resolved[field] = self._work_paths(task, [value['scope_id']])[0]
        ref = self.cleanup_tools.validate_reference(dict(tool_id=tool_id, parameters=resolved), allow_missing=True)
        source_fields = {'project_path', 'virtual_disk_path'}
        if tool_id in ('cloud_local_copy',):
            source_fields.add('target_path')
        for field in source_fields:
            path = ref['parameters'].get(field)
            if path and not any(within(path, r['path']) for r in task['roots'] if not task.get('analysis') or r.get('inspectable')):
                raise AppError('此工具的读取位置超出当前任务范围，请让用户选择新的目录。', 'outside_scope', 409)
        return ref

    def _work_paths(self, task, scope_ids=None, directory_ids=None):
        if scope_ids is not None and (not isinstance(scope_ids, list) or not 1 <= len(scope_ids) <= 8 or any(not isinstance(i, str) for i in scope_ids)):
            raise AppError('请使用当前任务的1到8个目录引用。', 'invalid_work_scope')
        roots = {r['id']: r for r in task['roots'] if not task.get('analysis') or r.get('inspectable')}
        if scope_ids is not None and any(i not in roots for i in scope_ids):
            raise AppError('不能检查当前任务之外或只用于卷统计的位置。', 'outside_scope', 409)
        paths = [roots[i]['path'] for i in scope_ids] if scope_ids is not None else [r['path'] for r in roots.values()]
        if directory_ids is not None:
            rows = self._selected(task, directory_ids)
            if any(i['kind'] != 'directory' or i['risk'] == 'protected' or not self._action_target(task, i) for i in rows):
                raise AppError('请使用当前任务已观察的普通目录。', 'outside_scope')
            paths = [i['path'] for i in rows] if scope_ids is None else paths + [i['path'] for i in rows]
        unique = []
        for path in sorted(set(paths), key=lambda p: len(canonical(p))):
            if not any(within(path, parent) for parent in unique):
                unique.append(path)
        if not 1 <= len(unique) <= 8:
            raise AppError('请先选择1到8个可检查目录；单个文件可使用compare_files。', 'work_scope_required')
        return unique

    def _ensure_system_diagnostics(self, key, cancel, progress):
        task = self._get(key)
        if task.get('preset') not in ('c_cleanup', 'system_cleanup') or task.get('diagnostic_snapshot_id'):
            return
        paths = self._work_paths(task)
        work = self.task_jobs.start(key, 'diagnostics', dict(paths=paths),
            lambda stop, report: self.diagnostics.scan(paths, task_id=key, cancel=stop, progress=report))
        self.task_jobs.wait(key, work['id'], cancel=cancel, progress=progress)
        self._consume_work(key, work['id'])

    def _consume_work(self, key, job_id):
        work = self.task_jobs.read(key, job_id)
        if work['status'] not in ('completed', 'cancelled'):
            return work
        result = self.task_jobs.result(key, job_id)
        with self.state.lock:
            task = self._get(key)
            consumed = task.setdefault('consumed_work', {})
            if job_id not in consumed:
                if work['kind'] == 'duplicates':
                    rows = [row for group in result.get('groups', []) for row in group['items']]
                    # Newly observed members are not known yet. Check their
                    # scope with a temporary known-ID set, before publishing;
                    # this preserves analysis directory boundaries without
                    # rejecting every legitimate new duplicate member.
                    candidate_task = dict(task, known_ids=list(set(task.get('known_ids', [])) | {row['id'] for row in rows}))
                    request_paths = self.task_jobs._get(key, job_id)['request'].get('paths', [])
                    if any(row.get('kind') != 'file' or not self._action_target(candidate_task, row)
                           or not any(within(row['path'], path) for path in request_paths) for row in rows):
                        raise AppError('后台结果超出当前任务范围。', 'outside_scope')
                    if rows:
                        self.state.publish(rows, 'task-duplicates:' + job_id)
                        task['known_ids'] = list(dict.fromkeys(task['known_ids'] + [i['id'] for i in rows]))[:50000]
                        if task.get('analysis'):
                            task['target_ids'] = list(dict.fromkeys(task.get('target_ids', []) + [i['id'] for i in rows]))[:50000]
                    task['usage']['hash_bytes'] += result.get('bytes_read', 0)
                    fact = dict(job_id=job_id, summary=result.get('summary', {}), partial=result.get('partial'),
                                status=result.get('status'), bytes_read=result.get('bytes_read'), read_only=True,
                                note='已核验组可按read_duplicate_groups/read_duplicate_members读取，未覆盖范围不是没有重复。')
                else:
                    task['diagnostic_snapshot_id'] = result['snapshot_id']
                    fact = self._diagnostic_brief(key, result['snapshot_id'])
                self._save(task)
                consumed[job_id] = self._record_fact(key, work['kind'] + '_work', fact)
                task = self._get(key)
                task.setdefault('consumed_work', {})[job_id] = consumed[job_id]
                self._save(task)
            work['fact_id'] = task.get('consumed_work', {}).get(job_id)
        return work

    def _diagnostic_brief(self, key, snapshot_id):
        summary = self.diagnostics.summary(snapshot_id, task_id=key)
        rows = summary.get('items', [])
        interesting = [row for row in rows if row.get('candidate') is True or row.get('status') in ('observed', 'partial')]
        return dict(snapshot_id=snapshot_id, at=summary.get('at'), counts=summary.get('counts'), status=summary.get('status'),
                    coverage=summary.get('coverage'), signals=interesting[:12],
                    note='这是轻量诊断和适用性，不是48项完整深查或固定推荐。可分页回读，未知不当0。')

    def _user_description(self, value):
        definition = value['definition']
        result = {key: copy.deepcopy(value.get(key)) for key in ('id', 'draft_id', 'version', 'status', 'provenance') if value.get(key) is not None}
        result.update(title=definition['title'], description=definition.get('description', ''), type=definition['type'])
        if definition['type'] == 'files':
            result['rules'] = {k: copy.deepcopy(v) for k, v in definition['rules'].items() if k != 'roots'}
            result['root_count'] = len(definition['rules']['roots'])
        else:
            ref = definition['builtin']
            result['builtin'] = dict(tool_id=ref['tool_id'], parameters={k: v for k, v in ref.get('parameters', {}).items()
                if not isinstance(v, str) or not os.path.isabs(v)})
        return result

    def orchestration_tool(self, key, name, args, cancel, provenance=None):
        task = self._get(key)
        if name == 'describe_cleanup_tool':
            self._args(args, ['tool_id'], ['tool_id'])
            return self._describe_tool_binding(args['tool_id'])
        if name == 'run_cleanup_diagnostics':
            self._args(args, ['scope_ids', 'refresh'])
            paths = self._work_paths(task, args.get('scope_ids'))
            if type(args.get('refresh', False)) is not bool:
                raise AppError('refresh必须是布尔值。')
            return self.task_jobs.start(key, 'diagnostics', dict(paths=paths),
                lambda stop, report: self.diagnostics.scan(paths, task_id=key, cancel=stop, progress=report), refresh=args.get('refresh', False))
        if name == 'read_cleanup_diagnostics':
            self._args(args, ['snapshot_id', 'offset', 'limit', 'tool_ids'], ['snapshot_id'])
            return self.diagnostics.read(args['snapshot_id'], task_id=key, offset=args.get('offset', 0), limit=args.get('limit', 12), tool_ids=args.get('tool_ids'))
        if name == 'start_duplicate_scan':
            self._args(args, ['scope_ids', 'directory_ids', 'max_entries', 'max_depth', 'max_bytes', 'refresh'])
            paths = self._work_paths(task, args.get('scope_ids'), args.get('directory_ids'))
            request = validate_duplicate_request(dict(paths=paths, **{k: args[k] for k in ('max_entries', 'max_depth', 'max_bytes') if k in args}))
            if type(args.get('refresh', False)) is not bool:
                raise AppError('refresh必须是布尔值。')
            return self.task_jobs.start(key, 'duplicates', request, lambda stop, report:
                run_duplicate_scan(request, patterns=lambda: self.assistant.public_settings()['protect_patterns'], cancel=stop, progress=report), refresh=args.get('refresh', False))
        if name in ('read_task_work', 'cancel_task_work'):
            self._args(args, ['job_id'], ['job_id'])
            return self.task_jobs.cancel(key, args['job_id']) if name == 'cancel_task_work' else self._consume_work(key, args['job_id'])
        if name in ('read_duplicate_groups', 'read_duplicate_members'):
            self._args(args, ['job_id', 'offset', 'limit'] + (['group_id'] if name.endswith('members') else []),
                       ['job_id'] + (['group_id'] if name.endswith('members') else []))
            offset, limit = args.get('offset', 0), args.get('limit', 12)
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 40:
                raise AppError('分页范围无效。')
            self._consume_work(key, args['job_id'])
            result = self.task_jobs.result(key, args['job_id'])
            if result.get('source') != 'local_duplicate_scan':
                raise AppError('此工作不是目录查重。')
            groups = result['groups']
            if name.endswith('members'):
                group = next((g for g in groups if g['group_id'] == args['group_id']), None)
                if not group:
                    raise AppError('重复组不属于此任务。', 'outside_scope')
                rows = [self._metadata(i) for i in group['items'][offset:offset + limit]]
                return dict(job_id=args['job_id'], group_id=group['group_id'], type=group['type'], items=rows,
                            total=group['items_total'], available=len(group['items']), omitted=group['omitted_items'],
                            has_more=offset + len(rows) < len(group['items']), next_offset=offset + len(rows), partial=result['partial'])
            rows = [{k: copy.deepcopy(g[k]) for k in ('group_id', 'type', 'size_bytes', 'items_total', 'omitted_items')} for g in groups[offset:offset + limit]]
            return dict(job_id=args['job_id'], groups=rows, summary=result['summary'], partial=result['partial'],
                        has_more=offset + len(rows) < len(groups), next_offset=offset + len(rows))
        if name == 'discover_user_tools':
            self._args(args, ['query'])
            data = self.user_tools.list(query=args.get('query', ''))
            return dict(items=[self._user_description(i) for i in data['items'] if i.get('status') != 'draft' or (i.get('source') or {}).get('task_id') == key][:30],
                        note='用户工具由用户保存后才可运行；草案需要用户审阅。')
        if name == 'describe_user_tool':
            self._args(args, ['tool_id', 'version'], ['tool_id'])
            return self._user_description(self.user_tools.get(args['tool_id'], args.get('version')))
        if name == 'draft_user_tool':
            self._args(args, ['title', 'description', 'type', 'scope_ids', 'rules', 'builtin', 'recipe_id', 'base_version'], ['title', 'type'])
            definition = dict(type=args['type'], title=args['title'], description=args.get('description', ''))
            if args['type'] == 'files':
                if 'builtin' in args:
                    raise AppError('文件规则不能同时带内置预设。')
                definition['rules'] = dict(args.get('rules') or {}, roots=self._work_paths(task, args.get('scope_ids')))
            elif args['type'] == 'preset':
                if 'rules' in args or 'scope_ids' in args:
                    raise AppError('内置预设不使用文件规则。')
                ref = args.get('builtin')
                if not isinstance(ref, dict) or set(ref) - {'tool_id', 'parameters'}:
                    raise AppError('请提供有效的内置预设。')
                definition['builtin'] = self._check_tool_read_scope(task, ref.get('tool_id'), ref.get('parameters', {}))
            else:
                raise AppError('请选择files或preset。')
            public_provenance = ({k: v for k, v in provenance.items() if k in
                ('source', 'provider', 'model', 'protocol', 'generated_at')} if isinstance(provenance, dict) else provenance)
            value = self.user_tools.create_draft(definition, provenance=public_provenance,
                recipe_id=args.get('recipe_id'), base_version=args.get('base_version'), source={'task_id': key})
            self._record_fact(key, 'user_tool_draft', dict(draft_id=value['draft_id'], title=definition['title'], needs_user_review=True))
            return self._user_description(value)
        if name == 'inspect_user_tool':
            self._args(args, ['tool_id', 'version', 'draft_id'])
            if bool(args.get('tool_id')) == bool(args.get('draft_id')):
                raise AppError('请提供一个工具或草案引用。')
            value = self.user_tools.get(args['tool_id'], args.get('version')) if args.get('tool_id') else self.user_tools._get_draft(args['draft_id'])
            if value.get('status') == 'draft' and (value.get('source') or {}).get('task_id') != key:
                raise AppError('此草案不属于当前任务。', 'outside_scope')
            definition = value['definition']
            if definition['type'] == 'preset':
                self._check_tool_read_scope(task, definition['builtin']['tool_id'], definition['builtin'].get('parameters', {}))
            if definition['type'] == 'files' and any(not any(within(p, root['path']) for root in task['roots'] if not task.get('analysis') or root.get('inspectable')) for p in definition['rules']['roots']):
                return dict(status='needs_user_scope', tool=self._user_description(value), note='此工具范围超出当前任务，请给用户user_tool选项自行打开。')
            run = self.user_tools.inspect(tool_id=args.get('tool_id'), version=args.get('version'), draft_id=args.get('draft_id'), cancel=cancel)
            return {k: copy.deepcopy(run.get(k)) for k in ('run_id', 'tool_id', 'tool_version', 'draft_id', 'status', 'complete', 'matched_count', 'matched_bytes', 'selectable_count', 'skipped')}
        return None
