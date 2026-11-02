'use strict';

// Task evidence and background progress remain readable without another model
// request. This module never starts analysis or applies a cleanup operation.
window.PanxuOrchestration = (() => {
  const mounted=new Set(),byRoot=new WeakMap();let serial=0;
  const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const add=(root,...children)=>{children.flat().filter(Boolean).forEach(c=>root.append(c));return root;};
  const button=(label,run,cls='button')=>{const b=node('button',cls,label);b.type='button';b.addEventListener('click',run);return b;};
  const active=t=>byRoot.get(t.root)===t&&t.root.isConnected;
  const running=work=>['queued','running','cancelling'].includes(work.status);
  const number=value=>Number.isFinite(value)&&value>=0?value:null;
  const bytes=value=>{if(!Number.isFinite(value))return '';const units=['B','KiB','MiB','GiB','TiB'];let n=value,i=0;while(n>=1024&&i<units.length-1){n/=1024;i++;}return (i?n.toFixed(n<10?1:0):String(n))+' '+units[i];};
  const statuses={observed:'已取得观察',empty:'暂无匹配内容',partial:'部分已检查',needs_input:'需补充设置',unknown:'尚未确认',not_applicable:'当前范围不适用',not_installed:'未检测到安装',not_checked:'尚未检查',completed:'轻量检查已完成',cancelled:'已停止检查'};
  const workStatuses={queued:'等待检查',running:'正在检查',cancelling:'正在停止',completed:'检查完成',cancelled:'已取消',interrupted:'检查中断',failed:'检查未完成'};
  const workTitle=work=>({diagnostics:'系统轻量检查',diagnostic:'系统轻量检查',duplicates:'重复文件查找',duplicate_scan:'重复文件查找',user_tool_inspection:'规则匹配'})[work.kind] || '后台检查';
  function changed(t,data){try{const value=t.helpers.onChange?.(data);value?.catch?.(()=>{});}catch(_){/* Refreshing a parent must not rewrite an acknowledged result. */}}
  function unmount(root){for(const t of [...mounted])if(!root||t.root===root){clearTimeout(t.timer);t.dialog?.close?.();t.dialog?.remove?.();if(byRoot.get(t.root)===t)byRoot.delete(t.root);mounted.delete(t);}}
  function token(root,helpers,kind){for(const old of [...mounted])if(!old.root.isConnected)unmount(old.root);unmount(root);const t={root,helpers,kind,id:++serial,request:0,error:'',timer:null};byRoot.set(root,t);mounted.add(t);return t;}
  function render(root,helpers={}){
    const t=token(root,helpers,'task');t.task={id:helpers.task?.id,diagnostics:helpers.task?.diagnostics,background_work:(helpers.task?.background_work || []).map(work=>({...work,progress:{...(work.progress || {})}}))};
    t.panel=node('section','orchestration-summary');t.panel.setAttribute('aria-label','诊断与后台检查');root.append(t.panel);paintTask(t);schedule(t);return t.panel;
  }
  function diagnosticsText(d){
    const counts=d.counts || {},coverage=d.coverage || {},tools=number(coverage.tool_count),confirmed=number(coverage.complete_tools);
    const needs=number(coverage.needs_input) ?? number(counts.needs_input) ?? 0,unknown=number(coverage.unknown_tools) ?? number(counts.unknown) ?? 0;
    const bits=[];if(tools!==null)bits.push(`已归纳 ${tools} 项能力`);if(confirmed!==null)bits.push(`${confirmed} 项取得完整观察`);if(needs)bits.push(`${needs} 项需补充设置`);if(unknown)bits.push(`${unknown} 项尚未确认`);
    return bits.join(' · ') || (statuses[d.status] || '轻量检查结果已保存');
  }
  function paintTask(t){
    if(!active(t))return;if(t.painted){for(const work of t.task.background_work)updateWork(t,work);return;}t.painted=true;
    const p=t.panel,diag=t.task.diagnostics,works=t.task.background_work.slice(0,4);p.replaceChildren();p.hidden=!diag&&!works.length;
    if(diag){
      const section=node('section','orchestration-diagnostics');add(section,node('h2','','系统轻量检查'),node('p','orchestration-coverage',diagnosticsText(diag)));
      const signals=Array.isArray(diag.signals)?diag.signals.slice(0,3):[];
      if(signals.length){const list=node('ul','orchestration-signals');for(const signal of signals){const li=node('li');if(typeof signal==='string')li.textContent=signal;else add(li,signal.title?node('strong','',signal.title+'：'):null,node('span','',signal.summary || signal.message || '等待进一步核对'));list.append(li);}section.append(list);}
      section.append(node('p','field-help','这是轻量观察；未知或未检查的部分仍需按需核对。'));
      if(diag.snapshot_id&&typeof t.helpers.api==='function')section.append(button('查看检查详情',()=>showDiagnostics(t),'plain-button'));
      p.append(section);
    }
    if(works.length){
      const section=node('section','orchestration-work');section.append(node('h2','','后台检查'));
      t.workNodes=new Map();
      for(const work of works){
        const row=node('article','orchestration-work-row');row.dataset.workId=work.id;const title=node('div','orchestration-work-heading'),state=node('span','orchestration-work-status');add(title,node('h3','',workTitle(work)),state);row.append(title);
        const progress=node('progress'),message=node('p','field-help'),detail=node('p','field-help'),partial=node('p','field-help','结果仅覆盖已经检查的部分。'),error=node('p','field-error'),pollError=node('p','field-help','进度暂未刷新，正在重试。'),cancelError=node('p','field-error'),cancel=button('停止检查',()=>cancelWork(t,work),'button small');
        progress.setAttribute('aria-label',workTitle(work)+'进度');cancelError.setAttribute('role','alert');add(row,progress,message,detail,partial,error,pollError,cancelError,cancel);section.append(row);
        t.workNodes.set(work.id,{row,state,progress,message,detail,partial,error,pollError,cancelError,cancel});updateWork(t,work);
      }p.append(section);
    }
  }
  function updateWork(t,work){
    const ui=t.workNodes?.get(work.id);if(!ui)return;const pending=running(work);ui.state.textContent=workStatuses[work.status] || '状态待确认';ui.progress.hidden=!pending;
    const total=number(work.progress?.total),processed=number(work.progress?.processed);
    if(total!==null&&total>0&&processed!==null){ui.progress.max=total;ui.progress.value=Math.min(total,processed);}else ui.progress.removeAttribute?.('value');
    ui.message.textContent=pending?(work.status==='cancelling'?'正在停止剩余检查，已取得的结果会保留。':work.progress?.message || (work.status==='queued'?'等待已有检查完成后继续。':'正在取得本地观察…')):workSummary(work);ui.message.hidden=!ui.message.textContent;
    ui.detail.textContent=pending?workProgressText(work.progress):'';ui.detail.hidden=!ui.detail.textContent;ui.partial.hidden=!work.partial;
    ui.error.textContent=work.error?(typeof work.error==='string'?work.error:work.error.message || '检查未完成，请稍后重试。'):'';ui.error.hidden=!ui.error.textContent;ui.pollError.hidden=!work._pollError;
    ui.cancelError.textContent=work._cancelError || '';ui.cancelError.hidden=!ui.cancelError.textContent;ui.cancel.hidden=!work.can_cancel||typeof t.helpers.api!=='function';ui.cancel.disabled=!!work._cancelling;ui.cancel.textContent=work._cancelling?'正在停止…':'停止检查';
  }
  function workProgressText(p={}){const values=[];if(number(p.processed)!==null&&number(p.total)!==null)values.push(`已检查 ${p.processed} / ${p.total} 项`);else if(number(p.scanned_entries)!==null)values.push(`已查看 ${p.scanned_entries} 项`);if(number(p.bytes_read)!==null&&p.bytes_read>0)values.push('已读取 '+bytes(p.bytes_read));return values.join(' · ');}
  function workSummary(work){const s=work.summary;if(typeof s==='string')return s;const bits=[];for(const [key,label] of [['duplicate_groups','组重复内容'],['verified_groups','组已核实重复内容'],['matched_count','项匹配'],['scanned_entries','项已查看']])if(number(s?.[key])!==null){bits.push(s[key]+' '+label);if(bits.length===2)break;}return bits.join(' · ');}
  function schedule(t,delay=2000){clearTimeout(t.timer);if(active(t)&&t.task.id&&t.task.background_work.some(running)&&typeof t.helpers.api==='function')t.timer=setTimeout(()=>poll(t),delay);}
  function replaceWork(t,value){const old=t.task.background_work.find(row=>row.id===value?.id);if(!old)return false;if(old.updated_at&&value.updated_at&&Date.parse(value.updated_at)<Date.parse(old.updated_at))return false;const rank=s=>s==='queued'?0:s==='running'?1:s==='cancelling'?2:3;if(rank(value.status)<rank(old.status))return false;Object.assign(old,value);return true;}
  async function poll(t){
    if(!active(t)){unmount(t.root);return;}const request=++t.request,works=t.task.background_work.filter(running).slice(0,2);let failed=false,completed=false;
    await Promise.all(works.map(async work=>{
      try{const data=await t.helpers.api('/api/tasks/'+encodeURIComponent(t.task.id)+'/work/'+encodeURIComponent(work.id));if(!active(t)||request!==t.request)return;const value=data.work || data;const wasRunning=running(work);if(replaceWork(t,value)){work._pollError=false;if(wasRunning&&!running(work))completed=true;}}
      catch(_){if(active(t)&&request===t.request){work._pollError=true;failed=true;}}
    }));
    if(!active(t)||request!==t.request)return;paintTask(t);if(completed)changed(t,{task_id:t.task.id,background_work:t.task.background_work});t.pollFailures=failed?(t.pollFailures || 0)+1:0;schedule(t,failed?Math.min(10000,2000*(t.pollFailures+1)):2000);
  }
  async function cancelWork(t,work){
    if(!active(t)||work._cancelling)return;work._cancelling=true;work._cancelError='';paintTask(t);
    try{const data=await t.helpers.api('/api/tasks/'+encodeURIComponent(t.task.id)+'/work/'+encodeURIComponent(work.id)+'/cancel',{});if(!active(t))return;replaceWork(t,data.work || data);changed(t,data);}
    catch(e){if(active(t))work._cancelError=e.message;}
    finally{if(active(t)){work._cancelling=false;paintTask(t);schedule(t);}}
  }
  async function showDiagnostics(t){
    if(!active(t)||t.dialog)return;const d=node('dialog','cleanup-confirm-dialog orchestration-dialog');d.setAttribute('aria-label','轻量检查详情');t.dialog=d;
    const heading=node('h2','','轻量检查详情'),note=node('p','field-help','只展示已取得的观察，不代表建议执行全部工具。'),host=node('div','orchestration-diagnostic-rows'),status=node('p','field-help'),actions=node('footer','orchestration-dialog-actions');
    const back=button('上一页',()=>diagnosticPage(t,Math.max(0,t.detailOffset-12)),'button'),next=button('下一页',()=>diagnosticPage(t,t.detailOffset+12),'button'),close=button('关闭',()=>d.close(),'button primary');
    add(actions,back,next,close);add(d,heading,note,status,host,actions);t.root.append(d);t.detailNodes={host,status,back,next};t.detailOffset=0;
    d.addEventListener('close',()=>{t.detailRequest=(t.detailRequest || 0)+1;d.remove();if(t.dialog===d)t.dialog=null;});
    if(typeof d.showModal!=='function'){d.remove();t.dialog=null;return;}d.tabIndex=-1;d.showModal();d.focus?.({preventScroll:true});d.scrollTop=0;await diagnosticPage(t,0);
  }
  async function diagnosticPage(t,offset){
    if(!active(t)||!t.dialog)return;const request=t.detailRequest=(t.detailRequest || 0)+1,ui=t.detailNodes;ui.back.disabled=true;ui.next.disabled=true;ui.status.textContent='正在读取检查详情…';
    try{const data=await t.helpers.api('/api/tasks/'+encodeURIComponent(t.task.id)+'/diagnostics/'+encodeURIComponent(t.task.diagnostics.snapshot_id)+'?offset='+offset+'&limit=12');if(!active(t)||!t.dialog||request!==t.detailRequest)return;
      t.detailOffset=offset;ui.host.replaceChildren();for(const row of data.items || []){const item=node('article','orchestration-diagnostic-row'),title=node('div','orchestration-diagnostic-heading');add(title,node('h3','',row.title || '清理工具'),node('span','orchestration-diagnostic-status',statuses[row.status] || '尚未确认'));item.append(title);item.append(node('p','',row.summary || '暂未取得可用观察。'));
        const variants=row.variants || [];const metrics=variants.flatMap(value=>value.metrics || []).slice(0,2);for(const metric of metrics)if(metric.value!==null&&metric.value!==undefined&&typeof metric.value!=='object')item.append(node('p','field-help',metric.label+'：'+(metric.unit==='bytes'?bytes(metric.value):metric.value)));
        ui.host.append(item);
      }
      const total=number(data.total);ui.status.textContent=total===null?`第 ${Math.floor(offset/12)+1} 页`:`显示 ${Math.min(total,offset+1)}–${Math.min(total,offset+(data.items || []).length)} 项，共 ${total} 项`;
      ui.back.disabled=offset===0;ui.next.disabled=!data.has_more;
    }catch(e){if(active(t)&&t.dialog&&request===t.detailRequest){ui.status.textContent=e.message;ui.status.setAttribute('role','alert');ui.host.replaceChildren(button('重试',()=>diagnosticPage(t,offset),'button'));ui.back.disabled=offset===0;}}
  }
  function renderBudget(root,helpers={}){
    const t=token(root,helpers,'budget'),details=node('details','orchestration-budget-settings');t.panel=details;details.append(node('summary','','助手工作额度'));
    const form=node('form','orchestration-budget-form'),intro=node('p','field-help','提高额度可让助手调查更多内容，也会使用更多时间与模型额度。'),fields=node('div','orchestration-budget-fields');
    const make=(name,label,min,max)=>{const line=node('label'),input=node('input');input.type='number';input.name=name;input.min=min;input.max=max;input.step=1;input.disabled=true;input.setAttribute('aria-label',label);add(line,node('span','field-label',label),input,node('small','field-help',`${min}–${max}`));fields.append(line);return input;};
    t.requests=make('model_requests','每轮最多模型请求',4,32);t.calls=make('tool_calls','每轮最多工具调用',6,128);
    const status=node('p','connection-test-status','正在读取当前额度…');status.setAttribute('role','status');t.status=status;t.save=button('保存额度',()=>{},'button');t.save.type='submit';t.save.disabled=true;
    form.addEventListener('submit',event=>{event.preventDefault();saveBudget(t);});add(form,intro,fields,t.save,status);details.append(form);root.append(details);loadBudget(t);return details;
  }
  function budget(value){const d=value?.budget || value;return d&&Number.isInteger(d.model_requests)&&d.model_requests>=4&&d.model_requests<=32&&Number.isInteger(d.tool_calls)&&d.tool_calls>=6&&d.tool_calls<=128?{model_requests:d.model_requests,tool_calls:d.tool_calls}:null;}
  async function loadBudget(t){
    const request=++t.request;
    try{const data=await t.helpers.api('/api/agent-budget');if(!active(t)||request!==t.request)return;const value=budget(data);if(!value)throw new Error('工作额度尚未完整读取，请重试。');t.requests.value=value.model_requests;t.calls.value=value.tool_calls;t.requests.disabled=false;t.calls.disabled=false;t.save.disabled=false;t.loaded=true;t.status.textContent='额度只影响助手的调查工作，不会自动批准清理。';}
    catch(e){if(active(t)&&request===t.request){t.status.textContent=e.message;t.status.setAttribute('role','alert');const retry=button('重新读取',()=>{retry.remove();t.status.textContent='正在读取当前额度…';loadBudget(t);},'plain-button');t.status.append(retry);}}
  }
  async function saveBudget(t){
    if(!active(t)||!t.loaded||t.saving)return;const value=budget({model_requests:Number(t.requests.value),tool_calls:Number(t.calls.value)});
    if(!value){t.status.textContent='请填写范围内的整数：模型请求4–32，工具调用6–128。';t.status.dataset.tone='warning';return;}
    const request=++t.request;t.saving=true;t.save.disabled=true;t.status.textContent='正在保存额度…';t.status.dataset.tone='info';
    try{const data=await t.helpers.api('/api/agent-budget',value);if(!active(t)||request!==t.request)return;if(!budget(data))throw new Error('未收到完整保存结果，请重新读取核对。');const unchanged=Number(t.requests.value)===value.model_requests&&Number(t.calls.value)===value.tool_calls;t.status.textContent=unchanged?'工作额度已保存。':'刚才的额度已保存；还有新修改尚未保存。';t.status.dataset.tone=unchanged?'neutral':'warning';changed(t,data);}
    catch(e){if(active(t)&&request===t.request){t.status.textContent=e.message;t.status.dataset.tone='warning';}}
    finally{if(active(t)&&request===t.request){t.saving=false;t.save.disabled=false;}}
  }
  return {render,renderBudget,unmount};
})();
