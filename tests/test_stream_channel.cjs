'use strict';

const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','stream-channel.js'),'utf8');
const flush=()=>new Promise(setImmediate);
const stream=(sequence,text='公开片段',overrides={})=>({epoch:'process-one',sequence,turn_id:'turn-one',protocol:'chat_completions',text,tool_pending:false,status:'streaming',updated_at:'2026-11-02T00:00:00Z',truncated:false,...overrides});

test('delivery mode survives projection so complete JSON fallback can be labelled',()=>{
  const f=fixture(),events=[];const off=f.subject.subscribe('task-one',value=>events.push(value));
  f.connections[0].message({stream:stream(1,'完整正文',{status:'completed',delivery:'json_fallback'})});
  assert.equal(events.at(-1).stream.delivery,'json_fallback');off();
});

function fixture({sse=true,fetcher}={}){
  const connections=[],timers=new Map(),listeners=new Map(),requests=[];let timerId=0;
  const document={hidden:false,addEventListener:(event,handler)=>listeners.set(event,handler),removeEventListener:event=>listeners.delete(event)};
  class EventSource {
    constructor(url){this.url=url;this.closed=false;connections.push(this);}
    close(){this.closed=true;}
    message(value){this.onmessage?.({data:JSON.stringify(value)});}
    error(){this.onerror?.({});}
  }
  const window={setTimeout:(handler,delay)=>{timers.set(++timerId,{handler,delay});return timerId;},clearTimeout:id=>timers.delete(id)};
  if(sse)window.EventSource=EventSource;
  if(fetcher)window.fetch=(url,options)=>{requests.push({url,options});return fetcher(url,options);};
  runInNewContext(source,{window,document,AbortController});
  return {subject:window.PanxuStreamChannel,connections,timers,listeners,requests,document,
    async tick(){assert.ok(timers.size,'a bounded retry/poll timer exists');const [id,timer]=[...timers].sort((a,b)=>a[1].delay-b[1].delay)[0];timers.delete(id);timer.handler();await flush();return timer.delay;},
    hide(){document.hidden=true;listeners.get('visibilitychange')?.();},show(){document.hidden=false;listeners.get('visibilitychange')?.();},
  };
}

test('same task shares one local EventSource and only the last unsubscribe closes it',()=>{
  const f=fixture(),a=[],b=[];const offA=f.subject.subscribe('task-one',value=>a.push(value));const offB=f.subject.subscribe('task-one',value=>b.push(value));
  assert.equal(f.connections.length,1);assert.equal(f.connections[0].url,'/api/tasks/task-one/stream/events');
  f.connections[0].message({stream:stream(1,'第一段')});assert.equal(a.at(-1).stream.text,'第一段');assert.equal(b.at(-1).stream.text,'第一段');
  f.connections[0].message({stream:stream(2,'第一段，第二段')});assert.equal(a.at(-1).stream.status,'streaming');assert.equal(a.at(-1).stream.text,'第一段，第二段','public snapshots arrive before completion');
  offA();assert.equal(f.connections[0].closed,false);offB();assert.equal(f.connections[0].closed,true);assert.equal(f.listeners.size,0);assert.equal(f.timers.size,0);
});

test('each subscription owns its listener lifetime and receives an isolated snapshot object',()=>{
  const f=fixture();let calls=0;
  const listener=value=>{calls++;if(value.stream)value.stream.text='consumer mutation';};
  const a=f.subject.subscribe('one',listener),b=f.subject.subscribe('one',listener),observed=[];
  const c=f.subject.subscribe('one',value=>observed.push(value));f.connections[0].message({stream:stream(1,'原文')});
  assert.equal(observed.at(-1).stream.text,'原文');const before=calls;a();f.connections[0].message({stream:stream(2,'新原文')});assert.equal(calls,before+1);
  b();c();
});

test('last-complete public text survives channel projection with bounded fields and isolated nested snapshots',()=>{
  const f=fixture(),received=[];
  const a=f.subject.subscribe('one',value=>{if(value.stream?.last_complete)value.stream.last_complete.text='consumer mutation';});
  const b=f.subject.subscribe('one',value=>received.push(value));
  const last={text:'上一轮完整公开正文',epoch:'old-epoch',turn_id:'old-turn',completed_at:'2026-11-02T04:00:00Z',protocol:'chat_completions',delivery:'sse',truncated:false,api_key:'must-not-project'};
  f.connections[0].message({stream:stream(1,'新的片段',{last_complete:last})});
  assert.equal(received.at(-1).stream.last_complete.text,'上一轮完整公开正文');assert.equal(received.at(-1).stream.last_complete.completed_at,last.completed_at);
  assert.equal(received.at(-1).stream.last_complete.api_key,undefined);
  f.connections[0].message({stream:stream(2,'更多新片段',{last_complete:{...last,text:'x'.repeat(8001)}})});
  assert.equal(received.at(-1).stream.text,'更多新片段');assert.equal(received.at(-1).stream.last_complete,null,'oversized previous text is not admitted into the shared channel');
  a();b();
});

test('epoch/sequence and closed-connection generation prevent older packets replacing current text',async()=>{
  const f=fixture(),events=[];const off=f.subject.subscribe('one',value=>events.push(value));const old=f.connections[0];
  old.message({stream:stream(9,'最新正文')});old.message({stream:stream(8,'旧正文')});assert.equal(events.at(-1).stream.text,'最新正文');
  old.error();assert.equal(events.at(-1).connection,'reconnecting');assert.equal(events.at(-1).incomplete,true);
  await f.tick();const current=f.connections[1];current.message({stream:stream(1,'重开后的公开正文',{epoch:'process-two'})});
  old.message({stream:stream(100,'旧进程的晚到包')});assert.equal(events.at(-1).stream.text,'重开后的公开正文');assert.equal(events.at(-1).connection,'open');
  current.message({stream:stream(0,'当前进程的旧包',{epoch:'process-two'})});assert.equal(events.at(-1).stream.text,'重开后的公开正文');off();
});

test('hidden pages close and pause the connection, then reconnect for a fresh snapshot',()=>{
  const f=fixture(),events=[];const off=f.subject.subscribe('one',value=>events.push(value));f.connections[0].message({stream:stream(1,'暂停前')});
  f.hide();assert.equal(f.connections[0].closed,true);assert.equal(events.at(-1).connection,'paused');assert.equal(f.timers.size,0);
  f.connections[0].message({stream:stream(2,'隐藏期间旧连接消息')});assert.equal(events.at(-1).stream.text,'暂停前');
  f.show();assert.equal(f.connections.length,2);f.connections[1].message({stream:stream(3,'恢复后最新')});assert.equal(events.at(-1).stream.text,'恢复后最新');off();
});

test('three consecutive failures stop bounded retries while retaining received text; a new subscription can explicitly retry',async()=>{
  const f=fixture(),events=[];const off=f.subject.subscribe('one',value=>events.push(value));f.connections[0].message({stream:stream(1,'保留这段公开文字')});
  f.connections[0].error();assert.equal(await f.tick(),250);f.connections[1].error();assert.equal(await f.tick(),500);f.connections[2].error();
  assert.equal(events.at(-1).connection,'stopped');assert.equal(events.at(-1).incomplete,true);assert.equal(events.at(-1).stream.text,'保留这段公开文字');assert.equal(f.timers.size,0);assert.equal(f.connections.length,3);
  f.hide();f.show();assert.equal(f.connections.length,3,'mere visibility changes do not retry an exhausted channel');
  const retry=f.subject.subscribe('one',()=>{});assert.equal(f.connections.length,4);retry();off();
});

test('completed and cancelled statuses only come from genuine snapshots, not transport success or errors',()=>{
  const f=fixture(),events=[];const off=f.subject.subscribe('one',value=>events.push(value));f.connections[0].onopen();assert.equal(events.at(-1).stream,null);
  f.connections[0].message({stream:stream(1,'本轮接收完',{status:'completed'})});f.connections[0].error();assert.equal(events.at(-1).stream.status,'completed');assert.equal(events.at(-1).incomplete,false);
  off();const cancel=f.subject.subscribe('two',value=>events.push(value));f.connections.at(-1).message({stream:stream(2,'',{status:'cancelled'})});assert.equal(events.at(-1).stream.status,'cancelled');cancel();
});

test('channels are bounded to four distinct tasks and free their memory slot after unsubscribe',()=>{
  const f=fixture(),off=[];for(let i=0;i<4;i++)off.push(f.subject.subscribe('task-'+i,()=>{}));let rejected;
  const fifth=f.subject.subscribe('task-4',value=>{rejected=value;});assert.equal(f.connections.length,4);assert.equal(rejected.connection,'unavailable');fifth();
  off.shift()();off.push(f.subject.subscribe('task-4',()=>{}));assert.equal(f.connections.length,5);
  off.forEach(dispose=>dispose());assert.equal(f.listeners.size,0);
});

test('invalid/oversized envelopes do not replace text; the Unicode bound accepts 8,000 code points',()=>{
  const f=fixture(),events=[];const off=f.subject.subscribe('one',value=>events.push(value));const source=f.connections[0];
  const unicode='😀'.repeat(8000);source.message({stream:stream(1,unicode)});assert.equal(events.at(-1).stream.text,unicode);
  source.message({stream:stream(2,'x'.repeat(8001))});assert.equal(events.at(-1).stream.text,unicode);assert.equal(events.at(-1).connection,'reconnecting');off();
  let unavailable;const invalid=f.subject.subscribe('../outside',value=>{unavailable=value;});assert.equal(unavailable.connection,'unavailable');invalid();
});

test('without EventSource, one bounded 250ms GET loop is shared and stops when hidden or unsubscribed',async()=>{
  let sequence=0;const f=fixture({sse:false,fetcher:async()=>({ok:true,json:async()=>({stream:stream(++sequence,'fallback-'+sequence)})})}),events=[];
  const off=f.subject.subscribe('one',value=>events.push(value)),other=f.subject.subscribe('one',()=>{});await flush();
  assert.equal(f.requests.length,1);assert.equal(f.requests[0].url,'/api/tasks/one/stream');assert.equal(f.requests[0].options.credentials,'same-origin');assert.equal(f.requests[0].options.cache,'no-store');
  assert.equal(await f.tick(),250);assert.equal(f.requests.length,2);assert.equal(events.at(-1).stream.text,'fallback-2');
  f.hide();assert.equal(f.timers.size,0);f.show();await flush();assert.equal(f.requests.length,3);off();other();assert.equal(f.timers.size,0);
});

test('fallback aborts an in-flight read when hidden and discards its late response',async()=>{
  let resolve;const f=fixture({sse:false,fetcher:()=>new Promise(done=>{resolve=done;})}),events=[];
  const off=f.subject.subscribe('one',value=>events.push(value));const signal=f.requests[0].options.signal;f.hide();assert.equal(signal.aborted,true);
  resolve({ok:true,json:async()=>({stream:stream(10,'隐藏后的晚包')})});await flush();assert.equal(events.at(-1).stream,null);assert.equal(events.at(-1).connection,'paused');assert.equal(f.timers.size,0);off();
});

test('fallback timeout is bounded and missing transports honestly report unavailable',async()=>{
  const f=fixture({sse:false,fetcher:(_url,{signal})=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('aborted'))))}),events=[];
  const off=f.subject.subscribe('one',value=>events.push(value));assert.equal(await f.tick(),4000);assert.equal(events.at(-1).connection,'reconnecting');off();assert.equal(f.timers.size,0);
  const none=fixture({sse:false});let value;const stop=none.subject.subscribe('one',snapshot=>{value=snapshot;});assert.equal(value.connection,'unavailable');assert.equal(value.stream,null);assert.equal(none.timers.size,0);stop();
});
