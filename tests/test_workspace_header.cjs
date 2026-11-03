'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');

// Exercise production header composition with real-node ownership semantics.
// Actual positioning/occlusion is checked in a browser and native window; this
// deliberately does not pretend a fake DOM can prove CSS sticky behaviour.
function fixture({observe=false}={}){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.parentNode=null;this.dataset={};this.attrs={};this.handlers=new Map();this.style={setProperty(name,value){this[name]=String(value);},getPropertyValue(name){return this[name]||'';}};this.className='';this._text='';this.hidden=false;this.disabled=false;this.value='';this.scrollTop=0;this.rect={top:0,bottom:0,height:0};}
    get parentElement(){return this.parentNode;}
    get isConnected(){return this.id==='workspace'||Boolean(this.parentNode?.isConnected);}
    getBoundingClientRect(){return {...this.rect};}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    set textContent(value){this.replaceChildren();this._text=String(value);}
    get firstChild(){return this.children[0]||null;}
    get childNodes(){return this.children;}
    get classList(){const self=this;return {add(...names){self.className=[...new Set([...self.className.split(' ').filter(Boolean),...names])].join(' ');},remove(...names){self.className=self.className.split(' ').filter(name=>!names.includes(name)).join(' ');},contains(name){return self.className.split(' ').includes(name);},toggle(name,force){const add=force===undefined?!this.contains(name):force;add?this.add(name):this.remove(name);return add;}};}
    append(...children){for(let child of children){if(child==null)continue;if(typeof child==='string')child=new Text(child);child.remove();child.parentNode=this;this.children.push(child);}}
    appendChild(child){this.append(child);return child;}
    replaceChildren(...children){for(const child of this.children)child.parentNode=null;this.children=[];this._text='';this.append(...children);}
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
    setAttribute(name,value){this.attrs[name]=String(value);}
    getAttribute(name){return this.attrs[name]??null;}
    removeAttribute(name){delete this.attrs[name];}
    addEventListener(name,callback){this.handlers.set(name,[...(this.handlers.get(name)||[]),callback]);}
    fire(name='click'){if(!this.disabled)for(const callback of this.handlers.get(name)||[])callback({target:this,preventDefault(){}});}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    contains(child){return child===this||this.all().includes(child);}
    querySelectorAll(selector){return this.all().filter(child=>{if(selector.startsWith('.'))return child.classList.contains(selector.slice(1));if(selector.startsWith('[')){const match=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);return match&&Object.hasOwn(child.attrs,match[1])&&(match[2]===undefined||child.attrs[match[1]]===match[2]);}return child.tag===selector;});}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
    set innerHTML(_){throw new Error('Content must remain literal; cloning loses live control state.');}
  }
  class Text extends Element{constructor(value){super('#text');this._text=String(value);}}
  const observers=[];
  class Observer{
    constructor(callback){this.callback=callback;this.targets=[];this.disconnected=false;observers.push(this);}
    observe(target){this.targets.push(target);}
    disconnect(){this.disconnected=true;this.targets=[];}
    fire(){if(!this.disconnected)this.callback();}
  }
  const window={},document={activeElement:null,createElement:tag=>new Element(tag),createTextNode:value=>new Text(value)};
  const source=readFileSync(join(__dirname,'..','web','workspace-header.js'),'utf8');
  runInNewContext(source,{window,document,...(observe?{ResizeObserver:Observer}:{})});
  return {api:window.PanxuWorkspaceHeader,Element,document,observers,make:(tag,text)=>{const n=new Element(tag);if(text!==undefined)n.textContent=text;return n;}};
}

test('page header keeps a single readable title and existing action controls without firing them',()=>{
  const {api,make}=fixture(),calls=[],action=make('button','刷新');action.disabled=true;action.addEventListener('click',()=>calls.push('refresh'));
  const header=api.create('后台与软件','了解开机启动与后台。',[action]);
  assert.equal(header.tag,'header');assert.ok(header.classList.contains('workspace-page-header'));assert.ok(header.classList.contains('page-heading'));
  assert.equal(header.querySelectorAll('h1').length,1);assert.equal(header.querySelector('h1').textContent,'后台与软件');
  assert.ok(header.textContent.includes('了解开机启动与后台。'));assert.ok(header.contains(action));assert.equal(action.disabled,true);assert.deepEqual(calls,[]);
});

test('moving live category and filter controls into named slots preserves identity, values and callbacks',()=>{
  const {api,make}=fixture(),legacy=make('section'),category=make('div'),button=make('button','后台服务'),search=make('input'),calls=[];
  search.value='保留搜索文字';button.setAttribute('aria-pressed','true');button.addEventListener('click',()=>calls.push('service'));category.append(button);legacy.append(category,search);
  const header=api.create('后台与软件','');const slot=api.set(header,'navigation',category);api.set(header,'controls',search);
  assert.ok(header.contains(category));assert.ok(slot.contains(category));assert.ok(header.contains(search));assert.equal(legacy.children.length,0);assert.equal(search.value,'保留搜索文字');assert.equal(button.getAttribute('aria-pressed'),'true');assert.deepEqual(calls,[]);
  button.fire();assert.deepEqual(calls,['service']);assert.equal(header.querySelectorAll('button').length,1);
});

test('replacing one slot and appending controls leave other live header controls intact',()=>{
  const {api,make}=fixture(),header=api.create('文件清理',''),first=make('input'),second=make('select'),navigation=make('div'),rating=make('select');
  first.value='old';second.value='modified';rating.value='4';api.set(header,'navigation',navigation);api.set(header,'controls',first);api.set(header,'controls',second);api.append(header,'controls',rating);
  assert.equal(header.contains(first),false);assert.equal(first.parentNode,null);assert.equal(header.contains(second),true);assert.equal(header.contains(rating),true);assert.equal(header.contains(navigation),true);assert.equal(second.value,'modified');assert.equal(rating.value,'4');
});

test('switch group decoration retains caller-owned pressed states and exact button order',()=>{
  const {api,make}=fixture(),group=make('div'),suggestions=make('button','已有建议'),all=make('button','全部项目'),calls=[];
  suggestions.setAttribute('aria-pressed','true');all.setAttribute('aria-pressed','false');all.addEventListener('click',()=>calls.push('all'));group.append(suggestions,all);
  api.switches(group,{kind:'view',label:'后台列表视图'});
  assert.equal(group.getAttribute('role'),'group');assert.equal(group.getAttribute('aria-label'),'后台列表视图');assert.deepEqual(group.children,[suggestions,all]);assert.equal(suggestions.getAttribute('aria-pressed'),'true');assert.equal(all.getAttribute('aria-pressed'),'false');assert.deepEqual(calls,[]);all.fire();assert.deepEqual(calls,['all']);
});

test('independent page headers keep titles and action ownership separate and text literal',()=>{
  const {api,make}=fixture(),action=make('button','返回'),first=api.create('文件清理','<img src=x onerror=alert(1)>',[action]),second=api.create('清理工具','工具说明');
  api.set(second,'controls',make('input'));assert.equal(first.querySelector('h1').textContent,'文件清理');assert.equal(second.querySelector('h1').textContent,'清理工具');assert.ok(first.textContent.includes('<img src=x onerror=alert(1)>'));assert.equal(first.querySelectorAll('img').length,0);assert.ok(first.contains(action));assert.equal(second.contains(action),false);
});

test('one measured-header observer is replaced and disposed without retaining previous page roots',()=>{
  const {api,make,observers}=fixture({observe:true}),workspace=make('main');workspace.id='workspace';
  const first=api.create('后台与软件','');assert.equal(observers.length,1);assert.deepEqual(observers[0].targets,[first]);
  observers[0].fire();assert.equal(workspace.style.getPropertyValue('--workspace-header-height'),'');
  workspace.append(first);first.rect.height=184.5;observers[0].fire();assert.equal(workspace.style.getPropertyValue('--workspace-header-height'),'184.5px');
  first.rect.height=246;observers[0].fire();assert.equal(workspace.style.getPropertyValue('--workspace-header-height'),'246px');
  const second=api.create('文件清理','');assert.equal(observers.length,2);assert.equal(observers[0].disconnected,true);assert.deepEqual(observers[0].targets,[]);
  workspace.replaceChildren(second);second.rect.height=192;observers[1].fire();assert.equal(workspace.style.getPropertyValue('--workspace-header-height'),'192px');
  api.dispose();assert.equal(observers[1].disconnected,true);api.dispose();assert.equal(observers.length,2);
});

test('explicit list reveal uses current measured header edge without moving focus or assuming a fixed height',()=>{
  const {api,make,document}=fixture(),workspace=make('main');workspace.id='workspace';workspace.scrollTop=900;
  const header=api.create('后台与软件',''),target=make('section'),search=make('input');workspace.append(header,target);api.set(header,'controls',search);document.activeElement=search;
  header.rect={top:43,bottom:282,height:239};target.rect={top:340,bottom:700,height:360};
  assert.equal(api.reveal(header,target),true);assert.equal(workspace.scrollTop,946);assert.equal(document.activeElement,search);
  header.rect={top:43,bottom:430,height:387};target.rect={top:452,bottom:800,height:348};
  assert.equal(api.reveal(header,target),true);assert.equal(workspace.scrollTop,956);assert.equal(document.activeElement,search);
  workspace.scrollTop=10;target.rect.top=0;assert.equal(api.reveal(header,target),true);assert.equal(workspace.scrollTop,0);
});

test('detached headers and foreign-page reveal targets cannot scroll the current workspace',()=>{
  const {api,make}=fixture(),workspace=make('main');workspace.id='workspace';workspace.scrollTop=600;
  const header=api.create('文件清理',''),target=make('section');workspace.append(header,target);
  const foreign=make('main');foreign.id='workspace';const elsewhere=make('section');foreign.append(elsewhere);
  assert.equal(api.reveal(header,elsewhere),false);assert.equal(workspace.scrollTop,600);
  header.remove();assert.equal(api.reveal(header,target),false);assert.equal(workspace.scrollTop,600);
});
