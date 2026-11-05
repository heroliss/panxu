"""End-to-end run of 盘序 in a throw-away 演练环境, through the same bridge the UI uses.

    python tools/sandbox_e2e.py            # scripted model, no network
    python tools/sandbox_e2e.py --live     # the configured real model
    python tools/sandbox_e2e.py --keep DIR # keep the simulation in DIR afterwards

init -> 一键体检 -> execute the whole plan -> check that files were deleted or
moved to the simulated Recycle Bin -> disable a startup item and undo it ->
change a service and undo it -> simulated uninstall -> 问 AI -> 操作记录.

With --live the API key is taken from PANXU_AI_KEY, else the key PanXu saved
(DPAPI, same Windows user), else the one PanXu 0.x saved. Only the simulated
PC's (fake) folder and file names are sent; the key is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from panxu import ai_client, paths, sandbox  # noqa: E402
from panxu.api import Backend, Bridge  # noqa: E402
from panxu.app import default_data_dir, legacy_key_paths  # noqa: E402
from panxu.settings import Settings  # noqa: E402


class ScriptedModel:
    """Plausible answers without a network: caches are 'clean', unknown folders 'review'."""
    model = 'scripted'

    def __init__(self):
        self.usage = ai_client.Usage()

    def complete_json(self, system, user):
        self.usage.add({})
        out = []
        for row in json.loads(user.split('\n', 1)[1]):
            if 'subdirs' in row:
                cache = any(w in row['path'].lower() for w in ('unity', 'playwright', 'cache'))
                out.append({'id': row['id'], 'verdict': 'clean' if cache else 'review', 'app': '', 'reason': 'scripted',
                            'effect': '', 'confidence': 0.85 if cache else 0.5})
            elif 'group' in row:
                out.append({'id': row['id'], 'verdict': 'review', 'what': '', 'reason': 'scripted'})
            elif 'copies' in row:  # duplicates: keep the first copy, the loose ones may go
                out.append({'id': row['id'], 'copies': [
                    {'n': c['n'], 'verdict': 'keep' if c['n'] == 1 else 'careful' if c.get('place') else 'delete',
                     'reason': 'scripted'} for c in row['copies']]})
            else:
                out.append({'id': row['id'], 'what': '', 'suggestion': 'optional', 'reason': 'scripted'})
        return {'results': out}

    def chat(self, system, messages):
        self.usage.add({})
        return '（脚本回答）可以删除。'


class Run:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lines: list[str] = []
        self.failed = 0

    def call(self, method, **params):
        r = self.bridge.call(method, params)
        if not r['ok']:
            raise RuntimeError(f'{method}: {r["error"]}')
        return r['data']

    def wait(self, method, **params):
        job = self.call(method, **params)
        while True:
            s = self.call('job', job_id=job)
            if s['status'] != 'running':
                if s['status'] != 'done':
                    raise RuntimeError(f'{method}: {s.get("error") or s["status"]}')
                return s['result']
            time.sleep(0.1)

    def check(self, ok, text):
        self.lines.append(('  ok   ' if ok else '  FAIL ') + text)
        if not ok:
            self.failed += 1

    def note(self, text):
        self.lines.append(text)
        print(text, flush=True)


def gb(n):
    return f'{(n or 0) / 1024 ** 3:.2f} GB'


def live_key() -> tuple[str, str]:
    key = os.environ.get('PANXU_AI_KEY', '').strip()
    if key:
        return key, 'PANXU_AI_KEY'
    real = Settings(default_data_dir())
    preset = real.get()['ai']['preset']
    key = real.get_secret('ai_key:' + preset)
    if key:
        return key, '盘序保存的 Key'
    tmp = tempfile.mkdtemp(prefix='panxu-key-')
    try:
        probe = Settings(tmp)
        if probe.import_legacy_key(legacy_key_paths(default_data_dir())):
            return probe.get_secret('ai_key:' + probe.get()['ai']['preset']), '0.x 保存的 Key'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return '', ''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='use the configured real model')
    parser.add_argument('--keep', metavar='DIR', help='build the simulation here and keep it')
    args = parser.parse_args()

    root = args.keep or tempfile.mkdtemp(prefix='panxu-e2e-')
    sb = sandbox.Sandbox(os.path.join(root, 'sb') if not args.keep else root)
    started = time.time()
    sandbox.activate(sb)
    data = os.path.join(sb.root, 'data')
    backend = Backend(data, sandbox=sb)
    run = Run(Bridge(backend))
    run.note(f'演练环境：{sb.root}（{time.time() - started:.1f} 秒）')
    try:
        if args.live:
            key, source = live_key()
            if not key:
                raise SystemExit('--live: 没有找到 API Key（PANXU_AI_KEY、盘序设置或 0.x 设置）')
            real_ai = Settings(default_data_dir()).get()['ai']
            backend.settings.update({'ai': real_ai})
            backend.settings.set_secret('ai_key:' + real_ai['preset'], key)
            run.note(f'模型：{real_ai["preset"]} / {real_ai["model"]}（Key 来源：{source}）')
        else:
            model = ScriptedModel()
            backend.client_or_none = lambda purpose='classify': model
            backend._client = lambda purpose='classify': model
            run.note('模型：脚本（不联网）')

        init = run.call('init')
        sysd = next(d for d in init['drives'] if d['system'])
        free_before = sysd['free']
        run.check(init['sandbox']['root'] == sb.root, 'init 报告演练环境')

        t = time.time()
        plan = run.wait('checkup')
        s = plan['summary']
        run.note(f'\n一键体检：{time.time() - t:.1f} 秒；放心清理 {s["safe"]["count"]} 项 {gb(s["safe"]["size"])}，'
                 f'建议你看看 {s["review"]["count"]} 项 {gb(s["review"]["size"])}，仅供了解 {s["info"]["count"]} 项')
        if plan['ai']['used']:
            run.note(f'AI：{plan["ai"]["usage"]}' + (f'，错误：{plan["ai"]["errors"][:2]}' if plan['ai']['errors'] else ''))
        for item in plan['items']:
            mark = 'x' if item['selected'] else ' '
            run.note(f'  [{mark}] {item["group"]:6} {item["source"]:9} {gb(item["size"]):>9}  {item["category"]}：{item["title"]}'
                     + (f'  （{item["ai"]["verdict_label"]}：{item["ai"]["reason"]}）' if item.get('ai') else '')
                     + (f'  [{item["blocked"]}]' if item['blocked'] else ''))
        selected = [i for i in plan['items'] if i['selected']]
        run.check(all(i['group'] == 'safe' and not i.get('personal') for i in selected), '默认只勾选「放心清理」，不勾个人文件')
        # the same font in two projects: the one whose CSS loads it stays, whatever the model says
        fonts = next((i for i in plan['items'] if i['source'] == 'dup' and 'NotoSerifJP' in i['title']), None)
        run.check(fonts is not None, '两个工程里的同一个字体列在重复文件里')
        if fonts:
            run.note(f'  重复字体：{int(fonts["score"] / 10 + 0.5)} 分；' + '；'.join(fonts['basis'][1:4]))
            for f in fonts['dup_files']:
                ai = f.get('ai') or {}
                run.note(f'    {"保留" if f["path"] == fonts["keep"] else "删除"}  …{f["path"][-46:]}  提到它的文件 '
                         f'{(f.get("refs") or {}).get("count")}  AI：{ai.get("verdict_label", "-")} {ai.get("reason", "")}')
            mentioned = [f for f in fonts['dup_files'] if (f.get('refs') or {}).get('count')]
            run.check([f['path'] for f in mentioned] == [fonts['keep']], '样式表引用的那份字体被保留')
            run.check(all((f.get('ai') or {}).get('verdict') != 'delete' for f in mentioned), '被引用的副本不会是「更推荐删除」')
        offered = [paths.key(p) for i in plan['items'] for p in i['paths']]
        run.check(len(offered) == len(set(offered)), '每个路径只出现在一项里')

        ids = [i['id'] for i in plan['items'] if not i['blocked'] and i['action'] != 'none']
        recycle = [p for i in plan['items'] if i['id'] in ids and i['action'] == 'recycle' for p in i['paths']]
        report = run.wait('plan_execute', item_ids=ids)
        run.note(f'\n执行全部 {len(ids)} 项：永久删除 {gb(report["deleted"]["bytes"])}，回收站 {report["recycled"]["count"]} 项 '
                 f'{gb(report["recycled"]["bytes"])}，禁用 {len(report["disabled"])} 个启动项，失败 {len(report["failures"])}')
        for f in report['failures'][:5]:
            run.note(f'    失败：{f}')
        run.check(all(not os.path.exists(p) for p in recycle), '回收站项目都已从原位置移走')
        bin_rows = run.call('sandbox_recycle_list')
        run.check({paths.key(r['original']) for r in bin_rows} == {paths.key(p) for p in recycle}, '演练回收站里正好是这些项目')
        free_after = next(d for d in run.call('drives') if d['system'])['free']
        run.check(free_after > free_before, f'系统盘可用空间增加 {gb(free_after - free_before)}')
        if bin_rows:
            restored = run.call('sandbox_restore', item_id=bin_rows[0]['id'])
            run.check(os.path.exists(restored), '从演练回收站还原一项')

        items = [i for i in run.call('startup_list') if i['enabled']]
        if items:
            target = items[0]
            run.call('startup_set', item_id=target['id'], enabled=False)
            entry = run.call('history')[0]
            run.call('revert', entry_id=entry['id'])
            again = next(i for i in run.call('startup_list') if i['id'] == target['id'])
            run.check(again['enabled'], f'禁用启动项「{target["display_name"]}」后撤销')
        svc = next(s for s in run.call('services_list') if s['editable'])
        run.call('service_set', name=svc['name'], start_type='disabled')
        run.call('revert', entry_id=run.call('history')[0]['id'])
        back = next(s for s in run.call('services_list') if s['name'] == svc['name'])
        run.check(back['start_type'] == svc['start_type'], f'服务「{svc["display_name"]}」改为禁用后撤销')

        apps = run.call('apps_list')['apps']
        victim = next(a for a in apps if a['name'] == '快看图大师')
        run.call('app_uninstall', app_id=victim['id'])
        run.check(victim['name'] not in [a['name'] for a in run.call('apps_list', fresh=True)['apps']], '模拟卸载「快看图大师」')

        t = time.time()
        answer = run.wait('ai_ask', messages=[{'role': 'user', 'content': '我的 C 盘为什么快满了？'}], page='首页')
        run.note(f'\n问 AI（{time.time() - t:.1f} 秒）：{answer["answer"][:400]}')
        run.check(bool(answer['answer'].strip()), '问 AI 有回答')

        kinds = [h['kind'] for h in run.call('history')]
        run.note('\n操作记录：' + '、'.join(f'{k}×{kinds.count(k)}' for k in dict.fromkeys(kinds)))
        for kind in ('junk', 'recycle', 'startup', 'service', 'uninstall'):
            run.check(kind in kinds, f'操作记录包含 {kind}')
    finally:
        sandbox.deactivate()
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    print('\n'.join(line for line in run.lines if line.startswith('  ok') or line.startswith('  FAIL')))
    print(f'\n{"全部通过" if not run.failed else f"{run.failed} 项失败"}（{time.time() - started:.0f} 秒）')
    return 1 if run.failed else 0


if __name__ == '__main__':
    sys.exit(main())
