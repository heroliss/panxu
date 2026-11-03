'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{runInNewContext}=require('node:vm');
const source=readFileSync('web/analysis-progress.js','utf8');
const task=(changes={})=>({id:'owned-analysis',status:'investigating',job_id:'owned-job',advice_total:2,analysis:{target_count:12},analysis_progress:{target_count:12,saved_count:2,remaining_count:10},advice:[],...changes});
function fixture(helpers={}){
  class Element{
    constructor(tag){this.tag=tag;this.className='';this.children=[];this.handlers={};this.attributes={};this.dataset={};this.style={};this.hidden=false;this.disabled=false;this.parent=null;this._text='';this.textWrites=0;}
    append(...items){for(const child of items){child.parent=this;this.children.push(child);}}
    remove(){if(this.parent){this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}}
    set textContent(value){this.textWrites++;this._text=String(value);this.children=[];}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    setAttribute(name,value){this.attributes[name]=String(value);}removeAttribute(name){delete this.attributes[name];}
    set value(value){this.attributes.value=String(value);}get value(){return Number(this.attributes.value||0);}
    addEventListener(name,listener){this.handlers[name]=listener;}
    all(){return [this,...this.children.flatMap(child=>child.all())];}
    async click(){if(!this.disabled&&!this.hidden)return this.handlers.click?.();}
    set innerHTML(_){throw Error('Untrusted text cannot be HTML.');}
  }
  const host=new Element('div'),window={};
  runInNewContext(source,{window,document:{createElement:tag=>new Element(tag)},console});
  const options={task:task(),...helpers},view=window.PanxuAnalysisProgress.mount(host,options);
  return {host,view,api:window.PanxuAnalysisProgress,find:cls=>host.all().find(item=>item.className.split(' ').includes(cls)),text:()=>host.textContent};
}
test('historical tool advice keeps completed counts and clearly separates fresh execution checks',()=>{
  const f=fixture({task:task({status:'completed',scope_kind:'cleanup_tools',advice_total:3,
    analysis_progress:{target_count:3,saved_count:3,rated_count:3,remaining_count:0,stale_count:3,rating_counts:{1:0,2:0,3:1,4:2,5:0}}})});
  assert.match(f.text(),/已保存 3 项建议 · 3 项目标/);assert.match(f.text(),/3 条建议执行前需刷新检查/);
  assert.equal(f.find('analysis-progress-meter').value,3);assert.equal(f.find('analysis-progress-note').hidden,false);
});

test('known selected targets show real saved progress without rebuilding the list-local DOM',()=>{
  const f=fixture(),meter=f.find('analysis-progress-meter'),header=f.find('analysis-progress-title');
  assert.equal(meter.max,12);assert.equal(meter.value,2);assert.match(f.text(),/已保存 2 项建议 · 12 项目标/);
  f.view.update(task({advice_total:3,analysis_progress:{target_count:12,saved_count:3,remaining_count:9}}));assert.equal(meter.value,3);assert.equal(f.find('analysis-progress-meter'),meter);assert.equal(f.find('analysis-progress-title'),header);
  assert.match(f.text(),/逐项出现在下方/);
});
test('repeated snapshots preserve readable text nodes and do not repeat live-region announcements',()=>{
  const initial=task(),f=fixture({task:initial,onPause:()=>{}}),readable=[f.find('analysis-progress-note'),f.find('analysis-progress-title'),f.find('analysis-progress-count'),f.find('analysis-progress-actions').children[0]],writes=readable.map(item=>item.textWrites);
  for(let n=0;n<3;n++)f.view.update(JSON.parse(JSON.stringify(initial)));
  assert.deepEqual(readable.map(item=>item.textWrites),writes);assert.equal(f.find('analysis-progress-note').attributes['aria-live'],'polite');
  f.view.update(task({analysis_progress:{target_count:12,saved_count:3,remaining_count:9}}));
  assert.equal(readable[0].textWrites,writes[0]+1);assert.equal(readable[1].textWrites,writes[1]);assert.equal(readable[2].textWrites,writes[2]+1);assert.equal(readable[3].textWrites,writes[3]);
});
test('unknown total stays indeterminate and explicit unknown cannot borrow an old target count',()=>{
  const f=fixture({task:task({analysis_progress:{target_count:null,saved_count:4}})}),meter=f.find('analysis-progress-meter');
  assert.equal(meter.hidden,false);assert.equal(Object.hasOwn(meter.attributes,'value'),false);assert.match(f.text(),/已保存 4 项建议/);assert.equal(f.text().includes('12 项目标'),false);
  f.view.update(task({status:'completed',analysis_progress:{target_count:null,saved_count:4}}));assert.equal(meter.hidden,true);assert.equal(f.text().includes('%'),false);
});
test('complete host counts describe all advice rather than a truncated public preview',()=>{
  const f=fixture({showRatingDistribution:true,task:task({advice_total:100,advice:[{id:'last-one',cleanup_rating:5}],analysis_progress:{target_count:120,saved_count:100,remaining_count:20,rated_count:98,skipped_count:1,no_advice_count:1,rating_counts:{1:30,2:20,3:10,4:20,5:18}}})});
  assert.match(f.text(),/已保存 100 项建议 · 120 项目标/);assert.match(f.text(),/建议保留 30/);assert.match(f.text(),/建议清理 18/);assert.match(f.text(),/跳过 1 项/);
  assert.match(f.find('analysis-progress-scorebar').attributes['aria-label'],/^评分分布：/);
});
test('legacy preview never claims to be the full score distribution',()=>{
  const f=fixture({showRatingDistribution:true,task:task({analysis_progress:undefined,advice_total:20,advice:[{id:'a',cleanup_rating:4},{id:'b',cleanup_rating:1}]})});
  assert.match(f.find('analysis-progress-scorebar').attributes['aria-label'],/^已显示建议的评分分布：/);
  assert.match(f.text(),/已保存 20 项建议/);assert.equal(f.find('analysis-progress-meter').hidden,false);assert.equal(Object.hasOwn(f.find('analysis-progress-meter').attributes,'value'),false);
});
test('target coverage accounts for inherited directory conclusions without equating suggestion count to coverage',()=>{
  const f=fixture({task:task({advice_total:1,analysis_progress:{target_count:100,saved_count:1,remaining_count:0,rated_count:1,rating_counts:{1:0,2:0,3:0,4:1,5:0}}})});
  assert.match(f.text(),/已保存 1 项建议 · 100 项目标/);assert.equal(f.find('analysis-progress-meter').value,100);assert.equal(f.find('analysis-progress-meter').max,100);
  assert.match(f.find('analysis-progress-meter').attributes['aria-valuetext'],/已有结论 100 项目标/);
});
test('pause does not claim success until host task actually stops and cannot be double-submitted',async()=>{
  let resolve,calls=0;const request=new Promise(done=>resolve=done),f=fixture({onPause:()=>{calls++;return request;}}),button=f.find('analysis-progress-actions').children[0];
  const pending=button.click();assert.equal(calls,1);assert.equal(button.disabled,true);assert.equal(button.textContent,'暂停中…');assert.match(f.text(),/正在停止后续请求/);
  await button.click();assert.equal(calls,1);resolve({job:{status:'cancelling'}});await pending;
  assert.equal(button.textContent,'暂停中…');assert.equal(button.disabled,true);
  f.view.update(task({status:'paused',job_id:null}));assert.equal(f.find('analysis-progress-title').textContent,'分析已暂停');assert.match(f.text(),/已保存 2 项建议/);
});
test('a rejected pause keeps old results visible and offers a genuine retry',async()=>{
  const f=fixture({onPause:async()=>{throw Error('暂时无法停止本次请求');}}),button=f.find('analysis-progress-actions').children[0];
  await button.click();assert.equal(button.disabled,false);assert.equal(button.textContent,'暂停分析');assert.match(f.text(),/暂时无法停止本次请求/);assert.match(f.text(),/已保存 2 项建议/);
});
test('limited state distinguishes request allowance from object totals and can continue',async()=>{
  const seen=[],f=fixture({task:task({status:'budget_exhausted',job_id:null,analysis_progress:{target_count:50,saved_count:12,rated_count:12,rating_counts:{1:2,2:2,3:2,4:3,5:3},budget:{model_requests:{used:8,limit:8}}}}),onPause:()=>{},onContinue:async t=>{seen.push(t.id);return {task:task({advice_total:12,analysis:{target_count:50}})};}});
  assert.match(f.text(),/本轮资源已用完/);assert.match(f.text(),/本轮 · 模型请求 8 \/ 8 次/);assert.equal(f.find('analysis-progress-budget-meter').value,8);
  const button=f.find('analysis-progress-actions').children[0];assert.equal(button.textContent,'继续分析');await button.click();assert.deepEqual(seen,['owned-analysis']);assert.equal(button.textContent,'暂停分析');
});
test('a tool-call limit highlights actual exhausted resource rather than a mostly unused model request allowance',()=>{
  const f=fixture({task:task({status:'budget_exhausted',analysis_progress:{target_count:12,saved_count:2,remaining_count:10,budget:{model_requests:{used:2,limit:24},tool_calls:{used:64,limit:64}}}})});
  assert.match(f.text(),/本轮 · 工具调用 64 \/ 64 次/);assert.equal(f.find('analysis-progress-budget-meter').value,64);assert.equal(f.find('analysis-progress-budget-meter').attributes['aria-label'],'本轮工具调用额度');
});
test('question and model settings use supplied navigation callbacks without issuing requests',async()=>{
  const calls=[],f=fixture({task:task({status:'needs_input',question:{id:'owned-question'}}),onQuestion:t=>calls.push(['question',t.id]),onContinue:()=>calls.push(['continue']),onSettings:t=>calls.push(['settings',t.id])});
  const actions=f.find('analysis-progress-actions');assert.equal(actions.children[0].hidden,true);assert.equal(actions.children[1].hidden,false);await actions.children[1].click();
  f.view.update(task({status:'needs_model'}));assert.equal(actions.children[0].textContent,'连接模型');await actions.children[0].click();assert.deepEqual(calls,[['question','owned-analysis'],['settings','owned-analysis']]);
});
test('late callback completion cannot replace a newer task or an unmounted region',async()=>{
  let resolve;const wait=new Promise(done=>resolve=done),f=fixture({onPause:()=>wait}),button=f.find('analysis-progress-actions').children[0];const pending=button.click();
  f.view.update(task({id:'newer-analysis',advice_total:9,analysis_progress:{target_count:12,saved_count:9,remaining_count:3}}));resolve({task:task({status:'paused',advice_total:2})});await pending;
  assert.match(f.text(),/已保存 9 项建议/);assert.equal(f.text().includes('分析已暂停'),false);
  f.view.unmount();assert.equal(f.host.children.length,0);f.view.update(task());assert.equal(f.host.children.length,0);
});
test('subscription is scoped, unsubscribed once, and mounting again leaves one region',()=>{
  let listener,stops=0;const f=fixture({taskId:'owned-analysis',subscribe:fn=>{listener=fn;return()=>stops++;}});
  listener(task({id:'another-task',advice_total:99}));assert.equal(f.text().includes('99'),false);listener(task({advice_total:5,analysis_progress:{target_count:12,saved_count:5,remaining_count:7}}));assert.match(f.text(),/已保存 5 项建议/);
  f.api.mount(f.host,{task:task({status:'completed'})});assert.equal(stops,1);assert.equal(f.host.children.length,1);f.view.unmount();assert.equal(stops,1);
  f.api.unmount();assert.equal(f.host.children.length,0);
});
test('an operation task is not misrepresented as an analysis and missing task hides the region',()=>{
  const f=fixture({task:task({task_kind:'operation',status:'executing'})});assert.equal(f.view.element.hidden,true);
  f.view.update(null);assert.equal(f.view.element.hidden,true);
});

test('internal checkpoints do not appear as a nearly exhausted whole-task budget',()=>{
  const f=fixture({onPause:()=>{},task:task({analysis_session:{limits:{model_requests:96,tool_calls:256,elapsed_minutes:30,total_tokens:1000000},used:{model_requests:6,tool_calls:12,elapsed_ms:12000,reported_usage_calls:0,input_tokens:null,output_tokens:null},checkpoints:1},analysis_progress:{target_count:12,saved_count:5,remaining_count:7,budget:{model_requests:{used:6,limit:6}}}})});
  assert.equal(f.find('analysis-progress-budget').hidden,true);
  assert.equal(f.find('analysis-progress-actions').children[0].textContent,'暂停分析');
  assert.equal(f.text().includes('费用'),false);
});
test('session resource meter uses actual overall consumption and preserves unknown token usage',()=>{
  const f=fixture({task:task({analysis_session:{limits:{model_requests:96,tool_calls:256,elapsed_minutes:30,total_tokens:1000000},used:{model_requests:80,tool_calls:30,elapsed_ms:60000,reported_usage_calls:0,input_tokens:null,output_tokens:null}}})});
  assert.equal(f.find('analysis-progress-budget').hidden,false);assert.match(f.text(),/本次连续分析 · 模型请求 80 \/ 96 次/);
  assert.equal(f.find('analysis-progress-budget-meter').max,96);
});
test('repeated observations pause with an explanation rather than claiming a numerical item limit',()=>{
  const f=fixture({task:task({status:'budget_exhausted',analysis_session:{stop_reason:'no_progress',limits:{model_requests:96},used:{model_requests:8}},analysis_progress:{target_count:12,saved_count:3,remaining_count:9}})});
  assert.match(f.text(),/没有取得新信息/);assert.match(f.text(),/已保存 3 项建议/);assert.match(f.text(),/还有 9 项目标待分析/);
});
