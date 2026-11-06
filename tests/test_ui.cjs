// node tests/test_ui.cjs — pure UI helpers (no DOM needed)
'use strict';
const assert = require('assert');
global.window = global;
const PX = require('../web/ui.js');

// Home-only defaults migrate without changing standalone tools; an empty disk selection stays empty.
const homeDisks = [{ root: 'C:\\', fixed: true, system: true }, { root: 'D:\\', fixed: true }, { root: 'F:\\', removable: true }];
assert.deepStrictEqual(PX.checkupDriveRoots(homeDisks), ['C:\\', 'D:\\']);
assert.deepStrictEqual(PX.checkupDriveRoots(homeDisks, { other_drives: false }), ['C:\\']);
assert.deepStrictEqual(PX.checkupDriveRoots(homeDisks, { selected_drives: ['d:/', 'F:\\', 'X:\\'] }), ['D:\\', 'F:\\']);
assert.deepStrictEqual(PX.checkupDriveRoots(homeDisks, { selected_drives: [] }), []);
const noAIScopes = Object.fromEntries(PX.checkupAIScopes.map(([key]) => [key, false]));
assert.strictEqual(PX.checkupAIEnabled({ ai_scopes: noAIScopes }), false);
assert.strictEqual(PX.checkupAIEnabled({ use_ai: false }), false);
assert.strictEqual(PX.checkupAIEnabled({ duplicates: false, ai_scopes: { ...noAIScopes, duplicates: true } }), false);
assert.strictEqual(PX.checkupAIEnabled({ ai_scopes: { ...noAIScopes, files: true } }), true);
// Coverage describes the completed run, with derived large-file results and partial startup coverage.
const coverage = PX.checkupCoverage({ options: { duplicates: false }, coverage: { space: null, startup: { count: 3 } } });
assert.strictEqual(coverage.find((r) => r.page === 'space').status, '未检查');
assert.deepStrictEqual(coverage.find((r) => r.page === 'space').extra, ['large', '大文件']);
assert.strictEqual(coverage.find((r) => r.page === 'dups').status, '未检查');
assert.strictEqual(coverage.find((r) => r.page === 'startup').status, '部分覆盖');
assert.strictEqual(coverage.find((r) => r.page === 'apps').status, '未覆盖');
const partial = PX.checkupCoverage({ options: { cross_drive_duplicates: false },
  personal_scan: { truncated: true }, coverage: { space: { incomplete: true }, duplicates: { truncated: true } } });
assert.strictEqual(partial.find((r) => r.page === 'space').status, '有跳过项');
assert.strictEqual(partial.find((r) => r.page === 'mine').status, '部分完成');
assert.strictEqual(partial.find((r) => r.page === 'dups').status, '部分完成');
assert.match(partial.find((r) => r.page === 'dups').note, /同一磁盘/);

// treemap: areas proportional, inside bounds, no overlap of total area
const items = [{ size: 60 }, { size: 25 }, { size: 10 }, { size: 4 }, { size: 1 }, { size: 0 }];
const rects = PX.treemap(items, 0, 0, 400, 300);
assert.strictEqual(rects.length, 5, 'zero-size items are skipped');
const area = rects.reduce((s, r) => s + r.w * r.h, 0);
assert.ok(Math.abs(area - 120000) < 1e-6, 'covers the whole box');
for (const r of rects) {
  assert.ok(r.x >= -1e-9 && r.y >= -1e-9 && r.x + r.w <= 400 + 1e-6 && r.y + r.h <= 300 + 1e-6, 'inside bounds');
  assert.ok(Math.abs((r.w * r.h) / 120000 - r.item.size / 100) < 1e-9, 'area proportional');
}
assert.deepStrictEqual(PX.treemap([], 0, 0, 10, 10), []);
assert.deepStrictEqual(PX.treemap([{ size: 5 }], 0, 0, 0, 10), []);

// formatting
assert.strictEqual(PX.fmt.bytes(0), '0 B');
assert.strictEqual(PX.fmt.bytes(1536), '2 KB');
assert.strictEqual(PX.fmt.bytes(5.25 * 1024 ** 3), '5.3 GB');
assert.strictEqual(PX.fmt.bytes(150 * 1024 ** 3), '150 GB');
assert.strictEqual(PX.fmt.bytes(null), '—');
assert.deepStrictEqual(PX.fmt.split(1024 ** 4 * 2), ['2.0', 'TB']);
assert.strictEqual(PX.fmt.seconds(75), '1 分 15 秒');
// Disk usage is distinct from content length, including a completely sparse (zero allocation) file.
assert.strictEqual(PX.sizeDifference({ size: 128 * 1024, logical_size: 62.5 * 1024 ** 3 }), '标称 62.5 GB，实际占用 128 KB');
assert.strictEqual(PX.sizeDifference({ size: 0, logical_size: 1024 ** 3 }), '标称 1.0 GB，实际占用 0 B');
assert.strictEqual(PX.sizeDifference({ size: 1024, logical_size: 1024 }), '');
assert.strictEqual(PX.sizeDifference({ size: 1024 }), '');
assert.strictEqual(PX.sizeDifference({ size: null, logical_size: 1024 }), '');
assert.strictEqual(PX.copySize({ size: 0 }, 1024 ** 3), 0, 'zero allocation must not fall back to logical size');
assert.strictEqual(PX.copySize({}, 1024), 1024, 'old records retain their original size');
assert.strictEqual(PX.duplicateWaste({ size: 1024 ** 3, files: [{ size: 128 * 1024 }, { size: 0 }, { size: 64 * 1024 }] }),
  64 * 1024, 'remaining duplicate copies count their own allocation');
// places under the user folder read like Explorer
const R = String.raw;
assert.strictEqual(PX.fmt.place(R`C:\Users\me\Documents\项目A`, R`C:\Users\me`), R`文档\项目A`);
assert.strictEqual(PX.fmt.place(R`c:\users\ME\Downloads`, R`C:\Users\me\ `.trim()), '下载');
assert.strictEqual(PX.fmt.place(R`C:\Users\me`, R`C:\Users\me`), '用户文件夹');
assert.strictEqual(PX.fmt.place(R`C:\Users\meme\x`, R`C:\Users\me`), R`C:\Users\meme\x`);
assert.strictEqual(PX.fmt.place(R`D:\资料`, R`C:\Users\me`), R`D:\资料`);
// 演练环境: simulated drive folders read as drive letters; real paths are untouched
PX.state.init = { drives: [{ root: R`C:\Temp\sb\C\ `.trim(), letter: 'C:' }, { root: R`D:\ `.trim() }] };
assert.strictEqual(PX.displayPath(R`C:\Temp\sb\C\Users\demo`), R`C:\Users\demo`);
assert.strictEqual(PX.displayPath(R`c:\temp\SB\c`), R`C:\ `.trim());
assert.strictEqual(PX.displayPath(R`C:\Temp\sb\Cx\y`), R`C:\Temp\sb\Cx\y`);
assert.strictEqual(PX.displayPath(R`D:\资料`), R`D:\资料`);
assert.strictEqual(PX.fmt.place(R`C:\Temp\sb\C\data`, R`C:\Temp\sb\C\Users\demo`), R`C:\data`);
// 建议清理度 shown as 0-10 points, rounded half up like the backend (checkup.points)
assert.strictEqual(PX.points(95), 10);
assert.strictEqual(PX.points(75), 8);
assert.strictEqual(PX.points(74), 7);
assert.strictEqual(PX.points(null), null);
assert.strictEqual(PX.scoreLevel(75), 'hi');
assert.strictEqual(PX.scoreLevel(74), 'mid');
assert.strictEqual(PX.scoreLevel(45), 'mid');
assert.strictEqual(PX.scoreLevel(44), 'lo');
PX.state.init = null;

// 按分数勾选: a threshold ticks eligible items by score; guarded items and (unless included) your files are left alone
const eight = { eligible: true, score: 80 }, six = { eligible: true, score: 60 }, mine = { personal: true, score: 90 },
  guarded = { eligible: false, score: 95 };
assert.deepStrictEqual([eight, six, mine, guarded].map((it) => PX.scoreTick(it, 7, false)), [true, false, null, null]);
assert.strictEqual(PX.scoreTick(mine, 7, true), true);
assert.deepStrictEqual([eight, six, mine, guarded].map((it) => PX.scoreTick(it, 'none', false)), [false, false, false, false]);
// the highlighted choice follows the ticks: the one picked, a threshold giving the same ticks, or none (by hand)
const plan = [eight, six, mine, guarded];
const ticks = (...on) => (it) => on.includes(it);
const ORDER = (picked) => [picked, 5, 'none', 9, 8, 7, 6, 5];
assert.strictEqual(PX.scoreMatch(plan, ticks(eight, six), false, ORDER(null)), 5, 'the check-up threshold');
assert.strictEqual(PX.scoreMatch(plan, ticks(eight), false, ORDER(7)), 7, 'the one just picked');
assert.strictEqual(PX.scoreMatch(plan, ticks(eight), false, ORDER(null)), 8, 'the first that gives the same ticks');
assert.strictEqual(PX.scoreMatch(plan, ticks(eight, guarded), false, ORDER(8)), 8, 'a guarded item ticked by hand does not count');
assert.strictEqual(PX.scoreMatch(plan, ticks(six), false, ORDER(8)), null, 'ticked by hand');
assert.strictEqual(PX.scoreMatch(plan, ticks(eight, six), true, ORDER(5)), null, 'your files included but not ticked');
assert.strictEqual(PX.scoreMatch(plan, ticks(), false, ORDER('none')), 'none');
assert.strictEqual(PX.scoreMatch(plan, ticks(), false, ORDER(8)), 'none', 'unticked by hand: 全不选, not ≥ 9 (which ticks nothing here either)');
assert.strictEqual(PX.scoreMatch(plan, ticks(), false, ORDER(9)), 9, 'but ≥ 9 stays highlighted when it was clicked');

// where a duplicate copy lives: the same words as duplicates.place_label
assert.strictEqual(PX.placeText({ place: 'project', place_root: R`E:\工程\萍萍瑜伽故事\代码工程` }), R`代码工程「萍萍瑜伽故事\代码工程」`);
assert.strictEqual(PX.placeText({ place: 'build', place_root: R`E:\游戏\ `.trim() }), '工程「游戏」的构建输出');
assert.strictEqual(PX.placeText({ place: 'program', place_root: R`D:\Tools\Snipaste` }), R`程序文件夹「Tools\Snipaste」`);
assert.strictEqual(PX.placeText({ place: 'near', place_root: R`E:\公用工程\萍萍瑜伽故事` }), R`「公用工程\萍萍瑜伽故事」里的代码工程旁边`);
assert.strictEqual(PX.placeText({ path: 'x' }), '');
const dupPy = require('fs').readFileSync(require('path').join(__dirname, '..', 'panxu', 'duplicates.py'), 'utf8');
for (const words of ['的构建输出', '里的代码工程旁边', "'程序文件夹'", "'代码工程'"]) assert.ok(dupPy.includes(words), 'python says it the same way: ' + words);

// reasons in an expanded row: evidence for cleaning, a caution, or the model's
for (const line of ['内置规则：公认可以重建的缓存和临时文件', '可能是旧版本留下的：3 年没有修改过', '旁边有更新的版本「IntelliJIdea2025.2」',
  '「已安装的应用」里找不到对应的程序', '内容逐字节相同', '保留一份，内容不会丢失', '随时可以重新启用']) assert.strictEqual(PX.basisKind(line), 'plus', line);
for (const line of ['最近 7 天内还有改动：可能还有程序在用', '里面有「Saved」文件夹：可能是存档、个人资料或备份', '属于仍在使用的程序「Adobe」：里面可能还有设置或许可证',
  '不在应用数据目录：只能由你决定', '没有经过 AI 复核', '里面有程序文件（.exe / .dll）：可能是程序本身，删掉后它就不能运行了',
  '「a.woff」被「src\\style.css」提到：删掉这份，用到它的地方很可能出问题', '内置规则，但删除后无法恢复：无法从回收站恢复']) assert.strictEqual(PX.basisKind(line), 'warn', line);
assert.strictEqual(PX.basisKind('AI：可清理（把握 86%）'), 'ai');

// 「打开」 is for documents; programs, installers, scripts and shortcuts would run
for (const p of [R`C:\a\report.PDF`, R`C:\a\movie.mkv`, R`C:\a\photos.zip`, R`C:\a\notes`, R`C:\a.b\readme.txt`]) assert.ok(PX.canOpen(p), p);
for (const p of [R`C:\a\setup.EXE`, R`C:\a\run.bat`, R`C:\a\x.ps1`, R`C:\a\app.lnk`, R`C:\a\pkg.msi`, R`C:\a\w.vbs`, R`C:\a\k.reg`]) {
  assert.ok(!PX.canOpen(p), p);
}
// the same list as the backend's api.RUNS_CODE
const api = require('fs').readFileSync(require('path').join(__dirname, '..', 'panxu', 'api.py'), 'utf8');
const pyList = api.slice(api.indexOf('RUNS_CODE = {'), api.indexOf('}', api.indexOf('RUNS_CODE = {'))).match(/'(\.[^']+)'/g).map((s) => s.slice(1, -1));
for (const ext of pyList) assert.ok(!PX.canOpen('x' + ext), 'web and api agree on ' + ext);

// one-line paths (PX.pathText): a fixed min-width on the head padded 「E:」 into a gap before the
// name, and a percentage cap on the tail cut short names inside the shrink-to-fit box
const css = require('fs').readFileSync(require('path').join(__dirname, '..', 'web', 'app.css'), 'utf8');
const rule = (sel) => (css.match(new RegExp('^\\' + sel + ' \\{([^}]*)\\}', 'm')) || [])[1] || '';
assert.ok(/min-width: calc-size\(max-content, min\(size, 3em\)\)/.test(rule('.pt-head')), 'the head keeps min(3em, itself)');
assert.ok(!/min-width: [\d.]+em/.test(rule('.pt-head')), 'no fixed min-width on the head');
assert.ok(rule('.pt-tail') && !/max-width: [^;]*%/.test(rule('.pt-tail')), 'no percentage cap on the name');
// the name's share of the shrink must round to zero in Blink's 1/64 px layout units, or its last letter is cut
assert.ok(Number((rule('.pt-head').match(/flex: 0 (\d+) auto/) || [])[1]) >= 1e6, 'the head gives way first, by a wide margin');
// A duplicate group on 首页 and its individual copies on the tool page share one selection.
const copies = { kind: 'dup', id: 'dup:sample', paths: ['D:\\copy.png', 'E:\\copy.png'] };
PX.sel.set(copies, true);
assert.ok(PX.sel.paths.has('D:\\copy.png') && PX.sel.paths.has('E:\\copy.png'));
PX.sel.paths.delete('D:\\copy.png');
assert.ok(!PX.sel.has(copies), 'a partial copy selection must not execute the whole home group');
PX.sel.paths.add('D:\\copy.png');
assert.ok(PX.sel.has(copies));
PX.sel.set(copies, false);
assert.ok(!PX.sel.paths.has('E:\\copy.png'));
console.log('ui tests passed');
