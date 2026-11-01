const assert=require('node:assert/strict');
const factory=require('../web/icons.js');

function fixture(fetch,fastRetry=false){
  const nodes=[],observers=[];
  class Node{
    constructor(tag){this.tag=tag;this.children=[];this.attrs={};this.hidden=false;this.isConnected=false;}
    setAttribute(k,v){this.attrs[k]=v;}
    append(...children){this.children.push(...children);for(const child of children)child.parent=this;}
    contains(other){return this===other||this.children.some(child=>child.contains(other));}
    set src(value){this._src=value;queueMicrotask(()=>this.onload?.());}
    get src(){return this._src;}
  }
  const doc={documentElement:{},createElement:tag=>{const node=new Node(tag);nodes.push(node);return node;},createElementNS:(_,tag)=>new Node(tag),querySelectorAll:()=>nodes.filter(node=>node.className==='item-icon'&&node.isConnected)};
  const env={document:doc,fetch,btoa:value=>Buffer.from(value,'binary').toString('base64'),AbortController,queueMicrotask,
    setTimeout:(fn,ms)=>fastRetry&&ms<1500?setTimeout(fn,0):setTimeout(fn,ms),clearTimeout,
    addEventListener(){},MutationObserver:class{observe(){}disconnect(){}},
    IntersectionObserver:class{constructor(callback){this.callback=callback;this.nodes=new Set();observers.push(this);}observe(node){this.nodes.add(node);}unobserve(node){this.nodes.delete(node);}disconnect(){this.nodes.clear();}}};
  const api=factory(env);
  return {api,nodes,mount(node){node.isConnected=true;api.refresh();},unmount(node){node.isConnected=false;api.refresh();},visible(){for(const observer of observers)observer.callback([...observer.nodes].map(target=>({target,isIntersecting:true})));}};
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
function response(status='ready',valid=true){
  const bytes=new Uint8Array(24);bytes.set([137,80,78,71,13,10,26,10]);new DataView(bytes.buffer).setUint32(16,valid?32:64);new DataView(bytes.buffer).setUint32(20,32);
  return {ok:true,headers:{get:name=>({'X-Panxu-Icon-Status':status,'Content-Type':'image/png','Content-Length':'24'}[name]||'')},arrayBuffer:async()=>bytes.buffer};
}

(async()=>{
  {
    let calls=0;const f=fixture(async()=>{calls++;return response();});
    const host=f.api.create({id:'software-one',signature:'v1'});
    assert.equal(host.attrs['aria-hidden'],'true');assert.equal(host.children[1].alt,'');assert.equal(host.children[1].loading,'eager');
    assert.equal(host.attrs['data-icon-kind'],'application');
    assert.equal(f.api.create({kind:'directory'}).attrs['data-icon-kind'],'directory');
    assert.equal(f.api.create({id:'service-test'}).attrs['data-icon-kind'],'service');
    await tick();assert.equal(calls,0,'detached icons must not request');
    f.mount(host);await tick();assert.equal(calls,0,'outside viewport must not request');
    f.visible();await tick();assert.equal(calls,1);assert.equal(host.children[1].hidden,false);assert.equal(host.children[0].hidden,true);
    f.unmount(host);const next=f.api.create({id:'software-one',signature:'v1'});f.mount(next);await tick();f.visible();await tick();assert.equal(calls,1,'rerender should use LRU');
    const changed=f.api.create({id:'software-one',signature:'v2'});f.mount(changed);await tick();f.visible();await tick();assert.equal(calls,2,'revision invalidates key');
    f.api.close();
  }
  {
    let calls=0;const f=fixture(async()=>{calls++;return response();});
    const absent=f.api.create({name:'history entry'}),invalid=f.api.create({id:'https://example.com/evil'});
    f.mount(absent);f.mount(invalid);await tick();f.visible();await tick();assert.equal(calls,0);f.api.close();
  }
  {
    const calls=[];let aborted=0;
    const f=fixture((url,options)=>new Promise((resolve,reject)=>{calls.push(url);options.signal.addEventListener('abort',()=>{aborted++;reject(new Error('aborted'));},{once:true});}));
    const hosts=[];for(let n=0;n<300;n++){const node=f.api.create({id:'file-'+n,signature:'one'});hosts.push(node);f.mount(node);}
    await tick();f.visible();await tick();
    assert.equal(calls.length,4);assert.equal(f.api._stats().active,4);assert.ok(f.api._stats().queued<=48);
    for(const host of hosts)f.unmount(host);await tick();await tick();
    assert.equal(aborted,4);assert.equal(calls.length,4,'detached queue must not request');assert.equal(f.api._stats().jobs,0);assert.equal(f.api._stats().watched,0);f.api.close();
  }
  {
    let calls=0;const f=fixture(async()=>{calls++;return response('pending');},true);
    const host=f.api.create({id:'process-pending'});f.mount(host);await tick();f.visible();
    for(let n=0;n<12;n++)await tick();
    assert.equal(calls,4,'initial + at most three retries');assert.equal(f.api._stats().jobs,0);assert.equal(host.children[0].hidden,false);f.api.close();
  }
  {
    let calls=0;const f=fixture(async()=>{calls++;return response('ready',false);});
    const host=f.api.create({id:'file-invalid-png'});f.mount(host);await tick();f.visible();await tick();
    assert.equal(host.children[1].hidden,true);assert.equal(host.children[0].hidden,false);assert.equal(calls,1);f.api.close();
  }
  {
    const f=fixture(async()=>response());const host=f.api.create({id:'file-valid'});f.mount(host);await tick();f.visible();await tick();
    host.children[1].onerror();assert.equal(host.children[1].hidden,true);assert.equal(host.children[0].hidden,false);f.api.close();
  }
  console.log('icons: mounted visibility, cache/revision, queue, abort, retry and fallback passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
