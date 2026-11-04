// node tests/test_ui.cjs — pure UI helpers (no DOM needed)
'use strict';
const assert = require('assert');
global.window = global;
const PX = require('../web/ui.js');

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

// 「打开」 is for documents; programs, installers, scripts and shortcuts would run
for (const p of [R`C:\a\report.PDF`, R`C:\a\movie.mkv`, R`C:\a\photos.zip`, R`C:\a\notes`, R`C:\a.b\readme.txt`]) assert.ok(PX.canOpen(p), p);
for (const p of [R`C:\a\setup.EXE`, R`C:\a\run.bat`, R`C:\a\x.ps1`, R`C:\a\app.lnk`, R`C:\a\pkg.msi`, R`C:\a\w.vbs`, R`C:\a\k.reg`]) {
  assert.ok(!PX.canOpen(p), p);
}
// the same list as the backend's api.RUNS_CODE
const api = require('fs').readFileSync(require('path').join(__dirname, '..', 'panxu', 'api.py'), 'utf8');
const pyList = api.slice(api.indexOf('RUNS_CODE = {'), api.indexOf('}', api.indexOf('RUNS_CODE = {'))).match(/'(\.[^']+)'/g).map((s) => s.slice(1, -1));
for (const ext of pyList) assert.ok(!PX.canOpen('x' + ext), 'web and api agree on ' + ext);
console.log('ui tests passed');
