'use strict';

// The recovery journal is authoritative. UI selection never moves or restores a path.
window.PanxuRecovery = (() => {
  let mounted=null,generation=0;
  const selection=new Set(),view={page:1,status:'retained',volume:''};
  const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const button=(label,run,cls='button')=>{const b=node('button',cls,label);b.type='button';b.addEventListener('click',run);return b;};
  const alive=t=>mounted===t&&t.root.isConnected;
  function unmount(){generation++;mounted?.dialog?.remove();mounted=null;}
  function prettyDate(value){const d=new Date(value);return !value||Number.isNaN(d.getTime())?'时间未记录':d.toLocaleString('zh-CN',{hour12:false});}
  function total(summary,h){return `${h.bytes(summary.logical_bytes || 0)}${summary.unknown_size_count?' + 未统计内容':''}`;}
  function volumeText(row,h){return `${row.volume || '其他位置'} · ${row.retained_count || 0} 项 · ${total(row,h)}`;}
  async function home(root,h){
    const host=node('section','recovery-home');host.setAttribute('aria-label','已删除文件');root.append(host);
    try{
      const data=await h.api('/api/recovery/summary');if(!host.isConnected)return;
      const copy=node('div','recovery-home-copy');copy.append(node('h2','','已删除文件'),node('p','field-help',data.retained_count?`${data.retained_count} 项可还原，记录大小 ${total(data,h)}。永久删除前仍占用原磁盘空间。`:'删除的文件会保留在原卷，供以后还原；由你手动永久删除。'));
      const details=node('div','recovery-home-volumes');for(const row of data.volumes || [])details.append(node('span','',volumeText(row,h)));
      copy.append(details);host.append(copy,button(data.retained_count?'查看与还原':'查看已删除文件',h.open));
    }catch(error){if(host.isConnected)host.append(node('p','field-help','暂时无法读取已删除文件。'),button('打开查看',h.open));}
  }
  function render(root,h){
    unmount();const t={root,h,busy:false,error:'',rows:[],summary:null};mounted=t;
    root.append(h.heading('已删除文件','先保留，随时还原。只有在这里确认永久删除，才会释放磁盘空间。'));
    t.host=node('section','recovery-content');root.append(t.host);load(t);
  }
  async function load(t){
    if(!alive(t)||t.busy)return;const run=++generation;t.loading=true;t.error='';paint(t);
    try{
      const q=new URLSearchParams({page:String(view.page),limit:'50',status:view.status,volume:view.volume});
      const [summary,data]=await Promise.all([t.h.api('/api/recovery/summary'),t.h.api('/api/recovery?'+q)]);
      if(!alive(t)||run!==generation)return;t.summary=summary;t.data=data;t.rows=data.items || [];
    }catch(error){if(alive(t)&&run===generation)t.error=error.message;}
    finally{if(alive(t)&&run===generation){t.loading=false;paint(t);}}
  }
  function changeFilter(t,key,value){view[key]=value;view.page=1;selection.clear();load(t);}
  function paint(t){
    if(!alive(t))return;const h=t.h;t.host.replaceChildren();
    if(t.summary){
      const summary=node('section','recovery-summary');summary.append(node('strong','',`${t.summary.retained_count || 0} 项可还原`),node('span','',`记录大小 ${total(t.summary,h)}`));
      for(const volume of t.summary.volumes || [])summary.append(node('small','',volumeText(volume,h)));
      t.host.append(summary);
    }
    const toolbar=node('div','recovery-toolbar'),status=node('select'),volume=node('select');
    status.setAttribute('aria-label','文件状态');for(const [value,label] of [['retained','可还原'],['restored','已还原'],['purged','已永久删除'],['all','全部记录']]){const option=node('option','',label);option.value=value;status.append(option);}status.value=view.status;status.disabled=t.busy;status.addEventListener('change',()=>changeFilter(t,'status',status.value));
    volume.setAttribute('aria-label','按磁盘筛选');const all=node('option','','全部磁盘');all.value='';volume.append(all);for(const row of t.summary?.volumes || []){const option=node('option','',row.volume || '其他位置');option.value=row.volume;volume.append(option);}volume.value=view.volume;volume.disabled=t.busy;volume.addEventListener('change',()=>changeFilter(t,'volume',volume.value));
    const refresh=button('刷新',()=>load(t));refresh.disabled=t.loading||t.busy;toolbar.append(status,volume,refresh);t.host.append(toolbar);
    if(t.error){const error=node('p','field-error',t.error);error.setAttribute('role','alert');t.host.append(error);}
    if(t.message){const message=node('p','field-help',t.message);message.setAttribute('role','status');t.host.append(message);}
    const selectable=t.rows.filter(row=>row.status==='retained'),label=node('label','workspace-select-all'),check=node('input');check.type='checkbox';check.checked=selectable.length>0&&selectable.every(row=>selection.has(row.id));check.indeterminate=!check.checked&&selectable.some(row=>selection.has(row.id));check.disabled=t.loading||t.busy||!selectable.length;check.setAttribute('aria-label','全选当前页可还原的项目');check.addEventListener('change',()=>{for(const row of selectable){if(check.checked)selection.add(row.id);else selection.delete(row.id);}paint(t);});label.append(check,node('span','','全选本页'));t.host.append(label);
    const actions=node('div','recovery-actions');actions.append(node('span','field-help',`已勾选 ${selection.size} 项`));
    for(const [action,text,cls] of [['restore','还原勾选项','button primary'],['purge','永久删除勾选项','button']]){const b=button(text,()=>prepare(t,action),cls);b.disabled=!selection.size||t.loading||t.busy;actions.append(b);}t.host.append(actions);
    const list=node('div','recovery-list');list.setAttribute('aria-busy',String(t.loading||t.busy));
    for(const row of t.rows){
      const card=node('article','recovery-row'),pick=node('input');pick.type='checkbox';pick.checked=selection.has(row.id);pick.disabled=t.loading||t.busy||row.status!=='retained';pick.setAttribute('aria-label','选择 '+row.name);pick.addEventListener('change',()=>{if(pick.checked)selection.add(row.id);else selection.delete(row.id);paint(t);});
      const item={...(row.snapshot || {}),...row,assessment:row.assessment},copy=node('div','recovery-copy'),title=node('div','item-title-line');
      const icon=window.PanxuIcons?.create(item);if(icon)title.append(icon);title.append(node('h2','',row.name || row.original_path));
      copy.append(title,node('p','recovery-path',row.original_path),node('p','recovery-time',`删除于 ${prettyDate(row.deleted_at)}${row.status==='restored'?' · 已还原':row.status==='purged'?' · 已永久删除':''}`));
      if(row.assessment){copy.append(window.PanxuAdvicePresentation.render(item,{compactUnknown:true}));const provenance=window.PanxuProvenance?.details(row.assessment,{presets:h.providerPresets});if(provenance)copy.append(provenance);}
      else copy.append(node('p','field-help','当时未记录分析建议，原路径和删除记录仍保留。'));
      const size=node('span','recovery-size',Number.isFinite(row.size_bytes)?h.bytes(row.size_bytes):'大小未统计');
      card.append(pick,window.PanxuAssessment.render(item,{primary:true}),copy,size);list.append(card);
    }
    if(!t.rows.length)list.append(node('p','empty-state',t.loading?'正在读取删除记录…':'这里还没有匹配的记录。'));t.host.append(list);
    const pager=node('div','file-pagination'),previous=button('上一页',()=>{view.page--;load(t);}),next=button('下一页',()=>{view.page++;load(t);});previous.disabled=t.loading||t.busy||view.page===1;next.disabled=t.loading||t.busy||!t.data?.has_more;pager.append(previous,node('span','',`第 ${view.page} 页`),next);t.host.append(pager);
  }
  async function prepare(t,action){
    if(!alive(t)||t.busy||t.loading||!selection.size)return;t.busy=true;t.error='';paint(t);
    try{const plan=await t.h.api('/api/recovery/prepare',{ids:[...selection],action});if(alive(t))confirm(t,plan);}
    catch(error){if(alive(t))t.error=error.message;}
    finally{if(alive(t)){t.busy=false;paint(t);}}
  }
  function confirm(t,plan){
    const dialog=node('dialog','recovery-dialog');t.dialog=dialog;const purge=plan.action==='purge';
    const title=node('h2','',purge?'永久删除这些文件？':'还原这些文件？');dialog.setAttribute('aria-labelledby','recovery-confirm-title');title.id='recovery-confirm-title';
    dialog.append(title,node('p','',`${plan.count ?? plan.items?.length ?? selection.size} 项，记录大小 ${t.h.bytes(plan.estimated_bytes || 0)}。`),node('p','',purge?'这次删除不会进入系统回收站，之后无法从盘序还原。':'将还原到原位置。遇到同名文件时不会直接覆盖。'));
    const names=node('ul','recovery-confirm-items');for(const row of (plan.items || []).slice(0,8))names.append(node('li','',row.name || row.original_path));dialog.append(names);
    const error=node('p','field-error');error.setAttribute('role','alert');dialog.append(error);
    const actions=node('div','dialog-actions'),cancel=button('返回调整',()=>dialog.close()),apply=button(purge?'确认永久删除':'确认还原',async()=>{
      if(t.busy)return;t.busy=true;apply.disabled=true;cancel.disabled=true;error.textContent='';
      try{const result=await t.h.api('/api/recovery/execute',{plan_id:plan.id,confirmation:plan.confirmation});await completed(t,result);dialog.close();}
      catch(e){error.textContent=e.message+'。可以核对结果，程序不会自动重复操作。';apply.hidden=true;const verify=button('核对操作结果',async()=>{verify.disabled=true;try{const value=await t.h.api('/api/recovery/plans/'+encodeURIComponent(plan.id));if(value.result){await completed(t,value.result);dialog.close();}else error.textContent='尚未完成操作，请关闭后重新预览。';}catch(e){error.textContent=e.message;}finally{verify.disabled=false;}});actions.append(verify);}
      finally{if(alive(t)){t.busy=false;cancel.disabled=false;paint(t);}}
    },'button primary');actions.append(cancel,apply);dialog.append(actions);
    dialog.addEventListener('cancel',e=>{if(t.busy)e.preventDefault();});dialog.addEventListener('close',()=>{dialog.remove();if(t.dialog===dialog)t.dialog=null;});t.root.append(dialog);dialog.showModal();
  }
  async function completed(t,result){
    if(!alive(t))return;
    selection.clear();t.message=result.action==='purge'?'永久删除结果已记录。':'还原结果已记录。';await t.h.changed?.(result);
    if(alive(t)){t.busy=false;await load(t);}
  }
  return {home,render,unmount};
})();
