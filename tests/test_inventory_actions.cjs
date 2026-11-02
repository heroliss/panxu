'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','app.js'),'utf8');
const extract=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const plain=value=>JSON.parse(JSON.stringify(value));
const entry=(id,kind='startup')=>({id,kind,name:id,signature:'sig-'+id,actions:['disable_startup']});
function actionFixture(responder){
  const items=[entry('chosen'),entry('kept')],calls=[];
  const state={view:'background',catalogRevision:'revision-A'};
  const environment={state,itemMap:new Map(items.map(item=>[item.id,item])),analysisIds:()=>new Set(items.map(item=>item.id)),
    canSelect:item=>item.id!=='kept',toast:value=>calls.push(['toast',value]),task:async callback=>callback(),
    api:async(path,body)=>{calls.push([path,plain(body)]);return responder?.() || {task:{id:'prepared'},plan:{items:[items[0]],version:1},picked_ids:['chosen']};},
    window:{PanxuCleanupReview:{acceptPrepared:data=>{calls.push(['prepared',data.task.id]);return true;}},PanxuTasks:{accept:task=>calls.push(['accept',task.id])}},
    openTaskResults:task=>calls.push(['open',task.id])};
  runInNewContext(extract('  async function manualSelected(','  function dispositionControl(')+'this.run=manualSelected;',environment);
  return {environment,state,calls};
}
test('selected inventory action prepares exact user picks through the host and never calls model analysis or approval',async()=>{
  const f=actionFixture();await f.environment.run();
  assert.deepEqual(f.calls,[['/api/actions/plan',{ids:['chosen'],snapshots:{chosen:'sig-chosen'},revision:'revision-A'}],['prepared','prepared'],['accept','prepared'],['open','prepared']]);
});
test('late prepared inventory action cannot navigate away from the user new page',async()=>{
  let resolve;const f=actionFixture(()=>new Promise(done=>{resolve=done;}));const pending=f.environment.run();
  f.state.view='cleanup';resolve({task:{id:'late'},plan:{version:1,items:[entry('chosen')]}});await pending;
  assert.equal(f.calls.filter(call=>['open','accept','prepared'].includes(call[0])).length,0);
});
test('explicit reanalysis flag is passed only on deliberate request; ordinary analysis leaves skip logic to host',async()=>{
  const calls=[],environment={state:{view:'background',query:'',risk:'all',subtype:'startup'},analysisDepth:'auto',analysisReanalyze:false,
    analysisIds:()=>new Set(['selected']),task:async callback=>callback(),api:async(path,body)=>{calls.push([path,plain(body)]);return {task:{id:'analysis'}};},window:{PanxuTasks:{accept(){}}},openTaskResults(){},setView(){}};
  runInNewContext(extract('  async function analyzeList(','  async function showAdvice(')+'this.run=analyzeList;',environment);
  await environment.run();await environment.run(entry('single'),null,true);
  assert.equal(calls[0][1].reanalyze,false);assert.equal(calls[1][1].reanalyze,true);assert.deepEqual(calls[0][1].ids,['selected']);
});
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.className='';this.handlers={};this._text='';this.parent=null;}
  append(...children){for(const child of children){if(child==null)continue;child.parent=this;this.children.push(child);}}
  replaceChildren(...children){this.children=[];this.append(...children);}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  set textContent(value){this.children=[];this._text=String(value);}
  get firstChild(){return this.children[0];}
  get isConnected(){return true;}
  setAttribute(key,value){this[key]=value;}
  addEventListener(key,value){this.handlers[key]=value;}
  all(){return this.children.flatMap(child=>[child,...child.all()]);}
  querySelector(selector){return this.all().find(child=>selector.startsWith('.')?child.className.split(' ').includes(selector.slice(1)):Object.hasOwn(child.dataset,selector.slice(6,-1).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())));}
}
test('full filter selection over500 items is atomic and leaves previous picks intact',()=>{
  const selected=new Set(['existing']),messages=[];
  const environment={el:tag=>new Element(tag),canAnalyze:()=>true,analysisSelected:item=>selected.has(item.id),analysisIds:()=>selected,
    toast:value=>messages.push(value),renderResults(){},renderSelection(){},toggleAnalysis:(item,checked)=>checked?selected.add(item.id):selected.delete(item.id)};
  runInNewContext(extract('  function selectionBox(','  function renderInventoryAdvice(')+'this.run=selectionBox;',environment);
  const check=environment.run(Array.from({length:700},(_,i)=>entry('row-'+i)),'全选');check.checked=true;check.handlers.change();
  assert.deepEqual([...selected],['existing']);assert.equal(messages.length,1);assert.ok(messages[0].includes('原有选择保留'));
});
test('large inventory replaces fixed50-row pages instead of accumulating DOM and keeps off-page selections',async()=>{
  const host=new Element('section'),rows=Array.from({length:1200},(_,i)=>entry('app-'+i)),selected=new Set(['app-0']),reveals=[];
  const create=(tag,cls='',value)=>{const node=new Element(tag);node.className=cls;if(value!=null)node.textContent=value;return node;};
  const environment={inventoryRatingControls:null,state:{view:'background',inventoryOffset:0,limit:50000},$:()=>host,visibleItems:()=>rows,sectionItems:()=>rows,canAnalyze:()=>true,
    analysisIds:()=>selected,analysisSelected:item=>selected.has(item.id),selectionBox:()=>create('input'),append:(parent,...children)=>{parent.append(...children);return parent;},el:create,
    button:(label,callback,cls)=>{const node=create('button',cls,label);node.addEventListener('click',callback);return node;},
    itemTitle:(_,node)=>node,kindNames:{startup:'启动项'},bytes:value=>value,itemSize:()=>0,api(){},showAdvice(){},showItem(){},analyzeList(){},
    window:{PanxuTasks:{adviceFor(){},lookupAdvice:async()=>{}},PanxuAssessment:{render:()=>create('span')},PanxuAdvicePresentation:{render:()=>create('p')},PanxuListNavigation:{capture:root=>({root}),reveal:(target,intent,label)=>reveals.push({target,intent,label})}}};
  runInNewContext(extract('  function renderInventoryAdvice(','  function renderSelection()')+'this.run=renderResults;',environment);
  environment.run();await new Promise(setImmediate);
  assert.equal(host.all().filter(node=>node.className==='item-row').length,50);assert.equal(reveals.length,0);
  const next=host.all().find(node=>node.tag==='button'&&node.textContent==='下一页');next.handlers.click();await new Promise(setImmediate);
  assert.equal(environment.state.inventoryOffset,50);assert.equal(host.all().filter(node=>node.className==='item-row').length,50);assert.deepEqual([...selected],['app-0']);
  assert.ok(host.textContent.includes('51–100 / 1200 项'));
  assert.equal(reveals.length,1);assert.equal(reveals[0].target.className,'table-scroll');assert.ok(reveals[0].target.textContent.includes('app-50'));assert.equal(reveals[0].intent.root,host);
  environment.run();await new Promise(setImmediate);assert.equal(reveals.length,1,'an ordinary inventory repaint does not navigate the viewport');
});
test('desktop shell bounds the scroll region and gives list and Inspector independent scroll containers',()=>{
  const css=readFileSync(join(__dirname,'..','web','style.css'),'utf8');
  const rules=selector=>[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(match=>match[1].trim()===selector).map(match=>match[2]).join(';');
  // Both panes must scroll inside the finite viewport: overflow:auto alone on
  // an unbounded parent would silently reintroduce a shared document scrollbar.
  assert.match(rules('.app-body'),/height:100vh/);assert.match(rules('.app-body'),/overflow:hidden/);
  assert.match(rules('.content-grid'),/min-height:0/);assert.match(rules('.content-grid'),/grid-template-rows:minmax\(0,1fr\)/);
  for(const selector of ['.content-grid>main','.content-grid>.inspector']){
    assert.match(rules(selector),/min-height:0/);assert.match(rules(selector),/overflow:auto/);assert.match(rules(selector),/overscroll-behavior:contain/);
  }
});
