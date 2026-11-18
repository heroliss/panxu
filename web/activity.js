/* One visible activity across pages; completed check-up stages publish existing results, never rescan. */
(function () {
  'use strict';
  const { h } = PX;
  const LABELS = {
    admin_refresh: ['补查管理员项目', 'home', '正在补查管理员项目…'],
    checkup: ['一键体检', 'home', '正在体检…'],
    junk_scan: ['扫描垃圾文件', 'junk', '正在扫描垃圾文件…'],
    junk_clean: ['清理垃圾文件', 'junk', '正在清理垃圾文件…'],
    space_scan: ['分析磁盘空间', 'space', '正在分析磁盘空间…'],
    insights_scan: ['整理我的文件', 'mine', '正在整理我的文件…'],
    dup_scan: ['查找重复文件', 'dups', '正在查找重复文件…'],
    app_residues_scan: ['查找软件残留', 'apps', '正在查找软件残留…'],
    ai_analyze: ['AI 分析目录', 'space', 'AI 正在分析目录…'],
    ai_review: ['AI 复核文件', 'mine', 'AI 正在复核文件…'],
    ai_review_files: ['AI 复核文件', 'mine', 'AI 正在复核文件…'],
    large_ai_review: ['AI 复核大文件', 'large', 'AI 正在复核大文件…'],
    dup_ai_review: ['AI 复核副本', 'dups', 'AI 正在复核副本…'],
    ai_explain: ['AI 分析', null, 'AI 正在分析…'],
    apps_measure: ['统计应用占用', 'apps', '正在统计应用占用…'],
    apps_measure_all: ['统计应用占用', 'apps', '正在统计应用占用…'],
    app_leftovers: ['查找卸载残留', 'apps', '正在查找卸载残留…'],
    delete: ['处理文件', null, '正在处理文件…'],
    delete_paths: ['处理文件', null, '正在处理文件…'],
    dup_delete: ['清理重复副本', 'dups', '正在清理重复副本…'],
    insights_delete: ['移到回收站', 'mine', '正在移到回收站…'],
    plan_execute: ['执行清理方案', 'home', '正在执行清理方案…'],
  };
  const MODAL_JOBS = new Set(['delete', 'delete_paths', 'dup_delete', 'insights_delete', 'app_leftovers']);
  const PAGES = { admin_refresh: ['home', 'junk', 'apps', 'startup'], checkup: ['home', 'junk', 'space', 'large', 'mine', 'dups', 'apps', 'startup'],
    junk_scan: ['junk'], space_scan: ['space', 'large'], insights_scan: ['mine'], dup_scan: ['dups'],
    app_residues_scan: ['apps'], ai_analyze: ['space'], ai_review: ['mine', 'large'], ai_review_files: ['mine'], large_ai_review: ['large'],
    dup_ai_review: ['dups'], ai_explain: ['startup', 'apps'], apps_measure: ['apps'], apps_measure_all: ['apps'],
    junk_clean: ['home', 'junk', 'large'], plan_execute: ['home', 'junk', 'space', 'large', 'mine', 'dups', 'apps', 'startup'],
    delete: ['home', 'space', 'large', 'mine', 'dups', 'apps'] };
  const finished = new Set();
  const jobOrigin = {};
  let job = null, win = {}, statusButton, maxButton, controls, polling = false;
  const label = (j) => LABELS[j.kind]?.[0] || '检查';
  const ownerPage = (j) => (j ? j.originPage || jobOrigin[j.id] || LABELS[j.kind]?.[1] || null : null);
  const runningText = (j) => {
    const s = j.progress?.stage_label;
    if (s) return (/^AI\b/i.test(s) ? s.replace(/^AI\s*/, 'AI 正在') : '正在' + s) + '…';
    return LABELS[j.kind]?.[2] || `正在${label(j)}…`;
  };
  const view = (j) => {
    const target = ownerPage(j) || 'home';
    if (j.kind === 'ai_analyze' && PX.space?.state) PX.space.state.view = 'ai';
    PX.go(target);
  };

  PX.workBlocked = () => {
    const active = job || (PX.home?.state.status === 'running' ? { kind: 'checkup' } : null);
    if (!active) return false;
    PX.toast(active.kind === 'checkup' ? '正在一键体检，本页会复用已完成的结果。范围不同的检查请等体检结束后再开始。'
      : `${label(active)}正在进行，完成后可开始另一项检查。`, false, { label: '查看进度', onClick: () => view(active) });
    return true;
  };
  PX.trackJob = (next) => {
    if (next.kind === 'ai_ask' || finished.has(next.id)) return;
    if (next.originPage) jobOrigin[next.id] = next.originPage;
    else if (jobOrigin[next.id]) next.originPage = jobOrigin[next.id];
    const prior = job;
    job = next;
    PX.renderActivity();
    // a check-up stage just published its results: the rail marks that tool
    if (prior?.id !== next.id || (prior.progress?.completed || []).length !== (next.progress?.completed || []).length) PX.refreshNav?.();
    if (next.kind === 'checkup') {
      const page = PX.currentPage?.();
      if (page && page !== 'home' && (next.progress?.completed || []).includes(page)
        && (prior?.id !== next.id || !(prior.progress?.completed || []).includes(page))) PX.refresh(page);
    }
  };
  PX.finishJob = (done, fromCaller) => {
    if (done.kind === 'ai_ask' || finished.has(done.id)) return;
    finished.add(done.id);
    const origin = done.originPage || jobOrigin[done.id] || null;
    delete jobOrigin[done.id];
    if (job?.id === done.id) job = null;
    if (done.kind === 'checkup' && done.status === 'done' && done.result) PX.home.adopt(done.result);
    PX.renderActivity();
    PX.refreshNav?.();
    const page = PX.currentPage?.();
    // When the user stayed on the initiating page and its own await PX.api.run(...) handled the redraw, skip a redundant PX.go(page) flash.
    if (fromCaller && origin && page === origin && done.kind !== 'admin_refresh') return;
    if ((PAGES[done.kind] || []).includes(page)) PX.refresh(page);
  };
  PX.renderActivity = () => {
    if (statusButton) {
      statusButton.hidden = !job;
      statusButton.textContent = job ? runningText(job) : '';
      statusButton.title = job ? `${label(job)}，点击查看进度` : '';
    }
    const el = document.getElementById('work-status');
    if (!el) return;
    const page = PX.currentPage?.();
    const owner = ownerPage(job);
    const onOwner = !!owner && page === owner && job.kind !== 'admin_refresh'
      && !(job.kind === 'ai_analyze' && PX.space?.state?.view && PX.space.state.view !== 'ai');
    el.hidden = !job || MODAL_JOBS.has(job.kind) || onOwner;
    if (el.hidden) return;
    const key = [job.id, job.progress?.stage, (job.progress?.completed || []).join(','), page].join('|');
    if (el.dataset.activity === key) return;
    el.dataset.activity = key;
    const ready = (job.progress?.completed || []).includes(page);
    const text = job.kind === 'checkup' ? (ready ? '本页已复用本次体检结果；后续复核完成会继续更新。'
      : '体检完成相应阶段后，本页自动更新。未完成的项目可能仍显示上次结果；无需重复检查。')
      : '可继续查看已有结果。另一项检查或清理请等当前操作完成后再开始。';
    PX.clear(el).append(h('div.grow', h('b', `${label(job)}正在进行${job.progress?.stage_label ? ' · ' + job.progress.stage_label : ''}`),
      h('p', text)), ...(page === owner ? [] : [h('button.btn.small', { type: 'button', onclick: () => view(job) }, '查看进度')]));
  };

  async function windowAction(action) {
    try { updateWindow(await PX.api.call('window_action', { action })); } catch (e) { PX.fail(e); }
  }
  function updateWindow(state) {
    win = state || {};
    if (!controls) return;
    controls.hidden = !win.available || !win.custom;
    const title = win.maximized ? '还原窗口' : '最大化';
    if (maxButton.title !== title) {
      maxButton.title = title;
      maxButton.setAttribute('aria-label', title);
      maxButton.innerHTML = win.maximized
        ? '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 5V2h9v9h-3"/><path d="M2 5h9v9H2z"/></svg>'
        : '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2h12v12H2z"/></svg>';
    }
  }
  PX.startActivity = async () => {
    const bar = PX.clear(document.getElementById('appbar'));
    let lastPress = null;
    bar.appendChild(h('div.appbar-drag.pywebview-drag-region', { onmousedown: (e) => {
      if (e.button !== 0 || !win.available || !win.custom) return;
      // Moving a native WebView between presses can reset Chromium's click count.
      const repeated = lastPress && e.timeStamp - lastPress.time < 500
        && Math.abs(e.screenX - lastPress.x) < 5 && Math.abs(e.screenY - lastPress.y) < 5;
      lastPress = { time: e.timeStamp, x: e.screenX, y: e.screenY };
      if (e.detail !== 2 && !repeated) return;
      lastPress = null;
      e.preventDefault(); e.stopPropagation(); windowAction('toggle_maximize');
    } }, h('div.appbar-brand', h('b', '盘序'), h('span', 'v' + PX.state.init.version))));
    PX.aiStatus?.mount(bar);
    statusButton = bar.appendChild(h('button.appbar-activity', { type: 'button', hidden: true, onclick: () => job && view(job) }));
    controls = bar.appendChild(h('div.window-controls', { hidden: true }));
    const button = (name, action, svg, cls = '') => {
      const el = controls.appendChild(h('button' + cls, { type: 'button', title: name,
        'aria-label': name, onclick: () => windowAction(action) }));
      el.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">${svg}</svg>`;
      return el;
    };
    button('最小化', 'minimize', '<path d="M2 8h12"/>');
    maxButton = button('最大化', 'toggle_maximize', '<path d="M2 2h12v12H2z"/>');
    button('关闭窗口', 'close', '<path d="m3 3 10 10M13 3 3 13"/>', '.window-close');
    async function poll() {
      if (polling) return;
      polling = true;
      try {
        const result = await PX.api.call('activity');
        updateWindow(result.window);
        PX.aiStatus?.update(result.ai_health);
        const active = result.jobs?.[0];
        if (active) PX.trackJob(active);
        else if (job) PX.finishJob(await PX.api.call('job', { job_id: job.id }));
      } catch (e) { /* retain the last status through a transient bridge failure */ }
      finally { polling = false; }
    }
    await poll();
    setInterval(poll, 1000);
  };
})();
