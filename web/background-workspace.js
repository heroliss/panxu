'use strict';

// Inventory uses the same assessment meaning as file/task reviews. This module
// only projects and filters host observations; it neither owns a selection nor
// starts analysis, scans or operations. Pass the complete inventory, not a page.
window.PanxuInventoryRatings = (() => {
  const scoreNames={1:'建议保留',2:'倾向保留',3:'待你决定',4:'倾向清理',5:'建议清理'};
  const options=[['all','全部倾向'],['5','5 分 · 建议清理'],['4','4 分 · 倾向清理'],['3','3 分 · 待你决定'],['2','2 分 · 倾向保留'],['1','1 分 · 建议保留'],['skipped','已跳过'],['unrated','未评分 / 尚未分析'],['no_advice','暂无建议']];
  const normal=value=>({view:value?.view==='suggestions'?'suggestions':'all',rating:options.some(([id])=>id===value?.rating)?value.rating:'all'});
  const time=value=>Date.parse(value?.provenance?.generated_at || value?.generated_at || value?.at || '') || 0;
  function presentation(item={},advice=null){
    const assessment=item.assessment;
    if(!advice || advice.signature&&item.signature&&advice.signature!==item.signature)return {record:item,direct:false};
    if(assessment&&assessment.state!=='unanalysed'&&!assessment.stale){
      if(advice.stale || time(assessment)>time(advice))return {record:item,direct:false};
    }
    return {record:advice,direct:true};
  }
  function read(item={},advice=null){
    const chosen=presentation(item,advice);
    if(window.PanxuAssessment?.read)return window.PanxuAssessment.read(chosen.record,{direct:chosen.direct});
    // Unknown must stay unknown if the shared presenter has not loaded yet.
    return {state:'unanalysed',rating:null,stale:false};
  }
  function category(value){
    if(value.state==='skipped'||value.state==='no_advice')return value.state;
    return value.rating!==null?String(value.rating):'unrated';
  }
  function matches(item,filters={},advice=null){
    const value=read(item,advice),wanted=normal(filters);
    if(wanted.view==='suggestions'&&value.state==='unanalysed')return false;
    return wanted.rating==='all'||category(value)===wanted.rating;
  }
  function summary(items=[],adviceFor){
    const result={total:0,analysed:0,unanalysed:0,scored:0,stale:0,counts:Object.fromEntries(options.filter(([id])=>id!=='all').map(([id])=>[id,0]))};
    for(const item of items){
      const value=read(item,adviceFor?.(item.id,item.signature)),key=category(value);
      result.total++;result.counts[key]++;
      if(value.state==='unanalysed')result.unanalysed++;else result.analysed++;
      if(value.rating!==null)result.scored++;
      if(value.stale)result.stale++;
    }
    return result;
  }
  const node=(tag,cls='',text)=>{const value=document.createElement(tag);value.className=cls;if(text!==undefined)value.textContent=String(text);return value;};
  function renderControls(host,helpers={}){
    let current=normal(helpers.state),items=helpers.items||[];
    const root=node('section','inventory-rating-controls'),line=node('div','inventory-rating-control-line'),tabs=node('div','subtabs inventory-view-tabs');
    tabs.setAttribute('role','group');tabs.setAttribute('aria-label','后台与软件查看方式');
    const buttons=new Map();
    for(const [id,label]of [['suggestions','已有建议'],['all','全部项目']]){
      const entry=node('button','subtab',label);entry.type='button';entry.dataset.inventoryView=id;
      entry.addEventListener('click',()=>{if(current.view===id)return;current={...current,view:id};paintState();helpers.onChange?.({...current});});
      buttons.set(id,entry);tabs.append(entry);
    }
    const select=node('select','inventory-rating-filter');select.setAttribute('aria-label','筛选清理倾向');
    for(const [id,label]of options){const option=node('option','',label);option.value=id;select.append(option);}
    select.addEventListener('change',()=>{current={...current,rating:select.value};paintState();helpers.onChange?.({...current});});
    line.append(tabs,select);
    const distribution=node('div','inventory-rating-distribution'),bar=node('div','inventory-rating-bar'),legend=node('div','inventory-rating-legend'),status=node('p','field-help inventory-rating-status');
    bar.setAttribute('role','img');bar.setAttribute('aria-label','尚无评分分布');
    const segments=new Map(),labels=new Map();
    for(const key of ['1','2','3','4','5','unrated','skipped','no_advice']){
      const segment=node('span','inventory-rating-segment');segment.dataset.rating=key;segment.setAttribute('aria-hidden','true');segments.set(key,segment);bar.append(segment);
      if(scoreNames[key]){const label=node('span','inventory-rating-key');label.dataset.rating=key;labels.set(key,label);legend.append(label);}
    }
    distribution.append(bar,legend);root.append(line,distribution,status);host.append(root);
    function paintState(){
      for(const [id,entry]of buttons){entry.setAttribute('aria-pressed',String(id===current.view));entry.className='subtab'+(id===current.view?' active':'');}
      select.value=current.rating;
    }
    function update(nextItems=items,nextState=current){
      items=Array.isArray(nextItems)?nextItems:[];current=normal(nextState);paintState();
      const data=summary(items,helpers.adviceFor),description=[];
      for(const [key,segment]of segments){const count=data.counts[key]||0;segment.style.flexGrow=String(count);segment.hidden=count===0;segment.title=(scoreNames[key]||{'unrated':'未评分 / 尚未分析',skipped:'已跳过',no_advice:'暂无建议'}[key])+`：${count} 项`;if(count)description.push(segment.title);}
      bar.setAttribute('aria-label',description.length?'评分分布：'+description.join('，'):'当前列表还没有项目');
      distribution.hidden=!data.total;
      for(const [key,label]of labels){label.textContent=scoreNames[key]+' '+data.counts[key];label.title=`${key} 分：${data.counts[key]} 项`;}
      const extras=[data.unanalysed?`尚未分析 ${data.unanalysed} 项`:'',data.counts.skipped?`已跳过 ${data.counts.skipped} 项`:'',data.counts.no_advice?`暂无建议 ${data.counts.no_advice} 项`:'',data.stale?`${data.stale} 项建议需重新核对`:''].filter(Boolean);
      status.textContent=data.total?`已分析 ${data.analysed} / ${data.total} 项${extras.length?' · '+extras.join(' · '):''}`:'检查后可按清理倾向筛选，再用列表上的全选框选择这一组。';
      return data;
    }
    update();
    return {element:root,update,state:()=>({...current}),remove:()=>root.remove()};
  }
  return {presentation,read,matches,summary,renderControls};
})();
