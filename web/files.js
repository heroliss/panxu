'use strict';

// A bounded page, not an ever-growing DOM. Selection belongs to the workspace.
window.PanxuFiles = (() => {
  const limit = 50, cache = new Map();
  let context, host, status, pager, scopeInput, searchInput, riskInput;
  let query = '', scope = '', risk = 'all', offset = 0, revision = '', page = null;
  let controller, sequence = 0, debounce, loading = false;
  const node = (tag, cls, text) => {
    const n = document.createElement(tag); if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text); return n;
  };
  function button(label, run, cls = 'button') {
    const b = node('button', cls, label); b.type = 'button'; b.addEventListener('click', run); return b;
  }
  function locationLabels(items) {
    const rows=items.map(item=>{
      const path=String(item.path || ''), separator=path.includes('\\')?'\\':'/';
      const parts=path.split(/[\\/]/).filter(Boolean);let prefix=path.startsWith('/')?'/':'';
      if(/^[a-z]:$/i.test(parts[0] || ''))prefix=parts.shift();
      else if(path.startsWith('\\\\'))prefix='\\\\'+parts.splice(0,2).join('\\');
      if(parts.at(-1)?.toLowerCase()===String(item.name || '').toLowerCase())parts.pop();
      return {item,parts,prefix,separator,depth:2};
    });
    const label=row=>`${row.prefix?row.prefix.replace(/[\\/]$/,'')+row.separator:''}${row.parts.length>row.depth?'…'+row.separator:''}${row.parts.slice(-row.depth).join(row.separator)}` || '所选位置';
    for(let round=0;round<100;round++) {
      const groups=new Map();rows.forEach(row=>{const key=`${row.item.name}\n${label(row)}`.toLowerCase();if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);});
      let changed=false;
      groups.forEach(group=>{if(group.length>1)group.forEach(row=>{if(row.depth<row.parts.length){row.depth++;changed=true;}});});
      if(!changed)break;
    }
    return new Map(rows.map(row=>[row.item.id,label(row)]));
  }
  function select(label, entries, value, change) {
    const s = node('select'); s.setAttribute('aria-label', label);
    entries.forEach(([key, label]) => { const o = node('option', '', label); o.value = key; s.append(o); });
    s.value = value; s.addEventListener('change', () => change(s.value)); return s;
  }
  function invalidate(token) {
    if (token && token !== revision) {
      revision = token; offset = 0; page = null; cache.clear();
      if (host?.isConnected) load();
    }
  }
  function unmount() { sequence++; controller?.abort(); clearTimeout(debounce); host = null; }
  function render(root, helpers) {
    unmount(); context = helpers; invalidate(helpers.revision);
    const tools = node('div', 'toolbar file-toolbar');
    searchInput = node('input', 'search-input'); searchInput.type = 'search';
    searchInput.placeholder = '搜索名称或位置'; searchInput.value = query;
    searchInput.setAttribute('aria-label', '搜索已检查的文件');
    searchInput.addEventListener('input', () => {
      query = searchInput.value; offset = 0; clearTimeout(debounce);
      sequence++; controller?.abort(); loading = true;
      status.textContent = '正在准备搜索…'; host.setAttribute('aria-busy', 'true');
      host.querySelectorAll('input,button').forEach((n) => { n.disabled = true; });
      pager.querySelectorAll('button').forEach((n) => { n.disabled = true; });
      debounce = setTimeout(load, 220);
    });
    const change = () => { offset = 0; clearTimeout(debounce); load(); };
    scopeInput = select('筛选磁盘', [['', '所有检查位置'], ...helpers.disks.map((d) => [d.path || d.name, d.name || d.path])], scope,
      (value) => { scope = value; change(); });
    riskInput = select('筛选文件', [['all', '全部内容'], ['low', '可处理'], ['review', '由你决定'], ['protected', '已保留']], risk,
      (value) => { risk = value; change(); });
    tools.append(searchInput, scopeInput, riskInput);
    status = node('p', 'file-page-status'); status.setAttribute('role', 'status');
    host = node('div', 'file-page-host'); host.id = 'file-page';
    pager = node('div', 'file-pagination');
    const navigation = node('div', 'file-page-navigation'); navigation.append(status, pager);
    root.append(tools, navigation, host);
    load();
  }
  async function load(retry = false) {
    if (!host?.isConnected) return;
    const run = ++sequence; controller?.abort(); controller = new AbortController();
    const target = host; loading = true; target.setAttribute('aria-busy', 'true');
    status.textContent = '正在载入文件…';
    target.querySelectorAll('input,button').forEach((n) => { n.disabled = true; });
    pager.querySelectorAll('button').forEach((n) => { n.disabled = true; });
    const params = new URLSearchParams({offset, limit, query, scope, risk, revision});
    const key = params.toString();
    const timeout = setTimeout(() => { if (run === sequence) controller.abort(); }, 15000);
    try {
      let data = cache.get(key);
      if (!data) {
        const response = await fetch('/api/files?' + key, {signal: controller.signal, credentials: 'same-origin'});
        data = await response.json();
        if (run !== sequence) return;
        if (!response.ok) {
          if (data.code === 'catalog_changed' && !retry) {
            revision = ''; cache.clear(); offset = 0; await load(true); return;
          }
          throw new Error(data.error || '文件暂时无法载入。');
        }
        cache.set(key, data); if (cache.size > 8) cache.delete(cache.keys().next().value);
      }
      if (run !== sequence || !target.isConnected) return;
      revision = data.revision; page = data; loading = false;
      context.ingest(data.items); paint(); context.changed(); refreshAdvice();
    } catch (error) {
      if (run !== sequence || !target.isConnected) return;
      status.textContent = error.name === 'AbortError' ? '载入时间较长，请重试。' : error.message;
      target.replaceChildren(button('重新载入', () => load())); pager.replaceChildren();
    } finally {
      clearTimeout(timeout); if (run === sequence) { loading = false; target.setAttribute('aria-busy', 'false'); }
    }
  }
  function selectionChanged() {
    if (!page || !host?.isConnected) return;
    host.querySelectorAll('[data-file-check]').forEach((check) => {
      const item = page.items.find((i) => i.id === check.dataset.fileCheck);
      check.checked = context.isSelected(item); check.disabled = loading || !context.canSelect(item);
    });
    const all = host.querySelector('[data-page-check]');
    const allowed = page.items.filter(context.canSelect), count = allowed.filter(context.isSelected).length;
    if (all) { all.disabled = loading || !allowed.length; all.checked = !!allowed.length && count === allowed.length; all.indeterminate = count > 0 && count < allowed.length; }
    const batch = host.querySelector('[data-analyze-list]');
    if (batch) { const selected = context.selectionCount(); batch.textContent = selected ? `分析所选（${selected} 项）` : `分析当前列表（${page.total.toLocaleString('zh-CN')} 项）`; batch.disabled = loading || !page.total; batch.hidden = selected > 0; }
  }
  function refreshAdviceLabels() {
    if(!host?.isConnected || !page) return;
    host.querySelectorAll('[data-file-id]').forEach(row=>{
      const item=page.items.find(entry=>entry.id===row.dataset.fileId); if(!item) return;
      const result=context.advice(item), labels={keep:'建议保留',review:'需要确认',recommend:'已有处理建议'};
      const label=row.querySelector('.analysis-state');
      if(label) label.textContent=result ? labels[result.conclusion] || '已有建议' : context.adviceKnown(item) ? item.risk==='protected' ? '已保护' : '待分析' : '待核对';
      const action=row.querySelector('.row-analyze'); if(action)action.textContent=result?'查看建议':'分析';
    });
  }
  async function refreshAdvice() {
    if(!page || !host?.isConnected || !context.lookupAdvice) return;
    const target=host, currentPage=page, run=sequence;
    const note=target.querySelector('.file-advice-note');
    try {
      const complete=await context.lookupAdvice(currentPage.items);
      if(run!==sequence || host!==target || page!==currentPage || !target.isConnected) return;
      refreshAdviceLabels();
      if(note) {note.hidden=complete!==false;note.textContent='调查记录已有更新，查看时将重新核对。';}
    } catch(error) {
      if(run!==sequence || host!==target || page!==currentPage || !target.isConnected) return;
      if(note) {note.hidden=false;note.textContent='暂时无法读取已存建议。点击“分析”时会先重试读取，不会直接重复调用模型。';}
    }
  }
  function paint() {
    host.replaceChildren(); pager.replaceChildren();
    const start = page.total ? page.offset + 1 : 0, end = Math.min(page.offset + limit, page.total);
    status.textContent = page.total ? `显示 ${start}–${end} 项，共 ${page.total.toLocaleString('zh-CN')} 项 · 按体积排列` :
      page.catalog_total ? '没有符合条件的文件。可以换个关键词或筛选。' : '还没有文件清单。展开上方“选择检查位置”开始检查，或回到整理助手。';
    if (!page.total) return;
    const panel = node('div', 'panel file-page-panel');
    const top = node('div', 'file-page-actions'); const label = node('label', 'check-line');
    const all = node('input'); all.type = 'checkbox'; all.dataset.pageCheck = '1';
    label.append(all, document.createTextNode('全选本页用于分析'));
    all.addEventListener('change', () => {
      page.items.filter(context.canSelect).forEach((i) => context.toggle(i, all.checked));
      selectionChanged(); context.changed();
    });
    const analyze = button('分析当前列表', () => context.analyze(null, {query,scope,risk}), 'button primary'); analyze.dataset.analyzeList = '1';
    top.append(label, analyze); panel.append(top);
    const adviceNote=node('p','file-advice-note field-help');adviceNote.hidden=true;adviceNote.setAttribute('role','status');panel.append(adviceNote);
    const locations=locationLabels(page.items);
    page.items.forEach((item) => {
      const row = node('div', 'paged-file-row'); row.dataset.fileId = item.id;
      const check = node('input'); check.type = 'checkbox'; check.dataset.fileCheck = item.id;
      check.setAttribute('aria-label', '选择分析' + item.name);
      check.addEventListener('change', () => { context.toggle(item, check.checked); selectionChanged(); context.changed(); });
      const copy = node('div', 'paged-file-copy'); const title = button(item.name, () => context.show(item), 'file-name-button');
      title.title = item.path;
      const location = node('small', '', locations.get(item.id)); location.title = item.path;
      location.setAttribute('aria-label','完整位置：'+item.path);
      copy.append(title, location);
      const result = context.advice(item);
      const labels = {keep:'建议保留',review:'需要确认',recommend:'已有处理建议'};
      const meta = node('div', 'paged-file-meta'); meta.append(node('span', '', context.bytes(item.size_bytes)),
        node('small', 'analysis-state', result ? labels[result.conclusion] || '已有建议' : context.adviceKnown(item) ? item.risk === 'protected' ? '已保护' : '待分析' : '待核对'));
      const analyze = button(result ? '查看建议' : '分析', () => context.showAdvice(item), 'button small row-analyze');
      row.append(check, copy, meta, analyze); panel.append(row);
    });
    host.append(panel); selectionChanged();
    const prev = button('上一页', () => { offset = Math.max(0, offset - limit); load(); }); prev.disabled = !page.offset;
    const next = button('下一页', () => { offset += limit; load(); }); next.disabled = !page.has_more;
    pager.append(prev, node('span', '', `第 ${Math.floor(page.offset / limit) + 1} / ${Math.ceil(page.total / limit)} 页`), next);
  }
  return {render, unmount, invalidate, selectionChanged, refreshAdvice, filters: () => ({query,scope,risk}), visibleIds: () => (page?.items || []).map((i) => i.id)};
})();
