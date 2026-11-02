'use strict';

// A reader for cumulative public text, independent of transport and task state.
window.PanxuStreamView = (() => {
  function createActivity(host,options={}){
    const doc=host.ownerDocument || document,minimum=Number.isFinite(options.minimumVisibleMs)?Math.max(0,Math.min(10000,options.minimumVisibleMs)):4000;
    const heading=doc.createElement('p'),urgent=doc.createElement('p'),list=doc.createElement('ol'),toggle=doc.createElement('button'),hint=doc.createElement('p');
    heading.className='task-live-caption';heading.textContent='最近动态';urgent.className='stream-activity-urgent';urgent.setAttribute('role','status');
    list.className='task-events stream-activity-events';list.tabIndex=0;list.setAttribute('aria-label','最近公开状态，可滚动阅读');
    toggle.className='plain-button';toggle.type='button';hint.className='field-help';host.className+=' stream-activity';host.setAttribute('aria-label','本次会话的最近公开动态');
    host.replaceChildren(heading,urgent,list,toggle,hint);
    let events=[],omitted=0,expanded=false,timer=null,disposed=false,lastKey='';const seen=new Map();
    const clock=()=>window.performance?.now?.() ?? Date.now();
    const clearTimer=()=>{if(timer!==null)window.clearTimeout?.(timer);timer=null;};
    function paint(){
      if(disposed)return;clearTimer();host.hidden=!events.length;if(!events.length)return;
      const now=clock(),latest=events.at(-1),criticalKinds=['error','cancelled','stopped','unavailable'];
      const critical=[...events].reverse().find(event=>criticalKinds.includes(event.kind)&&(event===latest||now-seen.get(event.id)<minimum));
      urgent.hidden=!critical;urgent.textContent=critical?.label || '';urgent.dataset.kind=critical?.kind || '';
      const recent=events.filter((event,index)=>index>=events.length-3||now-seen.get(event.id)<minimum);
      const shown=expanded?events:recent.slice(-8);
      list.replaceChildren(...shown.map(event=>{const entry=doc.createElement('li');entry.textContent=event.label;entry.dataset.activityId=event.id;return entry;}));
      toggle.hidden=events.length<=3&&!omitted;toggle.textContent=expanded?'收起状态记录':`查看最近 ${events.length} 条状态`;toggle.setAttribute('aria-expanded',String(expanded));
      hint.hidden=!omitted;hint.textContent=omitted?`仅保留本次会话最近 32 条公开状态，较早 ${omitted} 条已省略。`:'';
      if(typeof window.setTimeout==='function'){
        const due=events.filter((event,index)=>!expanded&&index<events.length-3||criticalKinds.includes(event.kind)).map(event=>minimum-(now-seen.get(event.id))).filter(delay=>delay>0);
        if(due.length)timer=window.setTimeout(paint,Math.min(...due)+1);
      }
    }
    function update(value={}){
      if(disposed)return;const allowed=new Set(['reply_receiving','reply_received','tool_receiving','tool_received','error','cancelled','reconnecting','stopped','unavailable']);
      const next=Array.isArray(value.events)?value.events.slice(-32).filter(event=>event&&typeof event.id==='string'&&event.id.length<=300&&typeof event.label==='string'&&event.label.length<=180&&allowed.has(event.kind)).map(event=>({id:event.id,label:event.label,kind:event.kind})):[];
      const count=Number.isSafeInteger(value.omitted)&&value.omitted>=0?value.omitted:0,key=JSON.stringify([next,count]);
      if(key===lastKey)return;lastKey=key;const ids=new Set(next.map(event=>event.id));for(const id of seen.keys())if(!ids.has(id))seen.delete(id);
      next.forEach(event=>{if(!seen.has(event.id))seen.set(event.id,clock());});events=next;omitted=count;paint();
    }
    function reset(){clearTimer();events=[];omitted=0;expanded=false;lastKey='';seen.clear();host.hidden=true;list.replaceChildren();urgent.textContent='';hint.textContent='';}
    function dispose(){reset();disposed=true;host.replaceChildren();}
    toggle.addEventListener('click',()=>{expanded=!expanded;paint();});reset();return {update,reset,dispose};
  }
  function create(host, options = {}) {
    const doc=host.ownerDocument || document;
    const body=doc.createElement('div'),notice=doc.createElement('p');
    body.className='stream-view-text';notice.className='stream-view-status';
    notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
    body.setAttribute('aria-live','off');host.replaceChildren(body,notice);
    const bounded=(value,fallback,min,max)=>Number.isFinite(value)?Math.min(max,Math.max(min,value)):fallback;
    const responseTime=bounded(options.responseTimeMs,240,60,1000);
    const finishTime=bounded(options.finishTimeMs,120,0,300);
    const maxFrame=bounded(options.maxFrameMs,50,16,100);
    const pauseCatchup=bounded(options.pauseCatchupMs,500,100,2000);
    const maxLag=bounded(options.maxLagGraphemes,240,1,8000);
    const media=window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let segmenter;try{segmenter=new Intl.Segmenter(undefined,{granularity:'grapheme'});}catch(_){}
    let text='',ends=[],cursor=0,velocity=0,visible=0,frame=null,lastFrame=null;
    let status='idle',phase='',finishedAt=null,disposed=false;
    const now=()=>window.performance?.now?.() ?? Date.now();
    const stop=()=>{if(frame!==null)window.cancelAnimationFrame?.(frame);frame=null;lastFrame=null;};
    function paint(count) {
      visible=Math.max(0,Math.min(ends.length,count));
      const next=visible===ends.length?text:text.slice(0,visible?ends[visible-1]:0);
      if(body.textContent!==next)body.textContent=next;
    }
    function converge() {stop();cursor=ends.length;velocity=0;paint(ends.length);}
    function immediate() {return doc.hidden || media?.matches || !segmenter || !window.requestAnimationFrame;}
    function schedule() {if(frame===null&&!disposed)frame=window.requestAnimationFrame(tick);}
    function tick(timestamp) {
      frame=null;if(disposed)return;
      if(immediate()){converge();return;}
      const clock=Number.isFinite(timestamp)?timestamp:now();
      const elapsed=lastFrame===null?16:Math.max(0,clock-lastFrame);lastFrame=clock;
      if(elapsed>=pauseCatchup || (finishedAt!==null&&clock-finishedAt>=finishTime)){converge();return;}
      const dt=Math.min(maxFrame,elapsed)/1000;
      const omega=6/(status==='completed'?Math.max(30,finishTime):responseTime)*1000;
      const target=ends.length;
      // Analytic critically damped spring. A cursor never moves back during
      // an append-only reply, and a bounded backlog avoids long typing queues.
      const origin=Math.max(cursor,target-maxLag),distance=origin-target;
      const coefficient=velocity+omega*distance,decay=Math.exp(-omega*dt);
      const candidate=target+(distance+coefficient*dt)*decay;
      velocity=Math.max(0,(velocity-omega*coefficient*dt)*decay);
      cursor=Math.max(cursor,Math.min(target,candidate));
      if(target-cursor<0.05){converge();return;}
      paint(Math.floor(cursor));schedule();
    }
    function feedback() {
      const running=status==='streaming';host.dataset.streamStatus=status;
      host.setAttribute('aria-busy',String(running));
      notice.textContent=status==='incomplete'||status==='error'?'回复未完成，已保留收到的内容。':running?(phase==='tool_pending'?'正在等待完整工具回复…':'正在接收回复…'):'';
      notice.hidden=!notice.textContent;
    }
    function update(snapshot = {}) {
      if(disposed)return;
      if(snapshot.status==='cancelled'){reset();return;}
      const next=typeof snapshot.text==='string'?snapshot.text:text;
      const revised=!next.startsWith(text);
      const changed=next!==text;text=next;
      if(changed){
        ends=segmenter?Array.from(segmenter.segment(text),part=>part.index+part.segment.length):[text.length];
        if(!text)ends=[];
      }
      if(typeof snapshot.phase==='string')phase=snapshot.phase;
      if(['streaming','completed','incomplete','error','idle'].includes(snapshot.status))status=snapshot.status;
      else if(status==='idle'&&text)status='streaming';
      feedback();
      if(status==='completed'&&finishedAt===null)finishedAt=now();
      else if(status!=='completed')finishedAt=null;
      // Sanitization/final revision wins over animation: never retain stale text.
      if(revised||immediate()||['incomplete','error','idle'].includes(status)){converge();return;}
      if(!visible&&ends.length)paint(1);
      cursor=Math.max(cursor,visible);
      if(cursor>=ends.length){paint(ends.length);return;}
      schedule();
    }
    function reset() {
      if(disposed)return;stop();text='';ends=[];cursor=0;velocity=0;visible=0;
      status='idle';phase='';finishedAt=null;body.textContent='';feedback();
    }
    const onVisibility=()=>{if(doc.hidden)converge();};
    const onMotion=()=>{if(media?.matches)converge();};
    doc.addEventListener('visibilitychange',onVisibility);
    if(media?.addEventListener)media.addEventListener('change',onMotion);else media?.addListener?.(onMotion);
    function dispose() {
      if(disposed)return;reset();disposed=true;
      doc.removeEventListener('visibilitychange',onVisibility);
      if(media?.removeEventListener)media.removeEventListener('change',onMotion);else media?.removeListener?.(onMotion);
      host.replaceChildren();
    }
    feedback();return {update,reset,dispose};
  }
  return {create,createActivity};
})();
