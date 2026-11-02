'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','stream-view.js'),'utf8');

function fixture({reduced=false,segmenter=true,options={}}={}) {
  const frames=new Map(),timers=new Map(),listeners=new Map(),motion=new Map();let frameId=0,timerId=0,clock=0;
  class Element {
    constructor(){this.children=[];this.dataset={};this.textContent='';this.ownerDocument=doc;this.handlers=new Map();this.className='';}
    setAttribute(name,value){this[name]=value;}
    replaceChildren(...children){this.children=children;}
    addEventListener(name,run){this.handlers.set(name,run);}
    fire(name){this.handlers.get(name)?.();}
    set innerHTML(_){throw new Error('stream content must never be parsed as HTML');}
  }
  const doc={hidden:false,createElement:()=>new Element(),addEventListener:(event,handler)=>listeners.set(event,handler),removeEventListener:event=>listeners.delete(event)};
  const media={matches:reduced,addEventListener:(event,handler)=>motion.set(event,handler),removeEventListener:event=>motion.delete(event)};
  const window={performance:{now:()=>clock},matchMedia:()=>media,
    setTimeout:(run,delay)=>{timers.set(++timerId,{run,at:clock+delay});return timerId;},clearTimeout:id=>timers.delete(id),
    requestAnimationFrame:handler=>{frames.set(++frameId,handler);return frameId;},cancelAnimationFrame:id=>frames.delete(id)};
  runInNewContext(source,{window,document:doc,Intl:segmenter?Intl:{},Date});
  const host=new Element(),subject=window.PanxuStreamView.create(host,options);
  const body=host.children[0],notice=host.children[1];
  return {host,body,notice,subject,frames,timers,listeners,motion,doc,media,activity(){const host=new Element();return {host,reader:window.PanxuStreamView.createActivity(host)};},
    tick(ms=16){clock+=ms;const scheduled=[...frames];frames.clear();scheduled.forEach(([,handler])=>handler(clock));for(const [id,timer] of [...timers])if(timer.at<=clock){timers.delete(id);timer.run();}},
    settle(){for(let i=0;i<160&&frames.size;i++)this.tick();assert.equal(frames.size,0,'animation must converge in bounded time');},
  };
}

test('append-only cumulative snapshots reveal whole Unicode graphemes monotonically and exactly',()=>{
  const f=fixture();const text='甲👨‍👩‍👧‍👦e\u0301🇨🇳乙'.repeat(12);
  const boundaries=new Set(['',...Array.from(new Intl.Segmenter(undefined,{granularity:'grapheme'}).segment(text),part=>text.slice(0,part.index+part.segment.length))]);
  f.subject.update({text,status:'streaming'});let previous=0;
  for(let i=0;i<20;i++){f.tick();assert.ok(boundaries.has(f.body.textContent),'no partial combining/emoji sequence');assert.ok(f.body.textContent.length>=previous);previous=f.body.textContent.length;}
  const extended=text+'公开结论';f.subject.update({text:extended,status:'streaming'});f.settle();assert.equal(f.body.textContent,extended);
  assert.equal(f.notice.textContent,'正在接收回复…');assert.equal(f.host['aria-busy'],'true');
});

test('completed text converges quickly and preserves every final character',()=>{
  const f=fixture({options:{maxLagGraphemes:8000}});const text='收到的公开内容'.repeat(900);
  f.subject.update({text,status:'streaming'});f.tick();assert.notEqual(f.body.textContent,text);
  f.subject.update({text,status:'completed'});for(let i=0;i<9;i++)f.tick();
  assert.equal(f.body.textContent,text);assert.equal(f.frames.size,0);assert.equal(f.notice.hidden,true);
});

test('non-prefix revision replaces stale visible content immediately instead of retaining incorrect text',()=>{
  const f=fixture();f.subject.update({text:'未经最终修订的文字'.repeat(20),status:'streaming'});f.settle();
  f.subject.update({text:'最终公开文本',status:'streaming'});
  assert.equal(f.body.textContent,'最终公开文本');assert.equal(f.frames.size,0);
  f.subject.update({text:'最终公开文本继续',status:'streaming'});f.settle();assert.equal(f.body.textContent,'最终公开文本继续');
});

test('reduced motion and hidden windows immediately catch up and do not keep animation frames alive',()=>{
  const f=fixture();const text='可读的流式回复'.repeat(80);f.subject.update({text,status:'streaming'});assert.equal(f.frames.size,1);
  f.doc.hidden=true;f.listeners.get('visibilitychange')();assert.equal(f.body.textContent,text);assert.equal(f.frames.size,0);
  f.doc.hidden=false;f.subject.update({text:text+'继续'.repeat(100)});f.media.matches=true;f.motion.get('change')();
  assert.equal(f.body.textContent,text+'继续'.repeat(100));assert.equal(f.frames.size,0);
  const reduced=fixture({reduced:true});reduced.subject.update({text,status:'streaming'});assert.equal(reduced.body.textContent,text);assert.equal(reduced.frames.size,0);
});

test('long frame pauses catch up, unavailable grapheme segmentation uses faithful immediate output',()=>{
  const f=fixture();const text='停顿后不积压'.repeat(120);f.subject.update({text,status:'streaming'});f.tick();f.tick(2000);
  assert.equal(f.body.textContent,text);assert.equal(f.frames.size,0);
  const fallback=fixture({segmenter:false});fallback.subject.update({text:'👨‍👩‍👧‍👦e\u0301',status:'streaming'});assert.equal(fallback.body.textContent,'👨‍👩‍👧‍👦e\u0301');assert.equal(fallback.frames.size,0);
});

test('disconnect/error retains all received text with incomplete status; tool pending never renders arguments',()=>{
  const f=fixture();const text='<img onerror="never-execute">公开文字'.repeat(20);f.subject.update({text,status:'streaming',phase:'tool_pending'});
  assert.equal(f.notice.textContent,'正在等待完整工具回复…');
  f.subject.update({status:'incomplete'});assert.equal(f.body.textContent,text);assert.match(f.notice.textContent,/未完成/);assert.equal(f.frames.size,0);
  f.subject.update({status:'error'});assert.equal(f.body.textContent,text);assert.equal(f.host.dataset.streamStatus,'error');assert.equal(f.host['aria-busy'],'false');
});

test('cancel/reset clears a switched task and dispose releases all callbacks',()=>{
  const f=fixture();f.subject.update({text:'旧任务的回复'.repeat(100),status:'streaming'});f.subject.update({status:'cancelled'});
  assert.equal(f.body.textContent,'');assert.equal(f.notice.hidden,true);assert.equal(f.frames.size,0);
  f.subject.update({text:'新任务',status:'streaming'});f.subject.reset();assert.equal(f.body.textContent,'');
  f.subject.update({text:'再开始'.repeat(100),status:'streaming'});f.subject.dispose();
  assert.equal(f.host.children.length,0);assert.equal(f.frames.size,0);assert.equal(f.listeners.size,0);assert.equal(f.motion.size,0);
  f.subject.update({text:'已释放不再更新'});assert.equal(f.host.children.length,0);
});

test('rapid public states remain readable for four seconds without delaying newer urgent states or background work',()=>{
  const f=fixture(),{host,reader}=f.activity(),events=Array.from({length:4},(_,index)=>({id:'event-'+index,label:'工具请求状态 '+index,kind:index%2?'tool_received':'tool_receiving',at:0}));
  reader.update({events,omitted:0});const list=host.children[2],urgent=host.children[1],toggle=host.children[3];assert.equal(list.children.length,4);assert.equal(f.frames.size,0);assert.equal(urgent.hidden,true);
  f.tick(100);events.push({id:'error',label:'回复中断，收到的内容已保留。',kind:'error',at:100});reader.update({events});assert.equal(urgent.hidden,false);assert.match(urgent.textContent,/中断/);assert.equal(list.children.length,5,'errors are immediate while earlier normal states remain visible');
  events.push({id:'next',label:'正在接收下一轮公开回复。',kind:'reply_receiving'});reader.update({events});assert.equal(urgent.hidden,false,'a fast following normal status cannot instantly hide an important interruption');
  f.tick(3890);assert.equal(list.children.length,6);f.tick(20);assert.equal(list.children.length,3);assert.equal(urgent.hidden,false);
  toggle.fire('click');assert.equal(list.children.length,6);assert.equal(toggle['aria-expanded'],'true');f.tick(10000);assert.equal(list.children.length,6);assert.equal(urgent.hidden,true);
  toggle.fire('click');assert.equal(list.children.length,3);reader.reset();assert.equal(host.hidden,true);assert.equal(list.children.length,0);assert.equal(f.timers.size,0);reader.dispose();assert.equal(host.children.length,0);
});

test('activity history is bounded, literal, and retained after completion without inventing a successful operation',()=>{
  const f=fixture(),{host,reader}=f.activity(),events=Array.from({length:40},(_,index)=>({id:'event-'+index,label:index===39?'<img src=x> 模型的工具请求已接收。':'收到公开状态 '+index,kind:'tool_received'}));
  reader.update({events,omitted:12});const list=host.children[2];assert.equal(list.children.length,8);assert.equal(list.children.at(-1).textContent,events.at(-1).label);assert.match(host.children[4].textContent,/较早 12 条已省略/);
  host.children[3].fire('click');assert.equal(list.children.length,32);f.tick(10000);assert.equal(host.hidden,false);assert.equal(list.children.length,32);assert.equal(host.children[1].hidden,true);reader.dispose();assert.equal(f.timers.size,0);
});
