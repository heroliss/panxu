/* Bridge to the Python backend (pywebview js_api) with job polling. */
(function () {
  'use strict';
  const PX = window.PX;

  const ready = new Promise((resolve) => {
    const ok = () => window.pywebview && window.pywebview.api && window.pywebview.api.call;
    if (ok()) return resolve();
    window.addEventListener('pywebviewready', () => resolve());
    const t = setInterval(() => { if (ok()) { clearInterval(t); resolve(); } }, 50);
  });

  async function call(method, params) {
    await ready;
    const r = await window.pywebview.api.call(method, params || {});
    if (!r) throw new Error('后台没有响应');
    if (!r.ok) {
      const error = new Error(r.error || '操作失败');
      error.busy = r.busy;
      if (r.busy && PX.trackJob) PX.trackJob(r.busy);
      throw error;
    }
    return r.data;
  }

  // Start a background job and poll until it finishes.
  async function run(method, params, onProgress, onStart) {
    const started = Date.now();
    const page = PX.currentPage ? PX.currentPage() : null;
    const jobId = await call(method, params);
    if (PX.trackJob && method !== 'ai_ask') PX.trackJob({ id: jobId, kind: method, progress: {}, status: 'running' });
    if (onStart) onStart(jobId);
    for (;;) {
      await new Promise((r) => setTimeout(r, 300));
      const s = await call('job', { job_id: jobId });
      s.kind = s.kind || method;
      if (s.status === 'running') { if (PX.trackJob) PX.trackJob(s); if (onProgress) onProgress(s.progress || {}, s); continue; }
      // Let the initiating page adopt the result before another visible page refreshes.
      if (PX.finishJob) setTimeout(() => PX.finishJob(s));
      if (s.status === 'done') {
        // something that took a while: say it is done (the user may be elsewhere by now)
        if (PX.jobDone) setTimeout(() => PX.jobDone(method, s.result, Date.now() - started, page));
        return s.result;
      }
      if (s.status === 'cancelled') { const e = new Error('已取消'); e.cancelled = true; throw e; }
      throw new Error(s.error || '操作失败');
    }
  }

  PX.api = { ready, call, run, cancel: (jobId) => call('cancel_job', { job_id: jobId }) };
})();
