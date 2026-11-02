'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{runInNewContext}=require('node:vm');
const source=readFileSync('web/background-workspace.js','utf8'),tasks=readFileSync('web/tasks.js','utf8');
const assessment=tasks.slice(tasks.indexOf('window.PanxuAssessment ='),tasks.indexOf('// Provenance is host-owned'));
const plain=value=>JSON.parse(JSON.stringify(value));
const row=(id,rating=4,state='explicit',extra={})=>({id,name:id,kind:'startup',signature:'sig-'+id,actions:['disable_startup'],assessment:{state,rating,stale:false},...extra});
function fixture(){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.attrs={};this.style={};this.dataset={};this._text='';this.hidden=false;}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}set textContent(value){this._text=String(value);this.children=[];}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    setAttribute(key,value){this.attrs[key]=String(value);}
    addEventListener(name,callback){this.handlers[name]=callback;}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
    all(){return this.children.flatMap(child=>[child,...child.all()]);}
    fire(event){this.handlers[event]?.({preventDefault(){}});}
  }
  const window={},root=new Element('main'),environment={window,document:{createElement:tag=>new Element(tag)}};
  runInNewContext(assessment+'\n'+source,environment);
  return {api:window.PanxuInventoryRatings,shared:window.PanxuAssessment,root};
}
test('inventory reads the same rating and state meaning as canonical assessment presenter',()=>{
  const {api,shared}=fixture();
  for(const [state,rating]of [['explicit',1],['explicit',3],['explicit',5],['inherited',4],['unanalysed',null],['skipped',null],['no_advice',null]]){
    const item=row('owned',rating,state);assert.deepEqual(plain(api.read(item)),plain(shared.read(item)));
  }
});
test('an exact newer direct suggestion replaces the cached item assessment without mutating either',()=>{
  const {api}=fixture(),item=row('owned',1,'explicit',{assessment:{state:'explicit',rating:1,provenance:{generated_at:'2026-11-03T00:00:00Z'}}}),advice={cleanup_rating:4,analysis_state:'explicit',provenance:{generated_at:'2026-11-03T00:01:00Z'}},before=JSON.stringify([item,advice]);
  assert.equal(api.read(item,advice).rating,4);assert.equal(api.presentation(item,advice).direct,true);assert.equal(JSON.stringify([item,advice]),before);
});
test('foreign signatures and older or stale direct cache results cannot replace a valid host assessment',()=>{
  const {api}=fixture(),item=row('owned',2,'explicit',{assessment:{state:'explicit',rating:2,provenance:{generated_at:'2026-11-03T00:10:00Z'}}});
  for(const advice of [{signature:'another',cleanup_rating:5},{stale:true,cleanup_rating:5},{cleanup_rating:5,provenance:{generated_at:'2026-11-03T00:00:00Z'}}])assert.equal(api.read(item,advice).rating,2);
});
test('suggestions view includes keep, neutral, cleanup and explicit unscored conclusions, excluding untouched inventory',()=>{
  const {api}=fixture(),items=[row('keep',1),row('neutral',3),row('clean',5),row('unscored',null),row('skip',null,'skipped'),row('none',null,'no_advice'),row('unknown',null,'unanalysed')];
  assert.deepEqual(items.filter(item=>api.matches(item,{view:'suggestions'})).map(item=>item.id),['keep','neutral','clean','unscored','skip','none']);
  assert.equal(items.filter(item=>api.matches(item,{view:'all'})).length,7);
});
test('all five score stages and non-score categories filter distinctly using complete rows',()=>{
  const {api}=fixture(),items=[1,2,3,4,5].map(score=>row('score-'+score,score));items.push(row('unknown',null,'unanalysed'),row('unscored',null),row('skip',null,'skipped'),row('none',null,'no_advice'));
  for(let score=1;score<=5;score++)assert.deepEqual(items.filter(item=>api.matches(item,{rating:String(score)})).map(item=>item.id),['score-'+score]);
  assert.deepEqual(items.filter(item=>api.matches(item,{rating:'unrated'})).map(item=>item.id),['unknown','unscored']);
  assert.deepEqual(items.filter(item=>api.matches(item,{rating:'skipped'})).map(item=>item.id),['skip']);
  assert.deepEqual(items.filter(item=>api.matches(item,{rating:'no_advice'})).map(item=>item.id),['none']);
});
test('whole inventory counts include saved advice far outside the first50 rendered rows',()=>{
  const {api}=fixture(),items=Array.from({length:1200},(_,index)=>row('startup-'+index,null,'unanalysed'));
  const lookup=(id,signature)=>id==='startup-1199'&&signature==='sig-startup-1199'?{cleanup_rating:5,reason:'Owned late-page suggestion'}:null;
  const summary=api.summary(items,lookup);
  assert.equal(summary.total,1200);assert.equal(summary.counts['5'],1);assert.equal(summary.counts.unrated,1199);assert.equal(summary.analysed,1);
  assert.deepEqual(items.filter(item=>api.matches(item,{view:'suggestions',rating:'5'},lookup(item.id,item.signature))).map(item=>item.id),['startup-1199']);
});
test('summary keeps skipped, no-advice, unanalysed and stale counts separate from scored items',()=>{
  const {api}=fixture(),summary=api.summary([row('scored',4),row('old',2,'explicit',{assessment:{state:'explicit',rating:2,stale:true}}),row('skip',null,'skipped'),row('none',null,'no_advice'),row('new',null,'unanalysed')]);
  assert.equal(summary.scored,2);assert.equal(summary.analysed,4);assert.equal(summary.unanalysed,1);assert.equal(summary.stale,1);assert.equal(summary.counts.skipped,1);assert.equal(summary.counts.no_advice,1);
});
test('controls change only filter intent and never mutate selected objects or call model/action APIs',()=>{
  const {api,root}=fixture(),changes=[],state={view:'all',rating:'all'},items=[row('owned',4)];
  const controller=api.renderControls(root,{state,items,onChange:value=>changes.push(plain(value))});
  const suggestion=root.all().find(child=>child.dataset.inventoryView==='suggestions');suggestion.fire('click');
  const select=root.all().find(child=>child.tag==='select');select.value='4';select.fire('change');
  assert.deepEqual(changes,[{view:'suggestions',rating:'all'},{view:'suggestions',rating:'4'}]);assert.deepEqual(state,{view:'all',rating:'all'});assert.equal(items[0].actions[0],'disable_startup');assert.deepEqual(plain(controller.state()),changes[1]);
});
test('incremental summary updates retain control DOM and current filter without moving keyboard focus',()=>{
  const {api,root}=fixture(),controller=api.renderControls(root,{state:{view:'all',rating:'4'},items:[row('owned',null,'unanalysed')]});
  const select=root.all().find(child=>child.tag==='select'),tabs=root.all().filter(child=>child.tag==='button');
  controller.update([row('owned',4),row('second',null,'unanalysed')]);
  assert.equal(root.all().find(child=>child.tag==='select'),select);assert.equal(select.value,'4');assert.deepEqual(root.all().filter(child=>child.tag==='button'),tabs);assert.match(root.textContent,/已分析 1 \/ 2 项/);
});
test('empty inventories show no fabricated progress or ratings while a wholly unknown set has a neutral distribution',()=>{
  const {api,root}=fixture(),controller=api.renderControls(root,{items:[]});
  const distribution=root.all().find(child=>child.className==='inventory-rating-distribution');assert.equal(distribution.hidden,true);assert.equal(root.textContent.includes('100%'),false);
  controller.update([row('new',null,'unanalysed')]);assert.equal(distribution.hidden,false);
  const bar=root.all().find(child=>child.className==='inventory-rating-bar');assert.match(bar.attrs['aria-label'],/未评分 \/ 尚未分析：1 项/);
  const scores=root.all().filter(child=>child.className==='inventory-rating-segment'&&/^[1-5]$/.test(child.dataset.rating));assert.ok(scores.every(child=>child.hidden));
});
test('malformed stored scores stay unscored and cannot acquire selection or execution authority',()=>{
  const {api}=fixture();for(const invalid of [0,6,'5',NaN,Infinity]){const item=row('owned',invalid);assert.equal(api.read(item).rating,null);assert.equal(api.matches(item,{rating:'5'}),false);assert.equal(api.matches(item,{rating:'unrated'}),true);}
});
