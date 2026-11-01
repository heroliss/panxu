'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const app=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
const tasks=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');

class Element {
  constructor(tag,cls='',value=''){this.tag=tag;this.className=cls;this.textContent=value;this.children=[];this.dataset={};this.hidden=false;this.disabled=false;}
  append(...nodes){for(const node of nodes.flat()){if(node){node.parent=this;this.children.push(node);}}}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  all(){return [this,...this.children.flatMap(node=>node.all())];}
  querySelector(selector){return this.all().find(node=>selector.startsWith('.')?node.className.split(' ').includes(selector.slice(1)):node.tag===selector);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
  addEventListener(name,handler){this.handlers=this.handlers || new Map();this.handlers.set(name,handler);}
  set innerHTML(_){throw Error('copy must remain literal text');}
}

function fixture(items=[]){
  const nodes=new Map(['plan-content','plan-execute','plan-title','plan-dialog'].map(id=>[id,new Element('div')]));
  const state={plan:{id:'plan-fixture',items},items,history:[],selected:new Set(),busy:false};
  const calls=[],notices=[],toasts=[];
  const el=(tag,cls='',value='')=>new Element(tag,cls,value),append=(parent,...children)=>{parent.append(...children);return parent;};
  const context={window:{},document:{createElement:tag=>el(tag)},state,$:id=>nodes.get(id),el,append,
    itemTitle:(_,title)=>title,advice:()=>({}),cleanCopy:value=>String(value),
    heading:(title,description)=>append(el('div'),el('h1','',title),el('p','',description)),
    empty:(title,description)=>append(el('div'),el('h2','',title),el('p','',description)),
    badge:value=>el('span','source-badge',value),button:(value,handler,cls)=>{const node=el('button',cls,value);node.click=handler;return node;},
    renderSelection:()=>{},notify:(message)=>notices.push(message),toast:message=>toasts.push(message),
    accept:()=>{},render:()=>{},setView:()=>{},canSelect:()=>true,
    api:async(path,payload)=>{calls.push({path,payload});throw Error('fixture rejection');},Date,Set};
  const helperEnd=tasks.indexOf('// Task UI owns presentation');assert.ok(helperEnd>0);
  runInNewContext(tasks.slice(0,helperEnd),context);
  context.window.PanxuTasks={renderHistory:()=>{}};
  const taskStart=app.indexOf('  async function task('),taskEnd=app.indexOf('  function heading(',taskStart);
  const start=app.indexOf('  function planActionCopy('),end=app.indexOf('  function settingsPayload(',start);
  assert.ok(start>0&&end>start&&taskEnd>taskStart);
  runInNewContext(app.slice(taskStart,taskEnd)+app.slice(start,end)+';this.subject={renderPlan,executePlan,renderHistory};',context);
  return {state,nodes,context,calls,notices,toasts,subject:context.subject,text:node=>node.all().map(item=>item.textContent).join(' ')};
}

const item=(action,kind='file',name='fixture')=>({id:action+'-'+kind,action,kind,name});

test('legacy confirmation names every concrete action and retains simulation boundary',()=>{
  for(const [action,kind,label] of [['delete','file','删除'],['delete','directory','删除'],['disable_startup','startup','关闭'],
    ['stop_service','service','停止'],['stop_process','process','结束'],['uninstall','software','卸载']]){
    const f=fixture([item(action,kind)]);f.subject.renderPlan(f.state.plan);
    assert.match(f.nodes.get('plan-execute').textContent,new RegExp('模拟'+label));
    const text=f.text(f.nodes.get('plan-content'));
    assert.ok(text.includes('仅记录模拟'));assert.ok(text.includes('不会实际删除文件或改动系统'));
    assert.ok(!text.includes('确认处理'));assert.ok(!f.nodes.get('plan-execute').disabled);
  }
});

test('mixed actions count selected items and show separate action counts',()=>{
  const f=fixture([item('delete'),item('delete','directory'),item('stop_service','service'),item('uninstall','software')]);
  f.subject.renderPlan(f.state.plan);
  assert.equal(f.nodes.get('plan-execute').textContent,'确认模拟 4 项');
  const text=f.text(f.nodes.get('plan-content'));
  for(const part of ['删除 1 个文件','删除 1 个文件夹','停止 1 个服务','卸载 1 个软件'])assert.ok(text.includes(part));
});

test('empty plans never retain a previous actionable confirmation label',()=>{
  const f=fixture([item('delete')]);f.subject.renderPlan(f.state.plan);f.subject.renderPlan({items:[]});
  assert.equal(f.nodes.get('plan-execute').textContent,'没有可模拟的操作');
  assert.equal(f.nodes.get('plan-execute').disabled,true);
  assert.equal(f.nodes.get('plan-title').textContent,'没有可模拟的操作');
});

test('execution rejection preserves plan and restores the precise action button',async()=>{
  const f=fixture([item('stop_service','service')]);f.subject.renderPlan(f.state.plan);
  await f.subject.executePlan();
  assert.equal(f.calls[0].path,'/api/execute');assert.equal(f.calls[0].payload.plan_id,'plan-fixture');
  assert.equal(f.state.plan.id,'plan-fixture');assert.equal(f.nodes.get('plan-execute').disabled,false);
  assert.equal(f.nodes.get('plan-execute').textContent,'模拟停止 1 个服务');
  assert.ok(f.text(f.nodes.get('plan-content')).includes('fixture rejection'));
  assert.equal(f.toasts.length,0);
});

test('history describes simulation and undo without claiming actual deletion or file recovery',()=>{
  const f=fixture(),root=new Element('main');
  f.state.history=[{id:'batch',status:'simulated',created_at:'2026-11-02',items:[item('delete')]}];
  f.subject.renderHistory(root);
  const text=f.text(root);
  assert.ok(text.includes('1 项模拟操作'));assert.ok(text.includes('实际释放空间为 0 B'));
  assert.ok(text.includes('不会恢复文件或系统设置'));assert.ok(root.all().some(node=>node.tag==='button'&&node.textContent==='撤销模拟'));
  assert.ok(!text.includes('处理了'));assert.ok(!text.includes('已删除'));assert.ok(!text.includes('删除成功'));
});

test('static confirmation defaults and reset copy match the simulation-only runtime',()=>{
  const html=readFileSync(join(__dirname,'..','web','index.html'),'utf8');
  assert.ok(html.includes('模拟模式：'));assert.ok(html.includes('不会实际改动文件或系统'));
  assert.ok(html.includes('撤销模拟状态'));assert.ok(html.includes('历史记录仍保留'));
  assert.ok(!html.includes('确认处理'));
});
