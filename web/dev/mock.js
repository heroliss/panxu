/* Development-only fake backend so the UI can be previewed in a plain browser.
   Never bundled into the app (see tools/build_preview.py). */
(function () {
  'use strict';
  const GB = 1024 ** 3, MB = 1024 ** 2;
  const now = Date.now() / 1000;
  const day = 86400;

  const rules = [
    ['user_temp', '用户临时文件', 'system', '程序运行和安装时留下的临时文件。只删除 24 小时前的文件，正在使用的文件会自动跳过。', true, false, 2.31 * GB],
    ['windows_temp', 'Windows 临时文件', 'system', '系统服务和安装程序留下的临时文件。只删除 24 小时前的文件。', true, true, 0],
    ['wu_download', 'Windows 更新下载缓存', 'system', '已下载的更新安装包，安装完成后不再需要。若有未装完的更新，Windows 会重新下载。', true, true, 0],
    ['wer', 'Windows 错误报告', 'system', '程序出错时生成的报告，已发送或无需发送。', true, false, 182 * MB],
    ['crash_dumps', '崩溃转储文件', 'system', '程序或系统崩溃时保存的内存快照，只在排查故障时有用。', true, false, 640 * MB],
    ['inetcache', 'Windows 网页缓存', 'system', '系统网络组件（Office、部分旧程序）使用的网页缓存。', true, false, 96 * MB],
    ['thumbcache', '缩略图缓存', 'system', '资源管理器的图片缩略图缓存，删除后会重新生成。', false, false, 310 * MB],
    ['recycle_bin', '回收站', 'system', '清空所有磁盘的回收站。清空后无法再从回收站恢复。', false, false, 1.2 * GB],
    ['chrome', 'Google Chrome', 'browser', 'Google Chrome 的网页缓存和图形缓存。不影响书签、密码、历史记录和登录状态。', true, false, 1.46 * GB],
    ['edge', 'Microsoft Edge', 'browser', 'Microsoft Edge 的网页缓存和图形缓存。不影响书签、密码、历史记录和登录状态。', true, false, 720 * MB],
    ['vscode', 'Visual Studio Code', 'app', 'VS Code 的界面缓存、扩展安装包缓存和旧日志。不影响设置和扩展。', true, false, 410 * MB],
    ['shader_cache', '显卡着色器缓存', 'app', 'DirectX 和显卡驱动编译的着色器缓存。删除后游戏首次运行会重新编译，可能短暂卡顿。', false, false, 2.1 * GB],
    ['npm', 'npm 缓存', 'dev', 'Node.js 包下载缓存。删除后再次安装依赖时会重新下载。', false, false, 3.4 * GB],
    ['pip', 'pip 缓存', 'dev', 'Python 包下载缓存。', false, false, 880 * MB],
    ['gradle', 'Gradle 缓存', 'dev', 'Gradle 构建与依赖缓存。删除后下次构建会重新下载，耗时较长。', false, false, 0],
    ['windows_old', '旧版 Windows 安装', 'advanced', '大版本升级后保留的旧系统（Windows.old），用于回退。', false, true, 0],
    ['component_store', '组件存储清理（WinSxS）', 'advanced', '用系统自带的 DISM 删除已被新版本取代的系统组件。', false, true, null],
  ];
  const catalogue = rules.map(([id, name, group, description, def, admin]) => ({ id, name, group, description, default: def, admin,
    mode: id === 'recycle_bin' ? 'recycle_bin' : id === 'component_store' ? 'command' : 'files',
    note: id === 'recycle_bin' ? '清空后无法从回收站恢复文件' : id === 'windows_old' ? '删除后无法回退到之前的 Windows 版本' : '',
    slow: id === 'component_store' || id === 'windows_old', processes: id === 'edge' ? ['msedge.exe'] : [] }));

  // ---- fake folder tree
  const T = (name, size, kind, label, children, extra) => ({ name, size, kind, label, children: children || [], ...extra });
  const tree = T('C:\\', 389 * GB, null, null, [
    T('Users', 214 * GB, null, null, [
      T('demo', 211 * GB, null, null, [
        T('AppData', 96 * GB, 'container', null, [
          T('Local', 81 * GB, null, null, [
            T('Docker', 28.4 * GB, 'vdisk', 'Docker Desktop 数据'),
            T('JetBrains', 9.6 * GB, null, null, [T('IntelliJIdea2025.3', 7.1 * GB), T('Toolbox', 2.5 * GB)]),
            T('Temp', 2.4 * GB, 'cache', '可清理：用户临时文件', [
              T('sparse-demo.bin', 128 * 1024, 'file', null, [], { logical_size: 62.5 * GB }),
            ], { logical_size: 510 * GB }),
            T('Google', 3.9 * GB, 'appdata', 'Chrome 用户数据'),
            T('npm-cache', 3.4 * GB, 'cache', '可清理：npm 缓存'),
            T('Unity', 6.2 * GB),
            T('Packages', 11.8 * GB),
            T('ms-playwright', 2.6 * GB),
            T('Programs', 7.4 * GB, 'program', '仅为你安装的程序'),
          ]),
          T('Roaming', 14 * GB, null, null, [T('Tencent', 6.3 * GB), T('Code', 1.9 * GB), T('Adobe', 2.2 * GB)]),
          T('LocalLow', 1 * GB),
        ]),
        T('Documents', 52 * GB, 'personal', '文档', [T('WeChat Files', 31 * GB, 'personal', '微信聊天文件')]),
        T('Downloads', 38 * GB, 'personal', '下载'),
        T('Pictures', 12 * GB, 'personal', '图片'),
        T('Videos', 8 * GB, 'personal', '视频'),
        T('.gradle', 3.1 * GB, 'dev', '命令行工具缓存'),
      ]),
      T('Public', 3 * GB),
    ]),
    T('Windows', 38 * GB, 'system', 'Windows 系统目录', [T('WinSxS', 11 * GB, 'system', '组件存储（WinSxS）'), T('System32', 9 * GB, 'system', 'Windows 系统目录'), T('Installer', 4.8 * GB, 'system', '安装程序缓存')]),
    T('Program Files', 52 * GB, 'program', '已安装的程序'),
    T('Program Files (x86)', 31 * GB, 'program', '已安装的程序（32 位）'),
    T('ProgramData', 18 * GB, null, null, [T('Package Cache', 6.1 * GB, 'program', '安装包缓存'), T('NVIDIA Corporation', 2.9 * GB), T('Microsoft', 5.3 * GB, 'system', 'Windows 与微软组件数据')]),
    T('hiberfil.sys', 12.7 * GB, 'file'),
    T('pagefile.sys', 16 * GB, 'file'),
  ]);
  (function logicalSizes(n) {
    for (const child of n.children) logicalSizes(child);
    if (n.logical_size === undefined) n.logical_size = n.size + n.children.reduce((sum, child) => sum + child.logical_size - child.size, 0);
  })(tree);
  const nodes = [];
  (function index(n, parent, path) {
    n.id = nodes.length; n.parent = parent; n.path = path; nodes.push(n);
    if (n.kind === 'file') return;
    for (const c of n.children) index(c, n, path.replace(/\\$/, '') + '\\' + c.name);
  })(tree, null, 'C:\\');
  const dirs = nodes.filter((n) => n.kind !== 'file');
  // a long explanation, as the knowledge base has for system folders (the detail card must not scroll for it)
  const ADVICE = { Windows: 'Windows 系统目录，包含系统组件、驱动和更新留下的备份。不要手动删除里面的任何东西：更新缓存、临时文件和组件存储'
    + '可以在「垃圾清理」里用对应的规则安全清理，旧的系统组件用「组件存储清理（WinSxS）」交给 Windows 自己处理。' };
  const note = (n) => n.label ? { kind: n.kind, label: n.label, advice: ADVICE[n.name] || '', rule: '', inherited: false } : null;
  let aiResults = {};
  // #multi: the check-up's scan of several disks, under a 「本机磁盘」 node that has no path of its own
  const multi = location.hash.includes('multi');
  const row = (n) => ({ id: n.id, name: n.parent ? n.name : multi ? '本机磁盘' : n.path, path: !n.parent && multi ? '' : n.path,
    virtual: !n.parent && multi, type: 'dir', size: n.size, logical_size: n.logical_size, files: Math.round(n.size / (3 * MB)) + 7,
    dirs: n.children.length, mtime: now - (n.id % 9) * 40 * day, has_children: n.children.length > 0, denied: n.name === 'Windows', links: false, cloud: false,
    note: note(n), ai: aiResults[n.id], blocked: n.parent && !n.parent.parent ? '这是 Windows 系统文件或目录' : null });

  // ---- jobs
  const jobs = {};
  function job(steps, result, progressFn) {
    const id = 'j' + Math.random().toString(36).slice(2, 8);
    jobs[id] = { i: 0, steps, result, progressFn };
    return id;
  }
  function status(id) {
    const j = jobs[id];
    if (!j) return { status: 'missing' };
    if (j.snapshot && j.snapshot.status !== 'running') return j.snapshot;
    j.i += 1;
    if (j.cancel) return j.snapshot = { id, kind: j.kind, status: 'cancelled' };
    if (j.i < j.steps) return j.snapshot = { id, kind: j.kind, status: 'running', progress: j.progressFn ? j.progressFn(j.i / j.steps) : {} };
    return j.snapshot = { id, kind: j.kind, status: 'done', result: typeof j.result === 'function' ? j.result() : j.result };
  }

  const settings = { theme: 'system', ai: { preset: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', protocol: 'openai',
    json_mode: true, thinking: 'off', anonymize_paths: true, send_file_content: false, max_requests: 8, min_dir_mb: 200, timeout: 120 },
  checkup: { space_scan: true, other_drives: true, selected_drives: null, duplicates: true, cross_drive_duplicates: true, apps_services: true,
    use_ai: true, ai_scopes: { space: true, files: true, duplicates: true, startup: true, apps: true, services: true }, auto_threshold: 7 },
  insights: { stale_days: 365, large_mb: 100, download_days: 180 },
  duplicates: { min_size_mb: 1 }, large_files: { min_size_mb: 100 }, excluded_paths: [], extra_personal_paths: ['D:\\资料'] };
  let hasKey = true;

  const startup = [
    ['hkcu_run:OneDrive', 'Microsoft OneDrive', 'Microsoft Corporation', '注册表（当前用户）', '"C:\\Program Files\\Microsoft OneDrive\\OneDrive.exe" /background', true],
    ['hkcu_run:WeChat', '微信', 'Tencent', '注册表（当前用户）', '"C:\\Program Files\\Tencent\\WeChat\\WeChat.exe" -autorun', true],
    ['hklm_run:SecurityHealth', 'Windows Security notification icon', 'Microsoft Corporation', '注册表（所有用户）', '%windir%\\system32\\SecurityHealthSystray.exe', true],
    ['hkcu_run:Steam', 'Steam', 'Valve Corporation', '注册表（当前用户）', '"C:\\Program Files (x86)\\Steam\\steam.exe" -silent', true],
    ['hklm_run:AdobeAAMUpdater', 'Adobe Updater Startup Utility', 'Adobe Inc.', '注册表（所有用户）', '"C:\\Program Files (x86)\\Common Files\\Adobe\\OOBE\\UpdaterStartupUtility.exe"', false],
    ['user_folder:Snipaste.lnk', 'Snipaste', 'Le Liu', '启动文件夹（当前用户）', '"C:\\Tools\\Snipaste\\Snipaste.exe"', true],
  ].map(([id, display_name, publisher, source_label, command, enabled]) => ({ id, display_name, name: id.split(':')[1], publisher, source_label, command,
    exe: command.replace(/^"([^"]+)".*$/, '$1'), exists: true, enabled, needs_admin: id.startsWith('hklm'), location: '' }));
  const services = [
    ['AdobeARMservice', 'Adobe Acrobat Update Service', 'Adobe 产品自动更新', 'running', 'auto', 'Adobe Inc.', 'third_party'],
    ['GoogleUpdaterService', 'Google Updater Service', '保持 Google 软件为最新版本', 'stopped', 'auto', 'Google LLC', 'third_party'],
    ['NVDisplay.ContainerLocalSystem', 'NVIDIA Display Container LS', '显示驱动的容器服务', 'running', 'auto', 'NVIDIA Corporation', 'third_party'],
    ['TeamViewer', 'TeamViewer', '远程控制', 'running', 'auto', 'TeamViewer Germany GmbH', 'third_party'],
    ['wuauserv', 'Windows Update', '启用检测、下载和安装 Windows 更新', 'running', 'manual', 'Microsoft Corporation', 'windows'],
    ['Spooler', 'Print Spooler', '打印后台处理', 'running', 'auto', 'Microsoft Corporation', 'windows'],
  ].map(([name, display_name, description, st, start, publisher, kind]) => ({ name, display_name, description, status: st,
    status_label: st === 'running' ? '正在运行' : '已停止', start_type: start, start_label: { auto: '自动', manual: '手动', disabled: '禁用', delayed: '自动（延迟启动）' }[start],
    binpath: 'C:\\Program Files\\' + name + '\\service.exe', exe: 'C:\\Program Files\\' + name + '\\service.exe', publisher, kind, editable: kind === 'third_party', needs_admin: true }));
  const history = [
    // PanXu was closed (or killed) half way through this one
    { id: 'h0', time: now - 600, kind: 'delete', title: '永久删除 2400 项', freed: 1.6 * GB, items: ['C:\\Users\\demo\\Downloads\\旧素材\\clip_0001.mp4',
      'C:\\Users\\demo\\Downloads\\旧素材\\clip_0002.mp4'], item_count: 2400, failures: [], failure_count: 0, details: { pending: true },
    state: 'interrupted', done_count: 1370 },
    { id: 'h1', time: now - 3600, kind: 'junk', title: '垃圾清理', freed: 3.2 * GB, items: [{ name: '用户临时文件', freed: 2.1 * GB, files: 4211, failed: 37, skipped: '' }, { name: 'Google Chrome', freed: 1.1 * GB, files: 9120, failed: 0, skipped: '' }], item_count: 2, failures: [], failure_count: 37 },
    { id: 'h2', time: now - 7200, kind: 'startup', title: '禁用开机启动：Adobe Updater Startup Utility', freed: 0, items: [{ name: 'Adobe Updater', command: 'UpdaterStartupUtility.exe' }], item_count: 1, failures: [], failure_count: 0, revert: { action: 'startup' } },
    { id: 'h3', time: now - 86400, kind: 'recycle', title: '移到回收站 3 项', freed: 9.4 * GB, items: ['C:\\Users\\demo\\Downloads\\ubuntu-24.04-desktop-amd64.iso', 'C:\\Users\\demo\\Downloads\\VSCodeUserSetup-x64.exe'], item_count: 3, failures: [], failure_count: 0 },
    { id: 'h4', time: now - 90000, kind: 'ai', title: 'AI 分析 14 个目录', freed: 0, items: [], item_count: 0, failures: [], failure_count: 0, details: { usage: { requests: 2, input_tokens: 5120, output_tokens: 1430 } } },
  ];
  const largeFiles = [
    ['C:\\Users\\demo\\Downloads\\ubuntu-24.04-desktop-amd64.iso', 5.9 * GB, 400, 'disk', '磁盘镜像'],
    ['C:\\Users\\demo\\Videos\\2025 年会录像.mp4', 4.2 * GB, 300, 'video', '视频'],
    ['C:\\pagefile.sys', 16 * GB, 1, 'other', '其他', '这是 Windows 系统文件或目录'],
    ['C:\\hiberfil.sys', 12.7 * GB, 1, 'other', '其他', '这是 Windows 系统文件或目录'],
    ['C:\\Users\\demo\\AppData\\Local\\Docker\\wsl\\disk\\docker_data.vhdx', 27.9 * GB, 2, 'disk', '磁盘镜像'],
    ['C:\\Users\\demo\\Downloads\\Win11_24H2_Chinese_Simplified_x64.iso', 5.4 * GB, 620, 'disk', '磁盘镜像'],
    ['C:\\Users\\demo\\Documents\\backup-2023.zip', 3.1 * GB, 800, 'archive', '压缩包'],
    ['C:\\Users\\demo\\Downloads\\VisualStudioSetup.exe', 1.2 * GB, 260, 'installer', '安装包'],
    ['C:\\Users\\demo\\Downloads\\PS2024.rar', 2.6 * GB, 500, 'archive', '压缩包'],
  ].map(([path, size, age, category, category_label, blocked]) => ({ path, name: path.split('\\').pop(), size, mtime: now - age * day, category, category_label, blocked: blocked || null, note: null }))
    .sort((a, b) => b.size - a.size);
  largeFiles.find((f) => f.path.endsWith('docker_data.vhdx')).logical_size = 128 * GB;

  const suggestions = () => [
    { node_id: 12, path: 'C:\\Users\\demo\\AppData\\Local\\Unity', size: 6.2 * GB, files: 18000, verdict: 'clean', verdict_label: '可清理', app: 'Unity 编辑器', reason: 'Unity 的资源导入与着色器缓存，打开项目时会自动重建', effect: '下次打开项目时重新导入，耗时较长', confidence: 0.86, source: 'ai', expanded: false, mtime: now - 200 * day,
      score: 86, selected: true, basis: ['AI：可清理（把握 86%）', '自动勾选：AI 判断可清理（把握 86%），不属于仍在使用的程序，最近也没有改动，9 分达到勾选门槛（7 分）'] },
    { node_id: 999, path: 'C:\\Users\\demo\\AppData\\Local\\Adobe', size: 3.4 * GB, files: 7400, verdict: 'clean', verdict_label: '可清理', app: 'Adobe Photoshop', reason: '大部分是媒体缓存和字体缓存', effect: '下次打开时重新生成缓存', confidence: 0.9, source: 'ai', expanded: false, mtime: now - 3 * day,
      score: 70, selected: false, basis: ['AI：可清理（把握 90%）', '属于仍在使用的程序「Adobe Photoshop 2025」：里面可能还有设置或许可证', '没有自动勾选：属于仍在使用的程序「Adobe Photoshop 2025」'] },
    { node_id: 14, path: 'C:\\Users\\demo\\AppData\\Local\\ms-playwright', size: 2.6 * GB, files: 3100, verdict: 'clean', verdict_label: '可清理', app: 'Playwright', reason: '自动化测试下载的浏览器内核，需要时可重新安装', effect: '运行测试前需执行 playwright install', confidence: 0.8, source: 'ai', expanded: false, mtime: now - 420 * day,
      score: 80, selected: true, basis: ['AI：可清理（把握 80%）', '自动勾选：AI 判断可清理（把握 80%），不属于仍在使用的程序，最近也没有改动，8 分达到勾选门槛（7 分）'] },
    { node_id: 10, path: 'C:\\Users\\demo\\AppData\\Local\\JetBrains\\IntelliJIdea2025.3', size: 7.1 * GB, files: 51000, verdict: 'review', verdict_label: '需要你确认', app: 'IntelliJ IDEA', reason: '含索引缓存和本地历史；若已不用这个版本可整体删除', effect: '重新打开会重建索引', confidence: 0.62, source: 'ai', expanded: false, caution: null, mtime: now - 90 * day,
      score: 50, selected: false, basis: ['AI：需要你确认（把握 62%）', '没有自动勾选：AI 也拿不准'] },
    { node_id: 17, path: 'C:\\Users\\demo\\AppData\\Roaming\\Tencent', size: 6.3 * GB, files: 9200, verdict: 'keep', verdict_label: '建议保留', app: 'QQ / 微信', reason: '聊天记录数据库与账号配置', effect: '', confidence: 0.9, source: 'cache', expanded: false, caution: '位于应用配置目录，可能包含应用设置或账号数据',
      score: null, selected: false, basis: ['AI：建议保留（把握 90%）'] },
    { node_id: 998, path: 'D:\\Temp', size: 2.4 * GB, files: 46073, verdict: 'clean', verdict_label: '可以清理', app: '临时文件夹',
      reason: '名字叫 Temp 的文件夹按惯例只放临时文件，程序或你自己用完就不再需要。看一眼里面没有要留的，就可以删除；正在被程序使用的文件会删不掉，跳过即可。',
      effect: '', confidence: null, source: 'knowledge', expanded: false,
      score: 60, selected: false, basis: ['盘序认得：临时文件夹，没有问 AI', '没有自动勾选：里面也可能有别人临时放的东西，请你看一眼再决定'] },
  ];

  // ---- 1.1: check-up plan, 我的文件, installed programs, sandbox
  const U = 'C:\\Users\\demo';
  const P = (o) => Object.assign({ group: 'review', count: 0, what: '', why: '', effect: '', action: 'recycle', source: 'heuristic',
    confidence: null, selected: false, paths: [], blocked: null, caution: null, status: 'pending', ai: null }, o);
  const planItems = [
    P({ id: 'rule:user_temp', group: 'safe', category: '垃圾文件', title: '用户临时文件', size: 2.31 * GB, logical_size: 510 * GB, count: 9120, action: 'delete', source: 'rule', selected: true,
      why: rules[0][3], locations: [U + '\\AppData\\Local\\Temp'] }),
    P({ id: 'rule:chrome', group: 'safe', category: '垃圾文件', title: 'Google Chrome', size: 1.46 * GB, action: 'delete', source: 'rule', selected: true, why: rules[8][3] }),
    P({ id: 'rule:crash_dumps', group: 'safe', category: '垃圾文件', title: '崩溃转储文件', size: 640 * MB, action: 'delete', source: 'rule', selected: true, why: '程序崩溃时保存的内存快照，只在排查故障时有用。' }),
    P({ id: 'dir:unity', group: 'safe', category: '应用数据', title: 'Unity 编辑器', size: 6.2 * GB, source: 'ai', confidence: 0.86, selected: true,
      why: 'Unity 的资源导入与着色器缓存，打开项目时会自动重建', effect: '下次打开项目时重新导入，耗时较长', paths: [U + '\\AppData\\Local\\Unity'] }),
    P({ id: 'rule:edge', category: '垃圾文件', title: 'Microsoft Edge', size: 720 * MB, action: 'delete', source: 'rule', blocked: '请先关闭 msedge.exe', why: '盘序内置的清理规则。' }),
    P({ id: 'rule:npm', category: '垃圾文件', title: 'npm 缓存', size: 3.4 * GB, action: 'delete', source: 'rule', why: 'Node.js 包下载缓存。删除后再次安装依赖时会重新下载。' }),
    P({ id: 'dir:idea21', category: '应用数据', title: 'IntelliJIdea2021.1', size: 1.6 * GB, confidence: 0.6,
      why: '可能是旧版本留下的：3 年没有修改过。', effect: '如果还在用旧版本，它的设置和缓存会丢失。', paths: [U + '\\AppData\\Local\\JetBrains\\IntelliJIdea2021.1'] }),
    P({ id: 'dir:oldgame', category: '应用数据', title: 'OldGameStudio', size: 1.1 * GB, confidence: 0.5,
      why: '在「已安装的应用」里没有找到对应的程序，而且 2 年没有修改过。', effect: '如果程序其实还在（例如绿色版），它的设置、存档或缓存会丢失。',
      paths: [U + '\\AppData\\Local\\OldGameStudio'], caution: '位于本地应用数据目录，可能是程序正在使用的数据' }),
    P({ id: 'file:iso', category: '很久没动过的大文件', title: 'Ubuntu-24.04.1-desktop-amd64.iso', size: 5.9 * GB, count: 1, source: 'insight', confidence: 0.4, personal: true,
      advice: '这些大文件很久没有修改过。不需要的可以删除；珍贵的照片视频建议移到移动硬盘或网盘。', why: '1.9 年没有修改过，系统镜像可以随时重新下载', paths: [U + '\\Downloads\\Ubuntu-24.04.1-desktop-amd64.iso'],
      ai: { verdict: 'delete', verdict_label: '可以删除', what: 'Ubuntu 安装镜像', reason: '可以从官网重新下载' } }),
    P({ id: 'file:wechat', category: '已安装程序的安装包', title: 'WeChatSetup_3.9.12.17.exe', size: 186 * MB, count: 1, source: 'insight', confidence: 0.85, personal: true,
      why: '已安装：微信', paths: [U + '\\Downloads\\WeChatSetup_3.9.12.17.exe'] }),
    P({ id: 'file:vscode', category: '已安装程序的安装包', title: 'VSCodeUserSetup-x64-1.95.3.exe', size: 98 * MB, count: 1, source: 'insight', confidence: 0.85, personal: true,
      why: '已安装：Microsoft Visual Studio Code (User)', paths: [U + '\\Downloads\\VSCodeUserSetup-x64-1.95.3.exe'] }),
    P({ id: 'file:crdl', category: '没有下载完的文件', title: '电影.mkv.crdownload', size: 702 * MB, count: 1, source: 'insight', confidence: 0.9, personal: true,
      why: '30 天前中断', paths: [U + '\\Downloads\\电影.mkv.crdownload'] }),
    P({ id: 'file:thesis', category: '同一文件的多个版本', title: '毕业论文_最终版 - 副本.docx', size: 1.7 * MB, count: 1, source: 'insight', confidence: 0.85, personal: true,
      why: '和「毕业论文_最终版.docx」内容完全相同', paths: [U + '\\Documents\\毕业论文_最终版 - 副本.docx'] }),
    P({ id: 'dup:1', category: '重复文件', title: '截图 2026-03-01 101530.png 等 3 份', size: 3 * MB, count: 1, source: 'dup', confidence: 0.9, personal: true,
      why: '保留「Desktop」里的那份，其余 1 份移到回收站', keep: U + '\\Desktop\\截图 2026-03-01 101530.png',
      paths: [U + '\\Pictures\\截图 2026-03-01 101530.png'], dup_size: 3 * MB,
      dup_files: [{ path: U + '\\Desktop\\截图 2026-03-01 101530.png', mtime: now - 40 * day }, { path: U + '\\Pictures\\截图 2026-03-01 101530.png', mtime: now - 30 * day },
        { path: U + '\\Downloads\\截图 2026-03-01 101530 (1).png', mtime: now - 20 * day }] }),
    P({ id: 'startup:qv', category: '开机启动', title: '快看图大师 资讯助手', action: 'disable', source: 'ai', confidence: 0.7, what: '看图软件附带的资讯弹窗程序',
      why: 'AI 建议禁用：开机弹出广告和资讯，禁用不影响看图' }),
    P({ id: 'app:a4', kind: 'app', app_id: 'a4', category: '可以卸载的程序', title: '快看图大师', size: 780 * MB, action: 'uninstall', source: 'ai',
      what: '看图软件，附带资讯弹窗', why: 'AI 建议卸载：常驻弹出广告资讯，可换用系统自带的照片应用', location: 'C:\\Program Files\\快看图大师',
      effect: '它的设置、存档或登录信息可能随卸载一起删除，或留在应用数据里。' }),
    P({ id: 'app:a5', kind: 'app', app_id: 'a5', category: '可以卸载的程序', title: 'Hollow Knight', size: 9.1 * GB, action: 'uninstall', source: 'heuristic',
      why: '1 年没有运行过，占用 9.1 GB', location: 'C:\\Program Files\\Hollow Knight' }),
    P({ id: 'service:AdobeARMservice', kind: 'service', service_name: 'AdobeARMservice', category: '服务', title: 'Adobe Acrobat Update Service',
      action: 'service', source: 'ai', what: 'Adobe 自动更新服务', why: 'AI 建议开机时不再自动运行：设为手动后仍可在应用内更新',
      effect: '这次不会停止正在运行的服务，下次开机起不再自动启动。', location: 'C:\\Program Files\\AdobeARMservice\\service.exe' }),
    P({ id: 'info:hiber', group: 'info', category: '说明', title: '休眠文件', size: 12.7 * GB, action: 'none', source: 'knowledge', location: 'C:\\hiberfil.sys',
      advice: '用于休眠和快速启动。不需要这两项时，可用管理员命令 powercfg /h off 关闭，文件会自动消失。', why: '体积较大，但不能直接删除。',
      actions: [{ kind: 'hibernate', label: '关闭休眠，删除这个文件' }] }),
    P({ id: 'info:docker', group: 'info', category: '说明', title: 'Docker Desktop 数据', size: 28.4 * GB, action: 'none', source: 'knowledge', location: U + '\\AppData\\Local\\Docker',
      advice: '镜像和容器存放在虚拟磁盘里。先用 docker system prune 清理，再在 Docker 设置中压缩磁盘。', why: '体积较大，但不能直接删除。',
      actions: [{ kind: 'copy', arg: 'docker system prune', label: '复制清理命令' }, { kind: 'reveal', label: '打开文件夹' }] }),
  ];
  const SCORES = { 'rule:user_temp': 95, 'rule:chrome': 75, 'rule:crash_dumps': 70, 'dir:unity': 70, 'rule:edge': 75, 'rule:npm': 70,
    'dir:idea21': 60, 'dir:oldgame': 30, 'file:iso': 30, 'file:wechat': 85, 'file:vscode': 85, 'file:crdl': 90, 'file:thesis': 85,
    'dup:1': 85, 'startup:qv': 60, 'app:a4': 60, 'app:a5': 40, 'service:AdobeARMservice': 60 };
  const HOLD = { 'dir:oldgame': '找不到对应的程序，但不确定是不是绿色版', 'startup:qv': '开机启动项由你决定',
    'app:a4': '卸载要运行它自己的卸载程序，请你逐个确认', 'app:a5': '卸载要运行它自己的卸载程序，请你逐个确认',
    'service:AdobeARMservice': '服务由你决定：改为手动会改变程序的行为' };
  for (const it of planItems) {
    if (it.action === 'none') { it.score = null; it.basis = []; continue; }
    it.score = SCORES[it.id] || 50;
    it.kind = it.kind || (it.id.startsWith('rule:') ? 'rule' : it.id.startsWith('dup:') ? 'dup' : it.id.startsWith('startup:') ? 'startup' : 'paths');
    if (it.kind === 'rule') it.rule = it.id.slice(5);
    it.eligible = !it.personal && !HOLD[it.id];
    it.hold_reason = it.personal ? '你的个人文件只由你决定' : HOLD[it.id] || null;
    it.auto_reason = it.source === 'rule' ? (it.score >= 90 ? '默认开启的内置规则' : '可以重建的缓存') : it.source === 'ai' ? 'AI 判断可以清理' : '盘序的判断';
  }
  // the same rule as checkup.PlanBuilder: eligible, not blocked, and at least the threshold
  const decide = (items, t) => {
    for (const it of items) {
      if (it.action === 'none') continue;
      const pts = PX.points(it.score);
      it.selected = it.eligible && !it.blocked && pts >= t;
      it.group = it.selected ? 'safe' : 'review';
      const note = it.blocked ? '没有勾选：' + it.blocked
        : it.selected ? `自动勾选：${it.auto_reason}，${pts} 分达到勾选门槛（${t} 分）`
        : it.hold_reason ? '没有自动勾选：' + it.hold_reason : `没有自动勾选：${pts} 分，低于勾选门槛（${t} 分）`;
      it.basis = [it.source === 'ai' ? `AI：可清理（把握 ${Math.round((it.confidence || 0) * 100)}%）` : it.why, note];
    }
    return items;
  };
  const planGroups = [{ id: 'safe', name: '放心清理' }, { id: 'review', name: '建议你看看' }, { id: 'info', name: '仅供了解' }];
  let plan = null;
  let startupAI = {};
  const summarize = () => Object.fromEntries(planGroups.map(({ id }) => {
    const rows = plan.items.filter((i) => i.group === id);
    const sel = rows.filter((i) => i.selected && i.status === 'pending');
    return [id, { count: rows.length, size: rows.reduce((s, i) => s + (i.size || 0), 0), selected: sel.length, selected_size: sel.reduce((s, i) => s + (i.size || 0), 0) }];
  }));
  const stages = [['junk', '扫描垃圾文件'], ['space', '分析本机磁盘空间'], ['insights', '整理我的文件'], ['dups', '查找重复文件'], ['apps', '检查应用与服务'], ['ai', 'AI 复核'], ['plan', '生成清理方案']]
    .map(([id, label]) => ({ id, label }));

  const F = (path, size, age, note, remove, extra) => Object.assign({ path, name: path.split('\\').pop(), size, mtime: now - age * day, note, remove,
    category: 'other', ai: null, blocked: null }, extra || {});
  const findings = [
    { id: 'incomplete', kind: 'incomplete_download', kind_label: '没有下载完的文件', advice: '浏览器或下载工具中断后留下的半成品，无法直接打开。确认不再继续下载后可以删除。',
      title: '2 个没有下载完的文件', reason: '扩展名表明下载没有完成，而且已经超过 3 天没有继续。', confidence: 0.9,
      files: [F(U + '\\Downloads\\电影.mkv.crdownload', 702 * MB, 30, '30 天前中断', true, { logical_size: 20 * GB }), F(U + '\\Downloads\\年度报告.pdf.part', 12 * MB, 45, '45 天前中断', true)] },
    { id: 'installers', kind: 'installer_installed', kind_label: '已安装程序的安装包', advice: '程序已经装好，安装包一般用不到了。需要重装时可以去官网下载最新版。',
      title: '3 个已安装程序的安装包', reason: '安装包对应的程序在「已安装的应用」中能找到。', confidence: 0.85,
      files: [F(U + '\\Downloads\\WeChatSetup_3.9.12.17.exe', 186 * MB, 200, '已安装：微信', true, { ai: { verdict: 'delete', verdict_label: '可以删除', what: '微信安装包', reason: '微信已安装，安装包不再需要' } }),
        F(U + '\\Downloads\\FooSoft_Setup_2.3.0.msi', 141 * MB, 180, '已安装：FooSoft 设计工具 2.3', true),
        F(U + '\\Downloads\\QuickView_Setup_v5.2.exe', 61 * MB, 410, '已安装：快看图大师', true)] },
    { id: 'ver-thesis', kind: 'name_versions', kind_label: '同一文件的多个版本', advice: '名字相近、像是同一份文件的不同版本或副本。通常只需要保留最新、最完整的一份。',
      title: '「毕业论文_最终版 - 副本」等 5 个版本', reason: '5 个文件名字相近，像是「毕业论文」的不同版本，其中 2 个与另一份内容完全相同。', confidence: 0.85,
      files: [F(U + '\\Documents\\毕业论文_最终版 - 副本.docx', 1.7 * MB, 860, '最新的一份（修改时间最晚）', false, { ai: { verdict: 'keep', verdict_label: '建议保留', what: '论文定稿', reason: '最终版，建议保留并备份' } }),
        F(U + '\\Documents\\毕业论文_最终版.docx', 1.7 * MB, 860, '与其他副本内容相同，保留这一份', false),
        F(U + '\\Documents\\毕业论文_修改版.docx', 1.5 * MB, 870, '较旧的版本', false),
        F(U + '\\Documents\\毕业论文(1).docx', 1.4 * MB, 899, '和「毕业论文.docx」内容完全相同', true),
        F(U + '\\Documents\\毕业论文.docx', 1.4 * MB, 900, '较旧的版本', false)] },
    { id: 'stale', kind: 'stale_large', kind_label: '很久没动过的大文件', advice: '这些大文件很久没有修改过。不需要的可以删除；珍贵的照片视频建议移到移动硬盘或网盘。',
      title: '2 个很久没动过的大文件', reason: '大于 100 MB，且超过 365 天没有修改。', confidence: 0.4,
      files: [F(U + '\\Downloads\\Ubuntu-24.04.1-desktop-amd64.iso', 5.9 * GB, 700, '1.9 年没有修改过，系统镜像可以随时重新下载', false),
        F(U + '\\Videos\\2019 家庭聚会.mp4', 2.4 * GB, 2400, '6.6 年没有修改过，如果是珍贵的视频可以移到移动硬盘', false, { category: 'video' })] },
    { id: 'empty', kind: 'empty_folders', kind_label: '空文件夹', advice: '这些文件夹里什么都没有，删除不会丢失任何内容。', title: '2 个空文件夹',
      reason: '里面没有任何文件（包括子文件夹）。', confidence: 0.95,
      files: [F(U + '\\Desktop\\新建文件夹', 0, 0, '空文件夹', true, { mtime: 0, category: 'folder' }), F(U + '\\Desktop\\新建文件夹 (2)', 0, 0, '空文件夹', true, { mtime: 0, category: 'folder' })] },
  ].map((f) => Object.assign(f, { count: f.files.length, size: f.files.reduce((s, x) => s + x.size, 0),
    logical_size: f.files.reduce((s, x) => s + (x.logical_size === undefined ? x.size : x.logical_size), 0) }));
  const KIND_SCORE = { incomplete_download: 90, installer_installed: 85, name_versions: 35, stale_large: 30, empty_folders: 30 };
  for (const f of findings) {
    for (const x of f.files) {
      x.score = f.kind === 'name_versions' ? (x.note.startsWith('最新') || x.note.startsWith('与其他') ? 5 : x.remove ? 85 : 35) : KIND_SCORE[f.kind];
      // like insights.with_ai: an older version that differs is raised only by a model that saw its content
      const nameOnly = ['name_versions', 'stale_large', 'old_downloads', 'archive_extracted'].includes(f.kind) && !x.remove && !(x.ai && x.ai.with_content);
      if (x.ai && x.ai.verdict === 'delete' && !nameOnly) x.score = Math.max(x.score, Math.min(75, x.score + (x.ai.with_content ? 20 : 10)));
      if (x.ai && x.ai.verdict === 'keep') x.score = Math.min(x.score, 15);
    }
  }
  let insights = null;
  const personalRoots = [U + '\\Desktop', U + '\\Documents', U + '\\Downloads', U + '\\Pictures', U + '\\Videos', U + '\\Music',
    'D:\\资料', U, 'C:\\Users\\Public', 'C:\\资料', 'D:\\', 'E:\\'];
  function personalResult(roots, cloudSkipped = 0) {
    const chosen = Array.isArray(roots) ? roots : personalRoots;
    const inScope = (path) => chosen.some((folder) => path.toLowerCase() === folder.toLowerCase()
      || path.toLowerCase().startsWith(folder.replace(/\\$/, '').toLowerCase() + '\\'));
    const rows = Array.isArray(roots) ? findings.map((finding) => {
      const files = finding.files.filter((file) => inScope(file.path));
      return { ...finding, files, count: files.length, size: files.reduce((sum, file) => sum + file.size, 0),
        logical_size: files.reduce((sum, file) => sum + (file.logical_size === undefined ? file.size : file.logical_size), 0) };
    }).filter((finding) => finding.files.length) : findings;
    return { findings: rows, stats: { files: Array.isArray(roots) ? 120 : 18233, dirs: Array.isArray(roots) ? 18 : 3061,
      bytes: rows.reduce((sum, finding) => sum + finding.size, 0), cloud_skipped: cloudSkipped,
      seconds: 3, roots: chosen, truncated: false, limit: 400000 }, roots: chosen, time: now };
  }
  let freedNow = 0;  // what the mock cleanup deleted: the system drive's free space grows by it

  const A = (id, name, publisher, version, kind, size, installed, used, extra) => Object.assign({ id, name, publisher, version, kind,
    kind_label: { app: '应用', runtime: '运行库', driver: '驱动', game: '游戏' }[kind], size, size_source: size ? 'estimated' : '', installed: now - installed * day,
    last_used: used === null ? null : now - used * day, usage_known: true, location: 'C:\\Program Files\\' + name, location_exists: true, can_uninstall: true,
    msi: false, needs_admin: true, scope: '所有用户', registry_key: id }, extra || {});
  let apps = [
    A('a1', 'Google Chrome', 'Google LLC', '130.0.6723.92', 'app', 640 * MB, 300, 0.2),
    A('a2', '微信', '腾讯科技(深圳)有限公司', '3.9.12.17', 'app', 520 * MB, 240, 1),
    A('a3', 'FooSoft 设计工具 2.3', 'FooSoft Inc.', '2.3.0', 'app', null, 180, 5),
    A('a4', '快看图大师', 'Fastview Network Technology Co., Ltd.', '5.2.0.1180', 'app', 140 * MB, 410, 400),
    A('a5', 'Hollow Knight', 'Team Cherry', '', 'game', 9.1 * GB, 420, 420),
    A('a6', 'Microsoft Visual C++ 2015-2022 Redistributable (x64)', 'Microsoft Corporation', '14.40.33810.0', 'runtime', 25 * MB, 300, null, { location_exists: false, location: '' }),
    A('a7', 'NVIDIA 图形驱动程序 566.36', 'NVIDIA Corporation', '566.36', 'driver', 1.2 * GB, 50, null),
  ];
  const A0 = apps.slice();
  const uninstalledAt = {};
  const KB = 1024;
  const sandbox = location.hash.includes('sandbox')
    ? { root: 'C:\\Users\\demo\\AppData\\Local\\PanXu\\sandbox', recycle_count: 2, recycle_size: 1.4 * GB } : null;
  const recycled = [
    { id: 'A1B2', original: U + '\\Downloads\\WeChatSetup.exe', name: 'WeChatSetup.exe', size: 182 * MB, time: now - 600 },
    { id: 'C3D4', original: U + '\\AppData\\Local\\OldGameStudio', name: 'OldGameStudio', size: 1.1 * GB, time: now - 300 },
  ];

  const handlers = {
    activity: () => ({ jobs: Object.entries(jobs).filter(([, j]) => j.kind !== 'ai_ask' && (!j.snapshot || j.snapshot.status === 'running'))
      .map(([id, j]) => j.snapshot || { id, kind: j.kind, status: 'running', progress: {} }), window: { available: false } }),
    checkup: () => {
      const opts = { ...settings.checkup };
      const roots = PX.checkupDriveRoots(handlers.drives(), opts);
      const activeStages = stages.filter((s) => (s.id !== 'space' || opts.space_scan !== false)
        && (s.id !== 'dups' || opts.duplicates !== false) && (s.id !== 'apps' || opts.apps_services !== false)
        && (s.id !== 'ai' || (PX.checkupAIEnabled(opts) && hasKey)));
      return job(40, () => {
      const t = settings.checkup.auto_threshold ?? 7;
      const scopeOf = (it) => ({ startup: 'startup', app: 'apps', service: 'services' }[it.kind] || 'space');
      const chosen = planItems.filter((it) => (opts.duplicates !== false || it.kind !== 'dup')
        && (opts.apps_services !== false || (it.kind !== 'app' && it.kind !== 'service'))
        && (it.source !== 'ai' || (hasKey && PX.checkupAIEnabled(opts) && opts.ai_scopes?.[scopeOf(it)] !== false))
        && !(it.paths || []).some((p) => !p.startsWith(U) && !roots.some((r) => p.startsWith(r))));
      plan = { id: 'p' + Date.now(), created: now, threshold: t, options: opts, items: decide(JSON.parse(JSON.stringify(chosen)), t),
        groups: planGroups, ai: { used: hasKey && PX.checkupAIEnabled(opts), enabled: PX.checkupAIEnabled(opts), errors: [], usage: { requests: 3 } } };
      plan.ai.scopes = hasKey && PX.checkupAIEnabled(opts) ? PX.checkupAIScopes.filter(([key]) => opts.ai_scopes?.[key] !== false
        && (key !== 'space' || opts.space_scan !== false) && (key !== 'duplicates' || opts.duplicates !== false)).map(([key]) => key === 'duplicates' ? 'dups' : key) : [];
      startupAI = plan.ai.scopes.includes('startup') ? Object.fromEntries(startup.filter((item) => item.enabled).map((item) => [item.id,
        { what: item.display_name, suggestion: 'keep', reason: '需要时可以在这里单独复核' }])) : {};
      plan.summary = summarize();
      insights = personalResult(null, 12);
      plan.personal_scan = { truncated: insights.stats.truncated, limit: insights.stats.limit, files: insights.stats.files };
      plan.coverage = { whole_drives: roots,
        junk: { rules: rules.length, unavailable: 1 }, startup: { count: startup.length, error: '', ai_count: Object.keys(startupAI).length },
        apps: { residues: opts.space_scan !== false, folders: plan.items.filter((i) => ['软件残留', '旧版本数据'].includes(i.category)).length, registry: 0, truncated: false,
          listed: opts.apps_services !== false ? apps.length : null, ai_count: plan.ai.scopes.includes('apps') ? 4 : 0,
          suggested: plan.items.filter((i) => i.kind === 'app').length },
        services: opts.apps_services !== false ? { count: services.filter((x) => x.kind === 'third_party').length, all: services.length, error: '',
          ai_count: plan.ai.scopes.includes('services') ? 4 : 0, suggested: plan.items.filter((i) => i.kind === 'service').length } : null,
        space: opts.space_scan !== false ? { roots: roots.includes('C:\\') ? roots : [U, ...roots], files: 1500000, incomplete: true, denied: 12 } : null,
        duplicates: opts.duplicates !== false ? { truncated: true, file_limit: 400000, hash_limit: 16 * GB, cross_drives: opts.cross_drive_duplicates !== false } : null,
        omitted_drives: handlers.drives().map((d) => d.root).filter((r) => !roots.includes(r)) };
      return plan;
    }, (f) => {
      // like checkup.run: each stage says how far it is (part), except 整理我的文件, which has no total
      const step = Math.min(activeStages.length, 1 + Math.floor(f * activeStages.length));
      const part = f * activeStages.length - (step - 1);
      const stage = activeStages[step - 1];
      const index = stages.findIndex((s) => s.id === stage.id);
      return { stage: stage.id, stage_label: stage.label, step, steps: activeStages.length, stages: activeStages,
        part: stage.id === 'insights' ? null : part, count: [`${Math.round(part * 44)} / 44 条规则`, `已统计 ${Math.round(part * 412000).toLocaleString()} 个文件`,
          `已看过 ${Math.round(part * 18233).toLocaleString()} 个文件`, '已找到 3,120 个文件', `已问 ${Math.round(part * 6)} 次`, ''][index],
        detail: [U + '\\AppData\\Local\\Temp', roots.join('、'), U + '\\Documents', '逐字节比对内容', '空间分析', ''][index] };
    });
    },
    plan: () => { if (plan) plan.summary = summarize(); return plan; },
    hibernate_set: ({ enabled }) => ({ enabled, freed: enabled ? 0 : 12.7 * GB }),
    // the first re-check finds Edge closed
    running_check: () => {
      const changed = [];
      const edge = plan && plan.items.find((i) => i.id === 'rule:edge');
      if (edge && edge.blocked) {
        edge.blocked = null;
        decide([edge], plan.threshold);
        changed.push(edge.id);
      }
      return { running: { edge: [], chrome: [] }, plan_changed: changed };
    },
    plan_dup_choose: ({ item_id, remove }) => {
      const it = plan.items.find((i) => i.id === item_id);
      const keep = it.dup_files.filter((f) => !remove.includes(f.path));
      Object.assign(it, { paths: remove, keep: keep.length ? keep[0].path : null, count: remove.length,
        size: it.dup_files.filter((f) => remove.includes(f.path)).reduce((sum, f) => sum + PX.copySize(f, it.dup_size), 0),
        logical_size: it.dup_size * remove.length,
        score: keep.length ? 85 : 15,
        why: keep.length ? `保留「${keep[0].path.split('\\').slice(-2, -1)[0]}」里的那份，其余 ${remove.length} 份移到回收站`
          : `这 ${remove.length} 份全部移到回收站` });
      return it;
    },
    plan_preview: ({ item_ids }) => {
      const out = { delete: { count: 0, size: 0, titles: [] }, recycle: { count: 0, size: 0, titles: [] }, disable: { count: 0, titles: [] },
        service: { count: 0, titles: [] }, skipped: [] };
      for (const it of plan.items) {
        if (!item_ids.includes(it.id)) continue;
        if (it.action === 'uninstall') { out.skipped.push({ title: it.title, reason: '要单独运行它自己的卸载程序' }); continue; }
        const slot = out[it.action]; slot.count += 1; slot.titles.push(it.title); if ('size' in slot) slot.size += it.size || 0;
      }
      return out;
    },
    plan_execute: ({ item_ids }) => job(6, () => {
      // like checkup.execute: freed sizes as numbers; one item, if chosen, is skipped to show a problem
      let del = 0, rec = 0, recN = 0; const dis = [], svc = [], failures = [], items = {};
      for (const it of plan.items) {
        if (!item_ids.includes(it.id) || it.action === 'uninstall') continue;
        if (it.id === 'dir:idea21') {
          it.status = 'skipped'; it.result = '文件在体检后被修改过，请重新体检';
          failures.push({ title: it.title, reason: '文件在体检后被修改过' });
        } else {
          it.status = 'done'; it.freed = it.size;
          it.result = it.action === 'delete' ? '已清理' : it.action === 'recycle' ? '已移到回收站' : it.action === 'service' ? '已改为手动启动' : '已禁用开机启动';
          if (it.action === 'delete') del += it.size; else if (it.action === 'recycle') { rec += it.size; recN += 1; }
          else if (it.action === 'service') svc.push(it.title); else dis.push(it.title);
        }
        items[it.id] = { status: it.status, note: it.result, freed: it.freed };
      }
      plan.summary = summarize();
      freedNow += del;  // only what was deleted: the Recycle Bin still holds the rest
      return { deleted: { count: 9000, bytes: del }, recycled: { count: recN, bytes: rec }, disabled: dis, services: svc, failures, items, summary: plan.summary };
    }, (f) => ({ stage: 'junk', detail: '用户临时文件', freed: f * 4 * GB })),
    insights_roots: () => personalRoots,
    insights_scan: ({ roots }) => job(6, () => { insights = personalResult(roots); return insights; },
      (f) => ({ files: Math.round(f * (roots ? 120 : 18233)), dirs: Math.round(f * (roots ? 18 : 3061)), current: roots ? roots[0] : U + '\\Documents' })),
    insights_result: () => insights,
    insights_delete: ({ paths_to_delete }) => job(2, { done: paths_to_delete, freed: 1 * GB, failed: 0, failures: [], mode: 'recycle' }),
    ai_review_preview: () => ({ files: findings.filter((f) => f.kind !== 'empty_folders').flatMap((f) => f.files).map((x) => ({ path: x.path, name: x.name, size: x.size })),
      content_files: [{ path: U + '\\Documents\\毕业论文.docx', name: '毕业论文.docx' }, { path: U + '\\Documents\\毕业论文(1).docx', name: '毕业论文(1).docx' }],
      content_allowed: !!settings.ai.send_file_content, anonymize: true }),
    ai_review_files: () => job(5, { files: {}, usage: { requests: 1 }, errors: [] }),
    large_ai_review: ({ files }) => job(5, () => { for (const f of largeFiles) if (files.includes(f.path)) f.ai = { verdict: 'archive', verdict_label: '建议归档', what: '很久以前的录像或备份', reason: '一年多没改过，体积大，适合移到移动硬盘' };
      return { files: {}, usage: { requests: 1 }, errors: [], sent: files.length }; }),
    app_residues_result: () => plan ? { items: plan.items.filter((i) => ['软件残留', '旧版本数据'].includes(i.category)), registry: [],
      stats: { roots: [U + '\\AppData\\Local'], checked: 12, truncated: false }, scanned_at: now } : null,
    app_residues_scan: () => job(3, { items: [P({ id: 'residue:oldeditor', category: '软件残留', title: '旧编辑器数据',
      paths: [U + '\\AppData\\Local\\OldEditor'], size: 18 * MB, score: 40, eligible: false, selected: false,
      action: 'recycle', why: '未匹配到安装登记，半年没有新增或修改；可能属于绿色软件。',
      basis: ['未匹配到安装登记不等于已经卸载', '需要你决定是否保留'], effect: '可能包含以前的设置' })], registry: [],
      stats: { roots: [U + '\\AppData\\Local'], checked: 12, truncated: false }, scanned_at: now }),
    apps_list: () => ({ apps, admin: true, usage_known: true, ai: plan && plan.ai.scopes.includes('apps')
      ? { a4: { what: '看图软件，附带资讯弹窗', suggestion: 'uninstall', reason: '常驻弹出广告资讯' } } : {} }),
    file_icons: () => ({}),
    // stand-ins for the shell's type icons: a yellow folder, a page with the extension on it
    type_icons: ({ kinds }) => Object.fromEntries(kinds.map((k) => [k, 'data:image/svg+xml,' + encodeURIComponent(k === 'folder'
      ? '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M2 7h11l3 3h14v17H2z" fill="#F2C14E"/><path d="M2 12h28v15H2z" fill="#F7D774"/></svg>'
      : `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M6 2h14l6 6v22H6z" fill="#fff" stroke="#9AA3AF"/><text x="16" y="24" font-size="8" text-anchor="middle" font-family="sans-serif" fill="${{ '.mp4': '#C2410C', '.docx': '#2B579A', '.iso': '#6B7280', '.zip': '#B45309', '.png': '#0E7490', '.jpg': '#0E7490', '.pdf': '#B91C1C' }[k] || '#4B5563'}">${k.slice(1, 5).toUpperCase()}</text></svg>`)])),
    app_icons: ({ ids }) => Object.fromEntries(ids.filter((id) => id === 'a1' || id === 'a2').map((id) => [id,
      'data:image/svg+xml;base64,' + btoa('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="5" fill="' + (id === 'a1' ? '#4285f4' : '#07c160') + '"/></svg>')])),
    app_measure: ({ app_id }) => ({ id: app_id, size: 2.3 * GB, files: 812 }),
    // the "uninstaller" finishes 6 seconds after it was opened
    app_uninstall: ({ app_id }) => {
      uninstalledAt[app_id] = Date.now();
      if (sandbox) apps = apps.filter((a) => a.id !== app_id); else setTimeout(() => { apps = apps.filter((a) => a.id !== app_id); }, 6000);
      return { started: true, simulated: !!sandbox };
    },
    app_installed: ({ app_ids }) => Object.fromEntries(app_ids.map((id) => [id, apps.some((a) => a.id === id)])),
    app_leftovers: ({ app_id }) => job(4, () => {
      const name = (A0.find((a) => a.id === app_id) || {}).name || 'QuickView';
      const folder = { a4: 'QuickView', a3: 'FooSoft' }[app_id] || name;
      return [
        { path: `${U}\\AppData\\Local\\${folder}`, where: '本地应用数据', size: 183 * MB, files: 412, mtime: now - 2 * day, caution: null, blocked: null },
        { path: `${U}\\AppData\\Roaming\\${folder}`, where: '漫游应用数据', size: 64 * KB, files: 3, mtime: now - 30 * day,
          caution: '通常是设置和账号数据：确认以后不会再装它', blocked: null },
        { path: `C:\\ProgramData\\${folder}`, where: '所有用户的应用数据', size: 662 * MB, files: 2, mtime: now - day, caution: null, blocked: null },
      ];
    }),
    ai_ask: ({ messages }) => job(4, { answer: messages.length > 1 ? '可以。删除后下次打开相关程序时会慢一些，因为缓存要重新生成。'
      : '可以删除。\n\n• 这是什么：Unity 编辑器为导入的资源生成的缓存。\n• 能不能删：可以，Unity 会在打开项目时重新生成。\n• 删了会怎样：第一次打开项目会比较慢。\n• 在哪里操作：首页的清理方案里勾选这一项，或在「空间分析」中选中它。',
    cached: false, usage: { requests: 1 }, model: settings.ai.model }),
    sandbox_recycle_list: () => recycled,
    sandbox_restore: ({ item_id }) => { const i = recycled.findIndex((r) => r.id === item_id); const [r] = recycled.splice(i, 1); sandbox.recycle_count -= 1; return r.original; },
    sandbox_reset: () => sandbox,
    drives: () => handlers.init().drives,
    init: () => ({ version: '2.0.3', admin: !!sandbox, windows: true, settings, has_key: hasKey, sandbox,
      plan_groups: planGroups, checkup_stages: stages,
      presets: [{ id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com', protocol: 'openai', models: ['deepseek-flash', 'deepseek-v4-pro'], json_mode: true, thinking_toggle: true, key_url: 'x' },
        { id: 'kimi', name: 'Kimi（月之暗面）', base_url: 'https://api.moonshot.cn/v1', protocol: 'openai', models: [], json_mode: true },
        { id: 'ollama', name: '本地 Ollama', base_url: 'http://127.0.0.1:11434/v1', protocol: 'openai', models: [], json_mode: true, needs_key: false }],
      drives: [{ root: 'C:\\', label: '', fstype: 'NTFS', total: 476 * GB, used: 389 * GB - freedNow, free: 87 * GB + freedNow, system: true, removable: false, fixed: true },
        { root: 'D:\\', label: '数据', fstype: 'NTFS', total: 931 * GB, used: 512 * GB, free: 419 * GB, system: false, removable: false, fixed: true },
        { root: 'E:\\', label: '工作', fstype: 'NTFS', total: 954 * GB, used: 702 * GB, free: 252 * GB, system: false, removable: false, fixed: true }],
      rules: catalogue, groups: [{ id: 'system', name: '系统垃圾' }, { id: 'browser', name: '浏览器缓存' }, { id: 'app', name: '应用缓存' }, { id: 'dev', name: '开发工具缓存' }, { id: 'advanced', name: '系统深度清理' }],
      start_types: { auto: '自动', delayed: '自动（延迟启动）', manual: '手动', disabled: '禁用' }, home: 'C:\\Users\\demo', system_drive: 'C:\\', data_dir: 'C:\\Users\\demo\\AppData\\Local\\PanXu' }),
    job: ({ job_id }) => status(job_id),
    cancel_job: ({ job_id }) => { if (jobs[job_id]) jobs[job_id].cancel = true; return true; },
    junk_result: () => plan ? handlers.junk_rows() : null,
    junk_rows: () => rules.map(([id, , , , , admin, size]) => ({ id, size: admin ? 0 : size,
      score: SCORES['rule:' + id] || 70, eligible: !['recycle_bin', 'windows_old', 'component_store'].includes(id),
      selected: !admin && !['edge', 'recycle_bin', 'windows_old', 'component_store'].includes(id)
        && PX.points(SCORES['rule:' + id] || 70) >= settings.checkup.auto_threshold,
      basis: ['按缓存重建成本和最近活动评分'],
      logical_size: id === 'user_temp' ? 510 * GB : admin ? 0 : size, count: size ? Math.round(size / (200 * 1024)) : 0,
      size_note: id === 'recycle_bin' ? 'Windows 提供的估算大小，可能包含稀疏或压缩文件的标称大小；清空后的已释放按磁盘可用空间变化计算。' : '',
      locations: ['C:\\Users\\demo\\AppData\\Local\\Temp'], needs_admin: admin, running: id === 'edge' ? ['msedge.exe'] : [], error: '', available: size === null || size > 0 })),
    junk_scan: () => job(4, handlers.junk_rows(),
      (f) => ({ index: Math.round(f * rules.length), total: rules.length, current: rules[Math.min(rules.length - 1, Math.round(f * rules.length))][1] })),
    junk_clean: ({ rule_ids }) => job(6, { freed: 3.9 * GB, rules: rule_ids.map((id) => ({ id, name: id, freed: 1 * GB, files: 1200, failed: 12, in_use: 12, skipped: '', failures: [] })) },
      (f) => ({ index: Math.round(f * rule_ids.length), total: rule_ids.length, current: '用户临时文件', freed: f * 3.9 * GB })),
    space_result: () => ({ root: row(tree), stats: { dirs: 186204, files: 1203344, bytes: tree.size, denied: 412, seconds: 74.2 }, scanned_at: 1 }),
    space_scan: ({ root }) => job(6, { root: row(tree), stats: { dirs: 186204, files: 1203344, bytes: tree.size, cloud_files: 0, cloud_bytes: 0, denied: 412, seconds: 74.2 } },
      (f) => ({ dirs: Math.round(f * 186204), files: Math.round(f * 1203344), bytes: f * tree.size, elapsed: f * 74 })),
    space_node: ({ node_id }) => {
      const n = nodes[node_id];
      const children = n.children.filter((c) => c.kind !== 'file').map(row);
      const files = n.children.filter((c) => c.kind === 'file').map((c) => ({ name: c.name, path: c.path, size: c.size, logical_size: c.logical_size,
        mtime: now - day, cloud: false, type: 'file', category: 'other', blocked: c.path.endsWith('.sys') ? '这是 Windows 系统文件或目录' : null }));
      const crumbs = []; for (let x = n; x; x = x.parent) crumbs.unshift({ id: x.id, name: x.parent ? x.name : x.path });
      return { node: row(n), crumbs, children, files, more_files: 23, more_bytes: 140 * MB };
    },
    space_treemap: ({ node_id }) => {
      const build = (n, level) => {
        const kind = aiResults[n.id] && aiResults[n.id].verdict === 'clean' ? 'cache' : n.kind && n.kind !== 'container' && n.kind !== 'file' ? n.kind : 'unknown';
        const item = { id: n.id, name: n.parent ? n.name : n.path, size: n.size, kind, has_children: n.children.length > 0 };
        if (level < 2 && n.children.length) {
          item.children = n.children.map((c) => build(c, level + 1));
          const rest = n.size - item.children.reduce((s, c) => s + c.size, 0);
          if (rest > 0) item.children.push({ id: null, name: '其他', size: rest, other: true });
        }
        return item;
      };
      return build(nodes[node_id], 0);
    },
    space_types: () => [
      { category: 'other', label: '其他', count: 820000, bytes: 160 * GB, top_exts: ['.dll', '.sys', '.dat', '.bin'] },
      { category: 'disk', label: '磁盘镜像', count: 14, bytes: 52 * GB, top_exts: ['.vhdx', '.iso'] },
      { category: 'video', label: '视频', count: 410, bytes: 41 * GB, top_exts: ['.mp4', '.mkv'] },
      { category: 'image', label: '图片', count: 52000, bytes: 24 * GB, top_exts: ['.jpg', '.png', '.heic'] },
      { category: 'archive', label: '压缩包', count: 900, bytes: 18 * GB, top_exts: ['.zip', '.rar', '.7z'] },
      { category: 'installer', label: '安装包', count: 1600, bytes: 9 * GB, top_exts: ['.exe', '.msi'] },
    ],
    space_find: ({ path }) => { const n = nodes.find((x) => x.path.toLowerCase() === path.toLowerCase()); return n ? n.id : null; },
    large_files: ({ min_mb, category }) => ({ files: largeFiles.filter((f) => f.size >= min_mb * MB && (!category || f.category === category)), scanned_root: 'C:\\', scanned_at: now }),
    check_paths: ({ items }) => items.map((p) => ({ path: p, exists: true, size: (nodes.find((n) => n.path === p) || largeFiles.find((f) => f.path === p) || { size: 1.5 * GB }).size,
      files: 10, blocked: null, caution: p.includes('Roaming') ? '位于应用配置目录，可能包含应用设置或账号数据' : null, note: null })),
    // a folder named Temp is too large for the Recycle Bin here, and the user says yes to Windows' prompt
    delete_paths: ({ items, mode }) => job(12, () => {
      const permanent = mode === 'recycle' ? items.filter((p) => /\\Temp$/i.test(p)) : [];
      return { done: items, freed: items.length * 1.5 * GB, failed: 0, failures: [], mode, permanent, permanent_bytes: permanent.length * 1.5 * GB };
    }, (f) => ({ total: items.length, done: Math.floor(f * items.length), current: items[Math.min(items.length - 1, Math.floor(f * items.length))], freed: f * items.length * GB })),
    ai_results: () => Object.values(aiResults),
    ai_preview: () => ({ candidates: suggestions().map((s) => ({ id: s.node_id, path: s.path, size: s.size, shown_as: s.path.replace('C:\\Users\\demo\\AppData\\Local', '%LOCALAPPDATA%').replace('C:\\Users\\demo\\AppData\\Roaming', '%APPDATA%'), cached: s.source === 'cache' })), fresh: 4, requests_estimate: 1, remaining: { count: 23, size: 1.6 * GB } }),
    ai_analyze: () => job(8, () => { const s = suggestions(); aiResults = {}; s.forEach((x) => { aiResults[x.node_id] = x; }); return { suggestions: s, usage: { requests: 2, input_tokens: 6230, output_tokens: 1520 }, errors: [], requests: 2, budget: 8, remaining: { count: 23, size: 1.6 * GB } }; },
      (f) => ({ stage: f < 0.4 ? 'summarize' : 'ask', requests: f < 0.6 ? 1 : 2, budget: 8, done: Math.round(f * 5), current: 'C:\\Users\\demo\\AppData\\Local\\Unity' })),
    dup_default_roots: () => ['C:\\', 'D:\\', 'E:\\'],
    dup_result: () => null,
    dup_scan: () => job(6, { groups: [
      { id: 0, size: 1.4 * GB, wasted: 1.4 * GB, files: [{ path: 'C:\\Users\\demo\\Downloads\\项目资料-final.zip', mtime: now - 200 * day }, { path: 'C:\\Users\\demo\\Documents\\归档\\项目资料-final (1).zip', mtime: now - 20 * day }] },
      { id: 1, size: 320 * MB, wasted: 640 * MB, files: [{ path: 'C:\\Users\\demo\\Videos\\旅行\\IMG_2041.MOV', mtime: now - 400 * day }, { path: 'C:\\Users\\demo\\Pictures\\手机备份\\IMG_2041.MOV', mtime: now - 300 * day }, { path: 'C:\\Users\\demo\\Desktop\\IMG_2041.MOV', mtime: now - 3 * day }] },
      { id: 2, size: 46 * MB, wasted: 46 * MB, files: [{ path: 'C:\\Users\\demo\\Downloads\\WeChatSetup.exe', mtime: now - 90 * day }, { path: 'C:\\Users\\demo\\Downloads\\WeChatSetup (2).exe', mtime: now - 30 * day }] },
      // the same font in two projects (each loads its own) and a sound kept in a project plus a loose copy
      { id: 3, size: 9 * MB, wasted: 9 * MB, files: [
        { path: 'E:\\工程\\萍萍瑜伽故事\\代码工程\\lib\\fonts\\NotoSerifJP-600.woff', mtime: now - 60 * day, place: 'project', place_root: 'E:\\工程\\萍萍瑜伽故事\\代码工程',
          refs: { found: ['src\\styles\\fonts.css'], count: 1, searched: 214, complete: true } },
        { path: 'E:\\工程\\动画短片\\代码工程\\lib\\fonts\\NotoSerifJP-600.woff', mtime: now - 40 * day, place: 'project', place_root: 'E:\\工程\\动画短片\\代码工程',
          refs: { found: [], count: 0, searched: 96, complete: true } }] },
      { id: 4, size: 8 * MB, wasted: 8 * MB, files: [
        { path: 'E:\\工程\\动画短片\\代码工程\\public\\long_score.wav', mtime: now - 50 * day, place: 'project', place_root: 'E:\\工程\\动画短片\\代码工程' },
        { path: 'C:\\Users\\demo\\Downloads\\long_score.wav', mtime: now - 70 * day }] },
    ], stats: { files: 18233, groups: 5, wasted: 2.1 * GB, seconds: 18 } }, (f) => ({ phase: f < 0.3 ? 'listing' : f < 0.6 ? 'quick' : 'full', files: Math.round(f * 18233), done: Math.round(f * 300), total: 300, bytes_done: f * 4 * GB, bytes_total: 4 * GB })),
    dup_ai_review: () => job(4, { copies: {
      'E:\\工程\\萍萍瑜伽故事\\代码工程\\lib\\fonts\\NotoSerifJP-600.woff': { verdict: 'keep', verdict_label: '建议保留', reason: '工程的样式表引用了它' },
      'E:\\工程\\动画短片\\代码工程\\lib\\fonts\\NotoSerifJP-600.woff': { verdict: 'delete', verdict_label: '更推荐删除', reason: '工程里没有用到，像是复制模板时带过来的' },
      'E:\\工程\\动画短片\\代码工程\\public\\long_score.wav': { verdict: 'keep', verdict_label: '建议保留', reason: '放在 public 里，页面可能直接加载' },
      'C:\\Users\\demo\\Downloads\\long_score.wav': { verdict: 'delete', verdict_label: '更推荐删除', reason: '下载文件夹里的零散副本' },
    }, usage: { requests: 1 }, errors: [] }),
    dup_delete: ({ paths_to_delete }) => job(3, { done: paths_to_delete, freed: 1.4 * GB, failed: 0, failures: [], mode: 'recycle' }),
    startup_list: () => startup.map((item) => ({ ...item, ai: startupAI[item.id] || null })),
    startup_set: ({ item_id, enabled }) => { const it = startup.find((x) => x.id === item_id); it.enabled = enabled; return it; },
    services_list: () => services.map((x) => ({ ...x, ai: plan && plan.ai.scopes.includes('services') && x.name === 'AdobeARMservice'
      ? { what: 'Adobe 自动更新服务', suggestion: 'disable', reason: '设为手动后仍可在应用内更新' } : null })),
    service_set: ({ name, start_type }) => ({ name, start_type, stopped: false }),
    ai_explain: ({ kind }) => job(4, () => { const result = { items: kind === 'apps' ? {
      a4: { what: '看图软件，附带资讯弹窗', suggestion: 'uninstall', reason: '常驻弹出广告资讯，可换用系统自带的照片应用' },
      a5: { what: '独立游戏', suggestion: 'optional', reason: '一年多没有运行，不玩了可以卸载，存档在云端' },
      a6: { what: 'VC++ 运行库', suggestion: 'keep', reason: '很多程序依赖它，卸载后这些程序无法启动' },
      a1: { what: '浏览器', suggestion: 'keep', reason: '常用程序' },
    } : kind === 'startup' ? {
      'hkcu_run:WeChat': { what: '微信开机自启', suggestion: 'optional', reason: '禁用后需手动打开微信，不影响收消息' },
      'hkcu_run:Steam': { what: 'Steam 游戏平台', suggestion: 'optional', reason: '禁用后需要时手动打开，开机更快' },
      'hklm_run:AdobeAAMUpdater': { what: 'Adobe 更新检查程序', suggestion: 'disable', reason: '只用于检查更新，可在 Adobe 应用内手动更新' },
      'hkcu_run:OneDrive': { what: 'OneDrive 同步', suggestion: 'keep', reason: '禁用后文件不会自动同步' },
      'hklm_run:SecurityHealth': { what: 'Windows 安全中心托盘图标', suggestion: 'keep', reason: '系统安全提醒入口' },
    } : { AdobeARMservice: { what: 'Adobe 自动更新服务', suggestion: 'disable', reason: '设为手动后仍可在应用内更新' } }, usage: {}, errors: [] }; if (kind === 'startup') startupAI = result.items; return result; }),
    history: () => history,
    revert: () => true,
    settings_save: ({ changes }) => {
      for (const [k, v] of Object.entries(changes)) settings[k] = v && typeof v === 'object' && !Array.isArray(v) ? Object.assign(settings[k] || {}, v) : v;
      return { settings, has_key: hasKey };
    },
    set_api_key: ({ key }) => { hasKey = !!key; return hasKey; },
    has_api_key: () => hasKey,
    ai_models: () => ['deepseek-chat', 'deepseek-reasoner'],
    ai_test: () => ({ reply: { ok: true }, model: settings.ai.model }),
    ai_clear_cache: () => true,
    pick_folder: () => 'D:\\Projects',
    reveal: ({ path }) => ({ shown: path, missing: false }),
    open_path: ({ path }) => ({ opened: path }),
    flash_window: () => false,
    open_system_page: () => true,
    open_key_page: () => true,
    restart_as_admin: () => { throw new Error('预览模式不能重启'); },
  };

  window.pywebview = { api: { call: async (method, params) => {
    await new Promise((r) => setTimeout(r, 40));
    const fn = handlers[method];
    if (!fn) return { ok: false, error: '未知操作：' + method };
    try {
      const data = fn(params || {});
      if (typeof data === 'string' && jobs[data]) jobs[data].kind = method;
      return { ok: true, data };
    } catch (e) { return { ok: false, error: e.message }; }
  } } };
})();
