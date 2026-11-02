"""Versioned user cleanup definitions and bounded metadata-only rule runs.

Definitions never execute code. File rules compile to the existing per-file
simulation lifecycle; builtin presets retain the specialist lifecycle.
"""
from __future__ import annotations

from collections import Counter
import copy
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time

from .scanner import (_checked_root, _ancestor_project, _project_at, _identity,
                      _metadata_signature, metadata_item, validate_item_snapshot)
from .simulation import AppError, canonical, is_reparse, is_user_protected, within


MAX_ENTRIES, MAX_MATCHES, MAX_DEPTH, MAX_SECONDS = 10000, 5000, 32, 3.0
_CLOUD = 0x1000 | 0x40000 | 0x400000
_MAX_BYTES = 8 * 1024 ** 4


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _text(value, label, limit, *, empty=False):
    if not isinstance(value, str) or '\x00' in value or len(value) > limit or not empty and not value.strip():
        raise AppError(label + '格式不正确。', 'invalid_user_tool')
    return value.strip()


def _path(value):
    value = _text(value, '目录', 32767)
    if not os.path.isabs(value) or value.startswith(('\\\\', '//', '\\??\\')):
        raise AppError('请选择明确的本地绝对目录。', 'invalid_user_tool')
    return os.path.abspath(value)


def _patterns(value, label, *, required=False):
    if (not isinstance(value, list) or len(value) > 20 or required and not value
            or any(not isinstance(p, str) for p in value)):
        raise AppError(label + '须为最多20个文件名或相对路径规则。', 'invalid_user_tool')
    result = []
    for raw in value:
        pattern = _text(raw, label, 160).replace('\\', '/')
        if pattern.startswith('/') or ':' in pattern or '..' in pattern.split('/'):
            raise AppError(label + '只能匹配文件名或根目录内的相对路径。', 'invalid_user_tool')
        if pattern.casefold() not in result:
            result.append(pattern.casefold())
    return result


def _matches(patterns, relative, name):
    relative, name = relative.replace('\\', '/').casefold(), name.casefold()
    def path_match(pattern):
        parts,tokens=relative.split('/'),pattern.split('/')
        memo={}
        def match(i,j):
            if (i,j) in memo:return memo[i,j]
            if j==len(tokens):result=i==len(parts)
            elif tokens[j]=='**':result=match(i,j+1) or i<len(parts) and match(i+1,j)
            else:result=i<len(parts) and fnmatch.fnmatchcase(parts[i],tokens[j]) and match(i+1,j+1)
            memo[i,j]=result;return result
        return match(0,0)
    return any(fnmatch.fnmatchcase(name,p.casefold()) if '/' not in p else path_match(p.casefold()) for p in patterns)


class UserToolStore:
    def __init__(self, state, harness, catalog=None, toolkit=None):
        self.state, self.harness = state, harness
        self.catalog = catalog
        self.toolkit = toolkit or harness.cleanup_tools
        with state.lock, state.db:
            state.db.executescript('''
                CREATE TABLE IF NOT EXISTS user_tool_definitions (
                    id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(id,version));
                CREATE TABLE IF NOT EXISTS user_tool_heads (
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS user_tool_drafts (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS user_tool_runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            ''')

    @staticmethod
    def schema():
        """Compact protocol description, shared by host forms and model drafts."""
        return dict(types=['files', 'preset'], fields=dict(title='required string,1..80',
            description='string,0..600', rules=dict(roots='1..8 local absolute directories',
            recursive='boolean,true', includes='1..20 filename/relative globs,default *.tmp',
            excludes='0..20 filename/relative globs', min_age_days='integer,0..36500;0 disables age filter',
            min_size_bytes='integer,0..8TiB', max_size_bytes='integer|null',
            time_basis='modified', action='recoverable_delete'),
            builtin=dict(tool_id='registered builtin id', parameters='builtin parameter object')),
            note='files只匹配普通文件；修改时间不是使用时间。preset保存内置工具参数。没有脚本执行。')

    def normalize(self, definition):
        if not isinstance(definition, dict) or set(definition) - {'type', 'title', 'description', 'rules', 'builtin'}:
            raise AppError('清理工具定义包含未知字段。', 'invalid_user_tool')
        kind = definition.get('type')
        if kind not in ('files', 'preset'):
            raise AppError('请选择文件规则或内置工具预设。', 'invalid_user_tool')
        result = dict(type=kind, title=_text(definition.get('title'), '名称', 80),
                      description=_text(definition.get('description', ''), '说明', 600, empty=True))
        if kind == 'preset':
            if 'rules' in definition:
                raise AppError('内置工具预设不能同时包含文件规则。', 'invalid_user_tool')
            result['builtin'] = self.toolkit.validate_reference(definition.get('builtin'), allow_missing=True)
            return result
        if 'builtin' in definition:
            raise AppError('文件规则不能同时包含内置工具。', 'invalid_user_tool')
        rules = definition.get('rules')
        allowed = {'roots', 'recursive', 'includes', 'excludes', 'min_age_days', 'min_size_bytes',
                   'max_size_bytes', 'time_basis', 'action'}
        if not isinstance(rules, dict) or set(rules) - allowed:
            raise AppError('文件匹配规则格式不正确。', 'invalid_user_tool')
        roots = rules.get('roots')
        if not isinstance(roots, list) or not 1 <= len(roots) <= 8:
            raise AppError('请选择1到8个目录。', 'invalid_user_tool')
        roots = list(dict.fromkeys(_path(p) for p in roots))
        recursive = rules.get('recursive', True)
        age, minimum, maximum = rules.get('min_age_days', 0), rules.get('min_size_bytes', 0), rules.get('max_size_bytes')
        if type(recursive) is not bool or type(age) is not int or not 0 <= age <= 36500:
            raise AppError('递归或修改时间条件无效。', 'invalid_user_tool')
        if (type(minimum) is not int or not 0 <= minimum <= _MAX_BYTES
                or maximum is not None and (type(maximum) is not int or not minimum <= maximum <= _MAX_BYTES)):
            raise AppError('文件大小条件无效。', 'invalid_user_tool')
        if rules.get('time_basis', 'modified') != 'modified' or rules.get('action', 'recoverable_delete') != 'recoverable_delete':
            raise AppError('当前只支持按修改时间筛选并移入盘序保留记录。', 'invalid_user_tool')
        result['rules'] = dict(roots=roots, recursive=recursive,
            includes=_patterns(rules.get('includes', ['*.tmp']), '包含条件', required=True),
            excludes=_patterns(rules.get('excludes', []), '排除条件'), min_age_days=age,
            min_size_bytes=minimum, max_size_bytes=maximum, time_basis='modified', action='recoverable_delete')
        return result

    def _get_draft(self, draft_id):
        if not isinstance(draft_id,str):
            raise AppError('工具草案引用无效。','invalid_user_tool')
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM user_tool_drafts WHERE id=?', (draft_id,)).fetchone()
            if not row:
                raise AppError('工具草案不存在。', 'user_tool_missing', 404)
            return json.loads(row[0])

    def get(self, tool_id, version=None):
        if not isinstance(tool_id, str) or version is not None and (type(version) is not int or version < 1):
            raise AppError('工具引用无效。', 'invalid_user_tool')
        with self.state.lock:
            head = self.state.db.execute('SELECT version,archived FROM user_tool_heads WHERE id=?', (tool_id,)).fetchone()
            if not head:
                raise AppError('自定义工具不存在。', 'user_tool_missing', 404)
            row = self.state.db.execute('SELECT payload FROM user_tool_definitions WHERE id=? AND version=?',
                                       (tool_id, version or head[0])).fetchone()
            if not row:
                raise AppError('工具版本不存在。', 'user_tool_missing', 404)
            result = json.loads(row[0]); result['archived'] = bool(head[1])
            result['current_version'] = head[0]
            return result

    def list(self, query='', include_drafts=True):
        query = _text(query, '搜索', 160, empty=True).casefold()
        if type(include_drafts) is not bool:
            raise AppError('列表选项无效。', 'invalid_user_tool')
        with self.state.lock:
            items = [self.get(r[0]) for r in self.state.db.execute('SELECT id FROM user_tool_heads WHERE archived=0 ORDER BY rowid DESC')]
            if include_drafts:
                items += [json.loads(r[0]) for r in self.state.db.execute('SELECT payload FROM user_tool_drafts ORDER BY rowid DESC')]
        items = [row for row in items if not query or query in (row['definition']['title']+' '+row['definition']['description']).casefold()]
        return dict(items=items[:1000], total=len(items), truncated=len(items)>1000)

    def create_draft(self, definition, provenance=None, recipe_id=None, base_version=None, source=None):
        definition = self.normalize(definition)
        if (recipe_id is None) != (base_version is None):
            raise AppError('修改工具时须提供当前版本。', 'invalid_user_tool')
        if base_version is not None and (type(base_version) is not int or base_version<1):
            raise AppError('工具版本无效。','invalid_user_tool')
        if recipe_id is not None:
            current = self.get(recipe_id)
            if current['archived'] or current['version'] != base_version:
                raise AppError('工具已更新，请重新打开后修改。', 'user_tool_changed', 409)
        provenance = provenance or {'source': 'user'}
        if not isinstance(provenance, dict) or set(provenance) - {'source', 'provider', 'model', 'protocol', 'generated_at'}:
            raise AppError('工具来源格式不正确。', 'invalid_user_tool')
        safe = {k: _text(v, '来源', 160, empty=True) for k,v in provenance.items() if v is not None}
        if source is not None and (not isinstance(source, dict) or set(source) - {'task_id'}
                or not isinstance(source.get('task_id'), str)):
            raise AppError('来源任务无效。', 'invalid_user_tool')
        if source:
            self.harness._get(source['task_id'])
        key = 'user-draft-' + secrets.token_hex(12)
        row = dict(id=key, draft_id=key, status='draft', definition=definition, recipe_id=recipe_id,
                   base_version=base_version, provenance=safe, source=copy.deepcopy(source), created_at=_now())
        with self.state.lock, self.state.db:
            self.state.db.execute('INSERT INTO user_tool_drafts VALUES(?,?)', (key,json.dumps(row,ensure_ascii=False)))
        return copy.deepcopy(row)

    def save(self, draft_id):
        with self.state.lock, self.state.db:
            draft = self._get_draft(draft_id)
            ident = draft['recipe_id'] or 'user-tool-' + secrets.token_hex(12)
            if draft['recipe_id']:
                current = self.get(ident)
                if current['archived'] or current['version'] != draft['base_version']:
                    raise AppError('工具已更新，旧草案未覆盖新版本。', 'user_tool_changed', 409)
                version, created = current['version']+1,current['created_at']
            else:
                version,created = 1,draft['created_at']
            definition = self.normalize(draft['definition'])
            row = dict(id=ident,version=version,status='saved',definition=definition,
                content_hash=_hash(definition),provenance=draft['provenance'],source=draft.get('source'),
                created_at=created,updated_at=_now())
            self.state.db.execute('INSERT INTO user_tool_definitions VALUES(?,?,?)', (ident,version,json.dumps(row,ensure_ascii=False)))
            self.state.db.execute('INSERT OR REPLACE INTO user_tool_heads VALUES(?,?,0)', (ident,version))
            self.state.db.execute('DELETE FROM user_tool_drafts WHERE id=?', (draft_id,))
            return copy.deepcopy(row)

    def archive(self, tool_id, version):
        if type(version) is not int or version<1:
            raise AppError('工具版本无效。','invalid_user_tool')
        with self.state.lock, self.state.db:
            current = self.get(tool_id)
            if current['version'] != version:
                raise AppError('工具已更新，请刷新。', 'user_tool_changed', 409)
            self.state.db.execute('UPDATE user_tool_heads SET archived=1 WHERE id=?',(tool_id,))
        return dict(id=tool_id,version=version,archived=True)

    def _run(self, run_id):
        if not isinstance(run_id,str):
            raise AppError('检查记录不存在。', 'user_tool_run_missing',404)
        with self.state.lock:
            row=self.state.db.execute('SELECT payload FROM user_tool_runs WHERE id=?',(run_id,)).fetchone()
        if not row:
            raise AppError('检查记录不存在，请重新检查。', 'user_tool_run_missing',404)
        return json.loads(row[0])

    def _save_run(self, run):
        with self.state.lock,self.state.db:
            self.state.db.execute('INSERT OR REPLACE INTO user_tool_runs VALUES(?,?)',(run['id'],json.dumps(run,ensure_ascii=False)))

    def inspect(self, tool_id=None, version=None, draft_id=None, *, cancel=None, progress=None):
        if draft_id is not None:
            if tool_id is not None or version is not None:
                raise AppError('请选择一个草案或已保存工具。', 'invalid_user_tool')
            definition=self._get_draft(draft_id); saved=False
        else:
            definition=self.get(tool_id,version)
            if definition['archived'] or definition['current_version']!=definition['version']:
                raise AppError('工具已更新或已归档，请重新打开。','user_tool_changed',409)
            saved=True
        body=definition['definition'];key='user-run-'+secrets.token_hex(12)
        run=dict(id=key,run_id=key,tool_id=definition['id'] if saved else None,
            tool_version=definition['version'] if saved else None,draft_id=None if saved else draft_id,
            content_hash=definition.get('content_hash',_hash(body)),definition=copy.deepcopy(body),
            title=body['title'],type=body['type'],at=_now(),source=definition.get('source'),items=[],
            warnings=[],skipped={},complete=False,matched_count=0,matched_bytes=0,selectable_count=0,selectable_bytes=0,
            read_only=True,simulation_only=True,actual_reclaimed_bytes=0)
        if body['type']=='preset':
            result=self.toolkit.inspect(**body['builtin']);run['inspection']=result
            run['presentation']=result.get('presentation',{})
            run['complete']=run['presentation'].get('complete') is True
            run['status']=run['presentation'].get('status','unknown')
            run['can_prepare']=run['status']!='needs_input' and run['presentation'].get('action_availability',{}).get('available') is not False
        else:
            self._inspect_files(run,cancel=cancel,progress=progress)
        self._save_run(run)
        return self.read_run(key)

    def _inspect_files(self, run, *, cancel, progress):
        rules=run['definition']['rules'];anchor=time.time();deadline=time.monotonic()+MAX_SECONDS
        skipped=Counter();warnings=[];queue=[];items={};seen=set();count=0;limited=False;cancelled=False
        patterns=self.harness.assistant.public_settings()['protect_patterns']
        for raw in rules['roots']:
            try:
                root,info=_checked_root(raw)
                if getattr(info,'st_file_attributes',0)&_CLOUD:
                    skipped['cloud_placeholder']+=1;continue
                if any(within(root,q[0]) for q in queue):continue
                project=_ancestor_project(root)
                queue.append((root,root,0,project,_identity(info,root)))
            except (OSError,ValueError):
                skipped['unavailable_root']+=1
                warnings.append('一个目录无法读取或包含链接，请核对位置。')
        def stopped():
            return bool(cancel is not None and (cancel() if callable(cancel) else cancel.is_set()))
        while queue:
            if stopped():cancelled=True;break
            if time.monotonic()>=deadline or count>=MAX_ENTRIES or len(items)>=MAX_MATCHES:
                limited=True;break
            path,root,depth,project,expected=queue.pop()
            if canonical(path) in seen:continue
            seen.add(canonical(path))
            try:
                absolute,info=_checked_root(path)
                if _identity(info,path)!=expected:skipped['changed_directory']+=1;continue
                if getattr(info,'st_file_attributes',0)&_CLOUD:skipped['cloud_placeholder']+=1;continue
                project=project or _project_at(path)
                directory=metadata_item(path,info,root=root,depth=depth,parent_id=None,project=project,is_dir=True)
                if is_user_protected(directory,patterns) or directory['risk']=='protected' and Path(path).parent!=Path(path):
                    skipped['protected_directory']+=1;continue
                with os.scandir(absolute) as entries:
                    for entry in entries:
                        if stopped():cancelled=True;break
                        if time.monotonic()>=deadline or count>=MAX_ENTRIES or len(items)>=MAX_MATCHES:
                            limited=True;break
                        count+=1
                        relative=os.path.relpath(entry.path,root).replace('\\','/')
                        if _matches(rules['excludes'],relative,entry.name):skipped['excluded']+=1;continue
                        try:
                            current=os.lstat(entry.path)
                            if is_reparse(current):skipped['reparse_point']+=1;continue
                            if getattr(current,'st_file_attributes',0)&_CLOUD:skipped['cloud_placeholder']+=1;continue
                            if stat.S_ISDIR(current.st_mode):
                                if rules['recursive'] and depth<MAX_DEPTH:
                                    queue.append((entry.path,root,depth+1,project,_identity(current,entry.path)))
                                elif rules['recursive']:skipped['depth_limit']+=1
                                continue
                            if not stat.S_ISREG(current.st_mode):skipped['not_regular']+=1;continue
                            if not _matches(rules['includes'],relative,entry.name):continue
                            if rules['min_age_days'] and current.st_mtime>anchor-rules['min_age_days']*86400:continue
                            if current.st_size<rules['min_size_bytes'] or rules['max_size_bytes'] is not None and current.st_size>rules['max_size_bytes']:continue
                            row=metadata_item(entry.path,current,root=root,depth=depth+1,parent_id=None,project=project,is_dir=False)
                            if row['risk']=='protected' or is_user_protected(row,patterns):skipped['protected_file']+=1;continue
                            row['signature']=_metadata_signature(entry.path,current);row['actions']=['delete']
                            row['details']['source']='user_cleanup_rule'
                            row['match_reason']='符合文件名、大小和修改时间条件；未读取正文。'
                            items[row['id']]=row
                        except (OSError,ValueError):skipped['unavailable']+=1
                        if progress and count%128==0:progress(dict(phase='matching',scanned_entries=count,matched_count=len(items)))
                if cancelled or limited:break
            except (OSError,ValueError):skipped['unavailable']+=1
        rows=sorted(items.values(),key=lambda row:canonical(row['path']))
        if rows:self.state.publish(rows,'user-tool:'+run['id'])
        if limited:warnings.append('已达到本次检查预算；未检查部分不代表没有匹配项。')
        if cancelled:warnings.append('检查已取消；仅保留已观察到的匹配项。')
        if skipped['protected_directory'] or skipped['protected_file']:
            warnings.append('受保护的位置或文件已跳过，没有加入操作清单。')
        if skipped['cloud_placeholder'] or skipped['reparse_point']:
            warnings.append('云端占位和链接已跳过，未读取其内容。')
        uncertain=any(skipped[k] for k in ('unavailable','unavailable_root','changed_directory','depth_limit',
                                         'cloud_placeholder','reparse_point','protected_directory'))
        run.update(items=rows,matched_count=len(rows),matched_bytes=sum(row['size_bytes'] for row in rows),
            scanned_entries=count,skipped=dict(skipped),warnings=warnings[:8],cancelled=cancelled,
            complete=not(limited or cancelled or uncertain),status='partial' if limited or cancelled or uncertain else 'complete',
            reference_time=datetime.fromtimestamp(anchor,timezone.utc).isoformat())

    def _project(self, rows):
        with self.state.lock:
            current={row['id']:row for row in self.state.snapshot(self.harness.assistant.public_settings()['protect_patterns'])['items']}
            result=[]
            for original in rows:
                row=copy.deepcopy(original);live=current.get(row['id'])
                reason=None
                if live is None or live['signature']!=row['signature']:reason='文件已变化，请重新检查。'
                elif live['risk']=='protected':reason='此项受保护。'
                elif live.get('simulated'):reason='此版本已有处理记录。'
                elif self.harness.assessments.dispositions.strong(live)=='direct_keep':reason='你已选择直接保留。'
                row['can_select']=reason is None
                if reason:row['block_reason']=reason
                result.append(row)
            return result

    def read_run(self, run_id, offset=0, limit=50):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
            raise AppError('检查分页无效。','invalid_user_tool')
        run=self._run(run_id);rows=self._project(run['items']);result={k:copy.deepcopy(v) for k,v in run.items() if k!='items'}
        selectable=[row for row in rows if row['can_select']]
        result.update(items=rows[offset:offset+limit],offset=offset,limit=limit,has_more=offset+limit<len(rows),
            next_offset=offset+limit if offset+limit<len(rows) else None,selectable_count=len(selectable),
            selectable_bytes=sum(row['size_bytes'] for row in selectable))
        if run['type']=='files':
            result['can_prepare']=bool(run['tool_id'] and selectable)
            result['impact']='匹配文件将从原位置进入盘序已删除文件；需要时可还原。'
            result['recovery']='保留在原卷，不进入系统回收站；永久清除前仍占用空间。当前仅记录模拟操作，实际释放0。'
        return result

    def validate_origin(self, origin):
        if (not isinstance(origin,dict) or set(origin)!={'id','version','content_hash','run_id'}
                or type(origin['version']) is not int or any(not isinstance(origin[k],str) for k in ('id','content_hash','run_id'))):
            raise AppError('自定义工具运行来源无效。','user_tool_changed',409)
        tool=self.get(origin['id']);run=self._run(origin['run_id'])
        if (tool['archived'] or tool['version']!=origin['version'] or tool['content_hash']!=origin['content_hash']
                or run['tool_id']!=tool['id'] or run['tool_version']!=tool['version'] or run['content_hash']!=tool['content_hash']):
            raise AppError('工具已更改或归档，请重新检查。','user_tool_changed',409)
        return copy.deepcopy(origin)

    def prepare(self, run_id, ids=None, excluded_ids=None):
        run=self._run(run_id)
        if not run['tool_id']:
            raise AppError('请先保存工具，再生成本次操作清单。','user_tool_not_saved',409)
        origin=dict(id=run['tool_id'],version=run['tool_version'],content_hash=run['content_hash'],run_id=run_id)
        source_task_id=(run.get('source') or {}).get('task_id')
        with self.state.lock:
            self.validate_origin(origin)
            if run['type']=='preset':
                if ids is not None or excluded_ids:
                    raise AppError('内置工具预设没有文件勾选项。','invalid_user_tool')
                self.toolkit.validate_reference(run['definition']['builtin'])
                if run.get('can_prepare') is False:
                    raise AppError('没有可执行的清理内容，请刷新状态后再试。','nothing_to_clean',409)
                source={'task_id':source_task_id} if source_task_id else None
                plan=self.toolkit.prepare(**run['definition']['builtin'],source=source)
                plan['user_tool']=origin
                # Root includes user_tool in the specialist frozen contract.
                plan['frozen_hash']=_hash(self.toolkit._frozen(plan))
                self.toolkit._save(plan)
                return dict(kind='preset',specialist_plan=plan,plan=plan,user_tool=origin)
            all_rows={row['id']:row for row in self._project(run['items'])}
            excluded_ids=[] if excluded_ids is None else excluded_ids
            for values in (ids,excluded_ids):
                if values is not None and (not isinstance(values,list) or len(values)>MAX_MATCHES
                        or any(not isinstance(v,str) or v not in all_rows for v in values) or len(set(values))!=len(values)):
                    raise AppError('请只选择本次检查中出现的文件。','invalid_user_tool_selection')
            if ids is not None and set(ids)&set(excluded_ids):
                raise AppError('选中和排除的内容不能重复。','invalid_user_tool_selection')
            selected=[row for ident,row in all_rows.items() if (ids is None or ident in ids) and ident not in excluded_ids and row['can_select']]
            if not selected:
                raise AppError('没有可执行的匹配文件，请检查或调整勾选。','nothing_to_clean',409)
            chosen=selected[:500]
            if any(not validate_item_snapshot(row) for row in chosen):
                raise AppError('匹配文件在检查后变化，请重新检查再选择。','target_changed',409)
            result=self.harness.prepare_assessment_plan([row['id'] for row in chosen])
            task=self.harness._get(result['task']['id']);task['plan']['user_tool']=origin;task['user_tool_origin']=origin
            if source_task_id:
                # Feedback linkage does not narrow or expand the independently
                # selected rule roots, unlike cleanup_source_task_id guards.
                task['user_tool_source_task_id']=source_task_id
            self.harness._save(task);result['task']=self.harness.public(task['id']);result['plan']=copy.deepcopy(task['plan'])
            result.update(user_tool=origin,selection=dict(counts=dict(total_targets=len(selected),returned=len(chosen),
                excluded=len(excluded_ids),blocked=sum(not r['can_select'] for r in all_rows.values())),
                has_more=len(selected)>500,requires_batches=len(selected)>500))
            return result
