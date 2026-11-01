'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const plain=value=>JSON.parse(JSON.stringify(value));
const item=(id,action='delete',kind='file',extra={})=>({id,action,kind,name:'测试 '+id,signature:'signature-'+id,
  reason:'明确原因',impact:'明确影响',path:'fixture/'+id,size_bytes:10,default_selected:true,personal_choice_required:false,...extra});
const task=(items,extra={})=>({id:'copy-task',updated_at:'first',status:'awaiting_review',summary:'请确认具体操作',
  goal:'测试任务',backend:'simulation',events:[],advice:[],plan:{version:7,items},...extra});

// Browser boundary only: the full production tasks.js supplies every behavior.
function fixture(value,responder=async()=>({})){
  let root;
  class Element {
    constructor(tag){this.tag=tag;this.className='';this.children=[];this.dataset={};this.handlers=new Map();this._text='';this.parent=null;this.open=false;this.disabled=false;}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
    get childNodes(){return this.children;}
    get isConnected(){return this===root || Boolean(this.parent?.isConnected);}
    get classList(){return {add:token=>{this.className+=' '+token;}};}
    append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
    replaceChildren(...children){this.children.forEach(child=>child.parent=null);this.children=[];this._text='';this.append(...children);}
    setAttribute(key,value){this[key]=value;}
    addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    matches(selector){return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tag===selector;}
    querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}
    querySelector(selector){return this.querySelectorAll(selector)[0] || null;}
    async fire(name){await Promise.all((this.handlers.get(name)||[]).map(handler=>handler({preventDefault(){}})));await flush();}
    focus(){}
    set innerHTML(_){throw new Error('untrusted content may not become HTML');}
  }
  root=new Element('main');
  const document={createElement:tag=>new Element(tag),getElementById:id=>root.all().find(node=>node.id===id),querySelector:selector=>root.querySelector(selector)};
  const window={},requests=[];
  runInNewContext(source,{window,document,setTimeout:()=>1,clearTimeout:()=>{}});
  const ui=window.PanxuTasks;
  const helpers={heading:()=>new Element('header'),bytes:n=>n+' B',isActive:()=>true,openSettings(){},openAssistant(){},refreshCatalog:async()=>{},
    api:async(path,payload)=>{requests.push({path,payload:payload===undefined?undefined:plain(payload)});return responder(path,payload);},
    redraw:()=>{root.replaceChildren();ui.render(root,helpers);}};
  ui.accept(value);ui.render(root,helpers);
  return {root,ui,copy:window.PanxuActionCopy,requests,helpers,
    get:id=>document.getElementById(id),checks:()=>root.all().filter(node=>node.tag==='input'&&node.type==='checkbox'),
    async choose(id,checked){const checkbox=this.get('pick-'+id);checkbox.checked=checked;await checkbox.fire('change');},
  };
}

test('shared copy describes each supported operation and unknown inherited names conservatively',()=>{
  const f=fixture(task([]));
  for(const [action,kind,expected] of [['delete','file','删除文件'],['delete','directory','删除文件夹'],
    ['disable_startup','startup','关闭开机启动'],['stop_service','service','停止服务'],['stop_process','process','结束应用'],['uninstall','software','卸载软件']]){
    assert.equal(f.copy.label(action,kind),expected);
    assert.ok(f.copy.summary([item('one',action,kind)]).button.startsWith('模拟'));
  }
  for(const action of ['__proto__','constructor','toString','unexpected']){
    assert.equal(f.copy.label(action,'file'),'未知操作');assert.equal(f.copy.summary([item('one',action)]).breakdown,'未知操作 1 项');
  }
});

test('shared summaries distinguish file/folder deletion, mixed actions, and an explicit future non-simulation copy mode',()=>{
  const f=fixture(task([])),file=item('f'),folder=item('d','delete','directory'),service=item('s','stop_service','service');
  const deletion=f.copy.summary([file,folder]);assert.equal(deletion.button,'模拟删除 2 项');assert.equal(deletion.breakdown,'删除 1 个文件、删除 1 个文件夹');
  assert.match(deletion.effect,/不会实际删除/);assert.match(deletion.effect,/包含内部文件/);
  const mixed=f.copy.summary([file,folder,service]);assert.equal(mixed.button,'确认模拟 3 项');assert.equal(mixed.groups.length,3);assert.match(mixed.breakdown,/停止 1 个服务/);
  assert.equal(f.copy.summary([service],{simulation:false}).button,'停止 1 个服务');assert.equal(f.copy.summary([file]).button,'模拟删除 1 个文件');
});

test('actual plan checkboxes update action wording and breakdown without changing personal defaults or sending requests',async()=>{
  const f=fixture(task([item('file'),item('folder','delete','directory',{personal_choice_required:true}),item('service','stop_service','service')]));
  assert.equal(f.get('pick-folder').checked,false);assert.equal(f.get('task-approve').textContent,'确认模拟 2 项');
  assert.equal(f.root.querySelector('.task-selection-count').textContent,'删除 1 个文件、停止 1 个服务');
  await f.choose('service',false);assert.equal(f.get('task-approve').textContent,'模拟删除 1 个文件');
  await f.choose('folder',true);assert.equal(f.get('task-approve').textContent,'模拟删除 2 项');assert.match(f.get('task-simulation-effect').textContent,/内部文件/);
  await f.choose('file',false);await f.choose('folder',false);assert.equal(f.get('task-approve').disabled,true);assert.equal(f.get('task-approve').textContent,'选择要模拟的操作');
  assert.equal(f.requests.length,0);assert.match(f.get('task-approve')['aria-describedby'],/task-selection-summary.*task-simulation-effect/);
});

test('empty single-action plans name what must be selected',()=>{
  const files=fixture(task([item('file','delete','file',{default_selected:false})]));
  assert.equal(files.get('task-approve').disabled,true);assert.equal(files.get('task-approve').textContent,'选择要删除的文件');
  const folders=fixture(task([item('folder','delete','directory',{default_selected:false})]));
  assert.equal(folders.get('task-approve').textContent,'选择要删除的文件夹');assert.match(folders.get('task-simulation-effect').textContent,/内部文件/);
});

test('actual approval submits the unchanged plan version/IDs and retains simulation wording while pending',async()=>{
  let complete;
  const value=task([item('service','stop_service','service')]);
  const f=fixture(value,(path)=>path.endsWith('/approve')?new Promise(resolve=>{complete=resolve;}):Promise.resolve({}));
  await f.get('task-approve').fire('click');
  assert.deepEqual(f.requests,[{path:'/api/tasks/copy-task/approve',payload:{version:7,ids:['service']}}]);
  assert.equal(f.get('task-approve').disabled,true);assert.equal(f.get('task-approve').textContent,'模拟停止 1 个服务');
  complete({task:{...value,status:'paused',plan:null,updated_at:'second'}});await flush();assert.equal(f.requests.length,1);
});

test('actual direct-action expansion names deletion and preserves signature/preparation/approval checks',async()=>{
  const folder=item('folder','delete','directory',{actions:['delete'],risk:'review'});
  const advice={...folder,conclusion:'review',purpose:'用途说明'};
  const value=task([],{status:'paused',plan:null,advice:[advice],advice_total:1});
  const f=fixture(value,async(path)=>{
    if(path.includes('/advice?'))return {items:[advice],total:1,has_more:false};
    if(path.startsWith('/api/item?'))return {item:folder};
    if(path.endsWith('/direct-plan'))return {task:{...value,status:'awaiting_review',plan:{version:11,items:[folder]}},plan:{version:11,items:[folder]}};
    if(path.endsWith('/approve'))return {task:{...value,updated_at:'done'}};
    throw new Error('unexpected fixture request');
  });
  const adviceDetails=f.root.all().find(node=>node.tag==='details'&&node.querySelector('summary')?.textContent.startsWith('用途分析'));
  adviceDetails.open=true;await adviceDetails.fire('toggle');
  const direct=f.root.querySelector('.task-direct');assert.equal(direct.querySelector('summary').textContent,'选择要模拟的操作');
  direct.open=true;await direct.fire('toggle');assert.match(direct.textContent,/不会实际删除.*包含内部文件/);
  const button=direct.querySelector('button');assert.equal(button.textContent,'模拟删除文件夹');await button.fire('click');
  assert.deepEqual(f.requests.filter(call=>call.path.endsWith('/direct-plan')),[{path:'/api/tasks/copy-task/direct-plan',payload:{ids:['folder'],action:'delete',snapshots:{folder:'signature-folder'}}}]);
  assert.deepEqual(f.requests.filter(call=>call.path.endsWith('/approve')),[{path:'/api/tasks/copy-task/approve',payload:{version:11,ids:['folder']}}]);
});

test('actual result status says simulation completed and never claims files were deleted',()=>{
  const f=fixture(task([],{status:'paused',plan:null,last_result:{total:1,succeeded:1,items:[{...item('one'),status:'succeeded',message:'只修改本地模拟记录。'}]}}));
  assert.equal(f.root.querySelector('.task-result-state').textContent,'模拟完成');assert.match(f.root.querySelector('.task-results').textContent,/实际文件和系统未改动/);
  assert.equal(f.root.querySelector('.task-result-state').dataset.tone,'info');
});
