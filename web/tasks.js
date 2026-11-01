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
  let livePreview = null;
  let questionKey = '', answerOption = '', answerText = '';
  const adviceCache = new Map(), adviceRequests = new Map();
  let adviceGeneration = 0;
  const adviceKey = (id, signature) => `${id}:${signature || ''}`;
  function cacheAdvice(key, value) { adviceCache.delete(key); adviceCache.set(key, value); if(adviceCache.size>400) adviceCache.delete(adviceCache.keys().next().value); }
  function adviceFor(id, signature) { const value=adviceCache.get(adviceKey(id,signature)); return value && !value.stale && (!value.signature || value.signature===signature) ? value : null; }
  function adviceKnown(id, signature) { return !current?.id || adviceCache.has(adviceKey(id,signature)); }
  async function lookupAdvice(items, requester=context?.api) {
    if(!current?.id || typeof requester!=='function') return true;
    const taskId=current.id, generation=adviceGeneration;
    const rows=[...new Map(items.map(item=>[adviceKey(item.id,item.signature),item])).entries()];
    const waiting=rows.map(([key])=>adviceRequests.get(key)).filter(Boolean);
    if(waiting.length) await Promise.all(waiting);
    if(generation!==adviceGeneration || current?.id!==taskId) return false;
    const missing=rows.filter(([key])=>!adviceCache.has(key));
    const requests=[];
    for(let offset=0;offset<missing.length;offset+=50) {
      const group=missing.slice(offset,offset+50);
      const request=Promise.resolve(requester(`/api/tasks/${taskId}/advice?ids=${encodeURIComponent(group.map(([,item])=>item.id).join(','))}&limit=50`)).then(data=>{
        if(generation!==adviceGeneration || current?.id!==taskId) return false;
        const found=new Map((data.items || []).map(item=>[item.id,item]));
        group.forEach(([key,item])=>cacheAdvice(key,found.get(item.id) || null));
        return true;
      }).finally(()=>{group.forEach(([key])=>{if(adviceRequests.get(key)===request) adviceRequests.delete(key);});});
      group.forEach(([key])=>adviceRequests.set(key,request));
      requests.push(request);
    }
    await Promise.all(requests);
    return generation===adviceGeneration && current?.id===taskId;
  }
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

  function stopLivePreview() {
    if (!livePreview) return;
    livePreview.off?.(); livePreview.view.dispose(); livePreview = null;
  }
  function renderLivePreview(panel) {
    if (!window.PanxuStreamView || !window.PanxuStreamChannel || !current?.id) return;
    const section = node('section', 'task-live-reply'); section.hidden = true;
    section.setAttribute('aria-label', '助手的公开回复');
    const body = node('div', 'task-live-body');
    const caption = node('p', 'task-live-caption', '助手正在回复');
    add(section, caption, body); add(panel, section);
    const token = {id: current.id, view: window.PanxuStreamView.create(body), off: null, turn: ''};
    livePreview = token;
    Promise.resolve().then(() => {
      if (livePreview !== token || !section.isConnected) return;
      token.off = window.PanxuStreamChannel.subscribe(token.id, packet => {
        if (livePreview !== token || current?.id !== token.id || !section.isConnected) return;
        const value = packet.stream;
        if (!value) {
          if (['stopped', 'unavailable'].includes(packet.connection)) {
            section.hidden = false; caption.textContent = '暂时无法显示实时回复，调查状态仍会继续更新。';
          }
          return;
        }
        const turn = `${value.epoch}:${value.turn_id}`;
        if (token.turn !== turn) {token.view.reset(); token.turn = turn;}
        section.hidden = value.status === 'cancelled' || (!busy() && current.status !== 'budget_exhausted' && value.status === 'completed');
        const interrupted = packet.incomplete || value.status === 'error';
        let phase = value.status === 'completed' ? '本轮回复已接收' : '正在接收公开回复';
        if (value.tool_pending) phase = value.status === 'completed' ? '工具请求已接收，程序将继续核对' : '工具请求接收中，尚未执行';
        if (value.delivery === 'json_fallback') phase = '已收到完整回复（接口未返回流式数据）';
        if (current.status === 'budget_exhausted' && value.status === 'completed') phase = '本轮回复已接收，调查待继续';
        if (interrupted) phase = '回复中断，以下内容可能不完整';
        if (value.truncated) phase += ' · 仅展示最近一轮的前 8000 字';
        caption.textContent = phase;
        token.view.update({text: value.text, phase: value.tool_pending ? 'tool_pending' : '', status: interrupted ? 'incomplete' : value.status});
      });
    });
  }

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
  function semanticTag(label, tone = 'neutral', cls = '') {
    const tag = node('span', `semantic-tag ${cls}`.trim(), label);
    tag.dataset.tone = tone;
    return tag;
  }
  function explanation(label, text) {
    if (!text) return null;
    return add(node('div', 'task-explanation'), node('span', 'task-explanation-label', label),
      node('p', 'task-explanation-text', text));
  }
  function icon(paths, className) {
    const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');
    Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'}).forEach(([key,value])=>svg.setAttribute(key,value));
    svg.classList.add(className);
    paths.forEach(value=>{const path=document.createElementNS(ns,'path');path.setAttribute('d',value);svg.appendChild(path);});
    return svg;
  }
  function textInput(label, value, handler, textarea = false) {
    const id = 'task-' + (textarea ? 'goal' : 'path');
    const input = node(textarea ? 'textarea' : 'input');
    input.id = id; input.value = value; input.maxLength = textarea ? 2000 : 2000;
    input.addEventListener('input', () => handler(input.value));
    const lab = node('label', 'field-label', label); lab.htmlFor = id;
    return {input, box: add(node('div', 'task-field'), lab, input)};
  }
  function accept(task) {
    if(current?.id!==task?.id || current?.updated_at!==task?.updated_at) {adviceGeneration++;adviceCache.clear();adviceRequests.clear();}
    current = task;
    const nextQuestionKey=task?.question?`${task.id}:${task.question.id}:${task.question.version}`:'';
    if(nextQuestionKey!==questionKey) {questionKey=nextQuestionKey;answerOption='';answerText='';}
    (task?.advice || []).forEach(entry=>cacheAdvice(adviceKey(entry.id,entry.signature),entry));
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
    stopLivePreview();
    adviceGeneration++; adviceCache.clear(); adviceRequests.clear();
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
        const paths={c_cleanup:['M4 5h16v14H4z','M4 14h16','M8 17h.01M11 17h.01'],disk_cleanup:['M4 4h16v6H4z','M4 14h16v6H4z','M8 7h.01M8 17h.01'],directory_cleanup:['M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z','M3 11h18'],duplicates:['M8 3h12v14H8z','M4 7v14h12','M12 7h4M12 11h4']};
        add(card,icon(paths[key],'task-intent-symbol'),add(node('span','task-intent-copy'),node('strong','',intents[key][0]),node('span','',intents[key][1])),icon(['M5 12h14m-5-5 5 5-5 5'],'task-intent-arrow')); add(grid, card);
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
    if (!current?.events?.length && !current?.handoff && !current?.usage && !current?.facts?.total) return;
    const taskId=current.id, box=detail('查看调查过程');
    const handoff=current.handoff;
    if(handoff?.summary) add(box,node('p','',handoff.summary));
    const steps=Array.isArray(handoff?.next_steps)?handoff.next_steps:[];
    if(steps.length) {const list=node('ul','task-events');steps.slice(0,5).forEach(value=>add(list,node('li','',typeof value==='string'?value:value.summary || value.description || '继续核对已有线索')));add(box,node('p','field-label','下一步会继续核对'),list);}
    const unresolved=Array.isArray(handoff?.unresolved)?handoff.unresolved:[];
    if(unresolved.length) {const list=node('ul','task-events');unresolved.slice(0,5).forEach(value=>add(list,node('li','',typeof value==='string'?value:value.summary || value.description || '尚待核对')));add(box,detail('尚未解决的内容',list));}
    if(handoff?.model?.model) add(box,node('p','field-help',`上轮使用 ${handoff.model.model}。继续时会根据当前连接和已保存的目标、事实、选择重建上下文。`));
    if(Array.isArray(current.loaded_skills) && current.loaded_skills.length) {
      const guides=detail('本次参考的指导');
      current.loaded_skills.slice(0,20).forEach(read=>{
        const section=detail(read.name || read.id), body=node('div','task-skill-content');let loaded=false,loading=false,cursor=null,content='';
        const more=action('继续读取指导',()=>load(),'plain-button');more.hidden=true;
        async function load() {
          if(loading)return;loading=true;more.disabled=true;
          try {
            const data=await context.api(`/api/skills/${encodeURIComponent(read.id)}${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`);
            if(!section.isConnected || current?.id!==taskId)return;
            const skill=data.skill || data;
            if(!content){
              body.replaceChildren(node('p','field-help',`参考版本 ${read.version || '未提供'} · 当前版本 ${skill.version || '未提供'}`));
              if(read.sha256 && skill.sha256!==read.sha256)add(body,node('p','field-help','这份指导已更新，下面展示当前版本。'));
              if(skill.summary)add(body,node('p','',skill.summary));
              const sources=(skill.sources || []).map(source=>`${source.title || '参考资料'}：${source.url || ''}`).join('\n');
              if(sources)add(body,detail('来源与核对日期',node('p','field-help',`资料核对日期：${skill.checked_at || '未提供'}`),node('p','task-skill-sources',sources)));
              add(body,node('pre','developer-json'));
            }
            content+=(skill.content || '');body.querySelector('pre').textContent=content;
            cursor=skill.next_cursor;more.hidden=!skill.has_more;loaded=true;
          } catch(error) {add(body,node('p','field-error',error.message));loaded=false;}
          finally {loading=false;more.disabled=false;}
        }
        section.addEventListener('toggle',()=>{if(section.open&&!loaded)load();});add(section,body,more);add(guides,section);
      });add(box,guides);
    }
    const usage=current.usage;
    if(usage) {
      const rows=node('dl','task-workload');
      const fields=[['model_requests','模型请求',n=>`${n} 次`],['tool_calls','工具调用',n=>`${n} 次`],['tool_failures','工具未完成',n=>`${n} 次`],['metadata_items','采集条目',n=>`${n} 项`],['scan_pages','扫描批次',n=>`${n} 批`],['hash_bytes','本地哈希读取',n=>context.bytes(n)],['input_tokens','输入 token',n=>n.toLocaleString('zh-CN')],['output_tokens','输出 token',n=>n.toLocaleString('zh-CN')],['elapsed_ms','累计调查用时',n=>n<60000?`${Math.round(n/1000)} 秒`:`${(n/60000).toFixed(1)} 分钟`]];
      fields.forEach(([key,label,format])=>{if(typeof usage[key]==='number' && Number.isFinite(usage[key]))add(rows,node('dt','',label),node('dd','',format(usage[key])));});
      if(rows.childNodes.length)add(box,detail('累计工作量',rows,node('p','field-help',
        `${usage.input_tokens == null && usage.output_tokens == null ? '服务商未返回 token 用量。' : ''}累计已记录的请求和本地工作；未记录的用量不推算为零，也不等于实际释放空间。`)));
    }
    if(Array.isArray(current.scans) && current.scans.length) {
      const scans=node('ul','task-events');
      current.scans.slice(0,20).forEach(scan=>{
        const coverage=scan.coverage || {}, count=Number.isFinite(coverage.observed_entries)?coverage.observed_entries.toLocaleString('zh-CN'):'若干';
        const state=coverage.invalidated?'已有变化，需重新核对':coverage.limit_reached?'达到本次检查上限':coverage.complete?'目录观察完成':'尚未完整检查';
        add(scans,node('li','',`${scan.root_name || '所选目录'}：已观察 ${count} 项，${state}。`));
      });
      add(box,detail('目录检查范围',scans,node('p','field-help','这里是目录观察的覆盖情况，不表示每个文件都已获得建议，也不代替处理前核查。最多展示 20 处。')));
    }
    if(current.facts?.total) {
      const facts=detail(`已保存的事实（${current.facts.total} 条）`),list=node('div','task-facts'),nav=node('div','file-pagination');
      let offset=0,loading=false,loaded=false;
      async function load() {
        if(loading || !facts.isConnected)return;loading=true;list.setAttribute('aria-busy','true');nav.querySelectorAll('button').forEach(button=>button.disabled=true);
        if(!loaded)list.replaceChildren(node('p','muted','正在载入已有事实…'));
        try {
          const data=await context.api(`/api/tasks/${taskId}/facts?offset=${offset}&limit=20`);
          if(!facts.isConnected || current?.id!==taskId)return;loaded=true;list.replaceChildren();nav.replaceChildren();
          (data.items || []).forEach(entry=>{
            const labels={scan_directory:'目录观察',survey_scope:'目录概况',inspect_directory:'深入目录',continue_scan:'继续目录观察',read_scan:'目录观察分页',validate_directory:'目录完整核查',list_items:'清单查询',inspect_items:'条目信息',describe_item:'条目信息',inspect_item:'条目信息',inspect_files:'文件信息',compare_duplicates:'重复核对',compare_files:'重复核对',read_facts:'已有事实',collect_apps:'软件与后台',list_applications:'软件与后台',cache_experience:'缓存经验',lookup_knowledge:'经验查询'};
            const stamp=entry.at?new Date(entry.at).toLocaleString('zh-CN'):'';
            const item=detail(`${labels[entry.tool] || '调查观察'}${stamp?' · '+stamp:''}`);
            add(item,node('p','field-help','这是当时采集的观察，执行前仍需核对当前状态。'));
            item.addEventListener('toggle',()=>{if(item.open&&!item.querySelector('pre'))add(item,node('pre','developer-json',JSON.stringify(entry,null,2)));});
            add(list,item);
          });
          if(!(data.items || []).length)add(list,node('p','muted','该页没有事实记录。'));
          if(data.total>20 || offset){const previous=action('上一页事实',()=>{offset=Math.max(0,offset-20);load();});previous.disabled=!offset;const next=action('下一页事实',()=>{offset+=20;load();});next.disabled=!data.has_more;add(nav,previous,node('span','',`${Math.floor(offset/20)+1} / ${Math.max(1,Math.ceil((data.total || 0)/20))}`),next);}
        } catch(error) {if(facts.isConnected){list.replaceChildren(node('p','field-error',error.message),action('重新载入事实',load));nav.replaceChildren();}}
        finally{loading=false;list.setAttribute('aria-busy','false');}
      }
      facts.addEventListener('toggle',()=>{if(facts.open&&!loaded)load();});add(facts,list,nav);add(box,facts);
    }
    const list = node('ol', 'task-events');
    (current.events || []).filter((event) => ['tool', 'result', 'feedback', 'summary'].includes(event.kind)).slice(-12).forEach((event) => add(list,node('li','',event.message)));
    if(list.childNodes.length)add(box,detail('最近调查记录',list));
    add(root,box);
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
  function question(root) {
    const q=current.question, taskId=current.id;
    const form=node('form','task-question'), choices=node('fieldset','task-question-options');
    add(choices,node('legend','field-label task-question-title',q.question));
    const submit=node('button','button primary task-primary','继续');submit.type='submit';
    const update=()=>{submit.disabled=pending || (!answerOption && !answerText.trim());};
    q.options.forEach(option=>{
      const label=node('label','task-question-option'), radio=node('input');
      radio.type='radio';radio.name='task-answer';radio.value=option.id;radio.checked=answerOption===option.id;radio.disabled=pending;
      radio.addEventListener('change',()=>{answerOption=option.id;update();});
      const copy=node('span','task-option-copy');add(copy,node('strong','task-option-label',option.label));
      if(option.description)add(copy,node('span','field-help task-option-description',option.description));
      add(choices,add(label,radio,copy));
    });
    const input=node('textarea');input.value=answerText;input.maxLength=2000;input.disabled=pending;
    input.setAttribute('aria-label','补充或用自己的话回答');input.placeholder='也可以直接写下你的想法。';
    input.addEventListener('input',()=>{answerText=input.value;update();});
    form.addEventListener('submit',event=>{
      event.preventDefault();if(submit.disabled || current?.id!==taskId || current.question?.id!==q.id || current.question?.version!==q.version)return;
      request(`/api/tasks/${taskId}/answer`,{version:q.version,question_id:q.id,...(answerOption?{option_id:answerOption}:{}),...(answerText.trim()?{text:answerText.trim()}:{})});
    });
    add(form,choices,detail('补充或用自己的话回答',input),add(node('div','task-question-footer'),submit,
      action('暂不回答',()=>request(`/api/tasks/${taskId}/answer`,{version:q.version,question_id:q.id,skip:true}),'plain-button')));
    update();add(root,form);
  }
  async function directAction(taskId,item,operation) {
    if(pending || current?.id!==taskId || busy())return;
    pending=true;lastError='';redraw();
    try {
      const prepared=await context.api(`/api/tasks/${taskId}/direct-plan`,{ids:[item.id],action:operation,snapshots:{[item.id]:item.signature}});
      if(current?.id!==taskId)return;
      const plan=prepared.plan || prepared.task?.plan;
      if(prepared.task?.id!==taskId || !plan || plan.items?.length!==1 || plan.items[0].id!==item.id || plan.items[0].action!==operation)throw new Error('处理对象已变化，请重新核对后选择。');
      accept(prepared.task);
      const result=await context.api(`/api/tasks/${taskId}/approve`,{version:plan.version,ids:[item.id]});
      if(current?.id===taskId && result.task)accept(result.task);
      if(!busy())await context.refreshCatalog();
    } catch(error) {lastError=error.message || '处理暂时未完成，已有记录会保留。';}
    finally {pending=false;redraw();}
  }
  function directControls(card,entry,taskId) {
    if(entry.stale || entry.user_kept)return;
    const box=detail('自行处理'), body=node('div','task-direct-options');box.classList.add('task-direct');let loaded=false;
    box.addEventListener('toggle',async()=>{
      if(!box.open || loaded)return;loaded=true;body.replaceChildren(node('p','field-help','正在核对可用操作…'));
      try {
        const {item}=await context.api(`/api/item?id=${encodeURIComponent(entry.id)}`);
        if(!box.isConnected || current?.id!==taskId)return;
        body.replaceChildren();
        if(!item || item.signature!==entry.signature || item.simulated || item.risk==='protected') {add(body,node('p','field-help','当前内容已变化或受保护，请重新核对。'));return;}
        const names={delete:item.kind==='directory'?'确认清理这个文件夹':'确认清理这份文件',disable_startup:'确认关闭此开机启动项',stop_service:'确认停止此服务',stop_process:'确认结束此应用',uninstall:'确认卸载此软件'};
        add(body,add(node('div','item-title-line'),window.PanxuIcons?.create(item),node('strong','',item.name)),node('p','task-path',item.path),node('p','field-help',entry.impact),
          node('p','field-help','由程序直接处理并核对，再把结果告知助手。测试保护下仍只记录预演。'));
        const available=(item.actions || []).filter(name=>Object.hasOwn(names,name));
        available.forEach(operation=>add(body,action(names[operation],()=>directAction(taskId,item,operation),'button')));
        if(!available.length)add(body,node('p','field-help','这项内容暂时没有可用的处理操作。'));
      } catch(error) {body.replaceChildren(node('p','field-error',error.message));loaded=false;}
    });add(box,body);add(card,box);
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
      const titleLine=node('div','item-title-line');
      add(titleLine,window.PanxuIcons?.create(item),title);
      add(copy, titleLine, add(node('div', 'task-plan-meta'),
        semanticTag(actionNames[item.action] || item.action, 'info', 'task-action-label'),
        item.personal_choice_required ? semanticTag('由你选择', 'warning', 'task-choice-note') : null),
        explanation('建议原因', item.reason));
      const info = detail('了解影响', explanation('影响', item.impact), node('p', 'task-path', item.path),
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
    const pendingCount=Math.max(0,result.total-result.succeeded);
    const box = detail(`本轮结果：${result.succeeded} 项预演完成${pendingCount?`，${pendingCount} 项未完成或待核实`:''}`,
      node('p', 'result-protection-note', '测试保护下完成预演，实际文件和系统未改动。'));
    box.className += ' task-results';
    const tones = {succeeded:'info', failed:'danger', unverified:'warning', cancelled:'neutral', running:'warning'};
    result.items.forEach((item) => {
      const undone = item.current_state === 'undone';
      const row = node('article', 'task-result-item');
      const heading = add(node('div', 'task-result-heading'),
        add(node('div', 'item-title-line'), window.PanxuIcons?.create(item), node('strong', 'task-result-name', item.name)),
        semanticTag(undone ? '已撤销预演' : labels[item.status] || '待核实', undone ? 'neutral' : tones[item.status] || 'warning', 'task-result-state'));
      add(row, heading, node('p', 'task-result-message', undone ? '已撤销这次预演。' : item.message));
      add(box, row);
    });
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
          const label = entry.stale ? '内容已变化，需重新核对' : entry.user_kept ? '你已选择保留' : labels[entry.conclusion] || '待确认';
          const tone = entry.stale ? 'warning' : entry.user_kept ? 'neutral' : {keep:'neutral', review:'warning', recommend:'info'}[entry.conclusion] || 'warning';
          add(card, add(node('div', 'task-advice-header'),
            add(node('div','item-title-line task-advice-title-line'),window.PanxuIcons?.create(entry),node('strong', 'task-advice-title', entry.name)),
            semanticTag(label, tone, 'task-advice-state')),
            node('p', 'task-purpose', entry.purpose), explanation('建议原因', entry.reason), detail('了解影响', explanation('影响', entry.impact)));
          if(!busy())directControls(card,entry,taskId);
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
    if(!current.question || busy()){
      const copy = node('div', 'task-summary-copy');
      const statuses = {completed:['本轮建议','info'], error:['调查未完成','danger'], paused:['已暂停','neutral'],
        awaiting_review:['待你确认','warning'], budget_exhausted:['调查待继续','warning']};
      if(!busy() && statuses[current.status])add(copy, semanticTag(...statuses[current.status], 'task-status-label'));
      add(copy, node('p', 'task-summary-text', current.summary));add(summary, copy);
    }
    if(summary.childNodes.length)add(panel, summary);
    if(!busy() && context.modelProfile?.model && current.handoff?.model?.model && ['provider','model','protocol'].some(key=>(context.modelProfile[key] || '')!==(current.handoff.model[key] || ''))) add(panel,node('p','field-help',`继续调查将使用当前连接的 ${context.modelProfile.model}，已有目标、事实和保留选择会继续使用。`));
    if (current.last_result && current.last_result.succeeded < current.last_result.total) {
      add(panel, node('p', 'task-result-notice', `还有 ${current.last_result.total-current.last_result.succeeded} 项未完成或待核实，可展开本轮结果查看原因。`));
    }
    if (busy()) {
      renderLivePreview(panel);
      add(panel, action('停止', async () => {
        if (!current.job_id) return;
        await request('/api/jobs/' + current.job_id + '/cancel', {});
      }, 'plain-button task-stop'));
    } else if (current.question) {
      question(panel);
    } else if (current.status === 'needs_model') {
      add(panel, context.modelConfigured
        ? action('继续调查', () => request('/api/tasks/' + current.id + '/continue', {}), 'button primary task-primary')
        : action('连接智能建议', context.openSettings, 'button primary task-primary'));
    } else if (current.status === 'awaiting_review' && current.plan) {
      plan(panel);
    } else {
      add(panel, action('继续调查', () => request('/api/tasks/' + current.id + '/continue', {}), 'button primary task-primary'));
      feedbackForm(panel, '补充或改变整理要求');
    }
    if (!busy() && ['error', 'paused', 'budget_exhausted'].includes(current.status)) renderLivePreview(panel);
    results(panel); renderAdvice(panel); processDetails(panel);
    add(root, panel);
    if (!busy()) add(root, action('开始新的整理', newTask, 'plain-button task-new'));
  }
  function render(root, helpers) {
    stopLivePreview();
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
        data.tasks.forEach((task) => {
          const button=action(task.goal, () => {accept(task); context.openAssistant();}, 'plain-button task-history-item');
          const date=Number.isFinite(Date.parse(task.updated_at))?new Date(task.updated_at).toLocaleString('zh-CN'):'';
          add(button,node('span','task-history-caption',[date,(task.summary || '').slice(0,140)].filter(Boolean).join(' · ')));
          add(entries,button);
        });
        if (!data.tasks.length) add(entries, node('p', '', '还没有整理任务。'));
      } catch (error) { add(entries, node('p', '', '暂时无法读取任务记录。')); }
    });
    add(root, panel);
  }
  return {render, accept, renderHistory, adviceFor, adviceKnown, lookupAdvice, currentTask: () => current, streamTaskId: () => current?.id || null, unmountLive: stopLivePreview};
})();
