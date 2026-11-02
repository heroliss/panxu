'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{join}=require('node:path'),{runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','duplicates.js'),'utf8');
const flush=()=>new Promise(setImmediate);
function fixture(){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.attrs={};this.parent=null;this._text='';this.hidden=false;this.disabled=false;this.value='';}
    get textContent(){return this._text+this.children.map(x=>x.textContent).join('');}set textContent(x){this._text=String(x);this.children=[];}
    get childNodes(){return this.children;}get isConnected(){return this===root||!!this.parent?.isConnected;}
    append(...items){items.forEach(x=>{x.parent=this;this.children.push(x);});}replaceChildren(){this.children.forEach(x=>x.parent=null);this.children=[];this._text='';}
    setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,fn){this.handlers[k]=fn;}
    async fire(k='click'){if(!this.disabled)await this.handlers[k]?.({});await flush();}
    all(){return [this,...this.children.flatMap(x=>x.all())];}
  }
  const root=new Element('main'),window={},timers=new Map(),calls=[];let next=0,back=null,job={id:'owned-job',status:'running',progress:{phase:'hashing',files_seen:4,bytes_read:16}};
  runInNewContext(source,{window,document:{createElement:tag=>new Element(tag)},setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)});
  const helpers={defaultPath:'E:/owned',bytes:n=>n+' B',heading:(title,subtitle)=>{const h=new Element('h1');h.textContent=title+' '+subtitle;return h;},setContextBack:(label,fn)=>{back={label,fn};},api:async(path,body)=>{calls.push([path,body]);if(path==='/api/duplicates/scan')return {job_id:'owned-job'};if(path.endsWith('/cancel'))return {job:{status:'running'}};return job;},redraw(){root.replaceChildren();window.PanxuDuplicates.render(root,helpers);}};
  helpers.redraw();const find=label=>root.all().find(x=>x.tag==='button'&&x.textContent===label),tick=async()=>{const f=[...timers.values()][0];timers.clear();await f?.();await flush();};
  return {root,subject:window.PanxuDuplicates,helpers,calls,find,tick,setJob:value=>{job=value;},back:()=>back,timers};
}
const result=()=>({source:'local_duplicate_scan',algorithm:'sha256',partial:false,status:'completed',bytes_read:16,roots:['E:/owned'],limits:{max_bytes:2*1024**3},summary:{content_groups:1,hardlink_groups:1,checked_files:4,total_groups:2,returned_groups:2},groups:[{group_id:'content-one',type:'content',size_bytes:8,verified:true,sha256:'a'.repeat(64),items_total:2,items:[{id:'a',name:'<script>.txt',path:'E:/owned/<script>.txt'},{id:'b',name:'b.txt',path:'E:/owned/b.txt'}]},{group_id:'link-one',type:'hardlink',verified:true,size_bytes:8,items_total:2,items:[{id:'l1',name:'l1',path:'E:/owned/l1'},{id:'l2',name:'l2',path:'E:/owned/l2'}]}],warnings:[]});

test('local duplicate start never invokes a model or delete plan, and cannot double submit',async()=>{
 const f=fixture();await f.find('开始本地查重').fire();assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0])),['/api/duplicates/scan',{paths:['E:/owned'],max_bytes:2*1024**3}]);
 assert.equal(f.find('正在检查…').disabled,true);await f.find('正在检查…').fire();assert.equal(f.calls.length,1);assert.equal(f.root.all().some(x=>x.tag==='details'),false);
});
test('leaving the page pauses polling without cancelling the owned job, and reentry resumes it',async()=>{
 const f=fixture();await f.find('开始本地查重').fire();f.subject.unmount();assert.equal(f.timers.size,0);assert.equal(f.calls.some(x=>x[0].endsWith('/cancel')),false);
 f.helpers.redraw();await f.tick();assert.equal(f.calls.at(-1)[0],'/api/jobs/owned-job');await f.find('停止检查').fire();assert.equal(f.calls.at(-1)[0],'/api/jobs/owned-job/cancel');
});
test('completed result uses group pages and explicit back navigation, with filename text kept literal',async()=>{
 const f=fixture();await f.find('开始本地查重').fire();f.setJob({id:'owned-job',status:'completed',result:result()});await f.tick();
 assert.match(f.root.textContent,/共享文件实体/);await f.find('查看这一组').fire();assert.equal(f.back().label,'返回重复结果');assert.match(f.root.textContent,/<script>\.txt/);assert.equal(f.root.all().some(x=>x.tag==='script'||x.tag==='details'),false);
 f.back().fn();assert.ok(f.find('检查说明'));await f.find('检查说明').fire();assert.match(f.root.textContent,/不逐字节比较/);f.back().fn();assert.ok(f.find('重新检查'));
 assert.equal(f.calls.some(x=>/tasks|assistant|plan|delete/.test(x[0])),false);
});
test('partial and truncated data report genuine totals rather than treating a returned page as the full result',async()=>{
 const f=fixture();await f.find('开始本地查重').fire();const r=result();r.partial=true;r.result_truncated=true;r.summary.total_groups=250;r.summary.returned_groups=2;
 f.setJob({id:'owned-job',status:'completed',result:r});await f.tick();assert.match(f.root.textContent,/显示 2 组，已确认共 250 组/);assert.match(f.root.textContent,/部分路径未展示/);
});
test('result-first layout can restart without losing the job transition or the prior complete result',async()=>{
 const f=fixture();await f.find('开始本地查重').fire();f.setJob({id:'owned-job',status:'completed',result:result()});await f.tick();
 assert.ok(f.find('更换位置'));assert.equal(f.root.all().some(x=>x.tag==='textarea'),false);
 await f.find('重新检查').fire();assert.equal(f.calls.filter(x=>x[0]==='/api/duplicates/scan').length,2);assert.match(f.root.textContent,/上次检查结果/);assert.ok(f.find('停止检查'));
});
