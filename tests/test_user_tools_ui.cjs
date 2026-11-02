'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{join}=require('node:path'),{runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','user-tools.js'),'utf8');
const plain=x=>JSON.parse(JSON.stringify(x));
const flush=async()=>{await new Promise(setImmediate);await new Promise(setImmediate);};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const definition=(extra={})=>({type:'files',title:'旧临时文件',description:'保留个人资料',rules:{roots:['E:\\Owned'],recursive:true,includes:['*.tmp'],excludes:['Keep/**'],min_age_days:7,min_size_bytes:0,max_size_bytes:null},...extra});
const saved=(extra={})=>({id:'recipe-a',version:2,status:'saved',definition:definition(),...extra});
const item=(id,extra={})=>({id,name:id+'.tmp',path:'E:\\Owned\\'+id+'.tmp',signature:'sig-'+id,size_bytes:10,can_select:true,...extra});
const inspection=(extra={})=>({id:'run-1',run_id:'run-1',type:'files',complete:true,status:'complete',matched_count:2,selectable_count:2,matched_bytes:20,items:[item('a'),item('b')],has_more:false,next_offset:null,warnings:[],...extra});
function fixture(respond){
 let root;class Element{
  constructor(tag){this.tag=tag;this.children=[];this.parent=null;this._text='';this.className='';this.attrs={};this.handlers={};this.dataset={};this.disabled=false;this.hidden=false;this.value='';}
  get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}set textContent(v){this._text=String(v);this.children=[];}
  get isConnected(){return this===root||Boolean(this.parent?.isConnected);}append(...children){for(const child of children){child.parent=this;this.children.push(child);}}replaceChildren(...children){for(const child of this.children)child.parent=null;this.children=[];this._text='';this.append(...children);}
  setAttribute(k,v){this.attrs[k]=String(v);}addEventListener(k,fn){this.handlers[k]=fn;}getAttribute(k){return this.attrs[k];}
  all(){return [this,...this.children.flatMap(c=>c.all())];}remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null;}
  matches(s){if(s.startsWith('.'))return this.className.split(' ').includes(s.slice(1));const m=s.match(/^\[([^=]+)="([^"]+)"\]$/);if(m)return (this[m[1]]||this.attrs[m[1]])===m[2];return this.tag===s;}
  querySelectorAll(s){return this.all().slice(1).filter(e=>s.split(',').some(x=>e.matches(x.trim())));}querySelector(s){return this.querySelectorAll(s)[0]||null;}
  async fire(name='click'){if(this.disabled)return;await this.handlers[name]?.({preventDefault(){}});await flush();}focus(){document.activeElement=this;}
  set innerHTML(_){throw new Error('Tool text must remain literal');}
 }
 root=new Element('main');const window={},document={createElement:tag=>new Element(tag)},requests=[],prepared=[],asked=[];let nav=null;
 runInNewContext(source,{window,document,URLSearchParams,AbortController,setTimeout,clearTimeout});
 const helpers={bytes:value=>value+' B',defaultPath:'E:\\Owned',setContextBack:(label,run)=>{nav={label,run};},askAssistant:async(goal,roots)=>asked.push({goal,roots:plain(roots)}),onPrepared:async data=>prepared.push(plain(data)),api:async(path,body)=>{
  requests.push({path,body:body===undefined?undefined:plain(body)});const answer=await respond?.(path,body);if(answer!==undefined)return answer;
  if(path==='/api/user-tools')return {items:[]};if(path==='/api/user-tools/draft')return {id:'draft-1',draft_id:'draft-1',status:'draft',definition:plain(body.definition)};
  if(path==='/api/user-tools/save')return saved();if(path==='/api/user-tools/inspect')return inspection();if(path.startsWith('/api/user-tool-runs/'))return inspection();
  if(path==='/api/user-tools/prepare')return {task:{id:'prepared-task'},plan:{version:1,items:[item('a')]},picked_ids:['a']};
  if(path==='/api/cleanup-tools')return {items:[{id:'recycle_bin',title:'清空回收站'}]};
  if(path==='/api/cleanup-tools/recycle_bin')return {id:'recycle_bin',title:'清空回收站',description:'只处理所选磁盘',impact:'清空回收站内容',recovery:'无法保证恢复',parameters:[{name:'volume',label:'磁盘',format:'volume',default:'C:'}]};
  if(path==='/api/file-browser/drives')return {items:[{path:'C:\\',name:'C:'},{path:'E:\\',name:'E:'}]};if(path==='/api/user-tools/archive')return {archived:true};throw new Error('Unexpected API '+path);
 }};
 return {root,window,helpers,requests,prepared,asked,all:()=>root.all(),button:label=>root.all().find(e=>e.tag==='button'&&e.textContent===label),field:name=>root.all().find(e=>e.name===name),check:label=>root.all().find(e=>e.tag==='input'&&e.attrs['aria-label']===label),get nav(){return nav;},async open(ref={}){await window.PanxuUserTools.open(root,ref,helpers);await flush();},async render(){window.PanxuUserTools.render(root,helpers);await flush();},async value(name,value,event='input'){const input=this.field(name);assert.ok(input,'Missing field '+name);if(input.type==='checkbox')input.checked=value;else input.value=value;await input.fire(event);}};
}

test('empty saved-tool workspace gives one creation action and never invokes AI or execution',async()=>{
 const f=fixture();await f.render();assert.match(f.root.textContent,/还没有自定义工具/);assert.ok(f.button('新建工具'));assert.equal(f.requests.length,1);assert.equal(f.asked.length,0);assert.equal(f.prepared.length,0);
 await f.button('让 AI 帮我创建').fire();assert.ok(f.field('roots'));assert.equal(f.asked.length,0);
});

test('saved tools and model drafts stay distinct and untrusted titles remain literal',async()=>{
 const f=fixture(path=>path==='/api/user-tools'?{items:[saved({definition:definition({title:'<script>not html</script>'})}),{id:'draft-owned',draft_id:'draft-owned',status:'draft',definition:definition({title:'模型草案'})}]}:undefined);
 await f.render();assert.match(f.root.textContent,/<script>not html<\/script>/);assert.match(f.root.textContent,/已保存/);assert.match(f.root.textContent,/待保存的草稿/);assert.ok(f.button('继续编辑'));assert.equal(f.requests.length,1);
});

test('saving a file rule sends a native definition then saves and auto-inspects without preparing or executing',async()=>{
 const f=fixture();await f.open({new:true,definition:definition()});await f.value('min_age_days','0');await f.value('includes','*.tmp，*.cache');await f.button('保存工具').fire();
 assert.deepEqual(f.requests.filter(r=>r.path==='/api/user-tools/draft')[0].body,{definition:definition({rules:{...definition().rules,min_age_days:0,includes:['*.tmp','*.cache']}})});
 assert.deepEqual(f.requests.find(r=>r.path==='/api/user-tools/save').body,{draft_id:'draft-1'});assert.deepEqual(f.requests.find(r=>r.path==='/api/user-tools/inspect').body,{tool_id:'recipe-a',version:2});
 assert.ok(f.button('清理勾选项'));assert.equal(f.requests.some(r=>/prepare|execute|approve/.test(r.path)),false);assert.equal(f.asked.length,0);
});

test('save errors preserve all editor fields and never clear a source draft',async()=>{
 const f=fixture(path=>path==='/api/user-tools/save'?Promise.reject(new Error('版本已更新，请重新读取')):undefined);await f.open({new:true,definition:definition()});await f.value('description','我的草稿说明');await f.button('保存工具').fire();
 assert.equal(f.field('description').value,'我的草稿说明');assert.equal(f.field('roots').value,'E:\\Owned');assert.equal(f.field('includes').value,'*.tmp');assert.match(f.root.textContent,/版本已更新/);assert.equal(f.prepared.length,0);
});

test('editing a saved definition creates a version-bound draft rather than overwriting a saved version',async()=>{
 const f=fixture();await f.open({tool:saved(),edit:true});await f.value('title','新的规则');await f.button('保存工具').fire();const body=f.requests.find(r=>r.path==='/api/user-tools/draft').body;
 assert.equal(body.recipe_id,'recipe-a');assert.equal(body.base_version,2);assert.equal(body.definition.title,'新的规则');assert.equal(f.requests.filter(r=>r.path==='/api/user-tools/save').length,1);
});

test('draft matching is read-only, remains editable, and becomes invalid when a condition changes',async()=>{
 const f=fixture();await f.open({new:true,definition:definition()});await f.button('检查匹配').fire();assert.match(f.root.textContent,/规则匹配结果/);assert.equal(f.button('清理勾选项'),undefined);assert.equal(f.requests.some(r=>r.path.endsWith('/save')),false);
 await f.value('includes','*.cache');assert.equal(f.root.textContent.includes('规则匹配结果'),false);assert.equal(f.field('includes').value,'*.cache');assert.equal(f.asked.length,0);
});

test('a saved tool automatically inspects and clearly disables an empty completed match',async()=>{
 const f=fixture(path=>path.endsWith('/inspect')?inspection({matched_count:0,selectable_count:0,matched_bytes:0,items:[]}):undefined);await f.open({tool:saved()});assert.equal(f.requests.length,1);assert.match(f.root.textContent,/当前没有符合规则的文件/);assert.equal(f.button('清理勾选项').disabled,true);assert.equal(f.requests.some(r=>r.path.endsWith('/prepare')),false);
});

test('partial and unknown inspection does not claim an empty directory or fabricate a size',async()=>{
 const f=fixture(path=>path.endsWith('/inspect')?inspection({status:'partial',complete:false,matched_count:null,selectable_count:null,matched_bytes:null,items:[],warnings:['目录读取尚未完成']}):undefined);await f.open({tool:saved()});assert.match(f.root.textContent,/匹配数量尚未确认/);assert.match(f.root.textContent,/尚未覆盖全部/);assert.equal(f.root.textContent.includes('0 项'),false);assert.equal(f.button('清理勾选项').disabled,true);
});

test('all selection preserves off-page exclusions and sends one frozen-plan request without invoking AI',async()=>{
 const f=fixture(path=>path.startsWith('/api/user-tool-runs/')?inspection({items:[item('c')],matched_count:3,selectable_count:3,matched_bytes:30,has_more:false}):path.endsWith('/inspect')?inspection({matched_count:3,selectable_count:3,has_more:true,next_offset:50}):undefined);
 await f.open({tool:saved()});const a=f.check('选择 a.tmp');a.checked=false;await a.fire('change');await f.button('下一页').fire();assert.equal(f.check('选择 c.tmp').checked,true);await f.button('清理勾选项').fire();
 assert.deepEqual(f.requests.find(r=>r.path==='/api/user-tools/prepare').body,{run_id:'run-1',excluded_ids:['a']});assert.equal(f.prepared.length,1);assert.equal(f.asked.length,0);assert.equal(f.requests.some(r=>/execute|approve/.test(r.path)),false);
});

test('clearing all and choosing an individual item passes only its ID, never the displayed page as authorization',async()=>{
 const f=fixture();await f.open({tool:saved()});const all=f.check('全选本次匹配结果');all.checked=false;await all.fire('change');assert.equal(f.button('清理勾选项').disabled,true);
 const b=f.check('选择 b.tmp');b.checked=true;await b.fire('change');await f.button('清理勾选项').fire();assert.deepEqual(f.requests.find(r=>r.path==='/api/user-tools/prepare').body,{run_id:'run-1',ids:['b']});
});

test('kept and changed matches remain visible but cannot be selected',async()=>{
 const f=fixture(path=>path.endsWith('/inspect')?inspection({selectable_count:1,items:[item('a',{can_select:false,block_reason:'你已选择保留'}),item('b')]}):undefined);await f.open({tool:saved()});assert.equal(f.check('选择 a.tmp').disabled,true);assert.equal(f.check('选择 a.tmp').checked,false);assert.match(f.root.textContent,/你已选择保留/);assert.equal(f.check('选择 b.tmp').checked,true);
});

test('the file rules view keeps the DOM bounded to the server page and renders a real next-page action',async()=>{
 const f=fixture(path=>path.endsWith('/inspect')?inspection({matched_count:6000,selectable_count:6000,items:Array.from({length:50},(_,i)=>item('n'+i)),has_more:true,next_offset:50}):undefined);await f.open({tool:saved()});assert.equal(f.all().filter(n=>n.className==='user-tool-match').length,50);assert.ok(f.button('下一页'));assert.match(f.root.textContent,/6000 项/);assert.equal(f.requests.length,1);
});

test('a late inspection response cannot replace a newly opened editor or submit a plan',async()=>{
 const wait=deferred();const f=fixture(path=>path.endsWith('/inspect')?wait.promise:undefined);const first=f.open({tool:saved()});await flush();await f.open({new:true,definition:definition({title:'新的编辑草稿'})});wait.resolve(inspection());await first;assert.equal(f.field('title').value,'新的编辑草稿');assert.equal(f.button('清理勾选项'),undefined);assert.equal(f.prepared.length,0);
});

test('built-in preset references load the same parameter schema and actual drive choices',async()=>{
 const d=definition({type:'preset',title:'回收站 E 盘',rules:undefined,builtin:{tool_id:'recycle_bin',parameters:{volume:'E:'}}});delete d.rules;
 const f=fixture();await f.open({new:true,definition:d});await flush();assert.equal(f.field('parameter-volume').tag,'select');assert.equal(f.field('parameter-volume').value,'E:');assert.deepEqual(f.field('parameter-volume').children.filter(n=>n.value).map(n=>n.value),['C:','E:']);
 await f.button('保存工具').fire();const body=f.requests.find(r=>r.path==='/api/user-tools/draft').body.definition;assert.deepEqual(body,d);assert.equal(f.requests.some(r=>/execute|approve/.test(r.path)),false);
});

test('a preset prepares through the same host callback and respects explicit no-op availability',async()=>{
 const d={type:'preset',title:'回收站预设',description:'',builtin:{tool_id:'recycle_bin',parameters:{volume:'E:'}}};let empty=false;
 const f=fixture(path=>path.endsWith('/inspect')?inspection({type:'preset',status:'observed',presentation:{summary:'已有回收站内容',action_availability:{available:!empty}},items:[]}):path.endsWith('/prepare')?{kind:'preset',specialist_plan:{id:'builtin-plan',version:1},plan:{id:'builtin-plan',version:1}}:undefined);
 await f.open({tool:saved({definition:d})});assert.equal(f.button('运行工具').disabled,false);await f.button('运行工具').fire();assert.deepEqual(f.requests.find(r=>r.path.endsWith('/prepare')).body,{run_id:'run-1'});assert.equal(f.prepared[0].kind,'preset');empty=true;await f.button('刷新').fire();assert.equal(f.button('运行工具').disabled,true);
});

test('AI creation requires a chosen scope and passes the current roots rather than silently scanning elsewhere',async()=>{
 const f=fixture();await f.open({new:true,definition:definition({rules:{...definition().rules,roots:[]}})});await f.button('让 AI 帮我创建').fire();assert.equal(f.asked.length,0);assert.match(f.root.textContent,/请先填写/);
 await f.value('roots','E:\\Owned\nE:\\Second');await f.value('assistant_goal','帮我保留日志，只清理可重建缓存');await f.button('让 AI 帮我创建').fire();assert.deepEqual(f.asked,[{goal:'帮我保留日志，只清理可重建缓存',roots:['E:\\Owned','E:\\Second']}]);assert.equal(f.requests.length,0);
});

test('a saved suggestion reads its exact tool version and source links are reduced to the permitted task reference',async()=>{
 const f=fixture(path=>path==='/api/user-tools/recipe-a?version=2'?saved():undefined);
 await f.open({tool_id:'recipe-a',version:2,source:{task_id:'origin-task',suggestion_id:'not-a-draft-field'}});
 assert.equal(f.requests[0].path,'/api/user-tools/recipe-a?version=2');
 await f.button('编辑规则').fire();await f.button('保存工具').fire();assert.deepEqual(f.requests.find(r=>r.path==='/api/user-tools/draft').body.source,{task_id:'origin-task'});
});
