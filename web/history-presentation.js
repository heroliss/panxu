'use strict';

// Present durable receipts, never infer that a simulated operation changed Windows.
window.PanxuHistoryPresentation = (() => {
  const recordPageSize=10,itemPageSize=20;
  const actionLabels={delete:'删除',disable_startup:'关闭开机启动',stop_service:'停止服务',stop_process:'结束进程',uninstall:'卸载',specialist_cleanup:'专项操作'};
  const outcomeLabels={simulated:'已记录',succeeded:'已记录并核验',failed:'未完成',cancelled:'已取消，未执行',unverified:'结果待核实',running:'结果待核实',skipped:'已跳过'};
  const currentLabels={undone:'已撤销记录',purged:'已永久删除记录',restored:'已还原记录',retained:'可还原',unverified:'当前状态待核实'};
  const parameterLabels={volume:'目标盘符',mode:'管理方式',operation:'操作',browser:'浏览器',profile:'资料配置',time_range:'时间范围',data_type:'清理类别',initial_mb:'初始大小（MB）',maximum_mb:'最大大小（MB）',quota_percent:'占用额度（%）',target_path:'目标目录',folder:'系统目录',program:'工具',application:'软件',published_name:'驱动包',feature_name:'组件名称',provider:'同步客户端',project_path:'项目目录',virtual_disk_path:'虚拟磁盘'};
  const valueLabels={full:'完整休眠',reduced:'仅保留快速启动',off:'关闭',system:'系统管理',custom:'自定义大小',review:'查看当前配置',quota:'调整占用额度',disable:'关闭系统保护',edge:'Microsoft Edge',chrome:'Google Chrome',firefox:'Firefox',last_hour:'最近一小时',last_day:'最近一天',last_week:'最近一周',all:'全部',history:'浏览记录',cookies:'Cookie与登录状态',download_history:'下载记录',site_data:'站点与离线数据',prune:'清理不再使用的缓存',clean:'清理缓存',wheels:'已构建安装包缓存',verify:'检查缓存完整性',documents:'文档',pictures:'图片',desktop:'桌面',downloads:'下载',onedrive:'OneDrive',dropbox:'Dropbox',google_drive:'Google Drive',current:'当前用户'};
  const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  function button(label,run){const b=node('button','button small',label);b.type='button';b.addEventListener('click',run);return b;}
  function when(value){const date=new Date(value);return !value?'时间未记录':Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',{hour12:false});}
  function outcome(row,current){
    const original=outcomeLabels[row.status] || '结果未记录';
    const updated=current==='not_applied'?(row.status==='cancelled'?'':'未提交记录'):current==='recorded'&&['failed','unverified','running'].includes(row.status)?'有处理记录':currentLabels[current];
    return {text:updated&&!(current==='unverified'&&original.includes('待核实'))?`${original} · ${updated}`:original,tone:row.status==='failed'?'attention':['unverified','running'].includes(row.status)||current==='unverified'?'review':'neutral'};
  }
  function ordinaryRows(batch){return (Array.isArray(batch.items)?batch.items:[]).map(item=>{const current=batch.status==='undone'?'undone':item.current_state,row={...item,status:item.verification_status || item.status};return {...item,current_state:current,...outcome(row,current==='recorded'&&item.recovery_status==='retained'&&!item.verification_status?'retained':current)};});}
  function canUndoBatch(batch){const total=batch.item_count ?? batch.items?.length ?? 0,counts=batch.recovery_counts;return batch.status!=='undone'&&!Boolean(batch.items?.length&&batch.items.every(item=>['undone','purged'].includes(item.current_state)))&&!Boolean(total&&counts?.retained===0&&(counts.purged || 0)+(counts.restored || 0)>=total);}
  function groupOrdinary(history){
    const rows=[],groups=new Map();
    for(const batch of history){
      const ref=batch.operation_group;
      // A legacy batch may contain siblings. Its undo remains a whole-batch action.
      if(!ref||typeof ref.task_id!=='string'||!ref.task_id||ref.task_id.length>160||!Number.isSafeInteger(ref.plan_version)||ref.plan_version<1||!Array.isArray(batch.items)||batch.items.length!==1){rows.push({batch});continue;}
      const key=JSON.stringify([ref.task_id,ref.plan_version]);let group=groups.get(key);
      if(!group){group={operation_group:ref,created_at:batch.created_at,items:[],batches:[]};groups.set(key,group);rows.push(group);}
      group.batches.push(batch);group.items.push(...ordinaryRows(batch).map(item=>({...item,receipt_batch_id:batch.id,undoable:canUndoBatch(batch)})));
    }
    return rows;
  }
  function groupSummary(group){
    const rows=group.items,count=predicate=>rows.filter(predicate).length,parts=[];
    for(const [label,predicate] of [
      ['待核实',row=>['unverified','running'].includes(row.verification_status)||row.current_state==='unverified'],
      ['未完成',row=>row.verification_status==='failed'],
      ['已撤销',row=>row.current_state==='undone'],
      ['已永久删除',row=>row.current_state==='purged'],
      ['可还原',row=>row.current_state==='recorded'&&row.recovery_status==='retained']]){
      const total=count(predicate);if(total)parts.push(`${total} 项${label}`);
    }
    return parts.join(' · ');
  }
  function parameters(values){return Object.entries(values || {}).filter(([key,value])=>parameterLabels[key]&&value!==undefined&&value!==null&&value!=='').map(([key,value])=>`${parameterLabels[key]}：${valueLabels[value] || String(value)}`).join(' · ');}
  function specialistRows(plan){
    const results=new Map((Array.isArray(plan.result?.items)?plan.result.items:[]).map(item=>[item.id,item]));
    const entries=Array.isArray(plan.entries)?plan.entries:[];
    const rows=entries.length?entries:Array.isArray(plan.result?.items)?plan.result.items:[];
    return rows.map(entry=>{
      const result=results.get(entry.id),current=plan.status==='undone'?'undone':result?.current_state;
      const copy=result?outcome(result,current):{text:plan.status==='awaiting_review'?'待确认，未执行':plan.status==='cancelled'?'已取消，未执行':plan.status==='executing'?'等待结果':'未记录执行结果',tone:'neutral'};
      return {...entry,...result,path:entry.path || '',parameters:parameters(entry.parameters),...copy,current_state:current};
    });
  }
  function paged(host,rows,size,label,render){
    let page=0;const content=node('div','history-page'),pager=node('nav','history-pagination');pager.setAttribute('aria-label',label);
    const count=node('span','history-page-count');count.setAttribute('aria-live','polite');
    function change(delta){const navigation=window.PanxuListNavigation?.capture(content);page+=delta;paint();window.PanxuListNavigation?.reveal(content,navigation,label+'，'+count.textContent);}
    const previous=button('上一页',()=>change(-1)),next=button('下一页',()=>change(1));
    function paint(){
      const start=page*size;content.replaceChildren();rows.slice(start,start+size).forEach(row=>content.append(render(row)));
      previous.disabled=page===0;next.disabled=start+size>=rows.length;count.textContent=`${start+1}–${Math.min(start+size,rows.length)} / ${rows.length} 项`;
    }
    pager.append(previous,count,next);host.append(content);if(rows.length>size)host.append(pager);paint();
  }
  function renderRows(host,rows,h){
    if(!rows.length){host.append(node('p','history-note','这条记录没有保存具体项目。'));return;}
    paged(host,rows,itemPageSize,'具体操作分页',row=>{
      const entry=node('div','history-entry'),copy=node('div','history-entry-copy'),title=node('div','history-entry-title');
      title.append(node('span','history-action',h.actionLabel?.(row.action,row.kind) || actionLabels[row.action] || '操作'));
      const name=node('strong','history-entry-name',row.name || row.title || row.path || '未命名项目');title.append(h.itemTitle?h.itemTitle(row,name):name);copy.append(title);
      if(row.path)copy.append(node('p','history-entry-path',row.path));
      if(row.parameters)copy.append(node('p','history-entry-path',row.parameters));
      if(row.at)copy.append(node('p','history-entry-time',when(row.at)));
      if(row.message&&!['操作结果已记录并核验。','你已取消，这项没有提交。'].includes(row.message))copy.append(node('p','history-entry-message',row.message));
      const result=node('span','history-entry-state',row.text);result.dataset.tone=row.tone;
      if(row.receipt_batch_id){
        const actions=node('div','history-entry-actions'),undo=button('撤销',()=>h.onUndo?.(row.receipt_batch_id));undo.disabled=!row.undoable;undo.setAttribute('aria-label',`撤销对“${row.name || '此项目'}”的操作`);actions.append(result,undo);entry.append(copy,actions);
      }else entry.append(copy,result);
      return entry;
    });
  }
  function recordHeader(card,title,status,date){const top=node('div','history-top');top.append(node('h3','',title),node('span','source-badge',status));card.append(top,node('p','history-date',when(date)));}
  function renderOrdinary(host,history,h={}){
    host.replaceChildren();if(!history.length)return;
    host.append(node('h2','history-section-title','文件与后台操作'));
    host.append(node('p','field-help','可还原的文件可到“已删除文件”查看；撤销后可重新选择相关项目。'));
    const records=groupOrdinary(history);
    paged(host,records,recordPageSize,'操作记录分页',record=>{
      if(record.operation_group){
        const card=node('article','history-card history-operation-group'),rows=record.items;
        const status=rows.every(row=>row.current_state==='undone')?'已撤销':rows.some(row=>row.verification_status==='failed')?'有未完成项':rows.some(row=>row.verification_status||row.current_state==='unverified')?'有待核实项':rows.some(row=>['undone','purged'].includes(row.current_state))?'部分记录已更新':'已记录';
        recordHeader(card,`当前显示 ${rows.length} 项操作`,status,record.created_at);
        const summary=groupSummary(record);if(summary)card.append(node('p','history-group-summary',summary));
        renderRows(card,rows,h);
        if(rows.some(row=>row.action==='delete'&&['file','directory'].includes(row.kind))){const footer=node('div','history-footer-actions');footer.append(button('查看已删除文件',()=>h.onRecovery?.()));card.append(footer);}
        return card;
      }
      const batch=record.batch;
      const card=node('article','history-card'),status=batch.status==='undone'?'已撤销':batch.status==='partially_undone'?'部分记录已更新':(batch.items || []).some(item=>item.verification_status)?'结果待核实':'已记录';
      recordHeader(card,`已记录 ${batch.item_count ?? batch.items?.length ?? 0} 项操作`,status,batch.created_at);renderRows(card,ordinaryRows(batch),h);
      if(batch.status==='partially_undone'&&(batch.items || []).some(item=>!item.current_state||item.current_state==='unverified'))card.append(node('p','history-note','本批部分项目的状态已更新；原操作保留在此，文件的当前状态可在“已删除文件”中查看。'));
      const footer=node('div','history-footer'),actions=node('div','history-footer-actions'),undo=button('撤销',()=>h.onUndo?.(batch.id));
      undo.disabled=!canUndoBatch(batch);actions.append(undo);
      if((batch.items || []).some(item=>item.action==='delete'&&['file','directory'].includes(item.kind)))actions.append(button('查看已删除文件',()=>h.onRecovery?.()));
      if(batch.recovery_counts?.purged)footer.append(node('span','','永久删除的记录不能还原，其余可恢复项仍可撤销。'));
      footer.append(actions);card.append(footer);return card;
    });
    if(history.length>=100)host.append(node('p','history-note','仅显示最近100条处理记录；同次操作可能还有更早的项目未显示。'));
  }
  function renderSpecialist(host,history,h={}){
    host.replaceChildren();if(!history.length)return;
    const heading=node('header','history-section-heading');heading.append(node('h2','history-section-title','专项操作'),node('p','field-help','工具、处理范围与结果直接列在下方。'));host.append(heading);
    const labels={awaiting_review:'待确认',completed:'已记录',cancelled:'已取消',interrupted:'中断待核实',undone:'已撤销记录',executing:'进行中'};
    paged(host,history,recordPageSize,'专项记录分页',plan=>{
      const card=node('article','history-card');recordHeader(card,plan.title || '专项操作',labels[plan.status] || '待核实',plan.completed_at || plan.created_at);renderRows(card,specialistRows(plan),h);
      const actions=node('div','history-footer-actions');actions.append(button('查看方案与结果',()=>h.onOpen?.(plan)));
      const undoable=(plan.result?.items || []).some(item=>!['undone','purged'].includes(item.current_state)&&(item.status==='succeeded'||item.batch_id));
      if(undoable&&['completed','interrupted'].includes(plan.status))actions.append(button('撤销记录',()=>h.onUndo?.(plan.id)));
      card.append(actions);return card;
    });
    if(history.length>=100)host.append(node('p','history-note','显示最近100条专项记录。'));
  }
  function renderAttempts(host,attempts,h={}){
    host.replaceChildren();if(!attempts.length)return;
    host.append(node('h2','history-section-title','未完成与待核实'));
    const rows=attempts.map(attempt=>({...attempt.item,...attempt,...outcome(attempt,attempt.current_state),at:attempt.updated_at || attempt.created_at}));
    renderRows(host,rows,h);
    if(h.hasMore)host.append(node('p','history-note','这里只显示最近100条未完成或待核实记录。'));
  }
  return {ordinaryRows,groupOrdinary,specialistRows,parameters,renderOrdinary,renderSpecialist,renderAttempts,recordPageSize,itemPageSize};
})();
