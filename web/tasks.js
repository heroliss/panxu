'use strict';

// Text can also be a navigation target or a label. A drag on that same
// target must finish selecting text, without activating its click/default.
// Existing selections elsewhere and keyboard activations remain untouched.
window.PanxuTextSelection = (() => {
  const installed=new WeakSet();
  function snapshot(selection) {
    if(!selection || selection.isCollapsed || !selection.toString())return null;
    return {anchor:selection.anchorNode,anchorOffset:selection.anchorOffset,
      focus:selection.focusNode,focusOffset:selection.focusOffset};
  }
  function same(left,right) {
    return Boolean(left&&right&&left.anchor===right.anchor&&left.anchorOffset===right.anchorOffset&&
      left.focus===right.focus&&left.focusOffset===right.focusOffset);
  }
  function target(event) {
    const element=event.target?.closest?event.target:event.target?.parentElement;
    if(element?.closest?.('input,textarea,select,option'))return null;
    return element?.closest?.('button.file-name-button,label,summary') || null;
  }
  function install(doc) {
    if(!doc?.addEventListener || installed.has(doc))return;
    installed.add(doc);
    let gesture=null;
    const current=()=>snapshot(doc.getSelection?.());
    doc.addEventListener('pointerdown',event=>{
      const control=event.button===0?target(event):null;
      gesture=control?{control,before:current()}:null;
    },true);
    doc.addEventListener('pointercancel',()=>{gesture=null;},true);
    doc.addEventListener('click',event=>{
      const active=gesture;gesture=null;
      if(!active || event.detail===0 || target(event)!==active.control)return;
      const selected=current();
      if(!selected || !(active.control.contains?.(selected.anchor)||active.control.contains?.(selected.focus)))return;
      if(same(active.before,selected))return;
      event.preventDefault();event.stopImmediatePropagation();
    },true);
  }
  install(typeof document==='undefined'?null:document);
  return {install};
})();

// Shared, pure action wording. It describes intent without granting permission.
window.PanxuActionCopy = (() => {
  const names = {disable_startup:'禁用开机启动', stop_service:'停止服务', stop_process:'结束进程', uninstall:'卸载软件'};
  function label(action, kind) {
    if(action === 'delete')return kind === 'directory' ? '删除文件夹' : kind === 'file' ? '删除文件' : '删除文件或文件夹';
    return Object.hasOwn(names, action) ? names[action] : '未知操作';
  }
  function counted(action, kind, count) {
    if(action === 'delete')return `删除 ${count} ${kind === 'directory' ? '个文件夹' : kind === 'file' ? '个文件' : '项'}`;
    return ({disable_startup:`禁用 ${count} 个开机启动项`, stop_service:`停止 ${count} 个服务`,
      stop_process:`结束 ${count} 个进程`, uninstall:`卸载 ${count} 个软件`})[action] || `未知操作 ${count} 项`;
  }
  function summary(items) {
    const groups = new Map();
    for(const item of Array.isArray(items) ? items : []){
      if(!item || typeof item !== 'object')continue;
      const action = item.action === 'delete' || Object.hasOwn(names, item.action) ? item.action : '';
      const kind = action === 'delete' ? (['file','directory'].includes(item.kind) ? item.kind : 'unknown') : '';
      const key = `${action}:${kind}`;
      if(!groups.has(key))groups.set(key, {action,kind,count:0,label:label(action,kind)});
      groups.get(key).count++;
    }
    const rows = [...groups.values()].map(group => ({...group,text:counted(group.action,group.kind,group.count)}));
    const count = rows.reduce((total,group) => total + group.count, 0);
    const actions = new Set(rows.map(group => group.action));
    const deletion = actions.size === 1 && actions.has('delete');
    const kinds = new Set(rows.map(group => group.kind));
    const emptyButton = deletion ? `选择要删除的${kinds.size === 1 && kinds.has('directory') ? '文件夹' : kinds.size === 1 && kinds.has('file') ? '文件' : '文件或文件夹'}` : '选择要执行的操作';
    let button = emptyButton;
    if(count){
      if(rows.length === 1 && rows[0].action)button = rows[0].text;
      else if(deletion)button = `删除 ${count} 项`;
      else button = `确认执行 ${count} 项`;
    }
    const folderNote = rows.some(group=>group.action === 'delete' && group.kind === 'directory') ? '删除文件夹的范围包含内部文件。' : '';
    const selectedButton=actions.size===1?({delete:'删除勾选项',disable_startup:'禁用勾选的启动项',stop_service:'停止勾选的服务',stop_process:'结束勾选的进程',uninstall:'卸载勾选的软件'})[[...actions][0]] || '执行勾选项':'执行勾选项';
    return {count,groups:rows,breakdown:rows.map(group=>group.text).join('、'),button,emptyButton,selectedButton,
      effect:'请核对所选项目、具体操作与影响。'+folderNote};
  }
  return {label,summary};
})();

// Shared, readable explanations. Capabilities and source text stay distinct
// from model judgement; missing evidence is never filled with a guessed score.
window.PanxuAdvicePresentation = (() => {
  const text=value=>typeof value==='string'?value.trim():'';
  function render(record={}, {direct=false,suggestion='',compactUnknown=false}={}){
    const value=window.PanxuAssessment.read(record,{direct}),assessment=record.assessment&&typeof record.assessment==='object'?record.assessment:record;
    const analysed=value.state!=='unanalysed',operation=record.action,action=operation?window.PanxuActionCopy.label(operation,record.kind):'';
    const operationAdvice=Boolean(text(suggestion))||record.origin==='user_direct'||record.provenance?.source==='llm'&&Boolean(operation);
    if(compactUnknown&&!analysed&&!operationAdvice){const pending=document.createElement('p');pending.className='inventory-advice-pending field-help';pending.textContent='尚未分析；取得建议后会直接显示用途、建议、原因和影响。';return pending;}
    const local=body=>body?'本机说明：'+body:'';
    const impactSource=direct&&Boolean(record.action)?record:assessment;
    const rawImpact=impactSource.impact_rating;
    const impactRating=!value.stale&&Number.isInteger(rawImpact)&&rawImpact>=1&&rawImpact<=5?rawImpact:null;
    const impactLabels={1:'影响很小',2:'影响轻微',3:'影响明显',4:'影响较大',5:'影响重大'};
    const impactDescription=value.stale?'影响程度需要重新核对。':impactRating===null?'影响程度尚未提供。':`影响程度 ${impactRating}/5，${impactLabels[impactRating]}。`;
    const purpose=text(assessment.purpose)||text(record.purpose),reason=text(assessment.reason)||text(record.reason),impact=text(impactSource.impact);
    let advice=text(suggestion);
    if(!advice){
      if(value.stale)advice='已有建议需要重新核对。';
      else if((assessment.specialist || record.specialist)?.mode==='replace')advice='建议通过专项方案处理，保留目录或程序主体。';
      else if(assessment.extra_suggestion_id || record.extra_suggestion_id)advice='建议先查看专项方案，再选择处理方式。';
      else if(value.state==='skipped')advice='本轮已跳过，未给出处理建议。';
      else if(value.state==='no_advice')advice='暂未形成操作建议。';
      else if(value.state==='unanalysed')advice=action&&operationAdvice?`已有操作建议：${action}；尚未评分。`:'尚未分析，未形成操作建议。';
      else if(value.conclusion==='keep'||[1,2].includes(value.rating))advice=value.rating===2?'倾向保留。':'建议保留。';
      else if(value.conclusion==='recommend')advice=action?`建议${action}。`:['file','directory'].includes(record.kind)?'建议清理。':'倾向调整，具体操作待确认。';
      else if(action&&[4,5].includes(value.rating))advice=`倾向${action}，是否执行由你决定。`;
      else if(value.rating===3)advice='清理与保留各有理由，由你决定。';
      else if(value.rating===null)advice='已分析，尚未给出倾向分；由你决定。';
      else advice=['file','directory'].includes(record.kind)?'倾向清理，是否执行由你决定。':'倾向调整，具体操作待确认。';
    }
    const fields=[['purpose','用途',purpose?(analysed?purpose:local(purpose)):'暂未说明用途。'],['suggestion','建议',advice],
      ['reason','原因',reason?(analysed||operationAdvice?reason:local(reason)):'暂无分析依据。'],['impact','影响',impact || text(record.action_effect) || '影响尚未说明，执行前需要核对。']];
    const group=document.createElement('section');group.className='advice-explanation-grid';group.dataset.rating=value.rating===null?'':String(value.rating);group.dataset.assessmentState=value.state;if(value.stale)group.dataset.stale='true';group.setAttribute('aria-label','用途、建议、原因与影响');
    for(const [key,label,body] of fields){const block=document.createElement('div'),heading=document.createElement('span'),content=document.createElement('p');block.className='advice-explanation-part';block.dataset.advicePart=key;
      if(key==='impact'){block.dataset.impactRating=impactRating===null?'':String(impactRating);block.dataset.impactState=value.stale?'stale':impactRating===null?'unknown':'rated';block.setAttribute('role','group');block.setAttribute('aria-label','影响；'+impactDescription);block.title=impactDescription;}
      heading.className='advice-explanation-label';heading.textContent=label;content.textContent=body;block.append(heading,content);if(key==='suggestion'){const link=window.PanxuSpecialistLinks?.create(record);if(link)block.append(link);}group.append(block);}
    return group;
  }
  return {render};
})();

// A score is advisory; it never changes action permission or user consent.
window.PanxuAssessment = (() => {
  const states=new Set(['explicit','inherited','unanalysed','skipped','no_advice']);
  const labels={1:'建议保留',2:'倾向保留',3:'待你决定',4:'倾向清理',5:'建议清理'};
  function read(record={}, {direct=false}={}) {
    const value=record.assessment && typeof record.assessment==='object'?record.assessment:record;
    const state=states.has(value.state)?value.state:states.has(value.analysis_state)?value.analysis_state:direct?({skip:'skipped',no_advice:'no_advice'}[value.conclusion] || 'explicit'):'unanalysed';
    const raw=Object.hasOwn(value,'rating')?value.rating:value.cleanup_rating;
    const rating=['explicit','inherited'].includes(state)&&Number.isInteger(raw)&&raw>=1&&raw<=5?raw:null;
    return {...value,state,rating,stale:value.stale===true,blocked_reasons:Array.isArray(value.blocked_reasons)?value.blocked_reasons:[]};
  }
  function label(record,options) {
    const value=read(record,options);
    if(value.stale)return '建议需重新核对';
    if(value.rating!==null)return `${value.rating}/5 · ${labels[value.rating]}${value.state==='inherited'?' · 继承':''}`;
    return ({explicit:'未评分',inherited:'继承建议 · 未评分',unanalysed:'尚未分析',skipped:'已跳过评分',no_advice:'暂无建议'})[value.state];
  }
  function render(record, {direct=false,cls='',primary=false}={}) {
    const value=read(record,{direct}),box=document.createElement('span');
    box.className=`cleanup-rating${primary?' cleanup-rating-primary':''} ${cls}`.trim();box.dataset.assessmentState=value.state;box.dataset.rating=value.rating===null?'':String(value.rating);
    if(value.stale)box.dataset.stale='true';
    if(value.rating!==null&&!value.stale){
      const scale=document.createElement('span');scale.className='cleanup-rating-scale';scale.setAttribute('aria-hidden','true');
      for(let step=1;step<=5;step++){const segment=document.createElement('span');segment.className='cleanup-rating-segment';segment.dataset.active=String(step<=value.rating);scale.append(segment);}
      box.append(scale);
    }
    const copy=document.createElement('span');copy.className='cleanup-rating-copy';
    copy.textContent=value.stale?'建议需重新核对':value.rating!==null?labels[value.rating]+(value.state==='inherited'?' · 继承':''):label(record,{direct});
    box.append(copy);
    const inherited=value.state==='inherited'?`，继承自${value.inherited_from_name || '上级目录'}，未逐项分析`:'';
    const accessible=value.stale?`${value.rating!==null?`旧清理倾向 ${value.rating}/5，`:''}建议已过期，需要重新核对${inherited}`:value.rating!==null?`清理倾向 ${value.rating}/5，${labels[value.rating]}${inherited}`:copy.textContent+inherited;
    box.setAttribute('role','img');box.setAttribute('aria-label',accessible+'；不代表危险程度或操作授权。');box.title=accessible+'；颜色表示清理倾向，不保证操作安全。';
    return box;
  }
  function blocked(record) {
    const names={stale_assessment:'文件或建议已有变化，需要重新核对',protected:'此项已受保护',user_kept:'你已选择保留此项',ancestor_barrier:'上级目录有保留或未完成的判断',keep:'此项建议保留',review:'需要先由你决定',skip:'此项已跳过评分',no_advice:'此项暂时没有建议',unscored_assessment:'此项尚未评分',protected_descendant:'内部包含受保护的项目',coverage_incomplete:'目录内容尚未完整核对',legacy_unknown_descendant:'内部有旧记录尚未确定位置',stale_or_unknown_descendant:'内部有已变化或尚未核对的项目',explicit_keep:'内部有明确建议保留的项目',explicit_skip:'内部有已跳过分析的项目',explicit_no_advice:'内部有尚无建议的项目',unscored_descendant:'内部有尚未评分的项目',undecided_descendant:'内部有需要单独决定的项目',self_only_directory:'目录建议没有覆盖全部内部内容',user_kept_descendant:'你已选择保留内部项目'};
    const reasons=read(record).blocked_reasons;
    return [...new Set(reasons.map(code=>Object.hasOwn(names,code)?names[code]:'内部存在保留、未核对或不适合整目录删除的内容'))].join('；');
  }
  return {read,label,render,blocked};
})();

// Provenance is host-owned history, never inferred from the active connection.
window.PanxuProvenance = (() => {
  const text=value=>typeof value==='string'?value:'';
  function time(value) {
    if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value))return null;
    const date=new Date(value);
    if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,19)!==value.slice(0,19))return null;
    const offset=-date.getTimezoneOffset(),absolute=Math.abs(offset);
    const zone=`UTC${offset<0?'-':'+'}${String(Math.floor(absolute/60)).padStart(2,'0')}:${String(absolute%60).padStart(2,'0')}`;
    let local,region='';
    try{
      local=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date);
      region=Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    }catch(_){local=date.toLocaleString();}
    return {local:`${local}（本机 ${zone}${region?' · '+region:''}）`,utc:value};
  }
  function describe(provenance,presets=[]) {
    const value=provenance&&typeof provenance==='object'?provenance:{};
    const source=['llm','local','user'].includes(value.source)?value.source:'unknown';
    if(source==='unknown')return {source,rows:[],note:'此条建议未记录模型来源和生成时间。'};
    const stamp=time(value.generated_at),rows=source==='llm'?[]:[['来源',source==='user'?'由你发起（不是模型建议）':'本机规则']];
    if(source==='llm'){
      const provider=text(value.provider),preset=(Array.isArray(presets)?presets:[]).find(entry=>entry.id===provider);
      rows.push(['服务商',preset?.name?`${preset.name}（${provider}）`:provider || '未记录'],['模型 ID',text(value.model) || '未记录']);
    }
    const timeLabel=source==='user'?'记录时间':'生成时间';
    rows.push([timeLabel,stamp?.local || '未记录有效时间']);
    return {source,rows,note:'',stamp,timeLabel};
  }
  function key(value){
    const source=['llm','local','user'].includes(value?.source)?value.source:'unknown';
    return source==='unknown'?'unknown':JSON.stringify([source,...(source==='llm'?['provider','model','protocol']:[]).map(name=>text(value?.[name])),text(value?.generated_at)]);
  }
  function details(record={}, {presets=[],origin='',plan=false,label='建议来源'}={}) {
    const node=(tag,cls,value)=>{const item=document.createElement(tag);if(cls)item.className=cls;if(value!==undefined)item.textContent=String(value);return item;};
    const box=node('details','advice-provenance');box.append(node('summary','field-help',label));
    let loaded=false;
    box.addEventListener('toggle',()=>{
      if(!box.open||loaded)return;loaded=true;
      const assessment=record.assessment;
      if(assessment?.state==='inherited')box.append(node('p','field-help',`继承自${assessment.inherited_from_name || '上级目录'}，此项未单独分析。`));
      const original=record.provenance || assessment?.provenance;
      const main=(origin || record.origin)==='user_direct'?{...(original || {}),source:'user'}:original;
      const prior=record.advice_provenance;
      const double=Boolean(prior)&&key(main)!==key(prior);
      const groups=double?[['本次操作建议',main],['原用途建议',prior]]:[[plan?'本次操作建议':'',main]];
      for(const [title,value] of groups){
        if(title)box.append(node('p','field-label',title));
        const description=describe(value,presets);
        if(description.note){box.append(node('p','field-help',description.note));continue;}
        const rows=node('dl','inspector-definition');
        description.rows.forEach(([name,content])=>{
          const value=node('dd');
          if(name===description.timeLabel&&description.stamp){
            const stamp=node('time','',content);stamp.dateTime=description.stamp.utc;stamp.title='UTC：'+description.stamp.utc;value.append(stamp);
          }else value.textContent=content;
          rows.append(node('dt','',name),value);
        });
        box.append(rows);
      }
    });
    return box;
  }
  return {describe,details,formatTime:time};
})();

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
  let homeVisible=false;
  const reviewPanes=new Map();
  let livePreview = null;
  const completedReplies=new Map();
  function completeReply(value,taskId) {
    if(!value || typeof value.text!=='string' || !value.text.trim() || value.text.length>16000
      || value.task_id && value.task_id!==taskId)return null;
    const chars=Array.from(value.text);
    return {text:chars.slice(0,8000).join(''),epoch:typeof value.epoch==='string'?value.epoch:'',
      turn_id:typeof value.turn_id==='string'?value.turn_id:'',truncated:value.truncated===true || chars.length>8000,
      completed_at:typeof value.completed_at==='string'?value.completed_at:typeof value.updated_at==='string'?value.updated_at:''};
  }
  function rememberReply(taskId,value,source='stream') {
    const record=completeReply(value,taskId);if(!record)return;
    const previous=completedReplies.get(taskId),stamp=Date.parse(record.completed_at),oldStamp=Date.parse(previous?.completed_at || '');
    if(previous && (Number.isFinite(stamp)&&Number.isFinite(oldStamp)&&stamp<oldStamp
      || source==='durable'&&!(Number.isFinite(stamp)&&Number.isFinite(oldStamp)&&stamp>oldStamp)))return;
    completedReplies.delete(taskId);completedReplies.set(taskId,record);
    if(completedReplies.size>8)completedReplies.delete(completedReplies.keys().next().value);
  }
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
    livePreview.off?.();livePreview.view.dispose();livePreview.activity?.dispose();livePreview = null;
  }
  function renderLivePreview(panel,sourceTask=current) {
    if (!window.PanxuStreamView || !window.PanxuStreamChannel || !sourceTask?.id) return;
    const section = node('section', 'task-live-reply'); section.hidden = true;
    section.setAttribute('aria-label', '助手的公开回复');
    const complete=node('div','task-last-complete'),completeCaption=node('p','task-live-caption','最近完整回复');
    const completeBody=node('div','task-live-body task-complete-body');completeBody.tabIndex=0;
    completeBody.setAttribute('aria-label','最近完整回复正文，可在此滚动阅读');
    add(complete,completeCaption,completeBody);
    const receiving=node('div','task-current-reply');receiving.hidden=true;
    const body = node('div', 'task-live-body');body.tabIndex=0;
    body.setAttribute('aria-label','公开回复全文，可在此滚动阅读');
    const caption = node('p', 'task-live-caption', '助手正在回复');
    const activityHost=node('div','task-stream-activity'),activity=window.PanxuStreamView.createActivity?.(activityHost);
    add(receiving,caption,body);add(section,complete,receiving,activityHost);add(panel,section);
    const token = {id: sourceTask.id,ownerId:current.id,view:window.PanxuStreamView.create(body),activity,off:null,turn:'',partialText:''};
    livePreview = token;
    function paintComplete(){
      const last=completedReplies.get(token.id);complete.hidden=!last;
      if(last){completeBody.textContent=last.text;completeCaption.textContent='最近完整回复'+(last.truncated?' · 可见内容已达 8000 字上限':'');}
      section.hidden=complete.hidden&&receiving.hidden&&(!activity||activityHost.hidden);
    }
    if(sourceTask.last_public_reply)rememberReply(token.id,sourceTask.last_public_reply,'durable');
    paintComplete();
    Promise.resolve().then(() => {
      if (livePreview !== token || !section.isConnected) return;
      token.off = window.PanxuStreamChannel.subscribe(token.id, packet => {
        if (livePreview !== token || current?.id !== token.ownerId || !section.isConnected) return;
        if(packet.activity)activity?.update(packet.activity);
        const value = packet.stream;
        if (!value) {
          if (['stopped', 'unavailable'].includes(packet.connection)) {
            receiving.hidden=false;caption.textContent = '暂时无法显示实时回复，最近完整回复会保留。';
          }
          paintComplete();
          return;
        }
        if(value.last_complete)rememberReply(token.id,value.last_complete);
        if(value.status==='completed'&&value.text?.trim())rememberReply(token.id,value);
        paintComplete();
        const turn = `${value.epoch}:${value.turn_id}`;
        if (token.turn !== turn) {token.view.reset(); token.turn = turn;token.partialText='';}
        const interrupted = packet.incomplete || value.status === 'error' || value.status==='cancelled';
        const partialText=interrupted&&!value.text?token.partialText:value.text || '';
        token.partialText=partialText;
        let phase = value.status === 'completed' ? '本轮回复已接收' : '正在接收公开回复';
        if (value.tool_pending) phase = value.status === 'completed' ? '工具请求已接收，程序将继续核对' : '工具请求接收中，尚未执行';
        if (value.delivery === 'json_fallback') phase = '已收到完整回复（接口未返回流式数据）';
        if (current.status === 'budget_exhausted' && value.status === 'completed') phase = '本轮回复已接收，调查待继续';
        if (interrupted) phase = value.status==='cancelled'?'本轮回复已停止，以下内容可能不完整':'回复中断，以下内容可能不完整';
        if (value.truncated) phase += ' · 仅展示最近一轮的前 8000 字';
        caption.textContent = phase;
        receiving.hidden=value.status==='completed' || !partialText.trim()&&!value.tool_pending&&!interrupted;
        section.hidden=complete.hidden&&receiving.hidden&&(!activity||activityHost.hidden);
        token.view.update({text: partialText, phase: value.tool_pending ? 'tool_pending' : '', status: interrupted ? 'incomplete' : value.status});
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
  function previewText(value, limit=100) {
    const text=typeof value==='string'?value:'';
    const chars=Array.from(text);
    return chars.length>limit?chars.slice(0,limit).join('')+'…':text;
  }
  function summaryText(root, value) {
    if(!value)return;
    const text=String(value);
    if(Array.from(text).length<=160){add(root,node('p','task-summary-text',text));return;}
    add(root,node('p','task-summary-text',previewText(text,100)),
      detail('展开完整说明',node('p','task-summary-original',text)));
  }
  function briefing(root,sourceTask=current,{flat=false}={}) {
    const value=sourceTask.briefing;
    if(!value || typeof value!=='object')return;
    const modules=[['highlights','本轮重点',3],['cautions','需要留意',2],['coverage','检查范围',2]];
    const group=node('section','task-briefing');group.setAttribute('aria-label','本轮建议说明');
    modules.forEach(([key,label,limit])=>{
      const points=Array.isArray(value[key])?value[key].filter(point=>typeof point==='string'&&point.trim()).slice(0,limit):[];
      if(!points.length)return;
      const list=node('ul','task-briefing-points');points.forEach(point=>add(list,node('li','',point)));
      if(key==='highlights'||flat)add(group,node('h3','task-briefing-title',label),list);
      else add(group,detail(`${label}（${points.length} 项）`,list));
    });
    if(group.childNodes.length){
      const provenance=sourceTask.briefing_provenance;
      if(provenance)add(group,window.PanxuProvenance.details({provenance},{presets:context.providerPresets,label:'本轮说明来源'}));
      add(root,group);
    }
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
    if(current?.id!==task?.id)homeVisible=false;
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
  function acceptPickedPlan(response) {
    const task=response?.task,plan=response?.plan || task?.plan;
    if(!task?.id || !plan || !task.plan || plan.version!==task.plan.version || !Array.isArray(response.picked_ids) || !Array.isArray(plan.items) || !Array.isArray(task.plan.items))return false;
    const entries=new Map(task.plan.items.map(item=>[item.id,item]));
    if(plan.items.length!==entries.size || plan.items.some(item=>{const entry=entries.get(item.id);return !entry || entry.signature!==item.signature || entry.action!==item.action;}))return false;
    if(response.picked_ids.some(id=>!entries.has(id)))return false;
    accept(task);selected=new Set(response.picked_ids);window.PanxuCleanupReview?.acceptPrepared?.(response);return true;
  }
  function redraw() { if (context?.isActive()) context.redraw(); }
  async function request(path, payload) {
    if (pending) return;
    pending = true; lastError = ''; redraw();
    try {
      const data = await context.api(path, payload);
      if (data.task) accept(data.task);
      if(data.task&&path==='/api/tasks'){intent='';context.onTaskCreated?.(data.task);}
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
      if(current?.id!==id)return;
      if(!busy()&&(current.advice_total || current.advice?.length || current.plan))context.onTaskReady?.(current);
      if (before !== current.status + ':' + current.summary) redraw();
    } catch (error) { lastError = '暂时连不上本机服务。重新打开程序后可以继续这个任务。'; redraw(); }
    if (busy()) schedule();
  }
  function newTask() {
    stopLivePreview();
    adviceGeneration++; adviceCache.clear(); adviceRequests.clear();
    current = null; homeVisible=false; intent = ''; planKey = ''; selected.clear(); lastError = ''; feedbackDraft = ''; redraw();
  }
  function returnHome(){stopLivePreview();homeVisible=true;intent='';redraw();}
  function reviewPane(pane=''){const id=current.cleanup_source_task_id || current.id;if(reviewPanes.size>=8&&!reviewPanes.has(id))reviewPanes.delete(reviewPanes.keys().next().value);reviewPanes.set(id,pane);redraw();}
  function choose(preset) {
    if(preset==='duplicates'&&context.openDuplicates){context.openDuplicates();return;}
    if (preset === 'c_cleanup' || preset === 'background') {
      request('/api/tasks', {preset}); return;
    }
    intent = preset; draftPath = ''; draftGoal = ''; redraw();
  }
  function welcome(root) {
    if (!intent) {
      context.setContextBack?.('',null);
      add(root, context.heading('今天想整理什么？', '助手先调查并解释，再由你确认具体操作。'));
      if(current&&context.page!=='home')add(root,action('返回当前整理',()=>{homeVisible=false;redraw();},'button task-return-current'));
      const grid = node('div', 'task-intents');
      ['c_cleanup', 'disk_cleanup', 'directory_cleanup', 'duplicates'].forEach((key) => {
        const card = action('', () => choose(key), 'task-intent'); card.dataset.preset = key;
        const paths={c_cleanup:['M4 5h16v14H4z','M4 14h16','M8 17h.01M11 17h.01'],disk_cleanup:['M4 4h16v6H4z','M4 14h16v6H4z','M8 7h.01M8 17h.01'],directory_cleanup:['M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z','M3 11h18'],duplicates:['M8 3h12v14H8z','M4 7v14h12','M12 7h4M12 11h4']};
        add(card,icon(paths[key],'task-intent-symbol'),add(node('span','task-intent-copy'),node('strong','',intents[key][0]),node('span','',intents[key][1])),icon(['M5 12h14m-5-5 5 5-5 5'],'task-intent-arrow')); add(grid, card);
      });
      const extras=node('div','task-secondary-intents');add(extras,...['background','installers','custom'].map(key=>action(intents[key][0],()=>choose(key),'plain-button more-task')));
      add(root,grid,extras,node('p','task-privacy-note','助手查看文件与软件信息，按需在本机核对；不会向模型发送文件正文，个人资料由你决定去留。'));
      context.renderHomeInfo?.(root);
      return;
    }
    context.setContextBack?.('返回首页',()=>{intent='';redraw();});
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
    if(!context.setContextBack)add(root,action('返回常用任务',()=>{intent='';redraw();},'plain-button task-new'));
  }
  function processDetails(root,{flat=false,sourceTask=current}={}) {
    if(!sourceTask?.events?.length&&!sourceTask?.handoff&&!sourceTask?.usage&&!sourceTask?.facts?.total&&!sourceTask?.scans?.length&&!sourceTask?.loaded_skills?.length){if(flat)add(root,node('p','field-help','本轮没有额外调查记录。'));return;}
    const taskId=sourceTask.id,box=flat?node('section','task-process-page'):detail('查看调查过程');
    const group=(label,...children)=>flat?add(node('section','task-process-section'),node('h3','',label),...children):detail(label,...children);
    const sameTask=()=>current&&(current.cleanup_source_task_id || current.id)===taskId;
    const handoff=sourceTask.handoff;
    if(handoff?.summary) add(box,node('p','',handoff.summary));
    const steps=Array.isArray(handoff?.next_steps)?handoff.next_steps:[];
    if(steps.length) {const list=node('ul','task-events');steps.slice(0,5).forEach(value=>add(list,node('li','',typeof value==='string'?value:value.summary || value.description || '继续核对已有线索')));add(box,node('p','field-label','下一步会继续核对'),list);}
    const unresolved=Array.isArray(handoff?.unresolved)?handoff.unresolved:[];
    if(unresolved.length) {const list=node('ul','task-events');unresolved.slice(0,5).forEach(value=>add(list,node('li','',typeof value==='string'?value:value.summary || value.description || '尚待核对')));add(box,group('尚未解决的内容',list));}
    if(handoff?.model?.model) add(box,node('p','field-help',`上轮使用 ${handoff.model.model}。继续时会根据当前连接和已保存的目标、事实、选择重建上下文。`));
    if(Array.isArray(sourceTask.loaded_skills) && sourceTask.loaded_skills.length) {
      const guides=group('本次参考的指导');
      sourceTask.loaded_skills.slice(0,20).forEach(read=>{
        const section=flat?node('section','task-guide-entry'):detail(read.name || read.id),body=node('div','task-skill-content');body.hidden=flat;let loaded=false,loading=false,cursor=null,content='';
        const more=action('继续读取指导',()=>load(),'plain-button');more.hidden=true;
        async function load() {
          if(loading)return;loading=true;more.disabled=true;
          try {
            const data=await context.api(`/api/skills/${encodeURIComponent(read.id)}${cursor?`?cursor=${encodeURIComponent(cursor)}`:''}`);
            if(!section.isConnected||!sameTask())return;
            const skill=data.skill || data;
            if(!content){
              body.replaceChildren(node('p','field-help',`参考版本 ${read.version || '未提供'} · 当前版本 ${skill.version || '未提供'}`));
              if(read.sha256 && skill.sha256!==read.sha256)add(body,node('p','field-help','这份指导已更新，下面展示当前版本。'));
              if(skill.summary)add(body,node('p','',skill.summary));
              const sources=(skill.sources || []).map(source=>`${source.title || '参考资料'}：${source.url || ''}`).join('\n');
              if(sources)add(body,group('来源与核对日期',node('p','field-help',`资料核对日期：${skill.checked_at || '未提供'}`),node('p','task-skill-sources',sources)));
              add(body,node('pre','developer-json'));
            }
            content+=(skill.content || '');body.querySelector('pre').textContent=content;
            cursor=skill.next_cursor;more.hidden=!skill.has_more;loaded=true;
          } catch(error) {add(body,node('p','field-error',error.message));loaded=false;}
          finally {loading=false;more.disabled=false;}
        }
        if(flat)add(section,action(read.name || read.id,()=>{guides.querySelectorAll('.task-skill-content').forEach(entry=>{entry.hidden=entry!==body;});body.hidden=false;if(!loaded)load();},'plain-button'));else section.addEventListener('toggle',()=>{if(section.open&&!loaded)load();});add(section,body,more);add(guides,section);
      });add(box,guides);
    }
    const usage=sourceTask.usage;
    if(usage) {
      const rows=node('dl','task-workload');
      const fields=[['model_requests','模型请求',n=>`${n} 次`],['tool_calls','工具调用',n=>`${n} 次`],['tool_failures','工具未完成',n=>`${n} 次`],['metadata_items','采集条目',n=>`${n} 项`],['scan_pages','扫描批次',n=>`${n} 批`],['hash_bytes','本地哈希读取',n=>context.bytes(n)],['input_tokens','输入 token',n=>n.toLocaleString('zh-CN')],['output_tokens','输出 token',n=>n.toLocaleString('zh-CN')],['elapsed_ms','累计调查用时',n=>n<60000?`${Math.round(n/1000)} 秒`:`${(n/60000).toFixed(1)} 分钟`]];
      fields.forEach(([key,label,format])=>{if(typeof usage[key]==='number' && Number.isFinite(usage[key]))add(rows,node('dt','',label),node('dd','',format(usage[key])));});
      if(rows.childNodes.length)add(box,group('累计工作量',rows,node('p','field-help',
        `${usage.input_tokens == null && usage.output_tokens == null ? '服务商未返回 token 用量。' : ''}累计已记录的请求和本地工作；未记录的用量不推算为零，也不等于实际释放空间。`)));
    }
    if(Array.isArray(sourceTask.scans) && sourceTask.scans.length) {
      const scans=node('ul','task-events');
      sourceTask.scans.slice(0,20).forEach(scan=>{
        const coverage=scan.coverage || {}, count=Number.isFinite(coverage.observed_entries)?coverage.observed_entries.toLocaleString('zh-CN'):'若干';
        const state=coverage.invalidated?'已有变化，需重新核对':coverage.limit_reached?'达到本次检查上限':coverage.complete?'目录观察完成':'尚未完整检查';
        add(scans,node('li','',`${scan.root_name || '所选目录'}：已观察 ${count} 项，${state}。`));
      });
      add(box,group('目录检查范围',scans,node('p','field-help','这里是目录观察的覆盖情况，不表示每个文件都已获得建议，也不代替处理前核查。最多展示 20 处。')));
    }
    if(sourceTask.facts?.total) {
      const facts=group(`已保存的事实（${sourceTask.facts.total} 条）`),list=node('div','task-facts'),nav=node('div','file-pagination'),viewer=node('div','task-fact-viewer');
      let offset=0,loading=false,loaded=false;
      async function load() {
        if(loading || !facts.isConnected)return;loading=true;list.setAttribute('aria-busy','true');nav.querySelectorAll('button').forEach(button=>button.disabled=true);
        if(!loaded)list.replaceChildren(node('p','muted','正在载入已有事实…'));
        try {
          const data=await context.api(`/api/tasks/${taskId}/facts?offset=${offset}&limit=20`);
          if(!facts.isConnected||!sameTask())return;loaded=true;list.replaceChildren();nav.replaceChildren();viewer.replaceChildren();
          (data.items || []).forEach(entry=>{
            const labels={scan_directory:'目录观察',survey_scope:'目录概况',inspect_directory:'深入目录',continue_scan:'继续目录观察',read_scan:'目录观察分页',validate_directory:'目录完整核查',list_items:'清单查询',inspect_items:'条目信息',describe_item:'条目信息',inspect_item:'条目信息',inspect_files:'文件信息',compare_duplicates:'重复核对',compare_files:'重复核对',read_facts:'已有事实',collect_apps:'软件与后台',list_applications:'软件与后台',cache_experience:'缓存经验',lookup_knowledge:'经验查询'};
            const stamp=entry.at?new Date(entry.at).toLocaleString('zh-CN'):'';
            const label=`${labels[entry.tool] || '调查观察'}${stamp?' · '+stamp:''}`;
            const item=flat?action(label,()=>viewer.replaceChildren(node('p','field-help','这是当时采集的观察，执行前仍需核对当前状态。'),node('pre','developer-json',JSON.stringify(entry,null,2))),'plain-button task-fact-choice'):detail(label);
            if(!flat){add(item,node('p','field-help','这是当时采集的观察，执行前仍需核对当前状态。'));item.addEventListener('toggle',()=>{if(item.open&&!item.querySelector('pre'))add(item,node('pre','developer-json',JSON.stringify(entry,null,2)));});}
            add(list,item);
          });
          if(!(data.items || []).length)add(list,node('p','muted','该页没有事实记录。'));
          if(data.total>20 || offset){const previous=action('上一页事实',()=>{offset=Math.max(0,offset-20);load();});previous.disabled=!offset;const next=action('下一页事实',()=>{offset+=20;load();});next.disabled=!data.has_more;add(nav,previous,node('span','',`${Math.floor(offset/20)+1} / ${Math.max(1,Math.ceil((data.total || 0)/20))}`),next);}
        } catch(error) {if(facts.isConnected){list.replaceChildren(node('p','field-error',error.message),action('重新载入事实',load));nav.replaceChildren();}}
        finally{loading=false;list.setAttribute('aria-busy','false');}
      }
      if(flat)add(facts,action('载入已保存事实',load,'button'));else facts.addEventListener('toggle',()=>{if(facts.open&&!loaded)load();});add(facts,list,nav,viewer);add(box,facts);
    }
    const list = node('ol', 'task-events');
    (sourceTask.events || []).filter((event) => ['tool', 'result', 'feedback', 'summary'].includes(event.kind)).slice(-12).forEach((event) => add(list,node('li','',event.message)));
    if(list.childNodes.length)add(box,group('最近调查记录',list));
    add(root,box);
  }
  function feedbackForm(root, label = '补充要求，让助手调整',taskId=current.id,{flat=false}={}) {
    const form = node('form', 'task-feedback');
    const input = node('textarea'); input.value = feedbackDraft; input.maxLength = 2000;
    input.setAttribute('aria-label', '补充整理要求');
    input.placeholder = '例如：这个安装包要保留离线版，继续看看其他内容。';
    input.addEventListener('input', () => { feedbackDraft = input.value; });
    const send = node('button', 'button', '让助手调整'); send.type = 'submit'; send.disabled = pending;
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!input.value.trim()) { input.focus(); return; }
      if(flat)reviewPanes.set(taskId,'');
      request('/api/tasks/' + taskId + '/feedback', {message: input.value.trim()});
    });
    add(form, input, send);add(root,flat?form:detail(label,form));
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
    const box=detail('选择要执行的操作'),body=node('div','task-direct-options');box.classList.add('task-direct');let loaded=false;
    box.addEventListener('toggle',async()=>{
      if(!box.open || loaded)return;loaded=true;body.replaceChildren(node('p','field-help','正在核对可用操作…'));
      try {
        const {item}=await context.api(`/api/item?id=${encodeURIComponent(entry.id)}`);
        if(!box.isConnected || current?.id!==taskId)return;
        body.replaceChildren();
        if(!item || item.signature!==entry.signature || item.simulated || item.risk==='protected') {add(body,node('p','field-help','当前内容已变化或受保护，请重新核对。'));return;}
        const available=(item.actions || []).filter(name=>['delete','disable_startup','stop_service','stop_process','uninstall'].includes(name));
        add(body,add(node('div','item-title-line'),window.PanxuIcons?.create(item),node('strong','',item.name)),node('p','task-path',item.path),node('p','field-help',entry.impact),
          node('p','field-help',window.PanxuActionCopy.summary(available.map(operation=>({action:operation,kind:item.kind}))).effect));
        available.forEach(operation=>add(body,action(window.PanxuActionCopy.label(operation,item.kind),()=>directAction(taskId,item,operation),'button')));
        if(!available.length)add(body,node('p','field-help','这项内容暂时没有可用操作。'));
      } catch(error) {body.replaceChildren(node('p','field-error',error.message));loaded=false;}
    });add(box,body);add(card,box);
  }
  function plan(root) {
    const cards = node('div', 'task-plan');
    current.plan.items.forEach(item => {
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
      const titleLine=node('div','item-title-line');
      add(titleLine,window.PanxuIcons?.create(item),title);
      add(copy, titleLine, add(node('div', 'task-plan-meta'),
        semanticTag(window.PanxuActionCopy.label(item.action,item.kind), 'info', 'task-action-label'),
        item.personal_choice_required && window.PanxuAssessment.read(item).rating!==3 ? semanticTag('由你选择', 'warning', 'task-choice-note') : null),
        window.PanxuAdvicePresentation.render(item,{direct:true,suggestion:window.PanxuActionCopy.label(item.action,item.kind)+'，待你确认。'}));
      add(copy,node('p','task-path',item.path),window.PanxuProvenance.details({...item,provenance:item.provenance || current.plan.provenance},{presets:context.providerPresets,origin:current.plan.origin,plan:true}),action('保留这项',()=>request('/api/tasks/'+current.id+'/feedback',{keep_ids:[item.id]}),'plain-button'));
      add(card, check, window.PanxuAssessment.render(item,{direct:true,primary:true,cls:'task-plan-rating'}),copy,
        node('span', 'task-item-size', item.kind === 'file' || item.kind === 'directory' ? '约 ' + context.bytes(item.size_bytes) : ''));
      add(cards,card);
    });
    add(root, cards);
    const confirm = action(window.PanxuActionCopy.summary(current.plan.items).emptyButton, () => {
      if (!selected.size) return;
      request('/api/tasks/' + current.id + '/approve', {version: current.plan.version, ids: [...selected]});
    }, 'button primary task-primary');
    confirm.id = 'task-approve';
    const copy = node('div', 'task-approval-copy');
    const breakdown = node('p', 'task-selection-count');breakdown.id='task-selection-summary';
    const effect = node('p', 'field-help', window.PanxuActionCopy.summary([]).effect);effect.id='task-simulation-effect';
    confirm.setAttribute('aria-describedby', 'task-selection-summary task-simulation-effect');
    add(copy,breakdown,effect);add(root, add(node('div', 'task-approval'), copy, confirm));
    updateApproval(); feedbackForm(root);
  }
  function updateApproval() {
    const button = document.getElementById('task-approve');
    if (!button) return;
    button.disabled = !selected.size || pending;
    const copy=window.PanxuActionCopy.summary(current.plan.items.filter(item=>selected.has(item.id)));
    button.textContent = selected.size ? copy.button : window.PanxuActionCopy.summary(current.plan.items).emptyButton;
    const count = document.querySelector('.task-selection-count');
    if (count) count.textContent = selected.size ? copy.breakdown : '个人文件不会默认选中';
    const effect = document.getElementById('task-simulation-effect');
    if (effect) effect.textContent = (selected.size ? copy : window.PanxuActionCopy.summary(current.plan.items)).effect;
  }
  function results(root) {
    if (!current.last_result) return;
    const result = current.last_result;
    const labels={succeeded:'已记录',failed:'未完成',unverified:'待核实',cancelled:'已停止',running:'待核实'};
    const pendingCount=Math.max(0,result.total-result.succeeded);
    const box=detail(`本轮结果：${result.succeeded} 项已记录${pendingCount?`，${pendingCount} 项未完成或待核实`:''}`,node('p','result-protection-note','实际释放空间：0 B。'));
    box.dataset.backend=result.backend || current.backend || '';
    box.className += ' task-results';
    const tones = {succeeded:'info', failed:'danger', unverified:'warning', cancelled:'neutral', running:'warning'};
    result.items.forEach((item) => {
      const undone = item.current_state === 'undone';
      const purged = item.current_state === 'purged';
      const row = node('article', 'task-result-item');
      const heading = add(node('div', 'task-result-heading'),
        add(node('div', 'item-title-line'), window.PanxuIcons?.create(item), node('strong', 'task-result-name', item.name)),
        semanticTag(undone?'记录已撤销':purged?'永久删除已记录':labels[item.status] || '待核实',undone||purged?'neutral':tones[item.status] || 'warning','task-result-state'));
      add(row,heading,node('p','task-result-message',undone?'这次处理记录已撤销。':purged?'此项已从可还原列表移除，不能再通过盘序还原。':item.message));
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
        (data.items || []).forEach((entry) => add(list,adviceCard(entry,taskId)));
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
  function adviceCard(entry,taskId) {
    const card=node('article','task-advice-item'),copy=node('div','task-advice-copy');
    add(copy,add(node('div','item-title-line task-advice-title-line'),window.PanxuIcons?.create(entry),node('strong','task-advice-title',entry.name)),
      entry.user_kept?semanticTag('你已选择保留','neutral','task-advice-state'):null,window.PanxuAdvicePresentation.render({...entry,task_id:taskId},{direct:true}),window.PanxuProvenance.details(entry,{presets:context.providerPresets}));
    if(!busy())directControls(copy,entry,taskId);
    add(card,window.PanxuAssessment.render(entry,{direct:true,primary:true,cls:'task-advice-rating'}),copy);
    return card;
  }
  function specialistSuggestions(root) {
    if(!current.extra_suggestions?.length || !context.openSpecialist)return;
    const section=node('section','task-specialist-suggestions');
    add(section,node('h2','','专项建议'),node('p','field-help',current.extra_suggestions.map(item=>item.title).join('、')),
      action('查看专项建议',()=>context.openSpecialist({extra_suggestions:current.extra_suggestions,source:{task_id:current.id}}),'button'));
    if(current.last_specialist_result)add(section,node('p','field-help','最近一次专项操作结果已记录，可在操作记录中查看。'));
    add(root,section);
  }
  function advicePreview(root) {
    if(current.plan || busy())return;
    const entries=(current.advice || []).slice(0,3);
    if(!entries.length)return;
    const list=node('div','task-advice-preview');list.setAttribute('aria-label','本轮逐项建议');
    entries.forEach(entry=>add(list,adviceCard(entry,current.id)));add(root,list);
  }
  function taskView(root) {
    context.setContextBack?.(context.page==='task'?'':'返回常用任务',context.page==='task'?null:returnHome);
    if(context.page==='task'&&context.openResults&&!busy()&&!current.question&&(current.advice_total || current.advice?.length || current.plan)){
      add(root,context.heading('当前任务',current.goal));
      const panel=node('section','panel task-session');add(panel,node('p','task-summary-original',current.summary || '建议已保存，可以查看清单并调整选择。'),action('查看建议与操作清单',()=>context.openResults(current),'button primary'));
      briefing(panel);renderLivePreview(panel);specialistSuggestions(panel);add(root,panel);return;
    }
    if(window.PanxuCleanupReview&&!current.question&&(current.status==='executing'||!busy()&&['awaiting_review','completed','paused','budget_exhausted','error'].includes(current.status))){reviewView(root);return;}
    add(root, context.heading(intents[current.preset]?.[0] || '整理助手', current.preset && current.preset !== 'custom' ? '按需调查，先给出最值得关注的建议。' : current.goal));
    const panel = node('section', 'panel task-session');
    const summary = node('div', 'task-summary'); summary.setAttribute('role', 'status');
    if (busy()) add(summary, node('span', 'spinner'));
    if(!current.question || busy()){
      const copy = node('div', 'task-summary-copy');
      const statuses = {completed:['本轮建议','info'], error:['调查未完成','danger'], paused:['已暂停','neutral'],
        awaiting_review:['待你确认','warning'], budget_exhausted:['调查待继续','warning']};
      if(!busy() && statuses[current.status])add(copy, semanticTag(...statuses[current.status], 'task-status-label'));
      summaryText(copy,current.summary);add(summary, copy);
    }
    if(summary.childNodes.length)add(panel, summary);
    if(!busy() && context.modelProfile?.model && current.handoff?.model?.model && ['provider','model','protocol'].some(key=>(context.modelProfile[key] || '')!==(current.handoff.model[key] || ''))) add(panel,node('p','field-help',`继续调查将使用当前连接的 ${context.modelProfile.model}，已有目标、事实和保留选择会继续使用。`));
    if (current.last_result && current.last_result.succeeded < current.last_result.total) {
      add(panel, node('p', 'task-result-notice', `还有 ${current.last_result.total-current.last_result.succeeded} 项未完成或待核实，可展开本轮结果查看原因。`));
    }
    advicePreview(panel);
    if (busy()) {
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
    briefing(panel);renderLivePreview(panel);results(panel); renderAdvice(panel); specialistSuggestions(panel);processDetails(panel);
    add(root, panel);
    if(!context.setContextBack&&!busy())add(root,action('返回常用任务',returnHome,'plain-button task-new'));
  }
  function reviewView(root) {
    const sourceId=current.cleanup_source_task_id || current.id,pane=reviewPanes.get(sourceId) || '';
    const knownSource=window.PanxuCleanupReview.sourceFor(current);
    const sourceTask=knownSource || current,appTask=sourceTask.scope_kind==='applications'||['background','software'].includes(sourceTask.analysis?.section);
    const listTitle=appTask?(sourceTask.analysis?.section==='software'?'软件整理清单':'优化清单'):'清理清单';
    if(pane&&knownSource){
      const returnLabel=pane==='process'?'返回分析详情':'返回'+listTitle,returnPane=pane==='process'?'analysis':'';
      context.setContextBack?.(returnLabel,()=>reviewPane(returnPane));
      const title={analysis:'分析详情',adjust:'调整整理要求',process:'调查过程'}[pane] || '分析详情';
      add(root,context.heading(title,pane==='adjust'?'说明要保留的内容，或让助手继续核对。':'查看本轮结论、依据和调查记录。'));
      const content=node('section','task-reading-page');add(root,content);
      if(pane==='adjust')feedbackForm(content,'补充整理要求',sourceId,{flat:true});
      else if(pane==='process')processDetails(content,{flat:true,sourceTask:knownSource});
      else{
        if(knownSource.summary)add(content,node('p','task-summary-original',knownSource.summary));
        briefing(content,knownSource,{flat:true});renderLivePreview(content,knownSource);
        const tools=node('div','task-reading-actions');add(tools,action('继续调查',()=>{reviewPanes.set(sourceId,'');request('/api/tasks/'+sourceId+'/continue',{});},'button'),action('查看调查过程',()=>reviewPane('process'),'plain-button'));add(content,tools);
      }
      if(!context.setContextBack)add(root,action(returnLabel,()=>reviewPane(returnPane),'plain-button task-new'));
      return;
    }
    if(!context.embeddedResult){const heading=context.heading(listTitle,'按评分查看用途、建议与影响，勾选后核对具体操作。');heading.classList?.add('cleanup-page-heading');add(root,heading);}
    const summary=node('div','cleanup-task-summary');add(root,summary);
    const list=node('div','cleanup-task-list');add(root,list);
    const explanation=node('div','cleanup-task-explanation');add(root,explanation);
    function showContext(source){
      if(!summary.isConnected)return;summary.replaceChildren();explanation.replaceChildren();
      if(source.summary)add(summary,node('p','task-summary-text',previewText(source.summary,160)));
      const caution=Array.isArray(source.briefing?.cautions)?source.briefing.cautions.find(point=>typeof point==='string'&&point.trim()):null;
      if(caution)add(summary,add(node('div','cleanup-task-caution'),semanticTag('留意','warning'),node('p','',caution)));
      stopLivePreview();renderLivePreview(explanation,source);
    }
    window.PanxuCleanupReview.render(list,{
      task:current,api:context.api,bytes:context.bytes,showItem:context.showItem,autoSelectFour:context.autoSelectFour,disabled:pending,
      singleSuggestions:context.embeddedResult&&current.scope_kind!=='applications',
      setContextBack:context.setContextBack,openFiles:context.openFiles,onSource:showContext,acceptTask:accept,redraw,
      renderTools:tools=>add(tools,action('分析详情',()=>reviewPane('analysis'),'plain-button'),action('调整要求',()=>reviewPane('adjust'),'plain-button'))
    });
    specialistSuggestions(root);
    if(!context.setContextBack&&!busy())add(root,action('返回常用任务',returnHome,'plain-button task-new'));
  }
  function render(root, helpers) {
    stopLivePreview();
    window.PanxuCleanupReview?.unmount();
    context = helpers;
    if (lastError) add(root, node('p', 'global-message error task-error', lastError));
    if(context.page==='home'){homeVisible=true;welcome(root);}
    else if(context.page==='task'){homeVisible=false;if(current)taskView(root);else add(root,context.heading('当前任务','还没有当前任务。'),action('选择常用任务',()=>context.openHome?.(),'button primary'));}
    else if(current&&!homeVisible)taskView(root);else welcome(root);
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
  function renderResultWorkspace(root,helpers){stopLivePreview();window.PanxuCleanupReview?.unmount();context=helpers;if(!current)return;reviewView(root);if(busy())schedule();}
  return {render,renderResultWorkspace, accept, acceptPickedPlan, renderHistory, adviceFor, adviceKnown, lookupAdvice, currentTask: () => current, streamTaskId: () => current?.cleanup_source_task_id || current?.id || null, unmountLive:()=>{stopLivePreview();window.PanxuCleanupReview?.unmount();}};
})();
