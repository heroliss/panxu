'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','stream-view.js'),'utf8');

function fixture({reduced=false,segmenter=true,options={}}={}) {
  const frames=new Map(),listeners=new Map(),motion=new Map();let frameId=0,clock=0;
  class Element {
    constructor(){this.children=[];this.dataset={};this.textContent='';this.ownerDocument=doc;}
    setAttribute(name,value){this[name]=value;}
    replaceChildren(...children){this.children=children;}
    set innerHTML(_){throw new Error('stream content must never be parsed as HTML');}
  }
  const doc={hidden:false,createElement:()=>new Element(),addEventListener:(event,handler)=>listeners.set(event,handler),removeEventListener:event=>listeners.delete(event)};
  const media={matches:reduced,addEventListener:(event,handler)=>motion.set(event,handler),removeEventListener:event=>motion.delete(event)};
  const window={performance:{now:()=>clock},matchMedia:()=>media,
    requestAnimationFrame:handler=>{frames.set(++frameId,handler);return frameId;},cancelAnimationFrame:id=>frames.delete(id)};
  runInNewContext(source,{window,document:doc,Intl:segmenter?Intl:{},Date});
  const host=new Element(),subject=window.PanxuStreamView.create(host,options);
  const body=host.children[0],notice=host.children[1];
  return {host,body,notice,subject,frames,listeners,motion,doc,media,
    tick(ms=16){clock+=ms;const scheduled=[...frames];frames.clear();scheduled.forEach(([,handler])=>handler(clock));},
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
