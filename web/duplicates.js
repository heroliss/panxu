// Local evidence only: this page never starts an LLM task or a delete plan.
window.PanxuDuplicates=(()=>{
  const state={paths:'',budget:2,job:null,result:null,starting:false,error:'',group:null,page:0,filePage:0,notes:false,setup:true};
  let mounted=null,timer=null,serial=0;
  const node=(tag,cls='',text='')=>{const x=document.createElement(tag);x.className=cls;if(text!==undefined)x.textContent=String(text);return x;};
  const add=(root,...items)=>{items.flat().filter(Boolean).forEach(x=>root.append(x));return root;};
  const button=(label,handler,cls='button')=>{const x=node('button',cls,label);x.type='button';x.addEventListener('click',handler);return x;};
  const alive=t=>mounted===t&&t.root.isConnected;
  function unmount(){if(timer!==null)clearTimeout(timer);timer=null;mounted=null;serial++;}
  function schedule(t){if(!alive(t)||timer!==null||state.job?.status!=='running')return;timer=setTimeout(()=>{timer=null;poll(t);},500);}
  async function poll(t){
    const id=state.job?.id;if(!alive(t)||!id)return;
    try{
      const job=await t.helpers.api('/api/jobs/'+encodeURIComponent(id));
      if(!alive(t)||state.job?.id!==id)return;
      state.job=job;
      if(job.status!=='running'){
        if(job.result){state.result=job.result;state.group=null;state.page=0;state.filePage=0;}
        state.error=job.error || '';t.helpers.redraw();return;
      }
      updateProgress(t);schedule(t);
    }catch(error){if(alive(t)){state.error=error.message;updateProgress(t);}}
  }
  async function start(t){
    if(state.starting||state.job?.status==='running')return;
    const paths=state.paths.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
    if(!paths.length||paths.length>8){state.error='请输入1到8个文件夹位置，每行一个。';updateProgress(t);return;}
    state.starting=true;state.error='';state.setup=false;if(t.progress)updateProgress(t);else t.helpers.redraw();
    try{
      const response=await t.helpers.api('/api/duplicates/scan',{paths,max_bytes:state.budget*1024**3});
      state.job={id:response.job_id,status:'running',progress:{phase:'scanning',message:'正在读取文件信息…'}};
      state.group=null;state.notes=false;
    }catch(error){state.error=error.message;}
    finally{state.starting=false;if(mounted)mounted.helpers.redraw();}
  }
  function render(root,helpers){
    unmount();const t={root,helpers,serial};mounted=t;
    if(!state.paths)state.paths=helpers.defaultPath || '';
    const group=state.result?.groups?.find(x=>x.group_id===state.group);
    if(group){detail(t,group);return;}
    if(state.notes&&state.result){notes(t);return;}
    add(root,helpers.heading('查找重复文件','在本机按大小和 SHA-256 核对，无需连接模型。'));
    if(state.result&&!state.setup&&!state.starting&&state.job?.status!=='running'){
      const actions=node('div','duplicate-result-actions');
      add(actions,button('更换位置',()=>{state.setup=true;helpers.redraw();},'button'),button('重新检查',()=>start(t),'plain-button'));root.append(actions);
      if(state.error)add(root,node('p','field-error',state.error));
      results(t);return;
    }
    const form=node('section','duplicate-search-form');
    const label=node('label','field-label','要检查的文件夹');label.htmlFor='duplicate-paths';
    const paths=node('textarea','duplicate-paths');paths.id='duplicate-paths';paths.rows=2;paths.maxLength=16000;paths.value=state.paths;
    paths.addEventListener('input',()=>{state.paths=paths.value;});
    const help=node('p','field-help','粘贴文件夹路径；多个位置可分行填写。');
    const controls=node('div','duplicate-search-controls'),budgetLabel=node('label','duplicate-budget-label','本次校验上限');
    const budget=node('select');budget.setAttribute('aria-label','本次内容读取上限');
    [1,2,4,8].forEach(n=>{const option=node('option','',n+' GiB');option.value=String(n);budget.append(option);});budget.value=String(state.budget);
    budget.addEventListener('change',()=>{state.budget=Number(budget.value);});budgetLabel.append(budget);
    const startButton=button('开始本地查重',()=>start(t),'button primary');
    add(controls,budgetLabel,startButton);add(form,label,paths,help,controls,node('p','duplicate-read-note','只读取同大小候选，不上传内容，也不会删除文件。'));
    const progress=node('section','duplicate-progress'),message=node('p','duplicate-progress-message'),meter=node('progress');meter.setAttribute('aria-label','本地查重进行中');
    const counters=node('p','field-help'),cancel=button('停止检查',async()=>{
      if(state.job?.status!=='running'||t.cancelling)return;t.cancelling=true;updateProgress(t);
      try{await helpers.api('/api/jobs/'+encodeURIComponent(state.job.id)+'/cancel',{});}catch(error){state.error=error.message;}
      finally{t.cancelling=false;if(alive(t)){updateProgress(t);schedule(t);}}
    },'button');
    const retry=button('重新获取进度',()=>{state.error='';poll(t);},'button');
    const error=node('p','field-error');error.setAttribute('role','alert');
    add(progress,message,meter,counters,cancel,retry,error);add(root,form,progress);
    Object.assign(t,{paths,budget,startButton,progress,message,meter,counters,cancel,retry,error});
    form.hidden=state.starting||state.job?.status==='running';
    updateProgress(t);if(state.result&&!state.setup)results(t);schedule(t);
  }
  function updateProgress(t){
    if(!alive(t))return;const running=state.job?.status==='running',pending=running||state.starting;
    t.paths.disabled=pending;t.budget.disabled=pending;t.startButton.disabled=pending;
    t.startButton.textContent=state.starting?'正在开始…':running?'正在检查…':state.result?'重新检查':'开始本地查重';
    t.progress.hidden=!pending&&!state.error;
    t.message.textContent=state.starting?'正在准备本地检查…':running?(state.job.progress?.message || '正在核对文件…'):state.job?.status==='cancelled'?'检查已停止，下面保留已核对的结果。':state.job?.status==='failed'?'本次检查未完成，已有结果保留。':state.result?.partial?'本次只完成了部分检查。':state.result?'本次检查完成。':'';
    t.meter.hidden=!pending;t.cancel.hidden=!running;t.cancel.disabled=!!t.cancelling;
    const p=state.job?.progress || {};
    t.counters.textContent=running?['scanning'===p.phase?'读取文件信息':'核对同大小候选',Number.isFinite(p.files_seen)?'已发现 '+p.files_seen+' 个文件':'',Number.isFinite(p.bytes_read)?'已读取 '+t.helpers.bytes(p.bytes_read):''].filter(Boolean).join(' · '):'';
    t.error.textContent=state.error;t.error.hidden=!state.error;t.retry.hidden=!running||!state.error;
  }
  function summary(t,root){
    const result=state.result,s=result.summary || {};
    const header=node('div','duplicate-result-header');
    add(header,node('h2','',state.job?.status==='running'||state.error?'上次检查结果':'检查结果'),button('检查说明',()=>{state.notes=true;t.helpers.redraw();},'plain-button'));
    add(root,header,node('p','duplicate-scope',(result.roots || []).map(x=>typeof x==='string'?x:x.path || '').filter(Boolean).join(' · ')));
    const stats=node('div','duplicate-stats');
    [[s.content_groups,'组内容重复'],[s.checked_files,'个文件已核对'],[t.helpers.bytes(result.bytes_read || 0),'已读取内容']].forEach(([value,label])=>{add(stats,add(node('div'),node('strong','',value??0),node('span','',label)));});add(root,stats);
    if(result.partial||result.result_truncated){
      const scopeNote=result.result_truncated?`显示 ${s.returned_groups || 0} 组，已确认共 ${s.total_groups || 0} 组；部分路径未展示。`:'达到预算、存在跳过项目，或检查被停止；尚未核对的内容不能视为没有重复。';
      add(root,node('p','duplicate-partial',scopeNote));
    }
    add(root,node('p','field-help','内容相同不代表其中一份没有用途；此处只列核对结果，不自动选出删除项。'));
  }
  function results(t){
    const host=node('section','duplicate-results');add(t.root,host);summary(t,host);
    const groups=state.result.groups || [];
    if(!groups.length){add(host,node('div','duplicate-empty',state.result.partial?'已核对的范围内没有确认重复组。可查看检查说明，缩小范围或提高校验上限。':'在本次检查范围内没有确认重复组。'));return;}
    const pageSize=12,pageCount=Math.ceil(groups.length/pageSize);state.page=Math.min(state.page,pageCount-1);
    const list=node('div','duplicate-groups');
    groups.slice(state.page*pageSize,(state.page+1)*pageSize).forEach((group,index)=>{
      const card=node('article','duplicate-group-row'),copy=node('div','duplicate-group-copy');
      const hardlink=group.type==='hardlink';
      add(copy,node('h3','',(hardlink?'共享文件实体':'内容重复')+' · '+(group.items_total ?? group.items?.length ?? 0)+' 个位置'),node('p','field-help',(hardlink?'这些路径指向同一个实体，不能按独立副本计算空间。':'每份 '+t.helpers.bytes(group.size_bytes || 0)+' · SHA-256 一致')));
      const names=(group.items || []).slice(0,2);names.forEach(item=>add(copy,node('p','duplicate-location',item.path || item.name)));
      add(card,copy,button('查看这一组',()=>{state.group=group.group_id;state.filePage=0;t.helpers.redraw();},'button'));list.append(card);
    });add(host,list);
    if(pageCount>1)add(host,pagination(state.page,pageCount,value=>{state.page=value;t.helpers.redraw();},'组'));
  }
  function pagination(page,count,change,label){
    const nav=node('nav','duplicate-pagination');nav.setAttribute('aria-label','重复'+label+'分页');
    const prev=button('上一页',()=>change(page-1)),next=button('下一页',()=>change(page+1));prev.disabled=page===0;next.disabled=page+1>=count;
    return add(nav,prev,node('span','field-help',`${page+1} / ${count}`),next);
  }
  function detail(t,group){
    t.helpers.setContextBack('返回重复结果',()=>{state.group=null;t.helpers.redraw();});
    const hardlink=group.type==='hardlink';
    add(t.root,t.helpers.heading(hardlink?'共享文件实体':'内容相同的文件',hardlink?'这些路径共享同一个文件实体，不是多份独立副本。':'已通过 SHA-256 核对；保留哪份仍要结合用途和所在位置。'));
    add(t.root,node('p','duplicate-detail-summary',`${group.items_total ?? group.items.length} 个位置 · 每份 ${t.helpers.bytes(group.size_bytes || 0)}`));
    if(group.omitted_items)add(t.root,node('p','duplicate-partial',`这一组还有 ${group.omitted_items} 个位置未展示；当前结果有显示上限。`));
    const count=Math.max(1,Math.ceil(group.items.length/50));state.filePage=Math.min(state.filePage,count-1);
    const list=node('div','duplicate-file-list');
    group.items.slice(state.filePage*50,(state.filePage+1)*50).forEach(item=>{
      const row=node('article','duplicate-file-row'),copy=node('div');
      add(copy,node('h2','duplicate-file-name',item.name),node('p','duplicate-location',item.path));
      const modified=item.details?.timestamps?.modified_at || item.details?.modified_at;
      if(modified){const date=new Date(modified);if(Number.isFinite(date.getTime()))add(copy,node('p','field-help','修改于 '+date.toLocaleString('zh-CN')));}
      const status=node('span','duplicate-verified',hardlink?'同一实体':'内容一致');add(row,copy,status);list.append(row);
    });add(t.root,list);if(count>1)add(t.root,pagination(state.filePage,count,value=>{state.filePage=value;t.helpers.redraw();},'文件'));
    if(group.sha256){const proof=node('div','duplicate-proof');add(proof,node('span','field-help','SHA-256'),node('code','',group.sha256));add(t.root,proof);}
  }
  function notes(t){
    t.helpers.setContextBack('返回重复结果',()=>{state.notes=false;t.helpers.redraw();});
    add(t.root,t.helpers.heading('检查说明','这里记录实际检查范围、读取预算与尚未完成的部分。'));
    const r=state.result,s=r.summary || {},stats=node('dl','duplicate-note-facts');
    const lines=[['算法','按大小筛选后计算 SHA-256，不逐字节比较'],['检查文件',String(s.files_seen || 0)],['同大小候选',String(s.candidate_files || 0)],['已核对文件',String(s.checked_files || 0)],['内容读取',t.helpers.bytes(r.bytes_read || 0)],['读取上限',t.helpers.bytes(r.limits?.max_bytes || 0)]];
    lines.forEach(([name,value])=>add(stats,node('dt','',name),node('dd','',value)));add(t.root,stats);
    const skipped=Object.entries(s.skipped || {});
    if(skipped.length)add(t.root,node('p','field-help','有受保护、链接、云占位或不可读取内容时会跳过，不触发云文件下载。'));
    const warnings=node('ul','duplicate-warnings');(r.warnings || []).forEach(message=>warnings.append(node('li','',message)));if(warnings.childNodes.length)add(t.root,warnings);
    if(!r.partial&&!r.warnings?.length)add(t.root,node('p','field-help','本次预算内的检查已完成。相同内容只说明字节内容相同，不表示应用引用或文件用途相同。'));
  }
  return {render,unmount};
})();
