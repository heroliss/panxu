'use strict';

// Task UI owns presentation and user choices; the server owns durable task state.
window.PanxuTasks = (() => {
  let current = null;
  let context = null;
  let timer = null;
  let draftGoal = '';
  let draftPath = '';
  let feedbackDraft = '';
  let planKey = '';
  let selected = new Set();
  let pending = false;
  let lastError = '';
  let intent = '';
  const intents = {
    c_cleanup: ['清理 C 盘垃圾', '先找常见缓存和临时内容'],
    disk_cleanup: ['清理某个磁盘', '逐步找出值得清理的内容'],
    downloads: ['整理下载文件夹', '看看旧安装包和下载资料'],
    directory_cleanup: ['清理某个目录', '选择文件夹，先分析再决定'],
    duplicates: ['查找重复文件', '在本地核对内容是否一致'],
    background: ['优化开机与后台', '看看哪些应用可以按需运行'],
    installers: ['清理旧安装包', '区分安装包与仍在使用的程序'],
    custom: ['其他整理要求', '用自己的话告诉助手']
  };
  const busy = () => current && ['investigating', 'executing'].includes(current.status);

  function node(tag, cls, text) {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function add(parent, ...children) { children.flat().forEach((child) => { if (child) parent.append(child); }); return parent; }
  function action(label, handler, cls = 'button') {
    const b = node('button', cls, label); b.type = 'button'; b.disabled = pending;
    b.addEventListener('click', handler); return b;
  }
  function detail(label, ...children) { return add(node('details', 'task-detail'), node('summary', '', label), ...children); }
  function textInput(label, value, handler, textarea = false) {
    const id = 'task-' + (textarea ? 'goal' : 'path');
    const input = node(textarea ? 'textarea' : 'input');
    input.id = id; input.value = value; input.maxLength = textarea ? 2000 : 2000;
    input.addEventListener('input', () => handler(input.value));
    const lab = node('label', 'field-label', label); lab.htmlFor = id;
    return {input, box: add(node('div', 'task-field'), lab, input)};
  }
  function accept(task) {
    current = task;
    if (task?.plan) {
      const key = task.id + ':' + task.plan.version;
      if (key !== planKey) {
        planKey = key;
        selected = new Set(task.plan.items.filter((item) => item.default_selected && !item.personal_choice_required).map((item) => item.id));
      }
    }
    if (busy()) schedule();
  }
  function redraw() { if (context?.isActive()) context.redraw(); }
  async function request(path, payload) {
    if (pending) return;
    pending = true; lastError = ''; redraw();
    try {
      const data = await context.api(path, payload);
      if (data.task) accept(data.task);
      feedbackDraft = '';
      if (!busy()) await context.refreshCatalog();
    } catch (error) { lastError = error.message || '操作暂时未完成，请重试。'; }
    finally { pending = false; redraw(); }
  }
  function schedule() {
    if (timer) return;
    timer = setTimeout(poll, 650);
  }
  async function poll() {
    timer = null;
    if (!busy() || !context) return;
    const id = current.id;
    try {
      const before = current.status + ':' + current.summary;
      const data = await context.api('/api/tasks/' + id);
      if (current?.id !== id) return;
      accept(data.task);
      if (!busy()) await context.refreshCatalog();
      if (before !== current.status + ':' + current.summary) redraw();
    } catch (error) { lastError = '暂时连不上本机服务。重新打开程序后可以继续这个任务。'; redraw(); }
    if (busy()) schedule();
  }
  function newTask() {
    current = null; intent = ''; planKey = ''; selected.clear(); lastError = ''; feedbackDraft = ''; redraw();
  }
  function choose(preset) {
    if (preset === 'c_cleanup' || preset === 'background') {
      request('/api/tasks', {preset}); return;
    }
    intent = preset; draftPath = ''; draftGoal = ''; redraw();
  }
  function welcome(root) {
    if (!intent) {
      add(root, context.heading('今天想整理什么？', '助手先调查并解释，再由你确认处理。'));
      const grid = node('div', 'task-intents');
      ['c_cleanup', 'disk_cleanup', 'directory_cleanup', 'duplicates'].forEach((key) => {
        const card = action('', () => choose(key), 'task-intent'); card.dataset.preset = key;
        add(card, node('strong', '', intents[key][0]), node('span', '', intents[key][1])); add(grid, card);
      });
      add(root, grid, detail('更多任务',
        ...['background', 'installers', 'custom'].map((key) => action(intents[key][0], () => choose(key), 'plain-button more-task'))),
        detail('助手会查看什么？', node('p', '', '根据目标分步检查文件名、大小、目录和软件信息，必要时在本地核对重复。不会向模型发送文件正文。个人资料的最后去留由你决定。')));
      return;
    }
    add(root, context.heading(intents[intent][0], intent === 'disk_cleanup' ? '选择位置，助手会分步调查，并说明哪些地方还没检查。' : '选择要检查的文件夹。'));
    const panel = node('section', 'task-welcome panel');
    const goal = textInput('你想怎么整理', draftGoal, (value) => { draftGoal = value; }, true);
    goal.input.placeholder = '例如：整理下载文件夹，保留照片，看看旧安装包和重复文件能否清理。';
    if (intent === 'custom') add(panel, goal.box);
    const path = textInput('检查位置', draftPath || context.defaultPath, (value) => { draftPath = value; });
    path.input.placeholder = '例如 E:\\Downloads';
    if (intent === 'disk_cleanup') {
      const disk = node('select'); disk.setAttribute('aria-label', '选择要清理的磁盘');
      add(disk, node('option', '', '选择一个磁盘'));
      context.disks.forEach((entry) => {
        const option = node('option', '', entry.name || entry.path); option.value = entry.path || entry.name; add(disk, option);
      });
      disk.firstChild.value = ''; disk.value = draftPath;
      path.input.value = draftPath;
      disk.addEventListener('change', () => { draftPath = disk.value; path.input.value = disk.value; });
      add(panel, disk);
    }
    if (intent !== 'disk_cleanup') {
      add(panel, path.box);
      add(panel, action('使用下载文件夹', () => { draftPath = context.defaultPath; path.input.value = draftPath; }, 'plain-button folder-shortcut'));
    }
    const start = action('开始分析', () => {
      if (!path.input.value.trim()) { lastError = '请先选择要检查的磁盘或文件夹。'; redraw(); return; }
      request('/api/tasks', {
      ...(intent === 'custom' ? {goal: draftGoal.trim() || '帮我整理这里的内容，找出值得清理的文件并解释原因；个人文件由我最后选择。'} : {preset: intent}),
      path: path.input.value.trim()
    }); }, 'button primary task-primary');
    add(panel, start);
    add(root, panel);
    add(root, action('返回常用任务', () => { intent = ''; redraw(); }, 'plain-button task-new'));
  }
  function processDetails(root) {
    if (!current?.events?.length) return;
    const list = node('ol', 'task-events');
    current.events.filter((event) => ['tool', 'result', 'feedback', 'summary'].includes(event.kind)).slice(-12).forEach((event) => {
      add(list, node('li', '', event.message));
    });
    add(root, detail('查看调查过程', list));
  }
  function feedbackForm(root, label = '补充要求，让助手调整') {
    const form = node('form', 'task-feedback');
    const input = node('textarea'); input.value = feedbackDraft; input.maxLength = 2000;
    input.setAttribute('aria-label', '补充整理要求');
    input.placeholder = '例如：这个安装包要保留离线版，继续看看其他内容。';
    input.addEventListener('input', () => { feedbackDraft = input.value; });
    const send = node('button', 'button', '让助手调整'); send.type = 'submit'; send.disabled = pending;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!input.value.trim()) { input.focus(); return; }
      request('/api/tasks/' + current.id + '/feedback', {message: input.value.trim()});
    });
    add(form, input, send); add(root, detail(label, form));
  }
  function plan(root) {
    const cards = node('div', 'task-plan');
    current.plan.items.forEach((item) => {
      const card = node('article', 'task-plan-item');
      const check = node('input'); check.type = 'checkbox'; check.checked = selected.has(item.id);
      check.disabled = pending; check.setAttribute('aria-label', '选择' + item.name);
      check.addEventListener('change', () => {
        if (check.checked) selected.add(item.id); else selected.delete(item.id);
        updateApproval();
      });
      const copy = node('div', 'task-plan-copy');
      const title = node('label', 'task-item-title', item.name);
      const id = 'pick-' + item.id; check.id = id; title.htmlFor = id;
      const actionNames = {delete: item.kind === 'directory' ? '清理整个文件夹' : '清理这份文件',
        disable_startup: '关闭开机启动', stop_service: '停止后台服务', stop_process: '结束应用', uninstall: '卸载软件'};
      add(copy, title, node('span', 'task-action-label', actionNames[item.action] || item.action),
        item.personal_choice_required ? node('span', 'task-choice-note', '由你选择') : null,
        node('p', '', item.reason));
      const info = detail('了解影响', node('p', '', item.impact), node('p', 'task-path', item.path),
        action('保留这项', () => request('/api/tasks/' + current.id + '/feedback', {keep_ids: [item.id]}), 'plain-button'));
      add(copy, info);
      add(card, check, copy, node('span', 'task-item-size', item.kind === 'file' || item.kind === 'directory' ? '约 ' + context.bytes(item.size_bytes) : ''));
      add(cards, card);
    });
    add(root, cards);
    const confirm = action('确认处理', () => {
      if (!selected.size) return;
      request('/api/tasks/' + current.id + '/approve', {version: current.plan.version, ids: [...selected]});
    }, 'button primary task-primary');
    confirm.id = 'task-approve';
    add(root, add(node('div', 'task-approval'), node('span', 'task-selection-count'), confirm));
    updateApproval(); feedbackForm(root);
  }
  function updateApproval() {
    const button = document.getElementById('task-approve');
    if (!button) return;
    button.disabled = !selected.size || pending;
    button.textContent = selected.size ? `确认处理 ${selected.size} 项` : '先选择要处理的内容';
    const count = document.querySelector('.task-selection-count');
    if (count) count.textContent = selected.size ? `已选择 ${selected.size} 项` : '个人文件不会默认选中';
  }
  function results(root) {
    if (!current.last_result) return;
    const result = current.last_result;
    const labels = {succeeded: '已核对', failed: '未完成', unverified: '待核实', cancelled: '已停止', running: '待核实'};
    const box = detail(`本轮处理结果 · ${result.succeeded}/${result.total} 项已核对`,
      node('p', 'result-protection-note', '测试保护下完成预演，实际文件和系统未改动。'));
    result.items.forEach((item) => add(box, node('p', '', item.current_state === 'undone'
      ? `${item.name}：已撤销这次预演。` : `${item.name}：${labels[item.status] || '待核实'}。${item.message}`)));
    add(root, box);
  }
  function renderAdvice(root) {
    const preview = current.advice || [];
    const adviceTotal = current.advice_total ?? preview.length;
    if (!adviceTotal && !current.analysis) return;
    const taskId = current.id, targetTotal = current.analysis?.target_count;
    const note = targetTotal ? `本次范围 ${targetTotal} 项，已形成 ${adviceTotal} 项用途建议。未形成建议的内容仍待调查。` : `已形成 ${adviceTotal} 项用途建议。`;
    const section = detail(`用途分析与保留建议（${adviceTotal} 项）`, node('p', 'muted', note));
    const list = node('div', 'task-advice-list'), nav = node('div', 'file-pagination');
    let offset = 0, loading = false, loaded = false;
    async function load() {
      if (loading || !section.isConnected) return;
      loading = true; list.setAttribute('aria-busy', 'true'); nav.querySelectorAll('button').forEach((b) => {b.disabled=true;});
      if (!loaded) list.replaceChildren(node('p', 'muted', '正在载入建议…'));
      try {
        const data = await context.api(`/api/tasks/${taskId}/advice?offset=${offset}&limit=20`);
        if (!section.isConnected || current?.id !== taskId) return;
        list.replaceChildren(); nav.replaceChildren(); loaded = true;
        (data.items || []).forEach((entry) => {
          const labels = {keep:'建议保留',review:'需要确认',recommend:'建议处理'};
          const card = node('article', 'task-advice-item');
          add(card, node('strong', '', entry.name), node('span', 'advice-label', labels[entry.conclusion] || '待确认'),
            node('p', '', entry.purpose), node('p', '', entry.reason), detail('了解影响', node('p', '', entry.impact)));
          add(list, card);
        });
        if (!(data.items || []).length) add(list, node('p', 'muted', '这次还没有形成逐项用途建议。可以继续调查。'));
        const total = data.total ?? adviceTotal;
        if (total > 20 || offset) {
          const previous = action('上一页建议', () => {offset=Math.max(0,offset-20);load();}); previous.disabled = !offset;
          const next = action('下一页建议', () => {offset+=20;load();}); next.disabled = !data.has_more;
          add(nav, previous, node('span', '', `${Math.floor(offset/20)+1} / ${Math.max(1,Math.ceil(total/20))}`), next);
        }
      } catch (error) {
        if (section.isConnected) {list.replaceChildren(node('p', 'field-error', error.message), action('重新载入建议', load));nav.replaceChildren();}
      } finally {loading=false;list.setAttribute('aria-busy','false');}
    }
    section.addEventListener('toggle', () => {if(section.open && !loaded)load();});
    add(section, list, nav); add(root, section);
  }
  function taskView(root) {
    add(root, context.heading(intents[current.preset]?.[0] || '整理助手', current.preset && current.preset !== 'custom' ? '按需调查，先给出最值得关注的建议。' : current.goal));
    const panel = node('section', 'panel task-session');
    const summary = node('div', 'task-summary'); summary.setAttribute('role', 'status');
    if (busy()) add(summary, node('span', 'spinner'));
    add(summary, node('p', '', current.summary)); add(panel, summary);
    if (current.last_result && current.last_result.succeeded < current.last_result.total) {
      add(panel, node('p', 'task-result-notice', `还有 ${current.last_result.total-current.last_result.succeeded} 项未完成或待核实，可展开本轮结果查看原因。`));
    }
    if (busy()) {
      add(panel, action('停止', async () => {
        if (!current.job_id) return;
        await request('/api/jobs/' + current.job_id + '/cancel', {});
      }, 'plain-button task-stop'));
    } else if (current.status === 'needs_model') {
      add(panel, context.modelConfigured
        ? action('继续调查', () => request('/api/tasks/' + current.id + '/continue', {}), 'button primary task-primary')
        : action('连接智能建议', context.openSettings, 'button primary task-primary'));
    } else if (current.status === 'awaiting_review' && current.plan) {
      plan(panel);
    } else {
      add(panel, action(current.status === 'completed' ? '继续看看还有什么' : '继续调查', () => request('/api/tasks/' + current.id + '/continue', {}), 'button primary task-primary'));
      feedbackForm(panel, '补充或改变整理要求');
    }
    renderAdvice(panel); results(panel); processDetails(panel);
    add(root, panel);
    if (!busy()) add(root, action('开始新的整理', newTask, 'plain-button task-new'));
  }
  function render(root, helpers) {
    context = helpers;
    if (lastError) add(root, node('p', 'global-message error task-error', lastError));
    if (current) taskView(root); else welcome(root);
    updateApproval();
    if (busy()) schedule();
  }
  function renderHistory(root) {
    if (!context) return;
    const entries = node('div', 'task-history-list');
    const panel = detail('查看以前的整理任务', entries);
    panel.addEventListener('toggle', async () => {
      if (!panel.open || entries.childNodes.length) return;
      try {
        const data = await context.api('/api/tasks');
        data.tasks.forEach((task) => add(entries, action(task.goal, () => {
          accept(task); context.openAssistant();
        }, 'plain-button task-history-item')));
        if (!data.tasks.length) add(entries, node('p', '', '还没有整理任务。'));
      } catch (error) { add(entries, node('p', '', '暂时无法读取任务记录。')); }
    });
    add(root, panel);
  }
  return {render, accept, renderHistory, adviceFor: (id, signature) => (current?.advice || []).find((a) => a.id === id && (!a.signature || a.signature === signature)), currentTask: () => current};
})();
