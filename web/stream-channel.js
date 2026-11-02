'use strict';

// Share volatile local SSE snapshots. No storage or model credentials live here.
window.PanxuStreamChannel = (() => {
  const channels=new Map(),maxChannels=4;
  const statuses=new Set(['streaming','completed','error','cancelled']);
  let watching=false;
  function snapshot(channel){return {stream:channel.stream?{...channel.stream,last_complete:channel.stream.last_complete?{...channel.stream.last_complete}:null}:null,connection:channel.connection,incomplete:Boolean(channel.stream&&(channel.stream.status==='error'||(channel.stream.status==='streaming'&&['reconnecting','stopped','unavailable'].includes(channel.connection))))};}
  function completed(value){
    if(!value || typeof value!=='object' || typeof value.text!=='string' || value.text.length>16000 || Array.from(value.text).length>8000)return null;
    return {text:value.text,epoch:typeof value.epoch==='string'?value.epoch.slice(0,120):'',turn_id:typeof value.turn_id==='string'?value.turn_id.slice(0,120):'',
      truncated:value.truncated===true,completed_at:typeof value.completed_at==='string'?value.completed_at.slice(0,64):'',
      protocol:typeof value.protocol==='string'?value.protocol.slice(0,40):'',delivery:typeof value.delivery==='string'?value.delivery.slice(0,24):''};
  }
  function notify(channel){for(const listener of [...channel.listeners]){try{listener(snapshot(channel));}catch(_){}}}
  function stopTransport(channel){
    channel.generation++;if(channel.source){channel.source.close();channel.source=null;}
    window.clearTimeout(channel.timer);window.clearTimeout(channel.timeout);channel.timer=null;channel.timeout=null;
    channel.abort?.abort();channel.abort=null;
  }
  function current(channel,generation){return channels.get(channel.id)===channel&&channel.generation===generation&&channel.listeners.size>0&&!document.hidden;}
  function receive(channel,envelope,generation){
    if(!current(channel,generation)||!envelope||!Object.hasOwn(envelope,'stream'))return false;
    const stream=envelope.stream;
    if(stream!==null){
      if(!stream||typeof stream!=='object'||typeof stream.epoch!=='string'||!stream.epoch||stream.epoch.length>120||!Number.isSafeInteger(stream.sequence)||stream.sequence<0||typeof stream.text!=='string'||stream.text.length>16000||Array.from(stream.text).length>8000||!statuses.has(stream.status))return false;
      if(channel.generationEpoch&&stream.epoch!==channel.generationEpoch)return false;
      channel.generationEpoch=stream.epoch;
      if(channel.lastEpoch===stream.epoch&&stream.sequence<=channel.lastSequence){channel.errors=0;channel.connection='open';notify(channel);return true;}
      channel.lastEpoch=stream.epoch;channel.lastSequence=stream.sequence;
      channel.stream={epoch:stream.epoch,sequence:stream.sequence,turn_id:typeof stream.turn_id==='string'?stream.turn_id:'',protocol:typeof stream.protocol==='string'?stream.protocol:'',text:stream.text,tool_pending:stream.tool_pending===true,status:stream.status,delivery:typeof stream.delivery==='string'?stream.delivery.slice(0,24):'',updated_at:typeof stream.updated_at==='string'?stream.updated_at:'',truncated:stream.truncated===true,last_complete:completed(stream.last_complete)};
    }else channel.stream=null;
    channel.errors=0;channel.connection='open';notify(channel);return true;
  }
  function failed(channel,generation){
    if(!current(channel,generation))return;
    stopTransport(channel);channel.errors++;
    channel.connection=channel.errors>=3?'stopped':'reconnecting';notify(channel);
    if(channel.errors<3)channel.timer=window.setTimeout(()=>start(channel),Math.min(1500,250*channel.errors));
  }
  async function poll(channel,generation){
    if(!current(channel,generation))return;
    const controller=new AbortController();channel.abort=controller;
    channel.timeout=window.setTimeout(()=>controller.abort(),4000);
    try{
      const response=await window.fetch(`/api/tasks/${encodeURIComponent(channel.id)}/stream`,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('stream unavailable');
      const envelope=await response.json();
      if(!current(channel,generation))return;
      if(!receive(channel,envelope,generation))throw new Error('invalid stream snapshot');
      channel.timer=window.setTimeout(()=>poll(channel,generation),250);
    }catch(_){failed(channel,generation);}
    finally{if(channel.abort===controller){window.clearTimeout(channel.timeout);channel.timeout=null;channel.abort=null;}}
  }
  function start(channel){
    if(!channel.listeners.size||document.hidden||channel.connection==='stopped')return;
    stopTransport(channel);const generation=channel.generation;channel.generationEpoch=null;
    channel.connection=channel.errors?'reconnecting':'connecting';notify(channel);
    if(typeof window.EventSource==='function'){
      try{
        const source=new window.EventSource(`/api/tasks/${encodeURIComponent(channel.id)}/stream/events`);channel.source=source;
        source.onopen=()=>{if(current(channel,generation)){channel.connection='open';notify(channel);}};
        source.onmessage=event=>{
          if(!current(channel,generation))return;
          try{if(!receive(channel,JSON.parse(event.data),generation))failed(channel,generation);}catch(_){failed(channel,generation);}
        };
        source.onerror=()=>failed(channel,generation);
      }catch(_){failed(channel,generation);}
    }else if(typeof window.fetch==='function')poll(channel,generation);
    else{channel.connection='unavailable';notify(channel);}
  }
  function visibility(){
    for(const channel of channels.values()){
      if(document.hidden){stopTransport(channel);if(channel.connection!=='stopped')channel.connection='paused';notify(channel);}
      else start(channel);
    }
  }
  function subscribe(taskId,listener){
    if(typeof listener!=='function')return ()=>{};
    if(typeof taskId!=='string'||! /^[A-Za-z0-9_-]{1,120}$/.test(taskId)||(!channels.has(taskId)&&channels.size>=maxChannels)){
      try{listener({stream:null,connection:'unavailable',incomplete:false});}catch(_){}return ()=>{};
    }
    let channel=channels.get(taskId),fresh=false;
    if(!channel){channel={id:taskId,listeners:new Set(),stream:null,connection:document.hidden?'paused':'connecting',source:null,timer:null,timeout:null,abort:null,generation:0,generationEpoch:null,lastEpoch:null,lastSequence:-1,errors:0};channels.set(taskId,channel);fresh=true;}
    else if(['stopped','unavailable'].includes(channel.connection)){channel.errors=0;channel.connection='connecting';fresh=true;}
    const subscription=value=>listener(value);channel.listeners.add(subscription);
    if(!watching){document.addEventListener('visibilitychange',visibility);watching=true;}
    try{listener(snapshot(channel));}catch(_){}
    if(fresh)start(channel);
    let active=true;
    return ()=>{
      if(!active)return;active=false;channel.listeners.delete(subscription);
      if(!channel.listeners.size){stopTransport(channel);channels.delete(taskId);channel.stream=null;}
      if(!channels.size&&watching){document.removeEventListener('visibilitychange',visibility);watching=false;}
    };
  }
  return {subscribe};
})();
