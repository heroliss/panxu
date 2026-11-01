'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const metadata={name:'盘序',subtitle:'AI 磁盘清理助手',version:'other-metadata-version',author:'Liss',
  repository_url:'https://github.com/heroliss/panxu',license_name:'PolyForm Noncommercial License 1.0.0',
  license_url:'https://github.com/heroliss/panxu/blob/main/LICENSE',description:'面向磁盘整理的 LLM Harness。',
  simulation:true,updates:{manual_check:true,source_state:'not_configured'}};

class Element {
  constructor(tag,className='',text=''){this.tag=tag;this.className=className;this._text=text;this.children=[];this.parent=null;this.root=false;this.open=false;this.dataset={};this.handlers=new Map();}
  get isConnected(){return this.root||Boolean(this.parent?.isConnected);}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  set textContent(value){this._text=String(value);this.children.forEach(child=>child.parent=null);this.children=[];}
  append(...children){children.forEach(child=>{child.parent=this;this.children.push(child);});}
  replaceChildren(...children){this.children.forEach(child=>child.parent=null);this._text='';this.children=[];this.append(...children);}
  setAttribute(name,value){this[name]=value;}
  addEventListener(name,handler){this.handlers.set(name,[...(this.handlers.get(name)||[]),handler]);}
  all(){return this.children.flatMap(child=>[child,...child.all()]);}
  matches(selector){return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tag===selector;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){return this.all().filter(child=>child.matches(selector));}
  async fire(name){await Promise.all((this.handlers.get(name)||[]).map(handler=>handler({preventDefault(){}})));await flush();}
  set innerHTML(_){throw new Error('about/update content must remain text');}
}

function fixture({info=metadata,response={status:'not_configured',message:'尚未提供公开更新源，目前不能比较版本。'},version='2.4.6-fixture'}={}){
  const root=new Element('main');root.root=true;const calls=[];let responder=()=>response;
  const state={version,items:[],settings:{custom_instructions:'existing preference'},summary:{},selected:new Set(),view:'model'};
  const context={state,URL,window:{},el:(tag,cls,text)=>new Element(tag,cls,text),
    append:(parent,...children)=>{parent.append(...children);return parent;},
    button:(label,handler,cls='button')=>{const value=new Element('button',cls,label);value.addEventListener('click',handler);return value;},
    api:async(path,payload)=>{calls.push({path,payload});if(path==='/api/about')return info;if(path==='/api/updates/check')return responder();throw new Error('unexpected request');},
  };
  const start=source.indexOf('  let aboutInfo='),end=source.indexOf('  function toggleDeveloperDock(',start);
  assert.ok(start>0&&end>start);runInNewContext(source.slice(start,end)+';this.renderAbout=renderAbout;',context);
  const render=(target=root)=>context.renderAbout(target);render();
  return {root,state,calls,context,render,setResponder:handler=>{responder=handler;},
    button:target=>(target||root).querySelector('button'),status:target=>(target||root).querySelector('.about-update-status')};
}

test('about uses the actual bootstrap version, keeps details closed and never checks updates on render/navigation',async()=>{
  const f=fixture();await flush();
  assert.match(f.root.querySelector('.about-identity').textContent,/版本 2\.4\.6-fixture.*作者 Liss/);
  assert.equal(f.root.querySelector('details').open,false);assert.ok(f.root.textContent.includes('LLM Harness'));
  assert.equal(f.calls.length,1);assert.equal(f.calls[0].path,'/api/about');assert.equal(f.status().textContent,'暂未提供公开更新源。');
  const second=new Element('main');second.root=true;f.render(second);await flush();assert.equal(f.calls.length,1,'cached local metadata does not initiate a remote check');
  assert.equal(f.state.settings.custom_instructions,'existing preference');
});

test('production bootstrap accept preserves its real version for about without a hardcoded UI release number',()=>{
  const f=fixture({version:''});const output=new Element('span');
  Object.assign(f.context,{defaultPathLoaded:false,itemMap:new Map(),unitCache:new Map(),protectedFiles:[],canSelect:()=>true,$:()=>output});
  const start=source.indexOf('  function accept(data,'),end=source.indexOf('  async function task(',start);
  assert.ok(start>0&&end>start);runInNewContext(source.slice(start,end)+';this.acceptBootstrap=accept;',f.context);
  f.context.acceptBootstrap({version:'7.8.9-backend'});assert.equal(f.state.version,'7.8.9-backend');
  f.context.acceptBootstrap({settings:{}});assert.equal(f.state.version,'7.8.9-backend','unrelated responses do not erase the current version');
});

test('manual check has one pending request, preserves drafts and renders only a verified release-page link',async()=>{
  let complete;const f=fixture();await flush();f.setResponder(()=>new Promise(resolve=>{complete=resolve;}));
  const draft=new Element('input');draft.value='unsaved-key-draft';f.root.append(draft);
  const request=f.button().fire('click');await flush();assert.equal(f.button().disabled,true);assert.equal(f.button()['aria-busy'],'true');assert.equal(f.status().textContent,'正在检查更新…');
  await f.button().fire('click');assert.equal(f.calls.filter(call=>call.path==='/api/updates/check').length,1);
  assert.deepEqual(Object.keys(f.calls.find(call=>call.path==='/api/updates/check').payload),[]);
  complete({status:'update_available',latest_version:'3.0.0',release_url:'https://github.com/heroliss/panxu/releases/tag/v3.0.0'});await request;
  const link=f.root.querySelector('.about-release').querySelector('a');assert.equal(link.href,'https://github.com/heroliss/panxu/releases/tag/v3.0.0');assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');
  assert.equal(f.button().disabled,false);assert.match(f.status().textContent,/发现新版本 3\.0\.0/);assert.equal(draft.value,'unsaved-key-draft');
  assert.equal(f.calls.filter(call=>call.path==='/api/updates/check').length,1,'no automatic download/install or follow-up request');
});

test('each check outcome is readable; absence of a public source is never reported as latest',async()=>{
  for(const [status,tone,phrase] of [['not_configured','neutral','不能比较'],['no_public_source','neutral','不能比较'],
    ['up_to_date','success','最新版本'],['current_ahead','neutral','无需降级'],['failed','danger','未完成']]){
    const f=fixture({response:{status}});await flush();await f.button().fire('click');
    assert.equal(f.status().dataset.tone,tone);assert.ok(f.status().textContent.includes(phrase),status);
    if(['not_configured','no_public_source'].includes(status))assert.ok(!f.status().textContent.includes('最新'));
  }
});

test('a failed check remains a manual retry and does not remove connection/settings drafts',async()=>{
  const f=fixture();await flush();f.setResponder(()=>{throw new Error('fixture offline');});await f.button().fire('click');
  assert.equal(f.button().textContent,'重新检查');assert.equal(f.status().dataset.tone,'danger');assert.match(f.status().textContent,/未完成.*fixture offline/);
  assert.equal(f.calls.filter(call=>call.path==='/api/updates/check').length,1);
  f.setResponder(()=>({status:'up_to_date'}));await f.button().fire('click');assert.equal(f.status().dataset.tone,'success');
  assert.equal(f.calls.filter(call=>call.path==='/api/updates/check').length,2);
});

test('late checks only update their own mounted about block across navigation',async()=>{
  let complete;const f=fixture();await flush();f.setResponder(()=>new Promise(resolve=>{complete=resolve;}));
  const oldStatus=f.status(),request=f.button().fire('click');await flush();
  const unrelated=new Element('input');unrelated.value='another-page-draft';f.root.replaceChildren(unrelated);
  const settings=new Element('main');settings.root=true;const key=new Element('input');key.value='still-unsaved';settings.append(key);f.render(settings);await flush();
  assert.equal(f.button(settings).disabled,true);complete({status:'up_to_date'});await request;await flush();
  assert.equal(oldStatus.textContent,'正在检查更新…','unmounted view is not rewritten');assert.match(f.status(settings).textContent,/最新版本/);
  assert.equal(unrelated.value,'another-page-draft');assert.equal(key.value,'still-unsaved');assert.equal(f.calls.filter(call=>call.path==='/api/updates/check').length,1);
});

test('metadata is plain text and external links cannot escape the fixed HTTPS GitHub repository',async()=>{
  const f=fixture({info:{...metadata,repository_url:'https://evil.example/heroliss/panxu',license_url:'javascript:alert(1)',description:'<img src=x onerror=alert(1)>'},
    response:{status:'update_available',release_url:'https://github.com/heroliss/panxu/releases/download/v3/binary.exe'}});await flush();await f.button().fire('click');
  assert.equal(f.root.querySelectorAll('a').length,0);assert.ok(f.root.textContent.includes('<img src=x onerror=alert(1)>'));
  for(const release_url of ['https://github.com/other/repo/releases/tag/v3','https://evil.example/releases/tag/v3',
    'http://github.com/heroliss/panxu/releases/tag/v3','https://user:password@github.com/heroliss/panxu/releases/tag/v3',
    'https://github.com/heroliss/panxu/releases/tag/v3?secret=x']){
    f.setResponder(()=>({status:'update_available',release_url}));await f.button().fire('click');assert.equal(f.root.querySelector('.about-release').querySelectorAll('a').length,0,release_url);
  }
});
