(function (factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  if (typeof window !== 'undefined' && window.document) window.PanxuIcons = factory(window);
})(function createIcons(env) {
  'use strict';
  const doc=env.document, records=new WeakMap(), watched=new Set(), jobs=new Map(), cache=new Map();
  const queue=[], maxActive=4, maxQueue=48, maxCache=256, maxBytes=2*1024*1024;
  let active=0, cacheBytes=0, scheduled=false, closed=false;
  const now=()=>Date.now();
  const later=(callback,delay)=>env.setTimeout(callback,delay);
  const clear=(timer)=>env.clearTimeout(timer);

  function cacheGet(key) {
    const entry=cache.get(key);
    if(!entry)return null;
    if(entry.expires<now()){cache.delete(key);cacheBytes-=entry.data?.length||0;return null;}
    cache.delete(key);cache.set(key,entry);return entry;
  }
  function cacheSet(key,data) {
    const old=cache.get(key);if(old)cacheBytes-=old.data?.length||0;
    cache.delete(key);cache.set(key,{data,expires:now()+(data?300000:20000)});cacheBytes+=data?.length||0;
    while(cache.size>maxCache||cacheBytes>maxBytes){const first=cache.keys().next().value,entry=cache.get(first);cache.delete(first);cacheBytes-=entry.data?.length||0;}
  }
  function fallback(record) {record.img.hidden=true;record.svg.hidden=false;}
  function apply(record,data) {
    if(!record.host.isConnected)return;
    if(!data){fallback(record);return;}
    record.img.onload=()=>{if(record.host.isConnected){record.img.hidden=false;record.svg.hidden=true;}};
    record.img.onerror=()=>{record.img.onerror=null;fallback(record);};
    record.img.src=data;
  }
  function finish(job,data) {
    if(jobs.get(job.key)!==job)return;
    jobs.delete(job.key);cacheSet(job.key,data);
    for(const record of job.subscribers){record.job=null;apply(record,data);}
    job.subscribers.clear();
  }
  function remove(record) {
    watched.delete(record);observer?.unobserve(record.host);record.requested=false;
    const job=record.job;record.job=null;
    if(!job)return;
    job.subscribers.delete(record);
    if(!job.subscribers.size){job.cancelled=true;job.controller?.abort();if(job.timer)clear(job.timer);jobs.delete(job.key);}
  }
  function pngData(bytes) {
    if(bytes.length<24||bytes.length>32768||[137,80,78,71,13,10,26,10].some((n,i)=>bytes[i]!==n))return null;
    const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    if(view.getUint32(16)!==32||view.getUint32(20)!==32)return null;
    let raw='';for(const byte of bytes)raw+=String.fromCharCode(byte);
    return 'data:image/png;base64,'+env.btoa(raw);
  }
  function pump() {
    if(closed)return;
    while(active<maxActive&&queue.length){
      const job=queue.shift();
      if(job.cancelled||jobs.get(job.key)!==job||!job.subscribers.size)continue;
      job.controller=new env.AbortController();active++;
      job.timeout=later(()=>job.controller.abort(),1800);
      Promise.resolve().then(()=>env.fetch('/api/icon?id='+encodeURIComponent(job.id)+'&v='+encodeURIComponent(job.version),{signal:job.controller.signal,credentials:'same-origin',cache:'no-store',headers:{Accept:'image/png,image/svg+xml'}})).then(async response=>{
        if(job.cancelled||closed)return;
        const status=response.headers.get('X-Panxu-Icon-Status');
        if(status==='pending'&&job.tries<3){
          job.tries++;
          job.timer=later(()=>{job.timer=null;if(!job.cancelled&&!closed&&job.subscribers.size){queue.push(job);pump();}},Math.min(1400,400*job.tries));
          return;
        }
        if(!response.ok||status!=='ready'||!String(response.headers.get('Content-Type')||'').startsWith('image/png')){finish(job,null);return;}
        const length=Number(response.headers.get('Content-Length')||0);
        if(length>32768){finish(job,null);return;}
        const bytes=new Uint8Array(await response.arrayBuffer());
        if(!job.cancelled&&!closed)finish(job,pngData(bytes));
      }).catch(()=>{if(!job.cancelled&&!closed)finish(job,null);}).finally(()=>{clear(job.timeout);active--;pump();});
    }
  }
  function request(record) {
    if(closed||!record.host.isConnected||!record.id||record.requested)return;
    record.requested=true;
    const hit=cacheGet(record.key);if(hit){apply(record,hit.data);return;}
    let job=jobs.get(record.key);
    if(!job){
      if(queue.length>=maxQueue){fallback(record);return;}
      job={key:record.key,id:record.id,version:record.version,subscribers:new Set(),tries:0};jobs.set(record.key,job);queue.push(job);
    }
    record.job=job;job.subscribers.add(record);pump();
  }
  const observer=env.IntersectionObserver?new env.IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){const record=records.get(entry.target);if(record)request(record);}}, {rootMargin:'100px'}):null;
  function scan() {
    scheduled=false;if(closed)return;
    for(const record of [...watched])if(!record.host.isConnected)remove(record);
    for(const host of doc.querySelectorAll('.item-icon[data-panxu-icon]')){
      const record=records.get(host);if(!record||watched.has(record))continue;
      watched.add(record);
      if(observer)observer.observe(host);else request(record);
    }
  }
  function refresh() {if(!scheduled&&!closed){scheduled=true;(env.queueMicrotask||((callback)=>Promise.resolve().then(callback)))(scan);}}
  const mutations=env.MutationObserver?new env.MutationObserver(refresh):null;
  if(mutations&&doc.documentElement)mutations.observe(doc.documentElement,{childList:true,subtree:true});

  function create(item={}) {
    const host=doc.createElement('span');host.className='item-icon';host.setAttribute('aria-hidden','true');host.setAttribute('data-panxu-icon','');
    const supplied=typeof item.kind==='string'?item.kind:typeof item.id==='string'?item.id.split('-')[0]:'';
    const kind=supplied==='directory'?'directory':supplied==='service'?'service':['software','process','startup'].includes(supplied)?'application':'file';
    host.setAttribute('data-icon-kind',kind);
    const svg=doc.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 32 32');svg.setAttribute('focusable','false');
    const background=doc.createElementNS('http://www.w3.org/2000/svg','rect');
    for(const [key,value]of Object.entries({x:'4',y:'4',width:'24',height:'24',rx:'6',fill:'#eef2f6'}))background.setAttribute(key,value);
    const glyph=doc.createElementNS('http://www.w3.org/2000/svg','path');
    const paths={directory:'M7 11h7l2 2h9v11H7z M7 13v-3h7l2 2',application:'M8 9h16v15H8z M8 13h16 M11 11h1 M15 11h1',service:'M13 8h6l1 3 3 1 1 5-3 2-1 4h-6l-1-3-3-1-1-5 3-2z M19 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0',file:'M11 9h7l4 4v10H11z M18 9v5h4 M14 18h5 M14 21h4'};
    for(const [key,value]of Object.entries({d:paths[kind],fill:'none',stroke:'#68788d','stroke-width':'1.5','stroke-linejoin':'round'}))glyph.setAttribute(key,value);
    svg.append(background,glyph);
    const img=doc.createElement('img');img.alt='';img.width=32;img.height=32;img.loading='eager';img.decoding='async';img.referrerPolicy='no-referrer';img.draggable=false;img.hidden=true;
    host.append(svg,img);
    const id=typeof item.id==='string'&&/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(item.id)?item.id:'';
    const version=typeof item.signature==='string'?item.signature.slice(0,160):'';
    const record={host,svg,img,id,version,key:id+'|'+version,requested:false,job:null};records.set(host,record);
    refresh();return host;
  }
  function dispose(root) {for(const record of [...watched])if(record.host===root||root?.contains?.(record.host))remove(record);}
  function close() {closed=true;mutations?.disconnect();observer?.disconnect();for(const record of [...watched])remove(record);queue.length=0;cache.clear();cacheBytes=0;}
  env.addEventListener?.('pagehide',close,{once:true});
  return {create,refresh,dispose,close,_stats:()=>({active,queued:queue.filter(job=>!job.cancelled).length,jobs:jobs.size,cached:cache.size,cacheBytes,watched:watched.size})};
});
