'use strict';

// Resolve the appearance before CSS; application setup waits for the DOM below.
window.PanxuAppearance = (() => {
  const defaults={theme:'system',developer_live:false,developer_conversation:true,developer_skills:true,developer_tools:false,cleanup_auto_select_four:true};
  const storageKey='panxu.ui-preferences.v1';
  let cached={};try{cached=JSON.parse(localStorage.getItem(storageKey) || '{}');}catch(_){}
  let values={...defaults};
  for(const [key,value] of Object.entries(cached || {})){
    if(key==='theme'&&['dark','light','system'].includes(value))values.theme=value;
    else if(key in defaults&&key!=='theme'&&typeof value==='boolean')values[key]=value;
  }
  const initial=document.documentElement.dataset.themePreference;
  if(['dark','light','system'].includes(initial))values.theme=initial;
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  function apply(){
    const theme=values.theme==='system'?(media.matches?'dark':'light'):values.theme;
    document.documentElement.dataset.theme=theme;
    document.documentElement.style.colorScheme=theme;
    document.documentElement.style.backgroundColor=theme==='dark'?'#10151e':'#f3f5f8';
    document.documentElement.dataset.themePreference=values.theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content',theme==='dark'?'#10151e':'#f3f5f8');
  }
  function update(patch){
    for(const [key,value] of Object.entries(patch || {})){
      if(key==='theme'&&['dark','light','system'].includes(value))values.theme=value;
      else if(key in defaults&&key!=='theme'&&typeof value==='boolean')values[key]=value;
    }
    try{localStorage.setItem(storageKey,JSON.stringify(values));}catch(_){}
    apply();return {...values};
  }
  if(media.addEventListener)media.addEventListener('change',()=>{if(values.theme==='system')apply();});
  else media.addListener?.(()=>{if(values.theme==='system')apply();});
  apply();return {get:()=>({...values}),update};
})();

// A bounded presentation model for already-sanitized protocol records.
window.PanxuDeveloperView = (() => {
  const privateKeys=new Set(['api_key','authorization','proxy-authorization','x-api-key','x-goog-api-key','password','secret','access_token','refresh_token','reasoning_content','thinking','redacted_thinking','encrypted_content','thoughtsignature','thought_signature','signature','_continuation']);
  const privateTypes=new Set(['reasoning','thinking','redacted_thinking']);
  const publicTools=new Set(['present_findings','propose_plan','ask_user','record_advice','checkpoint_task']);
  const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
  const clip=(text,max=8000)=>typeof text==='string'?(text.length>max?text.slice(0,max)+'\n…（此处显示已截断，更多内容见原始 JSON）':text):'';
  function parsed(value){if(typeof value!=='string')return value;try{return JSON.parse(value);}catch(_){return value;}}
  function clean(value,depth=0){
    if(depth>12)return '[内容层级过深，已省略]';
    if(typeof value==='string'){const decoded=parsed(value);return decoded!==value&&typeof decoded==='object'?JSON.stringify(clean(decoded,depth+1)):value.length>24000?value.slice(0,24000)+'\n…（字段显示已截断）':value;}
    if(Array.isArray(value))return value.slice(0,80).map(item=>clean(item,depth+1)).concat(value.length>80?['[其余条目未在此处展开]']:[]);
    if(!object(value))return value;
    if(privateTypes.has(value.type)||value.thought===true)return {omitted:'协议内部内容未展示'};
    const result={};
    for(const [key,child] of Object.entries(value)){
      const configuration=key==='thinking'&&object(child)&&Object.keys(child).every(name=>['type','budget_tokens','clear_thinking'].includes(name));
      result[key]=privateKeys.has(key.toLowerCase())&&!configuration?'[密钥或协议内部内容未展示]':clean(child,depth+1);
    }
    return result;
  }
  function unwrap(entry){const envelope=entry?.payload;return object(envelope)&&Object.hasOwn(envelope,'payload')?envelope.payload:envelope;}
  function category(entry){const value=unwrap(entry),name=value?.name || value?.tool || value?.function?.name || '';return /skill|guidance/i.test(entry?.kind || '')||/^(read_skill|list_skills)$/.test(name)?'skills':/^tool_/.test(entry?.kind || '')?'tools':'conversation';}
  function describe(entry){
    const value=unwrap(entry),envelope=entry?.payload,view={messages:[],sections:[],notes:[],meta:{}};
    const calls=new Map();let recognized=false;
    function section(title,data,kind='context'){if(view.sections.length<18)view.sections.push({title,data,kind});else if(!view.notes.includes('部分分层信息未展开，请查看原始 JSON。'))view.notes.push('部分分层信息未展开，请查看原始 JSON。');}
    function message(role,text,extra={}){if(view.messages.length<12)view.messages.push({role,text:clip(text),...extra});else if(!view.notes.includes('对话较长，此处仅展示前 12 条。'))view.notes.push('对话较长，此处仅展示前 12 条。');}
    function callRecord(call){
      const f=call?.function || call,name=typeof f?.name==='string'?f.name:'未知调用';
      const args=parsed(f?.arguments ?? f?.input ?? f?.args);
      if(call?.id)calls.set(call.id,name);if(call?.call_id)calls.set(call.call_id,name);
      const skill=/^(read_skill|list_skills)$/.test(name);
      section((skill?'技能调用：':'工具调用：')+name,args,skill?'skills':'tools');
      if(!publicTools.has(name))return;
      if(!object(args)){view.notes.push('无法提取这次调用的公开文本，可展开原始 JSON 核对。');return;}
      const titles={present_findings:'调查结论',propose_plan:'方案建议',ask_user:'想请你确认',record_advice:'用途与保留建议',checkpoint_task:'调查交接'};
      const extra={title:titles[name],publicCall:name};
      if(object(args.briefing)){
        const briefing={};
        for(const [key,limit] of [['highlights',3],['cautions',2],['coverage',2]])if(Array.isArray(args.briefing[key]))briefing[key]=args.briefing[key].filter(item=>typeof item==='string').slice(0,limit).map(item=>clip(item,1200));
        if(Object.keys(briefing).length)extra.briefing=briefing;
      }
      if(Array.isArray(args.options))extra.options=args.options.slice(0,5).map(option=>({label:clip(option?.label,600),description:clip(option?.description,1200)}));
      if(Array.isArray(args.items)){extra.items=args.items.slice(0,12).map(item=>{
        const value=Object.fromEntries(['id','name','conclusion','purpose','reason','impact'].filter(key=>typeof item?.[key]==='string').map(key=>[key,clip(item[key],1200)]));
        if(Number.isInteger(item?.cleanup_rating)&&item.cleanup_rating>=1&&item.cleanup_rating<=5)value.cleanup_rating=item.cleanup_rating;
        if(Object.hasOwn(item || {},'impact_rating'))value.impact_rating=Number.isInteger(item.impact_rating)&&item.impact_rating>=1&&item.impact_rating<=5?item.impact_rating:null;
        return value;
      });extra.omittedItems=Math.max(0,args.items.length-12);}
      if(name==='checkpoint_task')extra.steps=[...(Array.isArray(args.next_steps)?args.next_steps:[]),...(Array.isArray(args.unresolved)?args.unresolved:[])].filter(item=>typeof item==='string').slice(0,12).map(item=>clip(item,1200));
      if(typeof args.summary==='string'||typeof args.question==='string'||extra.items?.length||extra.options?.length||extra.briefing)message('assistant',args.summary || args.question || '',extra);
      else view.notes.push('此调用没有可识别的公开正文，可展开原始 JSON 核对。');
    }
    function toolResult(name,result,forceSkill=false){const skill=forceSkill||/^(read_skill|list_skills)$/.test(name);section((skill?'技能内容':'工具结果')+(name?'：'+name:''),parsed(result),skill?'skills':'tools');}
    function content(role,body){
      if(body==null)return;
      if(typeof body==='string'){
        const data=parsed(body);
        if(role==='system'||role==='developer'){section(role==='system'?'系统提示':'开发指引',body,'system');return;}
        if(role==='tool'){toolResult('',data);return;}
        if(object(data)){
          const context=['goal','facts','observations','authorized_scope','inventory_sample_untrusted','scopes','user_choices','information_guide'].some(key=>Object.hasOwn(data,key));
          if(context){
            if(typeof data.goal==='string')message('user',data.goal,{title:'任务目标'});
            const {facts,observations,...rest}=data;section('任务上下文',rest);
            if(facts||observations)section('历史事实与观察',{...(facts?{facts}:{}),...(observations?{observations}:{})},'facts');
          }else section((role==='assistant'?'助手':'用户')+'消息中的结构化内容',data);
        }else message(role,body);
        return;
      }
      if(Array.isArray(body))for(const part of body.slice(0,30)){
        if(!object(part)||privateTypes.has(part.type)||part.thought===true)continue;
        if(['tool_use','function_call'].includes(part.type))callRecord(part);
        else if(['tool_result','function_call_output'].includes(part.type))toolResult(calls.get(part.tool_use_id || part.call_id) || '',part.content ?? part.output);
        else if(part.functionCall)callRecord(part.functionCall);
        else if(part.functionResponse)toolResult(part.functionResponse.name,part.functionResponse.response);
        else if(typeof part.text==='string')content(role,part.text);
        else section('非文本消息内容',{type:part.type || '未识别类型'},'metadata');
      }
    }
    function row(row){
      if(!object(row))return;
      const role=row.role==='model'?'assistant':row.role || 'assistant';
      if(row.type==='function_call')callRecord(row);
      else if(row.type==='function_call_output')toolResult(calls.get(row.call_id) || '',row.output);
      else if(role==='tool')toolResult(calls.get(row.tool_call_id) || row.name || '',row.content);
      else{
        content(role,row.content ?? row.parts);
        if(Array.isArray(row.tool_calls))row.tool_calls.slice(0,20).forEach(callRecord);
        else if(row.tool_calls!=null)view.notes.push('调用格式未能识别，可展开原始 JSON 核对。');
        if(row.function_call)callRecord(row.function_call);
      }
    }
    if(object(envelope))for(const key of ['provider','protocol','endpoint'])if(typeof envelope[key]==='string')view.meta[key]=envelope[key];
    if(object(value))for(const key of ['model','status','stop_reason','finish_reason'])if(typeof value[key]==='string')view.meta[key]=value[key];
    if(object(value)&&value.truncated){view.notes.push('这条记录已被采集体积上限截断，无法完整解释。');recognized=true;}
    if(entry?.kind==='turn_end'&&object(value)){
      const status=value.status,delivery={sse:'流式接收（SSE）',json_fallback:'完整 JSON 回复（服务端未流式返回）',disabled:'未启用流式接收'};
      message('system',status==='completed'?'本轮接收完成':status==='cancelled'?'本轮接收已停止':'本轮接收未完成',{title:'模型通信'});
      view.notes.push('这里只表示一轮模型回复的接收状态；任务是否完成、是否产生处理结果，仍以任务记录和核验结果为准。');
      const details={};
      if(Object.hasOwn(delivery,value.delivery))details['接收方式']=delivery[value.delivery];
      if(typeof value.turn_id==='string')details['本轮标识']=clip(value.turn_id,100);
      for(const [key,label] of [['text_chars','正文字符数'],['tool_count','工具调用数']])if(Number.isSafeInteger(value[key])&&value[key]>=0)details[label]=value[key];
      if(Object.keys(details).length)section('本轮接收详情',details,'metadata');
      const usage=value.usage || value.usageMetadata;
      if(object(usage)){
        const counts={};
        for(const key of ['input_tokens','output_tokens','total_tokens','prompt_tokens','completion_tokens','cache_creation_input_tokens','cache_read_input_tokens','promptTokenCount','candidatesTokenCount','totalTokenCount','cachedContentTokenCount'])if(Number.isSafeInteger(usage[key])&&usage[key]>=0)counts[key]=usage[key];
        if(Object.keys(counts).length)section('服务商报告的用量',counts,'metadata');
      }
      recognized=true;
    }else if(/^tool_/.test(entry?.kind || '')||category(entry)==='skills'){
      const name=value?.name || value?.tool || '';
      if(entry.kind==='tool_call')callRecord({name,arguments:value?.arguments});
      else toolResult(name,value?.result ?? value,category(entry)==='skills');
      recognized=true;
    }else if(object(value)){
      if(typeof value.system==='string'||Array.isArray(value.system)){content('system',value.system);recognized=true;}
      const system=value.systemInstruction || value.system_instruction;if(system){content('system',system.parts || system);recognized=true;}
      if(Array.isArray(value.messages)){value.messages.slice(0,20).forEach(row);recognized=true;if(value.messages.length>20)view.notes.push('请求包含较多历史消息，此处仅展示前 20 条。');}
      if(typeof value.input==='string'){content('user',value.input);recognized=true;}
      else if(Array.isArray(value.input)){value.input.slice(0,20).forEach(row);recognized=true;}
      if(Array.isArray(value.contents)){value.contents.slice(0,20).forEach(row);recognized=true;}
      if(Array.isArray(value.choices)){value.choices.slice(0,3).forEach(choice=>row(choice.message || choice.delta));recognized=true;}
      if(Array.isArray(value.output)){value.output.slice(0,30).forEach(row);recognized=true;}
      if(Array.isArray(value.candidates)){value.candidates.slice(0,3).forEach(candidate=>row(candidate.content));recognized=true;}
      if(Array.isArray(value.content)||typeof value.content==='string'){content(value.role || 'assistant',value.content);recognized=true;}
      if(value.tools||value.functions)section('工具定义',value.tools || value.functions,'tools');
      if(value.usage||value.usageMetadata)section('服务商报告的用量',value.usage || value.usageMetadata,'metadata');
      if(entry.kind==='error'){section('通信错误',value,'metadata');recognized=true;}
      if(entry.kind==='context_event'){section('任务事件',value);recognized=true;}
    }else if(typeof value==='string'){message(entry.kind==='response'?'assistant':'user',value);recognized=true;}
    if(!recognized)view.notes.push('尚未识别这条记录的格式，可展开已脱敏的原始 JSON 查看；没有补造对话内容。');
    if(Object.keys(view.meta).length)section('连接信息（程序内部）',{...view.meta,说明:'provider 是程序内部的服务商标签，不是消息角色。实际消息角色由协议中的 role 或独立 system 字段定义。'},'metadata');
    if(view.messages.some(item=>item.publicCall))view.notes.push('以上为模型公开建议，不代表操作或审批结果。');
    return view;
  }
  function raw(entry){const value=JSON.stringify(clean(entry?.payload),null,2) || 'null';return value.length>60000?value.slice(0,60000)+'\n…（原始记录显示已截断，最多 60,000 字符）':value;}
  return {describe,category,clean,raw};
})();

function initializePanxu() {
  const $ = (id) => document.getElementById(id);
  const names = { overview: '首页', current: '当前任务', files: '文件清理', recovery: '已删除文件', background: '后台与软件', software: '后台与软件', history: '操作记录', model: '设置', duplicates: '查找重复文件', cleanup: '清理工具' };
  const kindNames = { file: '文件', directory: '目录汇总', startup: '启动项', service: '服务', process: '进程', software: '软件' };
  const state = { csrf: '', version: '', view: 'overview', items: [], summary: {}, settings: {}, history: [], disks: [], source: 'demo', warnings: [], selected: new Set(), focused: null, query: '', risk: 'all', origin: 'all', subtype: 'all', limit: 50, busy: false, job: null, plan: null, scanPath: '', inspector: false, assistant: null, recommendations: [], checked: false, analyzing: false, filesExpanded: false, backgroundExpanded: false, inventoryTarget: 'startup', checkDestination: 'overview', showAllCards: new Set() };
  let toastTimer;
  let developerCleanup=null;
  let developerController=null,developerDockOpen=false,developerReturnFocus=null;
  let unitCache = new Map();
  let protectedFiles = [];
  let defaultPathLoaded = false;
  let itemMap = new Map();
  let adviceMap = new Map();
  const analysisSelections = new Map();
  let analysisDepth = 'auto';
  const analysisGroup = () => state.view === 'files' ? 'files' : state.view === 'software' ? 'software' : state.subtype === 'all' ? 'background' : state.subtype;
  function analysisIds() { const key = analysisGroup(); if (!analysisSelections.has(key)) analysisSelections.set(key, new Set()); return analysisSelections.get(key); }
  function canAnalyze(item) { return !!item && !item.simulated && !synthetic(item); }
  function analysisSelected(item) { return analysisIds().has(item.id); }
  function toggleAnalysis(item, checked) {
    if (!canAnalyze(item)) return;
    const ids = analysisIds();
    if (checked && ids.size >= 500 && !ids.has(item.id)) { toast('每次可勾选 500 项；分析整个列表可使用“分析当前列表”。'); return; }
    if (checked) ids.add(item.id); else ids.delete(item.id);
  }
  async function analyzeList(item = null, filter = null) {
    const ids = item ? [item.id] : [...analysisIds()];
    const query = filter || (state.view === 'files' ? window.PanxuFiles.filters() : {query: state.query, risk: state.risk, kind: state.subtype === 'all' ? undefined : state.subtype});
    await task(async () => {
      const data = await api('/api/tasks/analyze', {section: state.view === 'files' ? 'files' : state.view === 'software' ? 'software' : 'background', depth: analysisDepth, ...query, ...(ids.length ? {ids} : {})});
      window.PanxuTasks.accept(data.task); setView('current');
    });
  }
  async function showAdvice(item) {
    try {
      if(item.assessment && item.assessment.state!=='unanalysed'){await showItem(item);return;}
      const complete=await window.PanxuTasks.lookupAdvice([item], api);
      if(complete===false){toast('调查记录已更新，请再点一次查看。');return;}
      if (window.PanxuTasks.adviceFor(item.id, item.signature)) await showItem(item);
      else await analyzeList(item);
    } catch(error) {notify('已有建议暂时无法读取，请重试。'+error.message,true);}
  }
  function analysisOptions(root) {
    const details = el('details', 'analysis-options'); append(details, el('summary', '', '分析方式'));
    append(details, makeSelect([['auto','智能按需（推荐）'],['overview','只看概况'],['detailed','逐项详细分析']], analysisDepth, (value) => { analysisDepth = value; }, '分析深度'), el('p', 'field-help', '智能按需先了解整体，仅深入值得查看的内容。逐项分析会使用更多时间与模型额度；不会自动上传正文。')); append(root, details);
  }
  async function manualSelected(item = null) {
    state.selected = new Set(item ? [item.id].filter(() => canSelect(item)) : [...analysisIds()].filter((id) => canSelect(itemMap.get(id))));
    if (!state.selected.size) { toast('这些内容暂不支持手动处理；仍可分析用途。'); return; }
    await previewPlan();
  }


  function el(tag, cls, value) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }
  function append(parent, ...children) { children.flat().forEach((child) => { if (child !== null && child !== undefined) parent.append(child); }); return parent; }
  function itemTitle(item, title) { return append(el('div', 'item-title-line'), window.PanxuIcons?.create(item), title); }
  function button(label, handler, cls = 'button') { const b = el('button', cls, label); b.type = 'button'; b.addEventListener('click', handler); return b; }
  function text(value) { if (value === null || value === undefined) return ''; if (typeof value === 'object') return JSON.stringify(value, null, 2); return String(value); }
  function bytes(value) { const n = Number(value) || 0; if (!n) return '0 B'; const units = ['B', 'KB', 'MB', 'GB', 'TB']; const i = Math.min(Math.floor(Math.log(Math.abs(n)) / Math.log(1024)), 4); return `${(n / (1024 ** i)).toLocaleString('zh-CN', { maximumFractionDigits: i > 1 ? 2 : 0 })} ${units[i]}`; }
  function itemSize(item) { if (item.kind === 'process') return Number(item.details?.memory_bytes ?? item.details?.working_set_bytes ?? item.size_bytes) || 0; if (item.kind === 'software') return Number(item.details?.estimated_install_bytes ?? item.size_bytes) || 0; return Number(item.allocated_bytes ?? item.size_bytes) || 0; }
  function diskSize(item) { return ['file', 'directory'].includes(item.kind) ? itemSize(item) : 0; }
  function inDirectory(path, parent) { const normalize = (v) => text(v).replaceAll('/', '\\').replace(/\\+$/, '').toLowerCase(); const a = normalize(path); const b = normalize(parent); return a === b || a.startsWith(b + '\\'); }
  function selectedDiskBytes(items) {
    const roots = items.filter((item) => ['file', 'directory'].includes(item.kind) && !items.some((other) => other.id !== item.id && other.kind === 'directory' && inDirectory(item.path, other.path)));
    const identities = new Set();
    let total = 0;
    roots.forEach((item) => {
      total += Number(item.size_bytes) || 0;
      const details = item.details || {};
      const entities = item.kind === 'directory' ? (Array.isArray(details.hardlink_entities) ? details.hardlink_entities : []) : (details.is_hardlink ? [{ file_identity: details.file_identity, size_bytes: item.size_bytes }] : []);
      entities.forEach((entity) => {
        if (!entity.file_identity) return;
        if (identities.has(entity.file_identity)) total -= Number(entity.size_bytes) || 0;
        else identities.add(entity.file_identity);
      });
    });
    return Math.max(0, total);
  }
  function synthetic(item) { return item.details?.synthetic === true; }
  function badge(label, cls = '') { const node=el('span', `source-badge semantic-tag ${cls}`, label);node.dataset.tone='neutral';node.dataset.purpose=/\b(real|demo)\b/.test(cls)?'source':'record-state';return node; }
  function riskBadge(item) { const labels = { low: '可处理', review: '你来决定', protected: '保留' };const node=el('span', `risk-badge semantic-tag risk-${item.risk || 'review'}`, labels[item.risk] || '需确认');node.dataset.tone=item.risk==='low'?'info':item.risk==='protected'?'neutral':'warning';node.dataset.purpose='recommendation';return node; }
  function canSelect(item) {
    if (item && typeof item.can_select === 'boolean') return item.can_select && !item.simulated;
    if (!item || item.risk === 'protected' || item.simulated || !Array.isArray(item.actions) || !item.actions.length) return false;
    if (item.kind === 'directory' && (!(item.details?.scan_complete === true || item.details?.complete === true) || item.details?.subtree_complete === false || item.details?.can_simulate_delete === false)) return false;
    if (item.kind === 'directory' && protectedFiles.some((other) => other.id !== item.id && inDirectory(other.path, item.path))) return false;
    return true;
  }
  function selectionReason(item) {
    if (item.risk === 'protected') return '已保护，保持原样。';
    if (item.simulated) return '此项已有操作记录，可到操作记录查看或撤销。';
    if (item.kind === 'directory' && !canSelect(item)) return '这个文件夹还没有完整检查，请选择里面的具体文件。';
    if (!item.actions?.length) return '此项暂时没有可用操作。';
    return `可以查看${window.PanxuActionCopy.label(item.actions[0],item.kind)}清单。`;
  }
  function verifiedCache(item) { const rec = advice(item); return canSelect(item) && rec.auto_select === true && rec.group === 'recommended' && rec.needs_user_choice !== true; }
  function advice(item) { return adviceMap.get(item.id)?.advice || item.advice || {}; }
  function adviceGroup(item) {
    const rec = advice(item);
    if (rec.group === 'recommended' && item.risk !== 'protected') return 'safe';
    if (rec.group === 'personal' && item.risk !== 'protected') return 'personal';
    return 'keep';
  }
  function cleanCopy(value) { return text(value).replaceAll('可模拟清理', '可删除').replaceAll('可模拟删除', '可删除').replaceAll('只读采集', '检查').replaceAll('只读盘点', '检查'); }
  function itemPurpose(item) {
    const rec = advice(item); if (rec.purpose) return rec.purpose;
    if (/cache|缓存/i.test(item.category || '')) return `${item.owner || '应用'}保存的中间结果，用来减少重复下载或处理，让下次使用更快。`;
    const purposes = { startup: '决定相关应用是否在你登录电脑后自动打开。', service: '在后台提供功能的程序，其他应用可能依赖它。', process: '当前正在运行的应用或后台任务。', software: '安装在电脑上的应用，可能还包含插件和配置。' };
    return purposes[item.kind] || `${item.owner || '应用或个人资料'}相关的${item.category || '文件'}；是否继续保留，需要结合具体用途判断。`;
  }
  function selectionUnits(item) { return canSelect(item) ? [item] : []; }
  function unitsInScope(items) {
    const ids = new Set(items.map((item) => item.id));
    const allowed = items.filter(canSelect).filter((item) => item.kind !== 'directory' || !state.items.some((other) => other.id !== item.id && ['file', 'directory'].includes(other.kind) && inDirectory(other.path, item.path) && !ids.has(other.id)));
    return allowed.filter((item) => !allowed.some((parent) => parent.id !== item.id && parent.kind === 'directory' && inDirectory(item.path, parent.path)));
  }
  function selectedAncestor(item) {
    return [...state.selected].map((id) => itemMap.get(id)).find((parent) => parent && parent.id !== item.id && parent.kind === 'directory' && inDirectory(item.path, parent.path));
  }
  function isSelected(item) {
    return Boolean(item && (state.selected.has(item.id) || selectedAncestor(item)));
  }
  function toggleItem(item, checked) {
    if (!canSelect(item)) return;
    if (checked) {
      if (selectedAncestor(item)) return;
      if (item.kind === 'directory') [...state.selected].forEach((id) => { const child = itemMap.get(id); if (child && inDirectory(child.path, item.path)) state.selected.delete(id); });
      if (state.selected.size >= 500 && !state.selected.has(item.id)) { toast('每次最多选择 500 项，请分几次处理。'); return; }
      state.selected.add(item.id);
    } else {
      const ancestor = selectedAncestor(item); if (ancestor) { state.selected.delete(ancestor.id); toast('为保留这项，已取消所在文件夹的整组选择。你可以自行挑选其余文件。'); }
      state.selected.delete(item.id);
      if (item.kind === 'directory') [...state.selected].forEach((id) => { const child = itemMap.get(id); if (child && inDirectory(child.path, item.path)) state.selected.delete(id); });
    }
  }
  function notify(message, error = false) { const target = $('global-message'); target.textContent = message; target.hidden = !message; target.classList.toggle('error', error); }
  function toast(message) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false; toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500); }
  async function api(path, body, timeout = 60000) {
    const options=typeof timeout==='object'?timeout:{};
    timeout=typeof timeout==='number'?timeout:options.timeout ?? 60000;
    const control = new AbortController();
    const abort=()=>control.abort();
    options.signal?.addEventListener('abort',abort,{once:true});
    if(options.signal?.aborted)control.abort();
    const timer = setTimeout(() => control.abort(), timeout);
    try {
      const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf }, body: body === undefined ? undefined : JSON.stringify(body), signal: control.signal, credentials: 'same-origin' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {const error=new Error(text(data.error || data.message || `请求失败（${response.status}）`));error.code=data.code;error.status=response.status;throw error;}
      const notice=data?.analysis_notice || data?.result?.analysis_notice;
      if(notice?.status==='retained_previous'&&notice.new_advice_accepted===false)toast(text(notice.message || '本次未取得新建议，保留上次建议。'));
      return data;
    } catch (error) { if (error.name === 'AbortError'&&!options.signal?.aborted) throw new Error('请求超时，请确认本地服务或模型接口后重试。'); throw error; }
    finally { clearTimeout(timer);options.signal?.removeEventListener('abort',abort); }
  }
  function accept(data, autoSelect = false) {
    if (Object.hasOwn(data, 'latest_task')) {
      window.PanxuTasks?.accept(data.latest_task);
      if(state.view==='files') window.PanxuFiles?.refreshAdvice();
      else if(['background','software'].includes(state.view)) renderResults();
      if(state.inspector&&state.focused)renderInspector();
    }
    if (data.catalog_revision) { state.catalogRevision = data.catalog_revision; window.PanxuFiles?.invalidate(data.catalog_revision); }
    if (data.csrf) state.csrf = data.csrf;
    if (typeof data.version === 'string') state.version = data.version;
    if (!defaultPathLoaded && data.default_scan_path) { state.scanPath = data.default_scan_path; defaultPathLoaded = true; }
    if (Array.isArray(data.items)) state.items = data.items;
    if (data.summary) state.summary = data.summary;
    if (data.settings) state.settings = data.settings;
    if (Array.isArray(data.history)) state.history = data.history;
    if (Array.isArray(data.disks)) state.disks = data.disks;
    if (data.source) state.source = data.source;
    if (data.warnings || data.scan?.warnings || data.inventory?.warnings) state.warnings = data.warnings || data.scan?.warnings || data.inventory?.warnings || [];
    itemMap = new Map(state.items.map((item) => [item.id, item]));
    unitCache = new Map(); protectedFiles = state.items.filter((item) => item.risk === 'protected' && ['file', 'directory'].includes(item.kind));
    const available = new Map(state.items.map((item) => [item.id, item]));
    state.selected = new Set([...state.selected].filter((id) => canSelect(available.get(id))));
    if (autoSelect) state.selected = new Set(state.items.filter((item) => !synthetic(item) && verifiedCache(item)).map((item) => item.id));
    if (state.focused) state.focused = available.get(state.focused.id) || null;
    $('files-count').textContent = state.summary.visible_file_items ?? state.summary.file_items ?? 0;
  }
  async function task(action) {
    if (state.busy) return;
    state.busy = true;
    notify('');
    try { await action(); } catch (error) { notify(error.message || '操作失败，请重试。', true); }
    finally { state.busy = false; renderSelection(); }
  }
  function heading(title, subtitle, actions = []) { const node = el('div', 'page-heading'); const copy = el('div'); append(copy, el('h1', '', title), el('p', '', subtitle)); append(node, copy); if (actions.length) append(node, append(el('div', 'heading-actions'), actions)); return node; }
  function section(title, note) { const h = el('div', 'section-heading'); append(h, el('h2', '', title)); if (note) append(h, el('p', '', note)); return h; }
  function empty(title, description, actions = []) { const node = el('div', 'empty-state'); append(node, el('span', 'empty-symbol', '▤'), el('h2', '', title), el('p', '', description), actions); return node; }
  function inputField(label, id, type = 'text', value = '') { const box = el('div', 'form-field'); const lab = el('label', 'field-label', label); lab.htmlFor = id; const input = el('input'); input.id = id; input.type = type; input.value = value || ''; append(box, lab, input); return { box, input }; }
  function sourceLine() { return el('p', 'source-line', `已检查 ${state.items.filter((item) => !item.simulated).length} 项，选择之后会先让你确认。`); }
  function chosen() { return state.items.filter((item) => state.selected.has(item.id)); }

  const navigationStack=[];
  const navigationSnapshots=new Map();let fileTaskId=null,backgroundTaskId=null;
  let cleanupReference=null,cleanupReturn=null;
  const locationSnapshot=()=>({view:state.view,query:state.query,risk:state.risk,origin:state.origin,subtype:state.subtype,limit:state.limit,scroll_y:window.scrollY || 0});
  async function openSpecialist(value) {
    try {
      const origin=locationSnapshot();
      let ref=value;
      if(value.item_id){
        const query=new URLSearchParams({item_id:value.item_id});
        if(value.signature)query.set('signature',value.signature);
        if(value.task_id)query.set('task_id',value.task_id);
        const resolved=await api('/api/specialist?'+query);
        if(!resolved.specialist&&!resolved.extra_suggestion)throw new Error('此建议当前没有可用的专项入口，请重新核对。');
        ref={...(resolved.specialist || {}),...(resolved.extra_suggestion?{extra_suggestion:resolved.extra_suggestion}:{}),source:resolved.source};
      }
      cleanupReturn=ref.source?.return_context || origin;
      cleanupReference={...ref,source:{...(ref.source || {}),return_context:cleanupReturn}};
      setView('cleanup',false);
    }catch(error){notify(error.message,true);}
  }
  window.PanxuSpecialistLinks?.setOpener(openSpecialist);
  function returnFromCleanup() {
    const previous=cleanupReturn;
    cleanupReference=null;cleanupReturn=null;
    setView(previous&&names[previous.view]?previous.view:'overview',false,previous);
    if(previous)requestAnimationFrame(()=>window.scrollTo({top:previous.scroll_y || 0,behavior:'instant'}));
  }
  function renderCleanupTools(root) {
    const context={api,source:cleanupReference?.source || {},onReturn:returnFromCleanup,toast,setContextBack,
      onOpenSettings:async(tool_id,entry,index,parameters)=>{
        const result=await api('/api/cleanup-tools/open-settings',{tool_id,index,parameters,source:cleanupReference?.source || {}});
        toast(result.message);
        return result;
      },
      onAnswer:async()=>{const key=cleanupReference?.source?.task_id;if(key){const value=await api('/api/tasks/'+encodeURIComponent(key));window.PanxuTasks.accept(value.task);}},
      onResult:async()=>{const key=cleanupReference?.source?.task_id;if(key){const value=await api('/api/tasks/'+encodeURIComponent(key));window.PanxuTasks.accept(value.task);}}
    };
    if(cleanupReturn)setContextBack('返回清单',returnFromCleanup);
    const render=cleanupReference?window.PanxuCleanupTools.open(root,cleanupReference,context):window.PanxuCleanupTools.mount(root,context);
    Promise.resolve(render).catch(error=>notify(error.message,true));
  }
  let contextBack=null;
  function setContextBack(label,handler) {
    const back=$('navigation-back');contextBack=typeof handler==='function'?handler:null;
    if(!back)return;
    back.hidden=!label||!contextBack;back.textContent=label?'← '+label:'返回';back.setAttribute('aria-label',label || '返回');
  }
  function defaultBack() {
    if(['overview','current','files','recovery','background','software','cleanup','history','model'].includes(state.view)){setContextBack('',null);return;}
    const previous=navigationStack.at(-1);
    if(previous)setContextBack('返回'+names[previous.view],()=>{
      navigationStack.pop();setView(previous.view,false,previous);
    });
    else if(state.view!=='overview')setContextBack('返回整理助手',()=>setView('overview',false));
    else setContextBack('',null);
  }
  function setView(view,remember=true,snapshot=null) {
    if (!names[view]) return;
    if(view!==state.view){navigationSnapshots.set(state.view,locationSnapshot());if(!snapshot)snapshot=navigationSnapshots.get(view);}
    if(view!==state.view&&remember){
      navigationStack.push({view:state.view,query:state.query,risk:state.risk,origin:state.origin,subtype:state.subtype,limit:state.limit});
      if(navigationStack.length>12)navigationStack.shift();
    }
    state.view = view; state.query = snapshot?.query || ''; state.risk = snapshot?.risk || 'all'; state.origin = snapshot?.origin || 'all'; state.subtype = snapshot?.subtype || 'all'; state.limit = snapshot?.limit || 50;
    const current = view === 'software' ? 'background' : view === 'duplicates' ? 'overview' : view;
    document.querySelectorAll('[data-view]').forEach((b) => { const active = b.dataset.view === current; b.classList.toggle('active', active); if (active) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    $('breadcrumb-current').textContent = names[view];
    toggleInspector(false); render();
  }
  function render() {
    window.PanxuRecovery?.unmount?.();
    window.PanxuFileWorkspace?.unmount?.();
    window.PanxuCleanupTools?.unmount?.();
    window.PanxuFiles?.unmount();
    window.PanxuTasks?.unmountLive?.();
    window.PanxuDuplicates?.unmount();
    defaultBack();
    const root = $('workspace'); root.replaceChildren();
    const renderers = { overview: renderOverview, current: renderCurrentTask, files: renderFiles, recovery:renderRecovery, background: renderBackground, software: renderSoftware, history: renderHistory, model: renderModel, duplicates: renderDuplicates, cleanup: renderCleanupTools };
    renderers[state.view](root);
    renderInspector(); renderSelection();
    $('connection-status').textContent = '本机检查 · 文件正文不会发送';
  }
  function diskCards() {
    const grid = el('div', 'disk-grid compact-disks');
    state.disks.forEach((disk) => {
      const total = Number(disk.total_bytes ?? disk.total) || 0; const free = Number(disk.free_bytes ?? disk.free) || 0;
      const card = el('article', 'disk-card'); const label = disk.name || disk.path || disk.drive || '磁盘';
      const title = el('div', 'disk-label'); append(title, el('span', '', label), el('span', 'disk-free', `${bytes(free)} 可用`));
      const meter = el('progress'); meter.max = total || 1; meter.value = Math.max(0, total - free); meter.setAttribute('aria-label', `${label}可用${bytes(free)}`);
      append(card, title, meter); append(grid, card);
    }); return grid;
  }
  function openTaskResults(task) {
    if(!task)return;
    const application=task.scope_kind==='applications'||['background','software'].includes(task.analysis?.section);
    if(application){backgroundTaskId=task.id;setView('background',false);}
    else{fileTaskId=task.id;window.PanxuFileWorkspace.open(task.scopes?.[0]?.path || '',{suggestions:true});setView('files',false);}
  }
  function taskHelpers(page) {
    return {
      api, heading, bytes, defaultPath: state.scanPath, disks: state.disks, providerPresets:state.providers, modelProfile: {provider:state.settings.provider,model:state.settings.model,protocol:state.settings.protocol},
      modelConfigured: Boolean(state.settings.base_url && state.settings.model),
      page,embeddedResult:page==='results',redraw: render, isActive: () => page==='home'?state.view==='overview':page==='task'?state.view==='current':['files','background'].includes(state.view),
      renderHomeInfo:host=>window.PanxuRecovery.home(host,{api,bytes,open:()=>setView('recovery')}),
      openSettings: () => setView('model'), openFiles: () => {fileTaskId=null;window.PanxuFileWorkspace.open('',{suggestions:true});setView('files');},
      openAssistant: () => setView('current'),openHome:()=>setView('overview'),
      onTaskCreated:()=>setView('current',false),onTaskReady:task=>{if(state.view==='current')openTaskResults(task);},openResults:openTaskResults,
      openDuplicates:()=>setView('duplicates'),setContextBack,
      showItem,openSpecialist,
      autoSelectFour:window.PanxuAppearance.get().cleanup_auto_select_four,
      refreshCatalog: async () => { accept(await api('/api/bootstrap?compact=1')); }
    };
  }
  function renderOverview(root) {window.PanxuTasks.render(root,taskHelpers('home'));}
  function renderCurrentTask(root) {window.PanxuTasks.render(root,taskHelpers('task'));}
  function renderRecovery(root) {window.PanxuRecovery.render(root,{api,bytes,heading,changed:async()=>{accept(await api('/api/bootstrap?compact=1'));},providerPresets:state.providers});}
  function renderDuplicates(root) {
    window.PanxuDuplicates.render(root,{api,heading,bytes,defaultPath:state.scanPath,disks:state.disks,setContextBack,redraw:render});
  }
  async function showItem(item) {
    state.focused = item; state.assistant = null; renderInspector(); toggleInspector(true);
    try {
      const data = await api('/api/item?id=' + encodeURIComponent(item.id));
      if (state.focused?.id !== item.id) return;
      state.focused = { ...data.item, ...(typeof item.can_select === 'boolean' ? {can_select: item.can_select} : {}) }; renderInspector();
    } catch (error) { notify(error.message, true); }
  }
  function ingest(items) {
    const retainedIds = new Set([...state.selected, ...[...analysisSelections.values()].flatMap((ids) => [...ids])]);
    const retained = state.items.filter((item) => retainedIds.has(item.id));
    const merged = new Map(items.map((item) => [item.id, item]));
    retained.forEach((item) => {if(!merged.has(item.id))merged.set(item.id,item);});
    state.items = [...merged.values()];
    itemMap = new Map(state.items.map((item) => [item.id, item]));
    if(state.focused && merged.has(state.focused.id))state.focused=merged.get(state.focused.id);
    protectedFiles = state.items.filter((item) => item.risk === 'protected');
    unitCache = new Map();
  }
  async function loadApplications(sectionName) {
    const data = await api('/api/applications?section=' + sectionName);
    ingest(data.items);
  }
  function sectionItems() {
    if (state.view === 'files') return state.items.filter((item) => ['file', 'directory'].includes(item.kind));
    if (state.view === 'background') return state.items.filter((item) => ['startup', 'service', 'process'].includes(item.kind));
    if (state.view === 'software') return state.items.filter((item) => item.kind === 'software');
    return [];
  }
  function visibleItems() {
    const q = state.query.toLowerCase();
    return sectionItems().filter((item) => {
      if (q && ![item.name, item.path, item.owner, item.category].some((value) => text(value).toLowerCase().includes(q))) return false;
      if (state.risk !== 'all' && item.risk !== state.risk) return false;
      if (state.origin === 'demo' && !synthetic(item)) return false;
      if (state.origin === 'real' && synthetic(item)) return false;
      if (state.subtype !== 'all' && item.kind !== state.subtype) return false;
      return true;
    });
  }
  function makeSelect(options, value, callback, label) { const select = el('select'); select.setAttribute('aria-label', label); options.forEach(([v, l]) => { const option = el('option', '', l); option.value = v; select.append(option); }); select.value = value; select.addEventListener('change', () => callback(select.value)); return select; }
  function listToolbar(root) {
    const tools = el('div', 'toolbar'); const search = el('input', 'search-input'); search.type = 'search'; search.placeholder = '搜索文件或软件名称'; search.setAttribute('aria-label', '搜索当前结果'); search.value = state.query;
    search.addEventListener('input', () => { state.query = search.value; state.limit = 50; renderResults(); renderSelection(); });
    append(tools, search, makeSelect([['all', '全部内容'], ['low', '可处理'], ['review', '需要决定'], ['protected', '已保留']], state.risk, (value) => { state.risk = value; renderResults(); renderSelection(); }, '筛选内容')); append(root, tools);
  }
  function locationPicker(root) {
    const details = el('details', 'location-picker'); const sum = el('summary', '', '选择检查位置'); append(details, sum);
    const controls = el('div', 'scan-controls'); const path = el('input'); path.value = state.scanPath; path.placeholder = '输入文件夹位置'; path.setAttribute('aria-label', '要检查的文件夹位置'); path.addEventListener('input', () => { state.scanPath = path.value; });
    const options = [['', '选择磁盘'], ...state.disks.map((disk) => [disk.path || disk.name, disk.name || disk.path])]; const disk = makeSelect(options, '', (value) => { if (value) { path.value = value; state.scanPath = value; } }, '选择一个磁盘');
    append(controls, disk, path, button('检查这个位置', () => { state.checkDestination = 'files'; startScan(path.value, false); }, 'button primary')); append(details, controls); append(root, details);
  }
  function renderFiles(root) {
    append(root, heading('文件清理', '按位置查看，按需分析，再选择要清理的内容。'));
    const advanced=el('div','file-advanced-controls');locationPicker(advanced);analysisOptions(advanced);appendJob(root);
    const prepare=async payload=>{
      const data=await api('/api/assessments/plan',payload);
      if(!window.PanxuTasks.acceptPickedPlan(data))throw new Error('返回的删除清单与选择不一致，请刷新后重试。');
      openTaskResults(data.task);
      return data;
    };
    const current=window.PanxuTasks.currentTask(),taskScoped=current?.id===fileTaskId;
    if(taskScoped){append(root,el('p','field-help','本次任务：'+current.goal),button('查看所有已有建议',()=>{fileTaskId=null;render();},'plain-button'));}
    const fileHost=el('section','file-workspace');root.append(fileHost);
    window.PanxuFileWorkspace.render(fileHost, {
      api,revision: state.catalogRevision, disks: state.disks, bytes, ingest,advanced,
      changed:renderSelection,analyze:(item)=>analyzeList(item),show:showItem,
      analyzeItems:async(items)=>{
        const ids=[...new Set(items.filter(canAnalyze).map(item=>item.id))];if(!ids.length)return;
        await task(async()=>{const data=await api('/api/tasks/analyze',{section:'files',depth:analysisDepth,ids});window.PanxuTasks.accept(data.task);setView('current');});
      },
      analyzePath:async path=>task(async()=>{const data=await api('/api/tasks',{preset:'directory_cleanup',path});window.PanxuTasks.accept(data.task);setView('current');}),
      renderTaskResults:taskScoped?host=>window.PanxuTasks.renderResultWorkspace(host,taskHelpers('results')):null,
      prepare,prepareLogical:prepare,latestTask:()=>window.PanxuTasks.currentTask(),
      adviceChanged:()=>{if(state.inspector&&state.focused)renderInspector();}
    });
  }
  function simpleItemRow(item) {
    const row = el('div', 'simple-item-row'); const check = el('input'); check.type = 'checkbox'; check.checked = isSelected(item); check.disabled = !canSelect(item); check.setAttribute('aria-label', `选择${item.name}`); check.addEventListener('change', () => { toggleItem(item, check.checked); renderSelection(); });
    append(row, check, append(el('div', 'simple-item-copy'), itemTitle(item, el('strong', '', item.name)), el('small', '', item.owner || item.category || '')), el('span', 'simple-item-size', `约 ${bytes(item.size_bytes || itemSize(item))}`), button('查看详情', () => showItem(item), 'plain-button')); return row;
  }
  function renderBackground(root) {
    const current=window.PanxuTasks.currentTask();
    if(current?.id===backgroundTaskId){append(root,heading('后台与软件',current.goal),button('查看全部后台与软件',()=>{backgroundTaskId=null;render();},'plain-button'));window.PanxuTasks.renderResultWorkspace(root,taskHelpers('results'));return;}
    append(root, heading('后台与软件', '了解开机启动、后台应用和已经安装的软件。'));
    appendJob(root);
    if (!state.backgroundExpanded) {
      const panel = el('div', 'panel action-list');
      [['startup', '开机启动', '管理开机后自动打开的应用。'], ['service', '后台服务', '先了解依赖和用途，再判断是否调整。'], ['process', '后台应用', '看看哪些应用正在运行。'], ['software', '已装软件', '了解用途，再考虑是否保留。']].forEach(([kind, title, description]) => {
        const row = el('div', 'action-row'); const count = state.items.filter((item) => item.kind === kind && !item.simulated).length;
        append(row, append(el('div', 'action-copy'), el('h3', '', title), el('p', '', description)), count ? el('span', 'muted', `${count} 项`) : null, button('查看', () => openInventory(kind), 'button small')); append(panel, row);
      }); append(root, panel, el('p', 'quiet-note', '系统关键内容会保留。检查不会结束正在运行的应用。')); return;
    }
    const tabs = el('div', 'subtabs'); [['startup', '开机启动'], ['process', '后台应用'], ['service', '更多后台服务']].forEach(([value, label]) => append(tabs, button(label, () => { state.subtype = value; render(); }, `subtab${state.subtype === value ? ' active' : ''}`))); append(root, tabs);
    analysisOptions(root);
    append(root, button('重新检查', () => { state.inventoryTarget = state.subtype; startInventory('background'); }, 'plain-button')); listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
  }
  async function openInventory(kind) {
    state.inventoryTarget = kind; state.backgroundExpanded = true;
    const target = kind === 'software' ? 'software' : 'background';
    try { await loadApplications(target); } catch (error) { notify(error.message, true); return; }
    if (state.items.some((item) => item.kind === kind && !synthetic(item))) { setView(target); state.subtype = kind === 'software' ? 'all' : kind; render(); }
    else await startInventory(target);
  }
  function renderSoftware(root) {
    append(root, heading('已装软件', '使用情况不确定时，建议先保留。', [button('返回', () => { state.backgroundExpanded = false; setView('background'); }, 'plain-button')]));
    appendJob(root); analysisOptions(root); listToolbar(root); const results = el('div'); results.id = 'results-container'; append(root, results); renderResults();
  }
  function selectionBox(items, label) {
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', label);
    const allowed = items.filter(canAnalyze), count = allowed.filter(analysisSelected).length;
    checkbox.disabled = !allowed.length; checkbox.checked = !!allowed.length && count === allowed.length; checkbox.indeterminate = count > 0 && count < allowed.length;
    checkbox.addEventListener('change', () => { allowed.forEach((item) => toggleAnalysis(item, checkbox.checked)); renderResults(); renderSelection(); });
    return checkbox;
  }
  function renderInventoryAdvice(host,item,entry) {
    host.replaceChildren();
    const assessed=item.assessment && item.assessment.state!=='unanalysed';
    if(!assessed&&!entry){append(host,el('p','inventory-advice-pending field-help','尚未分析。取得建议后，这里会直接显示用途、建议、原因和影响。'));return;}
    append(host,window.PanxuAdvicePresentation.render(assessed?item:{...item,...entry,assessment:undefined},{direct:!assessed}));
  }
  function renderResults() {
    if (state.view === 'files') { window.PanxuFiles?.selectionChanged(); return; }
    const host = $('results-container'); if (!host) return;
    host.replaceChildren(); const items = visibleItems().filter(canAnalyze); const all = sectionItems();
    if (!items.length) { append(host, append(el('div', 'panel'), empty(all.length ? '没有匹配的项目' : '这里暂时没有内容', all.length ? '试着调整搜索或筛选，之前的分析选择会保留。' : '完成检查后，可以了解用途并取得建议。'))); return; }
    const panel = el('div', 'panel'); const tools = el('div', 'result-tools'); const label = el('label', 'check-line');
    append(label, selectionBox(items, '选择当前结果用于分析'), el('span', '', `全选当前结果（${items.length} 项）`));
    const count = analysisIds().size;
    append(tools, label); if (!count) append(tools, button(`分析当前列表（${items.length} 项）`, () => analyzeList(), 'button primary list-analyze-button')); append(panel, tools);
    const scroll = el('div', 'table-scroll'); const table = el('table', 'result-table'); const head = el('tr');
    ['', '清理倾向', '名称', state.view === 'background' ? '运行占用' : '大小', ''].forEach((name) => append(head, el('th', '', name))); append(table, append(el('thead'), head));
    const body = el('tbody');
    function showRating(cell,item,result) {
      const record=item.assessment?item:result || {assessment:{state:'unanalysed',rating:null}};
      cell.replaceChildren(window.PanxuAssessment.render(record,{direct:Boolean(result),primary:true,cls:'item-advice-badge'}));
    }
    const renderedItems=items.slice(0,state.limit);
    renderedItems.forEach((item) => {
      const row = el('tr', 'item-row'); row.dataset.adviceId=item.id; const checkCell = el('td', 'check-cell'); const check = el('input'); check.type = 'checkbox'; check.checked = analysisSelected(item);
      const identity=item.kind==='process'&&Number.isInteger(item.details?.pid)?`PID ${item.details.pid}`:item.kind==='service'?item.details?.service_name:'';
      check.setAttribute('aria-label', '选择分析：' + item.name + (identity?' · '+identity:'')); check.addEventListener('change', () => { toggleAnalysis(item, check.checked); renderResults(); renderSelection(); }); append(checkCell, check);
      const copy = el('td', 'name-cell'); append(copy, itemTitle(item, button(item.name, () => showItem(item), 'file-name-button')), el('div', 'item-path item-secondary', [identity,item.owner || kindNames[item.kind]].filter(Boolean).join(' · ')));
      const size = el('td', 'size-cell', ['service','startup'].includes(item.kind) ? '—' : bytes(itemSize(item)));
      const result = window.PanxuTasks.adviceFor(item.id, item.signature);
      const status = el('td', 'kind-label result-rating-cell');status.dataset.adviceState='1';showRating(status,item,result);
      const known=result || item.assessment && item.assessment.state!=='unanalysed';
      const action = el('td'); const analyze=button(known ? '查看建议' : '分析', () => showAdvice(item), 'button small');analyze.dataset.rowAdvice='1';append(action,analyze);
      append(row, checkCell, status,copy,size,action); append(body, row);
      const explanationRow=el('tr','inventory-explanation-row'),explanationCell=el('td'),explanation=el('div','inventory-item-advice');
      explanationCell.colSpan=5;explanationRow.dataset.adviceDetailsId=item.id;
      renderInventoryAdvice(explanation,item,result);append(explanationCell,explanation);append(explanationRow,explanationCell);append(body,explanationRow);
    });
    append(table, body); append(scroll, table); append(panel, scroll, el('p', 'result-note', '勾选仅用于分析。助手提出建议后，才决定是否处理。'));
    if (items.length > state.limit) append(panel, button(`继续显示 ${Math.min(50, items.length-state.limit)} 项`, () => { state.limit += 50; renderResults(); }, 'plain-button'));
    const adviceNote=el('p','result-note');adviceNote.hidden=true;append(panel,adviceNote);append(host, panel);
    window.PanxuTasks.lookupAdvice(renderedItems,api).then(()=>{
      if(!panel.isConnected || host.firstChild!==panel) return;
      renderedItems.forEach(item=>{const row=[...body.children].find(r=>r.dataset.adviceId===item.id);if(!row)return;const entry=window.PanxuTasks.adviceFor(item.id,item.signature);showRating(row.querySelector('[data-advice-state]'),item,entry);row.querySelector('[data-row-advice]').textContent=entry || item.assessment && item.assessment.state!=='unanalysed'?'查看建议':'分析';const details=[...body.children].find(r=>r.dataset.adviceDetailsId===item.id);if(details)renderInventoryAdvice(details.querySelector('.inventory-item-advice'),item,entry);});
    }).catch(()=>{if(panel.isConnected){adviceNote.hidden=false;adviceNote.textContent='已有建议暂时无法读取；点击分析时会先重试读取。';}});
  }
  function renderSelection() {
    const active = ['background','software'].includes(state.view);
    const ids = active ? analysisIds() : new Set();
    $('selection-bar').hidden = !active || !ids.size;
    $('selection-count').textContent = `已选 ${ids.size} 项用于分析`;
    $('selection-size').textContent = '选择分析不等于确认删除或其他操作';
    const visible = new Set(state.view === 'files' ? window.PanxuFiles.visibleIds() : visibleItems().map((i) => i.id));
    const hidden = [...ids].filter((id) => !visible.has(id)).length;
    $('hidden-selection').hidden = !hidden; $('hidden-selection').textContent = `其中 ${hidden} 项在其他页面`;
    $('preview-plan').textContent = `分析所选（${ids.size} 项）`; $('preview-plan').disabled = state.busy;
  }
  function renderInspector() {
    const host = $('inspector-content'); host.replaceChildren();
    const head = el('div', 'inspector-heading'); append(head, el('h2', '', '项目说明'), button('关闭', () => toggleInspector(false), 'icon-button')); append(host, head);
    const item = state.focused;
    if (!item) { append(host, el('h3', '', '选一项，看看原因'), el('p', '', '这里会说明用途、操作建议和可能的影响。')); return; }
    const assessment=item.assessment&&item.assessment.state!=='unanalysed'?window.PanxuAssessment.read(item):null;
    const modelAdvice = assessment?null:window.PanxuTasks.adviceFor(item.id, item.signature);
    const rec = assessment?{...assessment,assessment}:modelAdvice || advice(item);
    if(assessment || modelAdvice)append(host,window.PanxuAssessment.render(assessment?item:modelAdvice,{direct:Boolean(modelAdvice),primary:true,cls:'inspector-primary-rating'}));
    append(host, itemTitle(item, el('h3', '', rec.title || item.name)));
    if(assessment?.state==='inherited')append(host,el('p','field-help',`继承自${assessment.inherited_from_name || '上级目录'}，此项未单独分析。`));
    const blocked=assessment?window.PanxuAssessment.blocked(item):'';if(blocked)append(host,el('p','field-help',blocked));
    const description={...item,...rec,assessment:modelAdvice?undefined:item.assessment,purpose:rec.purpose || itemPurpose(item),impact:rec.impact || item.impact};
    append(host,window.PanxuAdvicePresentation.render(description,{direct:Boolean(modelAdvice)}));
    const sourceTaskId=assessment?.source_ref?.task_id || assessment?.inherited_from?.task_id;
    if(sourceTaskId)append(host,button('查看来源任务',()=>task(async()=>{const data=await api('/api/tasks/'+encodeURIComponent(sourceTaskId));window.PanxuTasks.accept(data.task);setView('current');}),'button primary'));
    else if (modelAdvice) append(host,button(window.PanxuTasks.currentTask()?.plan?.items.some(entry=>entry.id===item.id)?'查看操作方案':'返回整理任务',()=>setView('current'),'button primary'));
    else if (canAnalyze(item)) append(host, button('分析这项', () => analyzeList(item), 'button primary'));
    append(host, button('以后保留这项', () => protectItem(item), 'plain-button'));
    const detail = el('details', 'inspector-advanced'); append(detail, el('summary', '', '更多详情'));
    if (item.path) append(detail, el('div', 'inspector-path', item.path));
    const data = el('dl', 'inspector-definition'); [['所属软件', item.owner || '未知'], ['类型', kindNames[item.kind]], ['文件逻辑量', bytes(item.size_bytes)], ['分配空间', item.allocated_bytes == null ? '未核实' : bytes(item.allocated_bytes)]].forEach(([key, value]) => append(data, el('dt', '', key), el('dd', '', value)));
    if(['file','directory'].includes(item.kind)){
      const timestamps=item.details?.timestamps || {};
      for(const [label,key] of [['创建时间','created_at'],['修改时间','modified_at'],['访问时间','accessed_at'],['信息采集时间','observed_at']]){
        const stamp=window.PanxuProvenance.formatTime(timestamps[key]),value=el('dd');
        if(stamp){const time=el('time','',stamp.local);time.dateTime=stamp.utc;time.title='UTC：'+stamp.utc;append(value,time);}else value.textContent='未核实';
        append(data,el('dt','',label),value);
      }
      append(detail,el('p','field-help',item.kind==='directory'?'这些时间只描述目录条目，不代表内部文件最近活动；访问时间不等于最后使用。':'访问时间不等于最后使用；文件时间只是判断依据。'));
    }
    if (item.kind === 'directory') { append(data, el('dt', '', '路径累计'), el('dd', '', bytes(item.details?.logical_bytes_all_paths ?? item.size_bytes)), el('dt', '', '唯一实体量'), el('dd', '', bytes(item.details?.unique_logical_bytes ?? item.size_bytes))); append(detail, el('p', '', '同一个文件可能有多个硬链接。这里的体积估计不等于实际可释放空间。')); }
    append(detail, data,window.PanxuProvenance.details(rec,{presets:state.providers}));
    if((assessment && assessment.state!=='unanalysed' || modelAdvice)&&canAnalyze(item))append(detail,button('重新分析此项',()=>analyzeList(item),'plain-button'));
    if (canSelect(item)) { const manual = el('details', 'manual-actions'); append(manual, el('summary', '', '手动选择操作'), el('p', 'field-help', '这项操作不依赖 AI 建议。确认前会列出具体内容与影响，保护规则继续生效。'), button(`查看${window.PanxuActionCopy.label(item.actions[0],item.kind)}清单`, () => manualSelected(item))); append(detail, manual); }
    if ($('show-evidence').checked) { const evidence = el('ul', 'evidence-list'); (Array.isArray(item.evidence) ? item.evidence : []).forEach((entry) => append(evidence, el('li', '', text(entry)))); append(detail, evidence, el('pre', 'assistant-trace', text(item.details || {}))); }
    if(!(modelAdvice || assessment && assessment.state!=='unanalysed')){
      const more = el('details', 'additional-analysis'); append(more, el('summary', '', '进一步分析这项'));
      const form = el('form', 'assistant-mini'); const input = el('textarea'); input.setAttribute('aria-label', '想了解什么'); input.placeholder = '例如：保留它还有什么用途？'; const submit = el('button', 'button', '分析'); submit.type = 'submit'; append(form, input, submit); form.addEventListener('submit', (event) => { event.preventDefault(); askAssistant(input.value || input.placeholder, more, submit); }); append(more, form); if (state.assistant) append(more, analysisOutput(state.assistant)); append(detail, more);
    }
    append(host, detail);
  }
  async function protectItem(item) {
    if (!item.path) { toast('暂时无法保存此项，请到设置查看保留位置。'); return; }
    await task(async () => {
      const patterns = [...new Set([...(state.settings.protect_patterns || []), item.path])];
      accept(await api('/api/settings', settingsPayload({ protect_patterns: patterns }))); accept(await api('/api/bootstrap?compact=1')); render(); toast('已加入保留清单。');
    });
  }
  function toggleInspector(force) {
    state.inspector = force === undefined ? !state.inspector : force;
    $('inspector').hidden = !state.inspector; $('content-grid').classList.toggle('inspector-hidden', !state.inspector);
    $('inspector-toggle').hidden = !state.inspector; $('inspector-toggle').setAttribute('aria-expanded', String(state.inspector));
  }
  function loadDemo() { return Promise.resolve(); }
  async function startScan(path, toSuggestions = false) {
    const clean = path.trim(); if (!clean) { notify('请先输入想检查的文件夹位置。', true); return; }
    if (state.job?.status === 'running') { notify('正在检查，请稍等或先取消。'); return; }
    await task(async () => {
      state.scanPath = clean; state.checkDestination = toSuggestions ? 'overview' : 'files';
      const data = await api('/api/scan', { path: clean, compact: true, max_entries: Number($('scan-limit').value) }); state.job = { id: data.job_id, status: 'running', progress: {}, kind: 'files' };
      setView(toSuggestions ? 'overview' : 'files'); pollJob(data.job_id);
    });
  }
  async function startInventory(sectionName) {
    if (state.job?.status === 'running') { notify('正在检查，请稍等或先取消。'); return; }
    await task(async () => {
      state.checkDestination = sectionName;
      const data = await api('/api/inventory', { section: sectionName, compact: true }); state.job = { id: data.job_id, status: 'running', progress: {}, kind: sectionName };
      setView(sectionName); state.backgroundExpanded = true; state.subtype = sectionName === 'software' ? 'all' : state.inventoryTarget || 'startup'; render(); pollJob(data.job_id);
    });
  }
  function appendJob(root) { if (state.job && (state.job.status !== 'completed' || state.job.result?.summary?.partial || state.job.result?.warnings?.length)) append(root, jobElement()); }
  function jobElement() {
    const panel = el('div', 'job-panel'); panel.id = 'job-panel'; const job = state.job; const running = job.status === 'running';
    const status = { running: '正在检查，请稍等', completed: '检查完成', failed: '这次检查未完成', cancelled: '已取消检查' };
    append(panel, el('p', job.status === 'failed' ? 'status-failed' : '', status[job.status] || '正在检查'));
    if (running) { const meter = el('progress'); meter.setAttribute('aria-label', '检查进度'); append(panel, meter, button('取消', () => task(async () => { const data = await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {}); state.job = { ...state.job, ...(data.job || data) }; updateJobPanel(); }), 'plain-button')); }
    if (job.error) append(panel, el('p', 'field-error', cleanCopy(job.error)));
    const notes = job.result?.warnings || [];
    if (job.result?.summary?.partial || notes.length) { const details = el('details'); append(details, el('summary', '', '有些位置尚未检查完整'), el('pre', '', notes.map(cleanCopy).join('\n') || '请指定更小的文件夹继续检查。')); append(panel, details); }
    if (!running) append(panel, button('收起', () => { state.job = null; render(); }, 'plain-button'));
    return panel;
  }
  function updateJobPanel() { const old = $('job-panel'); if (old) old.replaceWith(jobElement()); }
  async function pollJob(id) {
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(id)}`); if (!state.job || state.job.id !== id) return;
      state.job = { ...state.job, ...data }; updateJobPanel();
      if (data.status === 'running') { setTimeout(() => pollJob(id), 600); return; }
      if (data.status === 'completed' && data.result) {
        accept(data.result.catalog || data.result, false); state.warnings = data.result.warnings || []; state.selected.clear(); state.focused = null;
        if (['background', 'software'].includes(state.checkDestination)) await loadApplications(state.checkDestination);
        render();
      } else if (data.status === 'failed') { state.analyzing = false; notify(cleanCopy(data.error || '检查没有完成，请重试。'), true); render(); }
      else if (data.status === 'cancelled') { state.analyzing = false; render(); toast('已取消，之前的检查结果会保留。'); }
    } catch (error) { if (state.job?.id === id) { state.job.status = 'failed'; state.job.error = error.message; state.analyzing = false; updateJobPanel(); notify(error.message, true); render(); } }
  }
  async function previewPlan() {
    const ids = [...state.selected]; if (!ids.length) return;
    const mode = state.view === 'overview' && chosen().every(verifiedCache) ? 'recommended' : 'manual';
    await task(async () => { const plan = await api('/api/plan', { ids, selection_mode: mode, snapshots: Object.fromEntries(chosen().map((item) => [item.id, item.signature])) }); state.plan = plan; renderPlan(plan); $('plan-dialog').showModal(); });
  }
  function planActionCopy(plan) {
    const entries=Array.isArray(plan?.items)?plan.items:[];
    return window.PanxuActionCopy.summary(entries.map(entry=>{const item=entry.item || state.items.find(i=>i.id===(entry.id || entry.item_id)) || entry;return {action:entry.action || item.actions?.[0],kind:item.kind};}));
  }
  function renderPlan(plan) {
    const copy=planActionCopy(plan),host = $('plan-content'); host.replaceChildren(); $('plan-execute').hidden = false; $('plan-execute').disabled = false; $('plan-execute').textContent=copy.button;$('plan-title').textContent = !copy.count?'没有可用操作':copy.groups.length===1?`${copy.groups[0].label}前确认`:'确认所选操作';
    const entries = Array.isArray(plan.items) ? plan.items : [];
    append(host, append(el('div', 'plan-summary'), el('strong', '', copy.breakdown || '尚未选择操作'), el('span', '', copy.effect)));
    const list = el('div', 'plan-list'); entries.forEach((entry) => {
      const item = entry.item || state.items.find((i) => i.id === (entry.id || entry.item_id)) || entry; const rec = advice(item); const row = el('div', 'plan-item');
      append(row, itemTitle(item, el('strong', '', `${window.PanxuActionCopy.label(entry.action || item.actions?.[0],item.kind)} · ${item.name || entry.name}`)), el('p', '', cleanCopy(rec.impact || item.impact || '请确认了解这项操作的影响。')));
      const detail = el('details'); append(detail, el('summary', '', '查看位置与建议来源'), el('p', '', item.path || entry.path || '无文件位置'),
        window.PanxuProvenance.details({provenance:entry.provenance || entry.advice?.provenance || rec.provenance},{presets:state.providers})); append(row, detail); append(list, row);
    }); append(host, list);
    const warnings = (plan.warnings || []).filter(value=>value!=='此批次仅写入模拟记录，不删除文件、不修改启动项、不停止服务或进程、不卸载软件。'); if (warnings.length) { const detail = el('details', 'plan-additional'); append(detail, el('summary', '', '更多操作说明'), el('pre', '', warnings.map(cleanCopy).join('\n'))); append(host, detail); }
    if (!entries.length) { append(host, el('p', 'field-error', '没有可用操作，请返回重新选择。')); $('plan-execute').textContent='没有可用操作';$('plan-execute').disabled = true; }
  }
  async function executePlan() {
    if (!state.plan) return; const copy=planActionCopy(state.plan),target = $('plan-execute'); target.disabled = true; target.textContent = copy.groups.length===1?`正在${copy.groups[0].label}…`:'正在执行所选操作…';
    await task(async () => {
      try {
        const data = await api('/api/execute', { plan_id: state.plan.id }); accept(data); state.selected.clear(); state.plan = null; state.focused = null; $('plan-dialog').close();
        setView('history'); toast('操作结果已记录。');
      } catch (error) { const prior = $('plan-content').querySelector('.field-error'); if (prior) prior.remove(); append($('plan-content'), el('p', 'field-error', `${cleanCopy(error.message)} 请返回调整。`)); throw error; }
    }); target.disabled = false; target.textContent = copy.button;
  }
  function renderHistory(root) {
    append(root, heading('操作记录', '查看已确认的操作及其结果。'));
    window.PanxuTasks?.renderHistory(root);
    const emptyHistory=state.history.length?null:empty('还没有操作记录','确认操作后，结果会显示在这里。');
    if(emptyHistory)emptyHistory.hidden=true;
    const specialist=el('section','specialist-history');root.append(specialist);
    api('/api/cleanup-tools/history').then(({history})=>{
      if(!specialist.isConnected)return;
      if(!history?.length){if(emptyHistory)emptyHistory.hidden=false;return;}
      specialist.append(section('专项操作','查看工具方案、选择和核验结果。'));
      const labels={awaiting_review:'待确认',completed:'已记录',cancelled:'已取消',interrupted:'中断待核实',undone:'已撤销记录',executing:'进行中'};
      history.forEach(plan=>{
        const line=el('article','history-card');append(line,el('h3','',plan.title),el('p','field-help',labels[plan.status] || '待核实'),
          button('查看方案与结果',()=>openSpecialist({plan_id:plan.id,source:plan.source}),'button small'));
        if(plan.result&&['completed','interrupted'].includes(plan.status))line.append(button('撤销记录',()=>task(async()=>{await api('/api/cleanup-tools/undo',{plan_id:plan.id});render();}),'plain-button'));
        specialist.append(line);
      });
    }).catch(error=>{if(specialist.isConnected)specialist.append(el('p','field-error',error.message));});
    if (!state.history.length) { append(root,emptyHistory); return; }
    const list = el('div', 'history-list'); state.history.forEach((batch) => {
      const card = el('article', 'history-card'); const top = el('div', 'history-top'); const date = new Date(batch.created_at); const when = Number.isNaN(date.getTime()) ? text(batch.created_at) : date.toLocaleString('zh-CN', { hour12: false });
      append(top, el('h3', '', `已记录 ${batch.item_count ?? batch.items?.length ?? 0} 项操作`), badge(batch.status === 'undone' ? '已撤销' : batch.status==='partially_undone'?'部分记录已更新':'已记录')); append(card, top, el('p', '', when));
      const detail = el('details', 'history-details'); append(detail, el('summary', '', '查看具体操作')); const entries = el('ul'); (batch.items || []).forEach((item) => append(entries, append(el('li'), itemTitle(item, el('span', '', `${window.PanxuActionCopy.label(item.action,item.kind)} · ${item.name || item.id}`))))); append(detail, entries); append(card, detail);
      const footer = el('div', 'history-footer'); append(footer, el('span', '', batch.recovery_counts?.purged?'永久删除的项目无法还原，其余可恢复项仍可撤销。':'撤销后可重新选择相关项目。')); const restore = button('撤销', () => undoBatch(batch.id), 'button small'); restore.disabled = batch.status === 'undone'; append(footer, restore); if((batch.items || []).some(item=>item.action==='delete'&&['file','directory'].includes(item.kind)))footer.append(button('查看已删除文件',()=>setView('recovery'),'plain-button'));append(card, footer); append(list, card);
    }); append(root, list);
  }
  async function undoBatch(id) {
    await task(async () => { const batch = state.history.find((entry) => entry.id === id); accept(await api('/api/undo', { batch_id: id })); accept(await api('/api/bootstrap?compact=1')); state.selected = new Set((batch?.items || []).map((item) => item.id).filter((key) => canSelect(state.items.find((item) => item.id === key)))); state.checked = state.items.length > 0; render(); toast(state.history.find(entry=>entry.id===id)?.status==='partially_undone'?'已撤销可恢复项；其余项目保留原状态，请查看已删除文件。':'已撤销，相关项目可重新选择。'); });
  }
  function settingsPayload(overrides = {}) { return { provider: state.settings.provider || 'custom', protocol: state.settings.protocol || 'chat_completions', reasoning_effort: state.settings.reasoning_effort || 'default', base_url: state.settings.base_url || '', model: state.settings.model || '', stream: state.settings.stream !== false, send_paths: state.settings.send_paths === true, protect_patterns: Array.isArray(state.settings.protect_patterns) ? state.settings.protect_patterns : [], ...overrides }; }
  function renderModel(root) {
    append(root, heading('设置', '连接模型，按你的习惯整理。'));
    const panel = el('section', 'panel simple-setting'); append(panel, el('h2', '', '连接智能助手'));
    const form = el('form', 'form-panel provider-form'); const grid = el('div', 'form-grid');
    const presets = state.providers || [];
    const providerBox = el('div', 'form-field'); const providerLabel = el('label', 'field-label', '模型服务商'); providerLabel.htmlFor = 'model-provider';
    const provider = makeSelect([['','选择模型服务商'],['custom','自定义兼容服务'], ...presets.filter((p) => p.id !== 'custom').map((p) => [p.id,p.name])], state.settings.provider || 'custom', chooseProvider, '模型服务商'); provider.id = 'model-provider'; if (!state.settings.base_url) provider.value = ''; append(providerBox, providerLabel, provider);
    const modelBox = el('div', 'form-field'); const modelLabel = el('label', 'field-label', '模型'); modelLabel.htmlFor = 'model-choice';
    const modelChoice = el('select'); modelChoice.id = 'model-choice'; modelChoice.setAttribute('aria-label','模型'); append(modelBox, modelLabel, modelChoice);
    const key = inputField('API 密钥', 'model-key', 'password'); key.input.autocomplete = 'new-password'; key.input.placeholder = state.settings.has_key ? '已保存，留空保留当前连接的密钥' : '填写服务商提供的 API 密钥'; key.box.classList.add('wide');
    append(grid, providerBox, modelBox, key.box);
    const note = el('p', 'field-help field-description provider-note provider-guidance');
    const advanced = el('details', 'setting-details provider-advanced'); append(advanced, el('summary', '', '高级连接'));
    const base = inputField('服务地址', 'model-base', 'url', state.settings.base_url); base.input.placeholder = 'https://…/v1';
    const model = inputField('模型 ID（可手动输入）', 'model-name', 'text', state.settings.model);
    const protocolBox = el('div','form-field'); const protocolLabel = el('label','field-label','协议'); protocolLabel.htmlFor='model-protocol';
    const protocol = makeSelect([['chat_completions','Chat Completions'],['responses','Responses'],['anthropic','Anthropic Messages'],['gemini','Gemini']], state.settings.protocol || 'chat_completions', () => {}, '协议'); protocol.id='model-protocol'; append(protocolBox,protocolLabel,protocol);
    const effortBox = el('div','form-field'); const effortLabel = el('label','field-label','思考强度'); effortLabel.htmlFor='model-effort';
    const effort=makeSelect([['default','按模型默认'],['low','低'],['medium','中'],['high','高'],['max','最高']], state.settings.reasoning_effort || 'default',()=>{},'思考强度'); effort.id='model-effort'; append(effortBox,effortLabel,effort);
    const streaming=el('label','check-line'); const stream=el('input'); stream.id='model-stream'; stream.type='checkbox'; stream.checked=state.settings.stream !== false; append(streaming,stream,el('span','','流式回复'));
    const privacy=el('label','check-line'); const paths=el('input'); paths.type='checkbox'; paths.checked=state.settings.send_paths === true; append(privacy,paths,el('span','','允许发送文件位置'));
    const clear=el('label','check-line'); const clearKey=el('input'); clearKey.type='checkbox'; append(clear,clearKey,el('span','','清除已保存的密钥'));
    append(advanced,base.box,model.box,protocolBox,effortBox,streaming,el('p','field-help field-description','流式回复会边接收边显示文字，不改变所选协议。不兼容的自定义服务可关闭。'),privacy,clear);
    const status=el('p','connection-test-status'); status.setAttribute('role','status');status.dataset.tone='neutral';
    const save=el('button','button primary','保存并测试'); save.type='submit';
    const onlySave=button('仅保存',()=>saveConnection(false),'plain-button');
    function populateModels(preset, current='') {
      modelChoice.replaceChildren();
      const values=[...new Set([...(preset?.models || []), ...(current ? [current] : [])])];
      values.forEach((id)=>{const o=el('option','',id);o.value=id;modelChoice.append(o);});
      const custom=el('option','','手动输入模型 ID');custom.value='';modelChoice.append(custom);
      modelChoice.value=current || preset?.default_model || values[0] || ''; model.input.value=modelChoice.value || current;
    }
    function chooseProvider(value) {
      const preset=presets.find((entry)=>entry.id===value);
      if(preset) {base.input.value=preset.base_url;protocol.value=preset.protocol;effort.value='default';populateModels(preset,preset.default_model);}
      else populateModels(null,model.input.value);
      key.input.value='';
      const switched=value!==(state.settings.provider || 'custom');
      key.input.placeholder=switched ? '请填写这个服务商的 API 密钥' : state.settings.has_key ? '已保存，留空不修改' : '输入 API 密钥';
      note.textContent=(preset?.note || '填写兼容服务的地址、协议和模型 ID。') + (switched && state.settings.has_key ? ' 原连接的密钥不会自动用于新的服务商。' : '');
      advanced.open=!preset || !modelChoice.value;
    }
    modelChoice.addEventListener('change',()=>{model.input.value=modelChoice.value;if(!modelChoice.value){advanced.open=true;model.input.focus();}});
    model.input.addEventListener('input',()=>{modelChoice.value=model.input.value;});
    const initial=presets.find((p)=>p.id===provider.value); populateModels(initial,state.settings.model);
    note.textContent=initial?.note || '选择服务商可自动填写连接参数；自定义服务可展开高级设置。';
    if(!initial && state.settings.base_url) advanced.open=true;
    async function saveConnection(test) {
      if(save.disabled) return;
      if(!provider.value) {status.textContent='请先选择模型服务商。';status.classList.add('field-error');status.dataset.state='error';status.dataset.tone='danger';provider.focus();return;}
      save.disabled=true;onlySave.disabled=true;form.setAttribute('aria-busy','true');status.className='connection-test-status';status.dataset.state='working';status.dataset.tone='info';
      status.textContent=test?'正在保存并验证工具调用，请稍等…':'正在保存…';
      let saved=false;const submittedKey=key.input.value,submittedClear=clearKey.checked;
      function clearSubmittedKey(){if(key.input.value===submittedKey)key.input.value='';if(clearKey.checked===submittedClear)clearKey.checked=false;}
      try {
        const payload=settingsPayload({provider:provider.value,protocol:protocol.value,reasoning_effort:effort.value,base_url:base.input.value.trim(),model:model.input.value.trim(),stream:stream.checked,send_paths:paths.checked});
        if(key.input.value) payload.api_key=key.input.value;
        else if(clearKey.checked || ((provider.value!==(state.settings.provider || 'custom') || base.input.value.trim()!==state.settings.base_url) && state.settings.has_key)) payload.clear_key=true;
        const data=await api('/api/settings',payload);
        if(data.settings)data.settings={...data.settings,custom_instructions:state.settings.custom_instructions ?? data.settings.custom_instructions};
        accept(data); saved=true;
        key.input.placeholder=state.settings.has_key?'已保存，留空保留当前连接的密钥':'填写服务商提供的 API 密钥';
        if(test) {
          const result=await api('/api/settings/test',{},120000);
          status.textContent=result.passed?(result.message || '连接成功，工具往返测试通过。'):`设置已保存，但测试未通过。${result.message || '请检查配置后重试。'} 当前填写内容已保留。`;
          status.classList.toggle('field-error',!result.passed);
          status.dataset.state=result.passed?'verified':'warning';status.dataset.tone=result.passed?'success':'warning';
          if(result.passed)clearSubmittedKey();
        } else {status.textContent='设置已保存。';status.dataset.state='saved';status.dataset.tone='neutral';clearSubmittedKey();}
      }catch(error){status.textContent=saved?`设置已保存，但测试未完成。${error.message} 当前填写内容已保留。`:`设置未保存。${error.message} 当前填写内容已保留。`;status.classList.add('field-error');status.dataset.state=saved?'warning':'error';status.dataset.tone=saved?'warning':'danger';}
      finally{save.disabled=false;onlySave.disabled=false;form.setAttribute('aria-busy','false');}
    }
    form.addEventListener('submit',(event)=>{event.preventDefault();saveConnection(true);});
    append(form,grid,note,advanced,el('p','field-help field-description','连接测试只发送固定测试数据。正式分析按需提供所选对象的属性和可取得的部分应用启动记录；完整路径默认隐藏，不发送文件正文。官方文档支持不代表当前账号已通过实测。'),append(el('div','form-actions'),save,onlySave),status); append(panel,form); append(root,panel);
    renderOrganizingPreferences(root);
    renderAppearance(root);
    const preserve=el('section','panel simple-setting');append(preserve,el('h2','','保留的位置'),el('p','setting-intro','这些位置不会加入推荐处理。'));
    const keep=el('details','setting-details');append(keep,el('summary','','管理保留清单'));
    const lab=el('label','field-label','每行一个文件夹或匹配规则');lab.htmlFor='protect-patterns';
    const patterns=el('textarea');patterns.id='protect-patterns';patterns.value=(state.settings.protect_patterns || []).join('\n');
    append(keep,lab,patterns,button('保存保留清单',()=>task(async()=>{accept(await api('/api/settings',settingsPayload({protect_patterns:patterns.value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean)})));accept(await api('/api/bootstrap?compact=1'));render();toast('保留清单已保存。');})));append(preserve,keep);append(root,preserve);
    append(root,button('高级选项',()=>$('advanced-toggle').click(),'plain-button advanced-entry'));
    renderDeveloperEntry(root);
    renderAbout(root);
  }
  function renderOrganizingPreferences(root) {
    const panel=el('section','panel simple-setting organizing-preferences');
    append(panel,el('h2','','整理偏好'),el('p','field-help setting-intro','所有模型共用，用于后续分析。已有方案可继续调查后更新。'));
    const form=el('form','form-panel'),label=el('label','field-label','你的整理习惯与要求');
    const instructions=el('textarea');instructions.id='organizing-instructions';instructions.value=state.settings.custom_instructions || '';instructions.maxLength=4000;
    instructions.placeholder='例如：保留项目源码和个人照片；旧安装包优先建议清理。';label.htmlFor=instructions.id;
    const status=el('p','connection-test-status');status.setAttribute('role','status');status.dataset.tone='neutral';
    const save=el('button','button','保存偏好');save.type='submit';
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(save.disabled)return;
      const submitted=instructions.value;save.disabled=true;form.setAttribute('aria-busy','true');status.className='connection-test-status';status.dataset.state='working';status.dataset.tone='info';status.textContent='正在保存偏好…';
      try{
        const data=await api('/api/preferences',{custom_instructions:submitted});
        state.settings.custom_instructions=data.preferences?.custom_instructions ?? data.settings?.custom_instructions ?? submitted;
        status.textContent=instructions.value===submitted?'整理偏好已保存，切换模型也会沿用。':'刚才的偏好已保存；还有新修改尚未保存。';
        status.dataset.state=instructions.value===submitted?'saved':'warning';status.dataset.tone=instructions.value===submitted?'neutral':'warning';
      }catch(error){status.textContent=`偏好未保存。${error.message} 当前填写内容已保留。`;status.classList.add('field-error');status.dataset.state='error';status.dataset.tone='danger';}
      finally{save.disabled=false;form.setAttribute('aria-busy','false');}
    });
    append(form,label,instructions,append(el('div','form-actions'),save),status);append(panel,form);
    renderCleanupSelectionPreference(panel);append(root,panel);
  }
  function renderCleanupSelectionPreference(panel) {
    const section=el('div','cleanup-selection-preference'),line=el('label','check-line'),choice=el('input');
    choice.type='checkbox';choice.id='cleanup-auto-select-four';choice.checked=window.PanxuAppearance.get().cleanup_auto_select_four!==false;
    const description=el('p','field-help','5 分项仍会预选；只影响新分析，不改变当前勾选。');description.id='cleanup-auto-select-four-help';
    choice.setAttribute('aria-describedby',description.id);
    const status=el('p','field-help');status.hidden=true;status.setAttribute('role','status');
    choice.addEventListener('change',async()=>{
      if(choice.disabled)return;
      const value=choice.checked,previous=window.PanxuAppearance.get().cleanup_auto_select_four;
      choice.disabled=true;status.hidden=false;status.textContent='正在保存…';status.dataset.tone='neutral';
      try{
        await saveUiPreferences({cleanup_auto_select_four:value});
        status.textContent='已保存，下次分析时生效。';
      }catch(error){
        window.PanxuAppearance.update({cleanup_auto_select_four:previous});choice.checked=previous!==false;
        status.textContent=`未能保存，已恢复原设置。${error.message}`;status.dataset.tone='warning';
      }finally{choice.disabled=false;}
    });
    append(line,choice,el('span','','新分析自动勾选 4 分项'));append(section,line,description,status);append(panel,section);
  }
  let preferenceSave=Promise.resolve(),preferenceRevision=0;
  function saveUiPreferences(patch) {
    const revision=++preferenceRevision;
    window.PanxuAppearance.update(patch);
    preferenceSave=preferenceSave.catch(()=>{}).then(()=>api('/api/ui-preferences',patch)).then(data=>{
      if(revision===preferenceRevision)window.PanxuAppearance.update(data.preferences);
      return data;
    });
    return preferenceSave;
  }
  function renderAppearance(root) {
    const panel=el('section','panel simple-setting appearance-setting');
    const copy=el('div');append(copy,el('h2','','外观'),el('p','setting-intro','选择看起来更舒适的界面。'));
    const choice=makeSelect([['system','跟随系统（默认）'],['light','浅色'],['dark','深色']],window.PanxuAppearance.get().theme,async value=>{
      feedback.hidden=true;
      try{await saveUiPreferences({theme:value});}
      catch(_){feedback.textContent='本次窗口已切换；暂时无法保存偏好，重新打开后可再试。';feedback.hidden=false;}
    },'界面主题');choice.id='appearance-theme';
    const feedback=el('p','field-help field-description appearance-feedback');feedback.hidden=true;feedback.setAttribute('role','status');feedback.dataset.tone='warning';
    append(panel,copy,choice,feedback);append(root,panel);
  }
  function renderDeveloperEntry(root) {
    const panel=el('section','panel simple-setting developer-entry-setting'),copy=el('div');
    append(copy,el('h2','','开发模式'),el('p','setting-intro','在独立面板查看通信与工具记录。打开面板不会开始采集。'));
    const open=button('打开开发面板',()=>toggleDeveloperDock(true));open.dataset.openDeveloper='1';
    open.setAttribute('aria-controls','developer-dock');open.setAttribute('aria-expanded',String(developerDockOpen));
    append(panel,copy,append(el('div','developer-entry-actions'),open,el('kbd','','F12')));append(root,panel);
  }
  let aboutInfo=null,aboutInfoRequest=null,updateCheckRequest=null,updateCheckResult=null;
  function readAbout() {
    if(aboutInfo)return Promise.resolve(aboutInfo);
    if(!aboutInfoRequest)aboutInfoRequest=api('/api/about').then(data=>{aboutInfo=data;return data;}).finally(()=>{aboutInfoRequest=null;});
    return aboutInfoRequest;
  }
  function renderAbout(root) {
    const panel=el('section','panel simple-setting about-setting');
    const heading=el('div','about-heading'),copy=el('div');
    const title=el('h2','','关于盘序'),identity=el('p','about-identity');
    append(copy,title,identity);
    const status=el('p','about-update-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const release=el('div','about-release');
    const check=button('检查更新',checkUpdates);check.id='about-check-updates';
    check.setAttribute('aria-describedby','about-update-status');status.id='about-update-status';
    append(heading,copy,check);
    const details=el('details','setting-details about-details');append(details,el('summary','','项目与许可'));
    const resources=el('div','about-resources');append(details,resources);
    append(panel,heading,status,release,details);append(root,panel);
    function safeLink(label,value,releaseOnly=false) {
      try{
        if(typeof value!=='string')return null;
        const url=new URL(value);
        if(url.protocol!=='https:'||url.username||url.password||url.hostname!=='github.com'||!/^\/heroliss\/panxu(?:\/|$)/.test(url.pathname)||url.search)return null;
        if(releaseOnly&&!/^\/heroliss\/panxu\/releases(?:\/tag\/[^/]+)?\/?$/.test(url.pathname))return null;
        const link=el('a','about-link',label);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';return link;
      }catch(_){return null;}
    }
    function identityText(){identity.textContent=`版本 ${state.version || aboutInfo?.version || '暂未读取'} · 作者 ${aboutInfo?.author || 'Liss'}`;}
    function renderResources(info) {
      resources.replaceChildren();
      const links=el('div','about-links');
      const repository=safeLink('GitHub 代码仓库',info.repository_url);
      const license=safeLink(info.license_name || '查看许可证',info.license_url);
      if(repository)append(links,repository);append(links,license || el('span','field-help',info.license_name || '许可资料暂不可用'));
      append(resources,links,el('p','field-help',info.description || '面向磁盘整理的 LLM Harness，为模型提供信息、工具和用户协作界面。'),
        el('p','field-help','仓库访问权限与公开更新源分别管理。第三方材料保留各自许可。'));
    }
    function renderUpdate() {
      check.disabled=Boolean(updateCheckRequest);check.setAttribute('aria-busy',String(Boolean(updateCheckRequest)));release.replaceChildren();
      let tone='neutral',message='暂未提供公开更新源。';
      if(updateCheckRequest){tone='info';message='正在检查更新…';}
      else if(updateCheckResult){
        const result=updateCheckResult;
        const messages={not_configured:'尚未提供公开更新源，目前不能比较版本。',no_public_source:'未找到可用的公开版本，目前不能比较版本。',
          update_available:`发现新版本${result.latest_version?' '+result.latest_version:''}，可手动查看发布页。`,up_to_date:'当前已是公开发布的最新版本。',
          current_ahead:'本机版本比公开发布版本更新，无需降级。',failed:'检查更新未完成，请稍后重试。'};
        message=result.message || messages[result.status] || '暂时无法判断是否有更新。';
        tone=result.status==='failed'?'danger':result.status==='up_to_date'?'success':result.status==='update_available'?'info':'neutral';
        if(['update_available','current_ahead'].includes(result.status)){
          const link=safeLink('查看发布页',result.release_url,true);if(link)append(release,link);
        }
      }else if(aboutInfo?.updates?.source_state==='configured')message='点击“检查更新”比较公开版本，不会自动下载或替换。';
      status.dataset.tone=tone;status.textContent=message;
      check.textContent=updateCheckResult?.status==='failed'?'重新检查':'检查更新';
    }
    async function checkUpdates() {
      if(updateCheckRequest)return;
      updateCheckRequest=api('/api/updates/check',{},20000).then(result=>{updateCheckResult=result;})
        .catch(error=>{updateCheckResult={status:'failed',message:`检查更新未完成。${error.message || '请稍后重试。'}`};})
        .finally(()=>{updateCheckRequest=null;});
      renderUpdate();await updateCheckRequest;if(panel.isConnected)renderUpdate();
    }
    identityText();renderUpdate();
    if(updateCheckRequest)updateCheckRequest.then(()=>{if(panel.isConnected)renderUpdate();});
    readAbout().then(info=>{if(panel.isConnected){identityText();renderResources(info);renderUpdate();}})
      .catch(()=>{if(panel.isConnected)resources.replaceChildren(el('p','field-help','暂时无法读取项目资料，重新打开设置后可重试。'));});
  }
  function toggleDeveloperDock(force) {
    const dock=$('developer-dock');if(!dock)return;
    const open=force===undefined?!developerDockOpen:force;
    if(open&&!developerDockOpen)developerReturnFocus=document.activeElement;
    developerDockOpen=open;dock.hidden=!open;document.body.classList.toggle('developer-open',open);
    if(open&&!developerController)developerController=renderDeveloper($('developer-dock-content'));
    developerController?.setVisible(open);
    document.querySelectorAll('[data-open-developer]').forEach(button=>button.setAttribute('aria-expanded',String(open)));
    if(open)$('developer-dock-close').focus();
    else if(developerReturnFocus?.isConnected)developerReturnFocus.focus();
    else $('workspace').focus();
  }
  function renderDeveloper(root) {
    const panel=el('section','developer-panel');
    const body=el('div','developer-content');append(panel,body);append(root,panel);
    const intro=el('p','field-help','记录保存在本机。采集与显示分别控制；关闭显示不会停止采集。密钥、内部思考和协议私有续接不会显示。');
    const switches=el('div','developer-switches');
    function checkbox(label,id,checked){const line=el('label','check-line'),input=el('input');input.type='checkbox';input.id=id;input.checked=checked;append(line,input,el('span','',label));return {line,input};}
    const prefs=window.PanxuAppearance.get();
    const capture=checkbox('记录开发通信','developer-record',false);capture.input.disabled=true;
    const live=checkbox('实时显示通信','developer-live',prefs.developer_live);
    append(switches,capture.line,live.line);
    const filters=el('details','developer-display-options');append(filters,el('summary','','显示内容'));
    const conversation=checkbox('模型对话','developer-conversation',prefs.developer_conversation);
    const skills=checkbox('技能说明与内容','developer-skills',prefs.developer_skills);
    const tools=checkbox('工具定义、参数与结果','developer-tools',prefs.developer_tools);
    append(filters,conversation.line,skills.line,tools.line);
    const status=el('p','developer-state');status.setAttribute('role','status');
    const error=el('p','field-error');error.setAttribute('role','status');error.hidden=true;
    const controls=el('div','developer-controls');
    const refresh=button('刷新记录',()=>{stopStream();syncStream();load(true);});
    const clear=button('清空记录',async()=>{
      if(fetching||recordBusy)return;clear.disabled=true;readSequence++;stopPolling();
      try{const data=await api('/api/developer',{clear:true});offset=0;lastData=null;pendingData=null;renderedData=null;entries.clear();list.replaceChildren();renderData(data,true);}
      catch(failure){showError(failure.message);}finally{clear.disabled=false;schedule();}
    },'plain-button');append(controls,refresh,clear);
    const updated=button('有新记录，点击更新',()=>{if(pendingData){renderData(pendingData,true);pendingData=null;}},'button developer-update');updated.hidden=true;
    const list=el('div','developer-records');
    const paging=el('div','developer-controls developer-paging');
    const previous=button('上一页',()=>{if(offset){offset=Math.max(0,offset-10);load(true);}});
    const next=button('下一页',()=>{offset+=10;load(true);});const pageLabel=el('span','field-help');append(paging,previous,pageLabel,next);
    const viewer=el('div','developer-viewer');append(viewer,controls,updated,list,paging);
    const streaming=el('section','developer-stream');streaming.hidden=true;
    const streamHeading=el('h3','','正在接收的公开回复'),streamHost=el('div','developer-stream-body'),streamActivityHost=el('div','developer-stream-activity'),streamConnection=el('p','developer-note');streamConnection.setAttribute('role','status');
    append(streaming,streamHeading,streamHost,streamActivityHost,streamConnection);
    const displayNote=el('p','field-help','显示已关闭。打开“实时显示通信”后可查看记录；只有此面板可见时才会自动刷新。');
    append(body,intro,switches,filters,status,error,displayNote,streaming,viewer);
    let offset=0,fetching=false,recordBusy=false,loaded=false,active=true,dockVisible=false,refreshAfterRead=false,timer=null,readSequence=0,lastData=null,pendingData=null,renderedData=null;
    const entries=new Map();
    const names={request:'发给模型',response:'模型回复',tool_call:'工具调用',tool_result:'工具结果',skill:'技能参考',skill_read:'读取技能',context_event:'任务反馈'};
    function visible(){return active&&dockVisible&&panel.isConnected&&!document.hidden;}
    function stopPolling(){clearTimeout(timer);timer=null;}
    function schedule(){stopPolling();if(visible()&&live.input.checked&&offset===0&&lastData?.enabled)timer=setTimeout(()=>load(false),2500);}
    let streamTimer=null,streamUnsubscribe=null,streamTaskId='',streamView=null,streamActivity=null,streamGeneration=0,streamEpoch='',streamTurn='';
    function stopStream(clear=false){
      clearTimeout(streamTimer);streamTimer=null;streamGeneration++;streamUnsubscribe?.();streamUnsubscribe=null;
      if(clear){streamView?.reset();streamActivity?.dispose();streamActivity=null;streamActivityHost.replaceChildren();streaming.hidden=true;streamEpoch='';streamTurn='';}
    }
    function syncStream(){
      clearTimeout(streamTimer);streamTimer=null;
      if(!visible()||!live.input.checked||!conversation.input.checked||!window.PanxuStreamChannel||!window.PanxuStreamView){stopStream();streaming.hidden=true;return;}
      let id='';try{id=window.PanxuTasks?.streamTaskId?.() || '';}catch(_){}
      if(id!==streamTaskId){stopStream(true);streamTaskId=id;}
      if(id&&!streamUnsubscribe){
        if(!streamView)streamView=window.PanxuStreamView.create(streamHost);
        if(!streamActivity&&window.PanxuStreamView.createActivity)streamActivity=window.PanxuStreamView.createActivity(streamActivityHost);
        const run=++streamGeneration;
        streamUnsubscribe=window.PanxuStreamChannel.subscribe(id,snapshot=>{
          if(run!==streamGeneration||!visible()||!live.input.checked||!conversation.input.checked)return;
          const value=snapshot.stream;
          streamActivity?.update(snapshot.activity);
          const hasActivity=Boolean(snapshot.activity?.events?.length);
          if(!value){streamView.reset();streaming.hidden=!hasActivity&&!['reconnecting','stopped','unavailable'].includes(snapshot.connection);}
          else if(value.status==='cancelled'){streamView.reset();streaming.hidden=!hasActivity;streamHeading.textContent='最近任务动态';}
          else{
            if(value.epoch!==streamEpoch||value.turn_id!==streamTurn){streamView.reset();streamEpoch=value.epoch;streamTurn=value.turn_id;}
            streaming.hidden=false;streamHeading.textContent=!value.text&&hasActivity?'最近任务动态':value.status==='streaming'?'正在接收的公开回复':value.status==='completed'?'本轮公开回复':'未完成的公开回复';
            streamView.update({text:value.text,status:snapshot.incomplete?'incomplete':value.status,phase:value.tool_pending?'tool_pending':''});
          }
          const connection={reconnecting:'连接暂时中断，正在重连；已收到的内容会保留。',stopped:'实时连接未恢复，已收到的内容会保留。可点“刷新记录”重试。',unavailable:'暂时无法实时接收回复，可点“刷新记录”重试。'}[snapshot.connection] || '';
          streamConnection.textContent=[connection,value?.truncated?'公开回复已达到显示上限，内容已截断。':''].filter(Boolean).join(' ');streamConnection.hidden=!streamConnection.textContent;
        });
      }
      streamTimer=setTimeout(syncStream,200);
    }
    function showError(message){error.textContent=message;error.hidden=false;}
    const viewModel=window.PanxuDeveloperView;
    function included(entry){return ({skills:skills.input.checked,tools:tools.input.checked,conversation:conversation.input.checked})[viewModel.category(entry)];}
    const labels={goal:'目标',scopes:'检查范围',name:'名称',id:'标识',summary:'摘要',briefing:'本轮说明',highlights:'本轮重点',cautions:'需要留意',coverage:'检查范围',cleanup_rating:'清理倾向',impact_rating:'影响程度',purpose:'用途',reason:'原因',impact:'影响',conclusion:'建议',tool:'工具',at:'时间',total:'总数',items:'条目',facts:'历史事实',observations:'历史观察',user_feedback:'用户补充',user_choices:'用户选择',preferences:'整理偏好',usage:'累计工作量',handoff:'交接',version:'版本',content:'指导正文',content_fingerprint:'内容指纹',read_at:'读取时间',sources:'来源',provider:'服务商',protocol:'协议',endpoint:'地址',model:'模型',input_tokens:'输入 token',output_tokens:'输出 token',prompt_tokens:'输入 token',completion_tokens:'输出 token'};
    function readableData(value,parent,depth=0){
      value=viewModel.clean(value);
      if(value===null||value===undefined){append(parent,el('p','muted','未提供'));return;}
      if(typeof value!=='object'){append(parent,el('p','developer-text',typeof value==='boolean'?(value?'是':'否'):String(value)));return;}
      const rows=Array.isArray(value)?value.map((item,index)=>[String(index+1),item]):Object.entries(value);
      const fields=el('dl','developer-fields');
      for(const [key,child] of rows.slice(0,12)){
        if(child&&typeof child==='object'){
          const detail=el('details','developer-subsection');append(detail,el('summary','',`${labels[key] || key}${Array.isArray(child)?`（${child.length} 项）`:''}`));
          detail.addEventListener('toggle',()=>{if(detail.open&&!detail.dataset.loaded){detail.dataset.loaded='1';if(depth<3)readableData(child,detail,depth+1);else append(detail,el('p','muted','更多结构信息可在原始 JSON 中查看。'));}});
          append(parent,detail);
        }else append(fields,el('dt','developer-field-label',labels[key] || key),el('dd','developer-field-value',child==null?'未提供':typeof child==='boolean'?(child?'是':'否'):String(child)));
      }
      if(fields.children.length)append(parent,fields);
      if(rows.length>12)append(parent,el('p','muted',`此处展示前 12 项，其余 ${rows.length-12} 项见原始 JSON。`));
    }
    function renderReadable(entry,article){
      const model=viewModel.describe(entry),copy=el('div','developer-readable');
      const roles={user:'用户',assistant:'助手',system:'系统',tool:'程序工具',developer:'开发指引'};
      for(const message of model.messages){
        const row=el('div','developer-chat-message');row.dataset.role=message.role;
        row.dataset.purpose=entry.kind==='turn_end'?'transport':message.publicCall || (message.role==='assistant'?'reply':message.role==='user'?'input':'instruction');
        const role=el('strong','developer-role semantic-tag',roles[message.role] || message.role);role.dataset.tone=message.role==='assistant'?'info':'neutral';append(row,role);
        if(message.title){const title=el('span','developer-message-title',message.title);title.dataset.tone=message.publicCall==='ask_user'?'warning':message.publicCall?'info':'neutral';append(row,title);}
        if(message.text){const body=el('p','developer-text',message.text);body.dataset.purpose=row.dataset.purpose;append(row,body);}
        if(message.briefing)for(const [key,points] of Object.entries(message.briefing)){
          if(!points.length)continue;const module=el('div','developer-briefing-module'),list=el('ul','developer-options');
          append(module,el('strong','developer-field-label',labels[key]));points.forEach(point=>append(list,el('li','',point)));append(module,list);append(row,module);
        }
        if(message.options?.length){const options=el('ul','developer-options');message.options.forEach(option=>{const li=el('li');append(li,el('strong','',option.label));if(option.description)append(li,el('span','',option.description));append(options,li);});append(row,options);}
        if(message.items?.length)message.items.forEach(item=>{
          const advice=el('div','developer-public-advice');advice.dataset.purpose='recommendation';
          if(item.name||item.id)append(advice,el('strong','developer-advice-name',item.name || item.id));
          if(Number.isInteger(item.cleanup_rating))append(advice,window.PanxuAssessment.render(item,{direct:true,cls:'developer-advice-rating'}));
          if(Object.hasOwn(item,'impact_rating'))append(advice,el('p','developer-note developer-impact-level',item.impact_rating===null?'影响程度：未提供':`影响程度：${item.impact_rating}/5（${({1:'很小',2:'轻微',3:'明显',4:'较大',5:'重大'})[item.impact_rating]}）`));
          for(const key of ['purpose','conclusion','reason','impact'])if(item[key]){
            const field=el('p','developer-text developer-advice-field');field.dataset.field=key;
            const value=el('span','developer-field-value',({keep:'建议保留',review:'需要确认',recommend:'建议处理'})[item[key]] || item[key]);
            if(key==='conclusion')value.dataset.tone=item[key]==='review'?'warning':item[key]==='recommend'?'info':'neutral';
            append(field,el('strong','developer-field-label',`${labels[key]}：`),value);append(advice,field);
          }
          append(row,advice);
        });
        if(message.omittedItems)append(row,el('p','developer-note',`另有 ${message.omittedItems} 项建议，可在原始 JSON 中查看。`));
        if(message.steps?.length){const list=el('ul','developer-options');message.steps.forEach(text=>append(list,el('li','',text)));append(row,list);}
        append(copy,row);
      }
      for(const note of model.notes)append(copy,el('p','developer-note',note));
      if(!model.messages.length&&!model.notes.length){
        const first=model.sections[0],data=first?.data;
        const summary=typeof data?.summary==='string'?data.summary:typeof data?.message==='string'?data.message:'';
        const parts=[first?.title || '已记录结构化信息',typeof data?.name==='string'?data.name:'',typeof data?.version==='string'?`版本 ${data.version}`:'',summary?summary.slice(0,220):Array.isArray(data?.items)?`${data.items.length} 项`:'' ].filter(Boolean);
        append(copy,el('p','developer-note',parts.join(' · ')));
      }
      const auxiliary=el('details','developer-section developer-auxiliary');
      auxiliary.dataset.label=entry.kind==='request'?'随本次发送的说明与资料':'更多调试信息';
      append(auxiliary,el('summary','',auxiliary.dataset.label));
      model.sections.forEach(section=>{
        const detail=el('details','developer-section');detail.dataset.category=section.kind;detail.dataset.purpose=section.kind;
        append(detail,el('summary','developer-disclosure-title',section.title));
        detail.addEventListener('toggle',()=>{if(detail.open&&!detail.dataset.loaded){detail.dataset.loaded='1';const body=el('div','developer-section-body');readableData(section.data,body);append(detail,body);}});
        append(auxiliary,detail);
      });
      const raw=el('details','developer-raw');append(raw,el('summary','','原始 JSON（已脱敏）'));
      raw.addEventListener('toggle',()=>{if(raw.open&&!raw.querySelector('pre')){
        if(entry.payload&&typeof entry.payload==='object'&&Object.hasOwn(entry.payload,'payload'))append(raw,el('p','developer-note','这是完整诊断记录，含程序内部元数据。外层 provider 是服务商标签，不是 API 消息角色；实际请求或回复位于 payload 内。'));
        append(raw,el('pre','developer-json',viewModel.raw(entry)));
      }});
      append(auxiliary,raw);append(copy,auxiliary);append(article,copy);
    }
    function applyDisplay(entry){
      const categories=new Set(['raw']);
      entry.querySelectorAll('[data-category]').forEach(section=>{section.hidden=section.dataset.category==='tools'?!tools.input.checked:section.dataset.category==='skills'?!skills.input.checked:false;if(!section.hidden)categories.add(section.dataset.category);});
      const auxiliary=entry.querySelector('.developer-auxiliary');if(auxiliary)auxiliary.querySelector('summary').textContent=`${auxiliary.dataset.label}（${categories.size} 类）`;
    }
    function reading(){
      const scroller=panel.closest?.('.developer-dock-scroll');
      const selection=document.getSelection?.();
      return [...entries.values()].some(item=>item.querySelector('details[open]')) || (scroller?.scrollTop || 0)>60 || Boolean(selection?.toString()&&panel.contains?.(selection.anchorNode));
    }
    function renderEntries(data){
      const shown=(data.entries || []).filter(included).slice().reverse(),fresh=new Map();
      for(const entry of shown){
        let article=entries.get(entry.seq);
        if(!article){
          article=el('article','developer-entry');article.dataset.seq=entry.seq;article.dataset.entryKind=entry.kind;
          const time=entry.at?new Date(entry.at).toLocaleTimeString('zh-CN',{hour12:false}):'';
          const header=el('div','developer-message-meta'),kind=el('strong','developer-entry-kind',names[entry.kind] || entry.kind);kind.dataset.tone=entry.kind==='error'?'danger':entry.kind==='response'?'info':'neutral';append(header,kind,el('span','developer-entry-time',`#${entry.seq}${time?' · '+time:''}`));append(article,header);
          renderReadable(entry,article);
        }
        applyDisplay(article);fresh.set(entry.seq,article);
      }
      entries.clear();fresh.forEach((node,key)=>entries.set(key,node));
      list.replaceChildren(...fresh.values());
      renderedData=data;
      if(!shown.length)append(list,el('p','muted',(data.entries || []).length?'这一页没有勾选类型的记录。可调整显示内容或翻页。':'还没有开发记录。开启记录后，再发起一次调查或连接测试。'));
    }
    function renderData(data,force=false){
      if(!active||!panel.isConnected)return;
      loaded=true;capture.input.checked=data.enabled;capture.input.disabled=recordBusy;
      intro.textContent=(data.note || '仅记录开启后的请求、回复和工具往返；密钥与协议内部推理不记录。')+' 记录与本地显示分别控制。';
      const message=`${data.enabled?'正在记录':'采集已关闭'} · 共 ${data.total || 0} 条`;
      if(status.textContent!==message)status.textContent=message;
      const changed=JSON.stringify((lastData?.entries || []).map(item=>item.seq))!==JSON.stringify((data.entries || []).map(item=>item.seq));
      const keepOpen=!force&&changed&&reading();
      if(keepOpen){pendingData=data;updated.hidden=false;}
      else if(live.input.checked&&(force||!lastData||changed)){renderEntries(data);pendingData=null;updated.hidden=true;}
      lastData=data;
      previous.disabled=fetching||!offset;next.disabled=fetching||!data.has_more;
      pageLabel.textContent=`第 ${Math.floor(offset/10)+1} / ${Math.max(1,Math.ceil((data.total || 0)/10))} 页`;
      paging.hidden=!(data.total>10||offset);viewer.hidden=!live.input.checked;displayNote.hidden=live.input.checked;
    }
    async function load(force=false){
      if(fetching||!visible())return;
      fetching=true;const run=++readSequence;error.hidden=true;list.setAttribute('aria-busy','true');refresh.disabled=true;
      if(!loaded)status.textContent='正在读取记录状态…';
      try{
        const data=await api(`/api/developer?offset=${offset}&limit=10`);
        if(run!==readSequence||!visible())return;
        renderData(data,force);
      }catch(failure){if(visible()&&run===readSequence)showError(failure.message);}
      finally{fetching=false;list.setAttribute('aria-busy','false');refresh.disabled=false;if(lastData){previous.disabled=!offset;next.disabled=!lastData.has_more;}if(refreshAfterRead&&visible()){refreshAfterRead=false;load(false);}else schedule();}
    }
    capture.input.addEventListener('change',async()=>{
      const enabled=capture.input.checked;recordBusy=true;capture.input.disabled=true;readSequence++;stopPolling();error.hidden=true;
      try{const data=await api('/api/developer',{enabled});offset=0;renderData(data,true);}
      catch(failure){capture.input.checked=!enabled;showError(failure.message);}
      finally{recordBusy=false;capture.input.disabled=false;schedule();}
    });
    live.input.addEventListener('change',()=>{
      viewer.hidden=!live.input.checked;displayNote.hidden=live.input.checked;stopPolling();
      syncStream();
      saveUiPreferences({developer_live:live.input.checked}).catch(()=>showError('显示已切换；暂时无法保存此偏好。'));
      if(live.input.checked)load(true);
    });
    for(const [control,key] of [[conversation,'developer_conversation'],[skills,'developer_skills'],[tools,'developer_tools']])control.input.addEventListener('change',()=>{
      saveUiPreferences({[key]:control.input.checked}).catch(()=>showError('显示已切换；暂时无法保存此偏好。'));
      if(control===conversation)syncStream();
      if(lastData&&live.input.checked)renderEntries(renderedData || lastData);
    });
    const visibility=()=>{stopPolling();syncStream();if(visible()&&live.input.checked)load(false);};
    document.addEventListener('visibilitychange',visibility);
    developerCleanup=()=>{active=false;dockVisible=false;refreshAfterRead=false;readSequence++;stopPolling();stopStream(true);streamView?.dispose();document.removeEventListener('visibilitychange',visibility);};
    viewer.hidden=!live.input.checked;displayNote.hidden=live.input.checked;
    return {setVisible(value){
      dockVisible=Boolean(value);stopPolling();syncStream();
      if(!dockVisible){readSequence++;refreshAfterRead=false;return;}
      if(fetching)refreshAfterRead=true;else load(false);
    },destroy:developerCleanup};
  }
  function analysisOutput(data) {
    const output = el('div', 'analysis-output'); append(output, el('div', 'assistant-answer', text(data.answer || '没有返回建议。')));
    const details = el('details'); const trace = typeof data.trace === 'object' ? text(data.trace) : data.trace; append(details, el('summary', '', '查看分析来源与发送范围'),window.PanxuProvenance.details({provenance:data.provenance},{presets:state.providers}), el('pre', 'assistant-trace', text(trace || '未提供额外来源信息。'))); if (data.evidence) append(details, el('pre', 'assistant-trace', text(data.evidence))); append(output, details); return output;
  }
  async function askAssistant(question, host, submit) {
    if (!question.trim()) return; submit.disabled = true; const label = submit.textContent; submit.textContent = '正在分析…'; const previous = host.querySelector('.analysis-output'); if (previous) previous.remove();
    try { const ids = state.focused && state.view !== 'model' ? [state.focused.id] : [...state.selected]; const data = await api('/api/assistant', { question: question.trim(), ...(ids.length ? { ids } : {}) }); state.assistant = data; append(host, analysisOutput(data)); }
    catch (error) { append(host, append(el('div', 'analysis-output'), el('p', 'field-error', `分析未完成：${error.message}`), el('p', 'field-help', '请到设置检查自定义服务后重试。'))); }
    finally { submit.disabled = false; submit.textContent = label; }
  }
  async function findDuplicates() {
    const ids = chosen().filter((item) => item.kind === 'file').map((item) => item.id);
    if (ids.length < 2) { notify('请先选择至少两个文件，再进行重复文件校验；不会自动删除任何副本。'); return; }
    await task(async () => { const result = await api('/api/duplicates', { ids }); state.plan = null; $('plan-title').textContent = '重复文件校验'; $('plan-execute').hidden = true; const host = $('plan-content'); host.replaceChildren(); append(host, el('p', 'muted', '重复结果只供检查，不会自动选择或删除文件。')); (result.warnings || []).forEach((warning) => append(host, el('div', 'plan-warning', text(warning)))); const groups = result.groups || []; if (!groups.length) append(host, empty('没有确认的重复组', '所选文件内容不同，或尚未完成校验。')); groups.forEach((group, index) => { const box = el('div', 'duplicate-group'); append(box, el('strong', '', `重复组 ${index + 1}`)); (group.items || group.files || []).forEach((entry) => { const item = typeof entry === 'string' ? state.items.find((i) => i.id === entry) || { name: entry } : entry; append(box, el('p', '', item.path || item.name || text(item))); }); append(host, box); }); $('plan-dialog').showModal(); });
  }

  $('navigation').addEventListener('click', (e) => { const buttonNode = e.target.closest('[data-view]'); if (buttonNode) { if(buttonNode.dataset.view==='cleanup'){cleanupReference=null;cleanupReturn=null;}setView(buttonNode.dataset.view); } });
  $('navigation-back')?.addEventListener('click',()=>contextBack?.());
  document.querySelector('.sidebar-bottom [data-view]').addEventListener('click', () => setView('model'));
  document.querySelector('.brand').addEventListener('click', (e) => { e.preventDefault(); setView('overview'); });
  $('inspector-toggle').addEventListener('click', () => toggleInspector());
  $('advanced-toggle').addEventListener('click', () => { const open = $('advanced-drawer').hidden; $('advanced-drawer').hidden = !open; $('advanced-toggle').setAttribute('aria-expanded', String(open)); if (open) $('advanced-close').focus(); });
  $('advanced-close').addEventListener('click', () => { $('advanced-drawer').hidden = true; $('advanced-toggle').setAttribute('aria-expanded', 'false'); $('workspace').focus(); });
  $('show-evidence').addEventListener('change', renderInspector);
  $('clear-selection').addEventListener('click', () => { analysisIds().clear(); if (state.view === 'overview') render(); else { renderResults(); renderInspector(); renderSelection(); } });
  $('preview-plan').addEventListener('click', () => analyzeList());
  $('plan-close').addEventListener('click', () => $('plan-dialog').close()); $('plan-cancel').addEventListener('click', () => $('plan-dialog').close()); $('plan-execute').addEventListener('click', executePlan);
  $('developer-dock-close')?.addEventListener('click',()=>toggleDeveloperDock(false));
  document.addEventListener('keydown',event=>{
    if(event.key!=='F12'&&event.code!=='F12')return;
    event.preventDefault();event.stopPropagation();if(!event.repeat)toggleDeveloperDock();
  },true);
  window.addEventListener('beforeunload',()=>developerCleanup?.());
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('advanced-drawer').hidden) $('advanced-close').click(); });

  async function boot() {
    try {
      const [bootstrap, catalog, preferences] = await Promise.all([api('/api/bootstrap?compact=1'), api('/api/providers').catch(() => ({presets:[],protocols:[]})),api('/api/ui-preferences').catch(()=>({preferences:window.PanxuAppearance.get()}))]);
      window.PanxuAppearance.update(preferences.preferences);
      accept(bootstrap, false); state.providers = catalog.presets || []; state.protocols = catalog.protocols || []; state.selected.clear(); state.checked = false;
      render();
      if (state.job?.status === 'running') pollJob(state.job.id);
    } catch (error) {
      $('connection-status').textContent = '连接未完成';
      $('workspace').replaceChildren(empty('暂时无法打开盘序', '请确认程序仍在运行，然后再试一次。', [button('重试', boot, 'button primary')]));
      renderInspector(); notify(error.message, true);
    }
  }

  boot();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initializePanxu,{once:true});
else initializePanxu();
