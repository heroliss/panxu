'use strict';

// Reusable definitions and current matches are separate. The host prepares every
// concrete operation; these views never execute commands or call a model directly.
window.PanxuUserTools = (() => {
  let mounted=null, serial=0;
  const state={mode:'list',tool:null,form:null,source:null,linkSource:null,run:null,offset:0,allSelected:true,selected:new Set(),excluded:new Set(),archiveConfirm:false};
  const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const add=(root,...children)=>{children.flat().filter(Boolean).forEach(child=>root.append(child));return root;};
  const button=(label,run,cls='button')=>{const b=node('button',cls,label);b.type='button';b.addEventListener('click',run);return b;};
  const active=t=>mounted===t&&t.root.isConnected;
  const clone=value=>JSON.parse(JSON.stringify(value));
  const unwrap=value=>value?.tool || value?.item || value;
  const lines=value=>String(value || '').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  const patterns=value=>String(value || '').split(/[\r\n,，;；]+/).map(x=>x.trim()).filter(Boolean);
  const optionLabels={full:'完整休眠',reduced:'仅保留快速启动',off:'关闭',system:'系统管理',custom:'自定义大小',review:'查看当前配置',quota:'调整占用额度',disable:'关闭系统保护',edge:'Microsoft Edge',chrome:'Google Chrome',firefox:'Firefox',last_hour:'最近一小时',last_day:'最近一天',last_week:'最近一周',all:'全部',history:'浏览记录',cookies:'Cookie与登录状态',download_history:'下载记录',site_data:'站点与离线数据',prune:'清理不再使用的缓存',clean:'清理缓存',wheels:'已构建安装包缓存',verify:'检查缓存完整性',documents:'文档',pictures:'图片',desktop:'桌面',downloads:'下载',current:'当前用户',onedrive:'OneDrive',dropbox:'Dropbox',google_drive:'Google Drive'};
  const definition=()=>state.tool?.definition || {};
  const isDraft=tool=>tool?.status==='draft'||Boolean(tool?.draft_id);
  const runRef=tool=>isDraft(tool)?{draft_id:tool.draft_id || tool.id}:{tool_id:tool.id,version:tool.version};
  function unmount(){serial++;mounted?.abort?.abort();mounted=null;}
  function make(root,helpers){unmount();const t={root,helpers,request:0,busy:false,error:'',catalog:null,detail:null,drives:helpers.disks || []};mounted=t;return t;}
  function render(root,helpers={}){const t=make(root,helpers);if(state.mode==='edit'&&state.form)paintEditor(t);else if(state.mode==='detail'&&state.tool)inspect(t);else loadList(t);return t;}
  async function open(root,ref={},helpers={}){
    const t=make(root,helpers);
    t.source=ref.source || helpers.source || null;
    if(ref.new||ref.definition||ref.preset){newEditor(t,ref);return t;}
    if(ref.tool){state.tool=ref.tool;if(ref.edit||isDraft(ref.tool))edit(t,ref.tool);else await inspect(t);return t;}
    const id=ref.id || ref.tool_id || ref.draft_id;
    if(!id){await loadList(t);return t;}
    await loadTool(t,id,{edit:ref.edit,version:ref.version});return t;
  }
  function heading(t,title,description){const h=node('header','page-heading');add(h,node('h1','',title),description?node('p','',description):null);t.root.append(h);}
  function back(t,label,run){if(t.helpers.setContextBack)t.helpers.setContextBack(label,run);else t.root.append(button(label,run,'plain-button user-tool-back'));}
  function error(t,root=t.root){if(!t.error)return;const p=node('p','field-error',t.error);p.setAttribute('role','alert');root.append(p);}
  function busy(t,label){t.root.replaceChildren();heading(t,label,'正在读取…');t.root.setAttribute('aria-busy','true');}
  function notice(text,cls='field-help'){const p=node('p',cls,text);p.setAttribute('role','status');return p;}
  function toolSummary(tool){const d=tool.definition || {};if(d.type==='preset')return '内置工具预设';const r=d.rules || {};const first=(r.roots || [])[0] || '尚未选择目录';return `${first}${r.roots?.length>1?` 等 ${r.roots.length} 个目录`:''} · ${(r.includes || []).join('、') || '所有文件'}${r.min_age_days?` · 超过 ${r.min_age_days} 天未修改`:''}`;}
  async function loadList(t){
    state.mode='list';state.linkSource=null;t.source=null;state.archiveConfirm=false;const request=++t.request;t.busy=true;t.error='';busy(t,'我的工具');t.helpers.setContextBack?.('',null);
    try{const data=await t.helpers.api('/api/user-tools');if(!active(t)||request!==t.request)return;t.list=[...(data.items || []),...(data.drafts || []).filter(d=>!(data.items || []).some(i=>i.id===d.id))];}
    catch(e){if(active(t)&&request===t.request)t.error=e.message;}
    finally{if(active(t)&&request===t.request){t.busy=false;paintList(t);}}
  }
  function paintList(t){
    if(!active(t))return;t.root.replaceChildren();t.root.setAttribute('aria-busy','false');if(t.helpers.onReturn)back(t,t.helpers.returnLabel || '返回清理工具',t.helpers.onReturn);heading(t,'我的工具','把常用清理规则保存下来，下次直接使用。');
    const actions=node('div','user-tool-list-actions');actions.append(button('新建工具',()=>newEditor(t),'button primary'),button('刷新',()=>loadList(t),'plain-button'));if(t.helpers.askAssistant)actions.append(button('让 AI 帮我创建',()=>newEditor(t,{focusAssistant:true}),'button'));t.root.append(actions);error(t);
    const rows=t.list || [];
    if(!rows.length){const empty=node('section','user-tool-empty');add(empty,node('h2','','还没有自定义工具'),node('p','','可以保存目录中的文件筛选规则，也可以保存内置工具的常用设置。'));t.root.append(empty);return;}
    for(const [draft,label] of [[false,'已保存'],[true,'待保存的草稿']]){
      const items=rows.filter(item=>isDraft(item)===draft);if(!items.length)continue;
      const section=node('section','user-tool-list-section');section.append(node('h2','',label));const list=node('div','user-tool-list');
      for(const tool of items){const row=node('article','user-tool-list-row'),copy=node('div','user-tool-list-copy');add(copy,node('h3','',tool.definition?.title || tool.title || '未命名工具'),node('p','',toolSummary(tool)));if(tool.definition?.description)copy.append(node('small','',tool.definition.description));if(draft)copy.append(node('span','semantic-tag','尚未保存'));add(row,copy,button(draft?'继续编辑':'查看工具',()=>isDraft(tool)?edit(t,tool):show(t,tool),'button small'));list.append(row);}section.append(list);t.root.append(section);
    }
  }
  async function loadTool(t,id,{edit:editing=false,version}={}){
    const request=++t.request;t.busy=true;t.error='';busy(t,'我的工具');
    try{const data=await t.helpers.api('/api/user-tools/'+encodeURIComponent(id)+(Number.isInteger(version)&&version>0?'?version='+version:''));if(!active(t)||request!==t.request)return;const tool=unwrap(data);if(!tool?.definition)throw new Error('工具定义尚未读取完整，请刷新后重试。');state.tool=tool;if(editing||isDraft(tool))edit(t,tool);else await inspect(t);}
    catch(e){if(active(t)&&request===t.request){t.busy=false;t.error=e.message;state.mode='list';paintList(t);}}
  }
  function formFrom(d={}){return {type:d.type || 'files',title:d.title || '',description:d.description || '',roots:(d.rules?.roots || []).join('\n'),includes:(d.rules?.includes || ['*.tmp']).join('\n'),excludes:(d.rules?.excludes || []).join('\n'),recursive:d.rules?.recursive!==false,min_age_days:d.rules?.min_age_days ?? 7,min_size_mb:d.rules?.min_size_bytes?String(d.rules.min_size_bytes/1048576):'',max_size_mb:d.rules?.max_size_bytes==null?'':String(d.rules.max_size_bytes/1048576),builtin_id:d.builtin?.tool_id || '',parameters:clone(d.builtin?.parameters || {}),assistant_goal:''};}
  function newEditor(t,ref={}){
    const d=ref.definition || (ref.preset?{type:'preset',title:ref.title || '',builtin:ref.preset}:{});state.source=null;state.linkSource=ref.source || t.source || null;state.tool=null;state.run=null;state.form=formFrom(d);
    if(ref.roots)state.form.roots=ref.roots.join('\n');else if(!Object.hasOwn(d.rules || {},'roots')&&!state.form.roots&&t.helpers.defaultPath)state.form.roots=t.helpers.defaultPath;
    state.mode='edit';t.busy=false;t.error='';paintEditor(t);if(state.form.type==='preset')loadPresetCatalog(t);if(ref.focusAssistant)t.root.querySelector?.('[name="assistant_goal"]')?.focus?.();
  }
  function edit(t,tool){state.source=tool;state.linkSource=tool.source || t.source || null;state.tool=tool;state.run=null;state.form=formFrom(tool.definition);state.mode='edit';t.busy=false;t.error='';paintEditor(t);if(state.form.type==='preset')loadPresetCatalog(t);}
  function field(root,label,name,value,{type='text',help,rows=3,min,max,placeholder,change}={}){
    const line=node('label','user-tool-field'),input=node(type==='textarea'?'textarea':'input');input.name=name;input.setAttribute('aria-label',label);
    if(type==='textarea')input.rows=rows;else input.type=type;
    if(type==='checkbox')input.checked=Boolean(value);else input.value=String(value ?? '');
    if(min!==undefined)input.min=min;if(max!==undefined)input.max=max;if(placeholder)input.placeholder=placeholder;
    input.addEventListener('input',()=>{const raw=type==='checkbox'?input.checked:input.value;if(change)change(raw);else state.form[name]=raw;});
    add(line,node('span','field-label',label),input,help?node('small','field-help',help):null);root.append(line);return input;
  }
  function markChanged(t){state.run=null;t.error='';t.root.querySelector?.('.user-tool-inspection')?.remove?.();t.errorHost?.replaceChildren();}
  function collectDefinition(){
    const f=state.form,d={type:f.type,title:f.title.trim(),description:f.description.trim()};
    if(f.type==='preset')d.builtin={tool_id:f.builtin_id,parameters:clone(f.parameters)};
    else d.rules={roots:lines(f.roots),recursive:f.recursive,includes:patterns(f.includes),excludes:patterns(f.excludes),min_age_days:Number(f.min_age_days || 0),min_size_bytes:f.min_size_mb===''?0:Math.round(Number(f.min_size_mb)*1048576),max_size_bytes:f.max_size_mb===''?null:Math.round(Number(f.max_size_mb)*1048576)};
    return d;
  }
  function draftBody(){const source=state.source;return {definition:collectDefinition(),...(source?.recipe_id||source?.status==='saved'?{recipe_id:source.recipe_id || source.id,base_version:source.base_version ?? source.version}:{}),...(state.linkSource?.task_id?{source:{task_id:state.linkSource.task_id}}:{})};}
  function ready(){const f=state.form;return Boolean(f?.title.trim()&&(f.type==='preset'?f.builtin_id:lines(f.roots).length&&patterns(f.includes).length));}
  function paintEditor(t){
    if(!active(t))return;t.root.replaceChildren();t.root.setAttribute('aria-busy',String(t.busy));back(t,'返回我的工具',()=>loadList(t));heading(t,state.source?'编辑清理工具':'新建清理工具','填写规则后保存，下次运行会重新检查匹配内容。');
    const form=node('section','user-tool-editor'),intro=node('div','user-tool-core-fields');
    field(intro,'工具名称','title',state.form.title,{placeholder:'例如：清理项目的旧临时文件',change:v=>{state.form.title=v;markChanged(t);updateSave(t);}});
    const typeLine=node('label','user-tool-field'),type=node('select');type.name='type';type.setAttribute('aria-label','工具类型');for(const [value,label] of [['files','清理目录中的文件'],['preset','保存内置工具设置']]){const o=node('option','',label);o.value=value;type.append(o);}type.value=state.form.type;type.disabled=!!state.source;type.addEventListener('change',()=>{state.form.type=type.value;markChanged(t);paintEditor(t);if(type.value==='preset')loadPresetCatalog(t);});add(typeLine,node('span','field-label','工具类型'),type);intro.append(typeLine);form.append(intro);
    if(state.form.type==='files'){
      field(form,'要清理的文件夹','roots',state.form.roots,{type:'textarea',rows:2,placeholder:'粘贴完整文件夹路径',help:'多个文件夹每行一个。运行时只在这些位置查找。',change:v=>{state.form.roots=v;markChanged(t);updateSave(t);}});
      const rules=node('div','user-tool-rule-fields');field(rules,'包含的文件名','includes',state.form.includes,{type:'textarea',rows:2,placeholder:'*.tmp',help:'每行一种，可用 * 匹配任意名称。例如 *.tmp。',change:v=>{state.form.includes=v;markChanged(t);updateSave(t);}});
      field(rules,'超过多少天未修改','min_age_days',state.form.min_age_days,{type:'number',min:0,max:36500,help:'0 表示不限；修改时间不代表最近使用时间。',change:v=>{state.form.min_age_days=v;markChanged(t);}});form.append(rules);
      const recursive=node('label','check-line user-tool-checkbox'),check=node('input');check.type='checkbox';check.name='recursive';check.checked=state.form.recursive;check.addEventListener('change',()=>{state.form.recursive=check.checked;markChanged(t);});add(recursive,check,node('span','','同时查找子文件夹'));form.append(recursive);
      const more=node('details','user-tool-advanced');more.append(node('summary','','排除项与其他条件'));
      field(more,'排除的名称或子目录','excludes',state.form.excludes,{type:'textarea',rows:2,placeholder:'Keep/**',help:'例如 Keep/** 保留 Keep 目录及其中内容。',change:v=>{state.form.excludes=v;markChanged(t);}});
      const sizes=node('div','user-tool-rule-fields');field(sizes,'最小文件大小（MiB）','min_size_mb',state.form.min_size_mb,{type:'number',min:0,help:'留空表示不限。',change:v=>{state.form.min_size_mb=v;markChanged(t);}});field(sizes,'最大文件大小（MiB）','max_size_mb',state.form.max_size_mb,{type:'number',min:0,help:'留空表示不限。',change:v=>{state.form.max_size_mb=v;markChanged(t);}});more.append(sizes);field(more,'说明（可选）','description',state.form.description,{type:'textarea',rows:2,change:v=>{state.form.description=v;markChanged(t);}});form.append(more);
    }else renderPresetFields(t,form);
    const errorHost=node('div','user-tool-form-error');t.errorHost=errorHost;error(t,errorHost);form.append(errorHost);
    const actions=node('footer','user-tool-editor-actions'),saveButton=button(t.busy?'正在保存…':'保存工具',()=>save(t),'button primary');t.saveButton=saveButton;saveButton.disabled=t.busy||!ready();
    const check=button(t.busy?'正在检查…':'检查匹配',()=>checkDraft(t),'button');check.disabled=t.busy||!ready();t.checkButton=check;actions.append(saveButton,check);form.append(actions);t.root.append(form);
    if(state.run)renderInspection(t,t.root,{draft:true});
    if(t.helpers.askAssistant&&state.form.type==='files'){
      const ai=node('details','user-tool-ai');ai.append(node('summary','','让 AI 帮我完善规则'));field(ai,'希望怎样清理','assistant_goal',state.form.assistant_goal,{type:'textarea',rows:2,placeholder:'例如：只清理可以重新生成的临时文件，保留日志和源文件'});
      const ask=button('让 AI 帮我创建',()=>askAssistant(t),'button');ask.disabled=t.busy;ai.append(ask);t.root.append(ai);
    }
    if(t.busy)form.querySelectorAll?.('input,select,textarea').forEach(input=>{input.disabled=true;});
  }
  function updateSave(t){if(t.saveButton)t.saveButton.disabled=t.busy||!ready();if(t.checkButton)t.checkButton.disabled=t.busy||!ready();}
  async function askAssistant(t){
    if(t.assistantBusy||t.busy)return;
    const roots=lines(state.form.roots);if(!roots.length){t.error='请先填写要清理的文件夹，再让 AI 帮你创建规则。';paintEditor(t);return;}
    const goal=state.form.assistant_goal.trim() || `帮我创建可重复使用的清理工具。名称：${state.form.title || '目录临时文件清理'}；希望匹配：${state.form.includes}；排除：${state.form.excludes || '暂无'}；超过${state.form.min_age_days || 0}天未修改。请先核对用途，再生成供我审阅的清理规则草案。`;
    t.assistantBusy=true;
    try{await t.helpers.askAssistant(goal,roots);}catch(e){if(active(t)){t.error=e.message;paintEditor(t);}}finally{t.assistantBusy=false;}
  }
  async function loadPresetCatalog(t){
    const request=++t.request;t.error='';
    try{if(!t.catalog)t.catalog=await t.helpers.api('/api/cleanup-tools');if(!active(t)||request!==t.request||state.mode!=='edit'||state.form.type!=='preset')return;
      if(state.form.builtin_id)await loadPresetDetail(t,state.form.builtin_id);else paintEditor(t);
    }catch(e){if(active(t)&&request===t.request){t.error=e.message;paintEditor(t);}}
  }
  async function loadPresetDetail(t,id){
    const request=++t.request;t.error='';t.presetLoading=true;paintEditor(t);
    try{const tool=await t.helpers.api('/api/cleanup-tools/'+encodeURIComponent(id));if(!active(t)||request!==t.request||state.form.builtin_id!==id)return;t.detail=tool;
      const defaults=Object.fromEntries((tool.parameters || []).filter(f=>Object.hasOwn(f,'default')).map(f=>[f.name,f.default]));state.form.parameters={...defaults,...state.form.parameters};if(!state.form.title.trim())state.form.title=tool.title+'（常用设置）';
      if((tool.parameters || []).some(f=>f.format==='volume'||f.name==='volume')&&!t.drives.length){const d=await t.helpers.api('/api/file-browser/drives');if(!active(t)||request!==t.request)return;t.drives=d.items || [];}
    }catch(e){if(active(t)&&request===t.request)t.error=e.message;}
    finally{if(active(t)&&request===t.request){t.presetLoading=false;paintEditor(t);}}
  }
  function renderPresetFields(t,form){
    const line=node('label','user-tool-field'),select=node('select');select.name='builtin_id';select.setAttribute('aria-label','选择内置工具');const placeholder=node('option','','选择一个内置工具');placeholder.value='';select.append(placeholder);
    for(const tool of t.catalog?.items || []){const o=node('option','',tool.title);o.value=tool.id;select.append(o);}select.value=state.form.builtin_id;select.addEventListener('change',()=>{state.form.builtin_id=select.value;state.form.parameters={};state.run=null;t.detail=null;if(select.value)loadPresetDetail(t,select.value);else paintEditor(t);});add(line,node('span','field-label','选择内置工具'),select);form.append(line);
    if(t.presetLoading){form.append(notice('正在读取工具设置…'));return;}
    if(!t.detail||t.detail.id!==state.form.builtin_id){form.append(notice('选择工具后，可以保存常用参数。'));return;}
    if(t.detail.description)form.append(node('p','field-help',t.detail.description));const params=node('div','user-tool-preset-fields');
    for(const f of t.detail.parameters || []){
      if(t.detail.id==='pagefile'&&['initial_mb','maximum_mb'].includes(f.name)&&state.form.parameters.mode!=='custom')continue;
      const holder=node('label','user-tool-field'),options=f.enum || f.options;let input;
      if(f.format==='volume'||f.name==='volume'){input=node('select');const p=node('option','','选择磁盘');p.value='';input.append(p);for(const drive of t.drives){const path=drive.path || drive.name || '';if(!/^[A-Za-z]:/.test(path))continue;const o=node('option','',drive.name || path);o.value=path.slice(0,2).toUpperCase();input.append(o);}}
      else if(options){input=node('select');for(const option of options){const o=node('option','',typeof option==='object'?option.label || option.value:optionLabels[option] || option);o.value=typeof option==='object'?option.value:option;input.append(o);}}
      else{input=node('input');input.type=['integer','number'].includes(f.type)?'number':f.type==='boolean'?'checkbox':'text';if(f.minimum!==undefined)input.min=f.minimum;if(f.maximum!==undefined)input.max=f.maximum;}
      input.name='parameter-'+f.name;input.setAttribute('aria-label',f.label || f.name);if(input.type==='checkbox')input.checked=state.form.parameters[f.name]===true;else input.value=state.form.parameters[f.name] ?? '';
      input.addEventListener('input',()=>{const raw=input.type==='checkbox'?input.checked:input.value;state.form.parameters[f.name]=['integer','number'].includes(f.type)&&raw!==''?Number(raw):raw;markChanged(t);if(t.detail.id==='pagefile'&&f.name==='mode')paintEditor(t);});add(holder,node('span','field-label',f.label || f.name),input,f.help?node('small','field-help',f.help):null);params.append(holder);
    }form.append(params);
    if(t.detail.impact)add(form,node('h2','user-tool-minor-heading','操作影响'),node('p','field-help',t.detail.impact));if(t.detail.recovery)add(form,node('h2','user-tool-minor-heading','恢复说明'),node('p','field-help',t.detail.recovery));
  }
  async function makeDraft(t){return unwrap(await t.helpers.api('/api/user-tools/draft',draftBody()));}
  async function save(t){
    if(t.busy||!ready())return;t.busy=true;t.error='';paintEditor(t);const request=++t.request;
    try{const draft=await makeDraft(t);const data=await t.helpers.api('/api/user-tools/save',{draft_id:draft.draft_id || draft.id});if(!active(t)||request!==t.request)return;state.tool=unwrap(data);state.source=null;state.run=null;await inspect(t);}
    catch(e){if(active(t)&&request===t.request){t.error=e.message;t.busy=false;paintEditor(t);}}
  }
  async function checkDraft(t){
    if(t.busy||!ready())return;t.busy=true;t.error='';paintEditor(t);const request=++t.request;
    try{const draft=await makeDraft(t);if(!active(t)||request!==t.request)return;const data=await t.helpers.api('/api/user-tools/inspect',{draft_id:draft.draft_id || draft.id});if(!active(t)||request!==t.request)return;state.run=data.run || data;state.offset=0;resetSelection();}
    catch(e){if(active(t)&&request===t.request)t.error=e.message;}
    finally{if(active(t)&&request===t.request){t.busy=false;paintEditor(t);}}
  }
  function resetSelection(){state.allSelected=true;state.selected.clear();state.excluded.clear();}
  function show(t,tool){state.tool=tool;state.source=null;state.mode='detail';state.run=null;state.offset=0;state.archiveConfirm=false;inspect(t);}
  async function inspect(t){
    if(!state.tool)return;state.mode='detail';t.busy=true;t.error='';state.archiveConfirm=false;const request=++t.request;paintDetail(t);
    try{const data=await t.helpers.api('/api/user-tools/inspect',runRef(state.tool));if(!active(t)||request!==t.request)return;state.run=data.run || data;state.offset=0;resetSelection();}
    catch(e){if(active(t)&&request===t.request)t.error=e.message;}
    finally{if(active(t)&&request===t.request){t.busy=false;paintDetail(t);}}
  }
  async function page(t,offset){
    if(t.busy||!state.run)return;const id=state.run.run_id || state.run.id,request=++t.request;t.busy=true;t.error='';paintDetail(t);
    try{const data=await t.helpers.api('/api/user-tool-runs/'+encodeURIComponent(id)+'?offset='+offset+'&limit=50');if(!active(t)||request!==t.request)return;state.run=data.run || data;state.offset=offset;}
    catch(e){if(active(t)&&request===t.request)t.error=e.message;}
    finally{if(active(t)&&request===t.request){t.busy=false;paintDetail(t);}}
  }
  const checked=item=>item.can_select!==false&&(state.allSelected?!state.excluded.has(item.id):state.selected.has(item.id));
  function toggle(item,value){if(item.can_select===false)return;if(state.allSelected){if(value)state.excluded.delete(item.id);else state.excluded.add(item.id);}else if(value)state.selected.add(item.id);else state.selected.delete(item.id);}
  function canPrepare(){const run=state.run;if(!run||isDraft(state.tool)||run.can_prepare===false)return false;if(run.type==='preset')return !['needs_input'].includes(run.status)&&run.presentation?.action_availability?.available!==false;const available=run.selectable_count ?? run.matched_count;if(!state.allSelected)return state.selected.size>0;if(!Number.isFinite(available)||!available)return false;if(!run.has_more&&!state.offset)return (run.items || []).some(checked);return true;}
  function paintDetail(t){
    if(!active(t))return;t.root.replaceChildren();t.root.setAttribute('aria-busy',String(t.busy));back(t,'返回我的工具',()=>loadList(t));const d=definition();heading(t,d.title || state.tool?.title || '我的工具',d.description || '');
    const summary=node('section','user-tool-definition');summary.append(node('p','user-tool-definition-summary',toolSummary(state.tool)));
    if(d.type==='files'){const rules=d.rules || {};if(rules.excludes?.length)summary.append(node('p','field-help','保留：'+rules.excludes.join('、')));summary.append(node('p','field-help','文件进入盘序“已删除文件”，后续可还原；永久删除另行确认。'));}t.root.append(summary);
    const status=node('div','user-tool-detail-top-actions'),refresh=button('刷新',()=>inspect(t),'plain-button'),editing=button('编辑规则',()=>edit(t,state.tool),'button');refresh.disabled=t.busy;editing.disabled=t.busy;status.append(refresh,editing);t.root.append(status);error(t);
    if(t.busy&&!state.run)t.root.append(notice('正在检查当前匹配内容…'));
    if(state.run)renderInspection(t,t.root);
    const footer=node('footer','user-tool-run-actions'),run=button(state.run?.type==='preset'?'运行工具':'清理勾选项',()=>prepare(t),'button primary');run.disabled=t.busy||!canPrepare();footer.append(run);
    if(d.type==='files'&&state.run){const total=state.run.selectable_count ?? state.run.matched_count;footer.append(node('span','field-help',state.allSelected?`已选择全部可操作匹配项${!state.excluded.size&&Number.isFinite(total)?`（${total} 项）`:''}${state.excluded.size?`，另排除 ${state.excluded.size} 项`:''}`:`已选择 ${state.selected.size} 项`));}t.root.append(footer);
    const details=node('details','user-tool-source');details.append(node('summary','','来源与管理'));const p=state.tool?.provenance;
    details.append(node('p','field-help',p?.model?`由 ${p.model} 协助创建${p.generated_at?' · '+new Date(p.generated_at).toLocaleString('zh-CN'):''}`:'由你保存的清理工具'));
    if(state.tool?.version)details.append(node('p','field-help','版本 '+state.tool.version));details.append(button('归档工具',()=>{state.archiveConfirm=true;paintDetail(t);},'plain-button'));t.root.append(details);
    if(state.archiveConfirm){const archive=node('section','user-tool-archive-confirm');add(archive,node('p','','归档后从我的工具列表中隐藏，已有操作记录继续保留。'),button('确认归档',()=>archiveTool(t),'button'),button('取消',()=>{state.archiveConfirm=false;paintDetail(t);},'plain-button'));t.root.append(archive);}
  }
  function renderInspection(t,root,{draft=false}={}){
    const run=state.run,section=node('section','user-tool-inspection');section.setAttribute('aria-label','当前匹配结果');const heading=node('div','user-tool-inspection-heading');heading.append(node('h2','',draft?'规则匹配结果':'当前匹配'));section.append(heading);
    if(run.type==='preset'){
      const presentation=run.presentation || {};section.append(node('p','',presentation.summary || (run.status==='needs_input'?'请补充所需设置后再运行。':'当前状态尚未确认。')));
      for(const metric of (presentation.metrics || []).slice(0,6))if(metric.value!==null&&metric.value!==undefined)section.append(node('p','field-help',metric.label+'：'+(metric.unit==='bytes'?t.helpers.bytes(metric.value):metric.value)));root.append(section);return;
    }
    const count=Number.isFinite(run.matched_count)?run.matched_count:null,size=Number.isFinite(run.matched_bytes)?run.matched_bytes:null;
    section.append(node('p','user-tool-match-summary',count===null?'匹配数量尚未确认':`${count} 项${size===null?'':' · '+t.helpers.bytes(size)}`));
    if(run.status==='needs_input')section.append(notice('条件尚不完整，请编辑后补充。'));
    else if(!run.complete)section.append(notice('当前检查尚未覆盖全部内容；以下只显示已经找到的匹配项。'));
    else if(count===0)section.append(notice('当前没有符合规则的文件。'));
    for(const warning of (run.warnings || []).slice(0,3))section.append(node('p','field-help',typeof warning==='string'?warning:warning.message || String(warning.reason || '部分内容未检查')));
    if(!draft&&(run.items || []).length){const all=node('label','check-line user-tool-select-all'),check=node('input');check.type='checkbox';check.checked=state.allSelected&&!state.excluded.size;check.indeterminate=state.allSelected&&state.excluded.size>0||!state.allSelected&&state.selected.size>0;check.disabled=t.busy;check.setAttribute('aria-label','全选本次匹配结果');check.addEventListener('change',()=>{state.allSelected=check.checked;state.selected.clear();state.excluded.clear();paintDetail(t);});add(all,check,node('span','','全选本次匹配结果（包括其他页）'));section.append(all);}
    const list=node('div','user-tool-matches');list.setAttribute('aria-busy',String(t.busy));
    for(const item of run.items || []){
      const row=node('article','user-tool-match'),copy=node('div','user-tool-match-copy');
      if(!draft){const check=node('input');check.type='checkbox';check.checked=checked(item);check.disabled=t.busy||item.can_select===false;check.setAttribute('aria-label','选择 '+item.name);check.addEventListener('change',()=>{toggle(item,check.checked);paintDetail(t);});row.append(check);}
      const title=node('div','item-title-line');const icon=window.PanxuIcons?.create(item);if(icon)title.append(icon);title.append(node('strong','',item.name));add(copy,title,node('small','user-tool-match-path',item.path));if(item.block_reason)copy.append(node('p','field-help',item.block_reason));add(row,copy,node('span','user-tool-match-size',Number.isFinite(item.size_bytes)?t.helpers.bytes(item.size_bytes):'大小未知'));list.append(row);
    }section.append(list);
    if(!draft&&(run.has_more||state.offset)){const pager=node('div','file-pagination'),prev=button('上一页',()=>page(t,Math.max(0,state.offset-50))),next=button('下一页',()=>page(t,run.next_offset ?? state.offset+50));prev.disabled=t.busy||!state.offset;next.disabled=t.busy||!run.has_more;add(pager,prev,node('span','',`第 ${Math.floor(state.offset/50)+1} 页`),next);section.append(pager);}else if(draft&&run.has_more)section.append(notice('这里只显示前50项；保存后可分页查看完整匹配结果。'));
    root.append(section);
  }
  async function prepare(t){
    if(t.busy||!canPrepare())return;const request=++t.request;t.busy=true;t.error='';paintDetail(t);
    const payload={run_id:state.run.run_id || state.run.id,...(state.run.type==='preset'?{}:state.allSelected?{excluded_ids:[...state.excluded]}:{ids:[...state.selected]})};
    try{if(typeof t.helpers.onPrepared!=='function')throw new Error('当前页面无法打开确认清单，请重新打开工具。');const data=await t.helpers.api('/api/user-tools/prepare',payload);if(!active(t)||request!==t.request)return;await t.helpers.onPrepared(data);}
    catch(e){if(active(t)&&request===t.request)t.error=e.message;}
    finally{if(active(t)&&request===t.request){t.busy=false;paintDetail(t);}}
  }
  async function archiveTool(t){
    if(t.busy)return;t.busy=true;t.error='';paintDetail(t);
    try{await t.helpers.api('/api/user-tools/archive',{tool_id:state.tool.id,version:state.tool.version});if(active(t))await loadList(t);}
    catch(e){if(active(t)){t.busy=false;t.error=e.message;paintDetail(t);}}
  }
  return {render,open,unmount};
})();
