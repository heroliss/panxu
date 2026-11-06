"""Read-only check-up of this PC: what 一键体检 would offer, and what looks wrong.

    python tools/real_check.py                    # no AI, report on screen
    python tools/real_check.py --ai               # also ask the configured model (sends anonymised names)
    python tools/real_check.py --save base.json   # keep a summary to compare with later
    python tools/real_check.py --compare base.json
    python tools/real_check.py --out report.txt --full-paths
    python tools/real_check.py --threshold 8      # report with a stricter 勾选门槛 (default: 5)

Runs the whole check-up on the real system but executes nothing: no file is deleted or moved, no
setting is changed, and PanXu's own settings and history are not touched (a throw-away data folder
is used). Run it after a big Windows update, on a new PC or after installing many programs, and
look at 「值得注意」: categories that suddenly have hundreds of items, items that are absurdly big,
anything pre-ticked that should never be. See docs/RULES.md for what to do with a finding.

Exit code: 0 nothing noticed, 1 something worth a look, 2 a rule PanXu must never break was broken.
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

from panxu import paths  # noqa: E402
from panxu.checkup import DEFAULT_THRESHOLD, points  # noqa: E402
from panxu.insights import managed_dir  # noqa: E402

GB = 1024 ** 3
HUGE_ITEM = 20 * GB          # one item bigger than this is suspicious (130 GB of "temp files" was a bug)
MANY_IN_CATEGORY = 100       # a review category this long is probably catching the wrong things
MANY_VERSION_GROUPS = 30


def check_plan(plan: dict, env: dict, insights: dict | None = None, junk_rows: list | None = None) -> list[dict]:
    """Sanity checks on a check-up result. Each finding: {'level': 'severe'|'warn', 'text': ...}.

    'severe' means a rule PanXu promises was broken (e.g. a personal file pre-ticked)."""
    out = []

    def add(level, text):
        out.append({'level': level, 'text': text})

    app_bases = [env[k] for k in ('LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData') if env.get(k)]
    home = env.get('USERPROFILE', '')
    public = env.get('PUBLIC', '')
    users = os.path.dirname(home.rstrip('\\/')) if home else ''
    for item in plan.get('items', []):
        if item['action'] == 'none':
            continue
        title = item['title']
        if item['selected'] and item.get('personal'):
            add('severe', f'个人文件被自动勾选：{title}')
        elif item['selected'] and item['kind'] == 'paths':
            for p in item['paths']:
                if not any(paths.is_within(p, b) for b in app_bases):
                    add('severe', f'自动勾选了应用数据目录以外的文件夹：{title}（{p}）')
        for p in item['paths']:
            if users and paths.is_within(p, users) and home and not paths.is_within(p, home) \
                    and not (public and paths.is_within(p, public)):
                add('severe', f'方案里出现了其他账户的文件：{p}')
            if item.get('personal') and home and paths.is_within(p, home):
                below = p[len(home.rstrip('\\/')):].replace('/', '\\').strip('\\').split('\\')
                if any(managed_dir(x) for x in below[:-1]):
                    add('severe', f'程序自己管理的文件夹里的文件被当成了个人文件：{p}')
        blocked = item.get('blocked') or ''
        if blocked and not blocked.startswith(('请先关闭', '需要管理员')):
            add('warn', f'方案里有受保护的位置（规则或 AI 指错了地方？）：{title}——{blocked}')
        # (a big Recycle Bin is just big: emptying it is never pre-ticked)
        if (item.get('size') or 0) > HUGE_ITEM and item.get('rule') != 'recycle_bin':
            add('warn', f'一项异常大：{title} {item["size"] / GB:.1f} GB——如果不像真的，可能是规则找错了地方'
                        '（例如临时文件夹里放了别的东西）')
    counts: dict[str, int] = {}
    for item in plan.get('items', []):
        if item['group'] == 'review':
            counts[item['category']] = counts.get(item['category'], 0) + 1
    for category, n in counts.items():
        if n > MANY_IN_CATEGORY:
            add('warn', f'「{category}」有 {n} 项，可能在误报')
    if insights:
        stats = insights.get('stats') or {}
        if stats.get('truncated'):
            add('warn', f'我的文件没有全部扫完：已到 {stats.get("limit", 0):,} 个文件上限，请按文件夹分批整理')
        groups = sum(1 for f in insights.get('findings', []) if f['kind'] == 'name_versions')
        if groups > MANY_VERSION_GROUPS:
            add('warn', f'「同一文件的多个版本」有 {groups} 组，可能把编号文件当成了版本')
    for row in junk_rows or []:
        if row.get('error'):
            add('warn', f'清理规则 {row["id"]} 扫描出错：{row["error"]}')
    for err in (plan.get('ai') or {}).get('errors') or []:
        add('warn', f'AI 出错：{err}')
    return out


def summary(plan: dict) -> dict:
    """What a later run is compared with."""
    cats: dict[str, dict] = {}
    for item in plan['items']:
        c = cats.setdefault(f'{item["group"]}/{item["category"]}', {'count': 0, 'size': 0})
        c['count'] += 1
        c['size'] += item['size'] or 0
    return {'time': time.time(), 'categories': cats,
            'auto': sorted(item['id'] + ' ' + item['title'] for item in plan['items'] if item['selected'])}


def compare(old: dict, new: dict) -> list[str]:
    lines = []
    for key in sorted(set(old['categories']) | set(new['categories'])):
        a, b = old['categories'].get(key), new['categories'].get(key)
        if a is None:
            lines.append(f'新出现：{key} {b["count"]} 项')
        elif b is None:
            lines.append(f'消失了：{key}（之前 {a["count"]} 项）')
        elif abs(b['count'] - a['count']) >= 10 and (b['count'] > a['count'] * 1.5 or b['count'] < a['count'] / 1.5):
            lines.append(f'变化很大：{key} {a["count"]} → {b["count"]} 项')
    added = sorted(set(new['auto']) - set(old['auto']))
    if added:
        lines.append('新加入自动勾选：' + '、'.join(a.split(' ', 1)[1] for a in added[:10]) + (' 等' if len(added) > 10 else ''))
    return lines


def _pts(item: dict) -> str:
    return f'[{points(item["score"])}分]' if item.get('score') is not None else ''


def render(plan: dict, findings: list[dict], env: dict, seconds: float, full_paths: bool, drive: dict | None,
           ai_used: bool) -> str:
    show = (lambda p: p) if full_paths else (lambda p: paths.anonymize(p, env))
    lines = ['盘序 · 真实电脑检查（只读）  ' + time.strftime('%Y-%m-%d %H:%M'), '']
    if drive:
        lines.append(f'系统盘 {drive["root"]} 可用 {drive["free"] / GB:.1f} GB / {drive["total"] / GB:.1f} GB')
    lines.append(f'用时 {seconds:.0f} 秒；勾选门槛：{plan.get("threshold", DEFAULT_THRESHOLD)} 分；AI：{"使用了" if ai_used else "没有使用"}')
    lines.append('没有删除、移动或修改任何东西。' + ('' if full_paths else '路径里的用户名已替换成 %USERPROFILE% 等。'))
    titles = {'safe': '会自动勾选（放心清理）', 'review': '建议你看看', 'info': '仅供了解'}
    for n, (group, title) in enumerate(titles.items(), 1):
        rows = [i for i in plan['items'] if i['group'] == group]
        size = sum(i['size'] or 0 for i in rows)
        lines += ['', f'{"一二三"[n - 1]}、{title}  {len(rows)} 项  {size / GB:.2f} GB']
        if group == 'safe':
            for i in rows:
                where = show(i['paths'][0]) if i['paths'] else ''
                lines.append(f'  {_pts(i):>6} {i["title"]}  {(i["size"] or 0) / GB:.2f} GB  （{i["basis"][-1] if i.get("basis") else ""}）'
                             + (f'\n        {where}' if where else ''))
            continue
        cats: dict[str, list] = {}
        for i in rows:
            cats.setdefault(i['category'], []).append(i)
        for cat, items in cats.items():
            examples = '、'.join(f'{i["title"]}' + _pts(i)
                                + (f'（{i["blocked"]}）' if i.get('blocked') else '') for i in items[:3])
            lines.append(f'  {cat}：{len(items)} 项 {sum(i["size"] or 0 for i in items) / GB:.2f} GB  例：{examples}')
    lines += ['', f'四、值得注意（{len(findings)} 条）']
    if not findings:
        lines.append('  没有发现异常。')
    for f in findings:
        lines.append(('  ✖ 严重：' if f['level'] == 'severe' else '  ⚠ ') + f['text'])
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Read-only check-up of this PC (nothing is changed).')
    parser.add_argument('--ai', action='store_true', help='also ask the configured model (anonymised names are sent)')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD, help='勾选门槛 0-10 (default %(default)s)')
    parser.add_argument('--careful', action='store_true', help='same as --threshold 8 (稳妥)')
    parser.add_argument('--no-space', action='store_true', help='skip the system drive scan')
    parser.add_argument('--no-dups', action='store_true', help='skip the duplicate search')
    parser.add_argument('--out', help='also write the report to this file')
    parser.add_argument('--save', help='write a summary (JSON) to compare with later')
    parser.add_argument('--compare', help='compare with a summary written by --save')
    parser.add_argument('--full-paths', action='store_true', help='do not replace the user name in paths')
    parser.add_argument('--sandbox', help=argparse.SUPPRESS)  # run against a 演练环境 (tests of this tool)
    args = parser.parse_args(argv)

    from panxu.api import Backend, Bridge
    from panxu.app import default_data_dir
    from panxu.settings import Settings

    real_data = default_data_dir()
    tmp = tempfile.mkdtemp(prefix='panxu-check-')
    sb = None
    try:
        if args.sandbox:
            from panxu import sandbox
            sb = sandbox.Sandbox(args.sandbox)
            sandbox.activate(sb)
        backend = Backend(os.path.join(tmp, 'data'), sandbox=sb)
        if args.ai:
            real = Settings(real_data)
            ai = real.get()['ai']
            key = os.environ.get('PANXU_AI_KEY', '') or real.get_secret('ai_key:' + ai['preset'])
            if not key and ai.get('preset') != 'ollama':
                print('--ai: 盘序里没有保存 API Key', file=sys.stderr)
                return 2
            backend.settings.update({'ai': ai})
            backend.settings.set_secret('ai_key:' + ai['preset'], key)
        bridge = Bridge(backend)
        options = {'use_ai': bool(args.ai), 'space_scan': not args.no_space, 'duplicates': not args.no_dups,
                   'auto_threshold': 8 if args.careful else max(0, min(10, args.threshold))}
        started = time.time()
        r = bridge.call('checkup', {'options': options})
        if not r['ok']:
            print(r['error'], file=sys.stderr)
            return 2
        last = ''
        while True:
            s = bridge.call('job', {'job_id': r['data']})['data']
            stage = (s.get('progress') or {}).get('stage_label') or ''
            if stage and stage != last:
                print('…' + stage, file=sys.stderr, flush=True)
                last = stage
            if s['status'] != 'running':
                break
            time.sleep(0.3)
        if s['status'] != 'done':
            print(f'体检没有完成：{s.get("error") or s["status"]}', file=sys.stderr)
            return 2
        plan = s['result']
        env = backend.env
        findings = check_plan(plan, env, backend.insights, backend.junk_rows)
        drive = next((d for d in bridge.call('drives')['data'] if d['system']), None)
        report = render(plan, findings, env, time.time() - started, args.full_paths, drive, bool(plan['ai'].get('used')))
        if args.compare:
            with open(args.compare, encoding='utf-8') as fh:
                changes = compare(json.load(fh), summary(plan))
            report += '\n\n五、和上次相比\n' + ('\n'.join('  ' + c for c in changes) if changes else '  没有明显变化。')
        print(report)
        if args.out:
            with open(args.out, 'w', encoding='utf-8') as fh:
                fh.write(report + '\n')
        if args.save:
            with open(args.save, 'w', encoding='utf-8') as fh:
                json.dump(summary(plan), fh, ensure_ascii=False, indent=1)
        if any(f['level'] == 'severe' for f in findings):
            return 2
        return 1 if findings else 0
    finally:
        if sb is not None:
            from panxu import sandbox
            sandbox.deactivate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
