'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');

function fixture(){
  class Element{
    constructor(tag){this.tag=tag;this.children=[];this.dataset={};this._text='';this.attrs={};}
    set textContent(value){this._text=String(value);this.children=[];}get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    append(...children){this.children.push(...children);}setAttribute(name,value){this.attrs[name]=value;}
    set innerHTML(_){throw new Error('advice text must remain literal');}
  }
  const window={},document={createElement:tag=>new Element(tag)};
  runInNewContext(readFileSync(join(__dirname,'..','web','tasks.js'),'utf8'),{window,document});
  const render=(record,options)=>window.PanxuAdvicePresentation.render(record,options);
  const fields=node=>Object.fromEntries(node.children.map(child=>[child.dataset.advicePart,child.children[1].textContent]));
  return {render,fields};
}

test('shared explanations show all four complete labelled fields and prefer the saved assessment over older raw inventory text',()=>{
  const f=fixture(),reason='<img src=x>很长的分析依据。'.repeat(40),node=f.render({kind:'file',purpose:'旧用途',reason:'旧原因',assessment:{state:'explicit',rating:4,purpose:'最新用途',reason,impact:'重新下载后可再生成。'}});
  assert.equal(node.children.length,4);assert.deepEqual(node.children.map(child=>child.children[0].textContent),['用途','建议','原因','影响']);
  assert.equal(f.fields(node).reason,reason);assert.equal(f.fields(node).purpose,'最新用途');assert.equal(f.fields(node).impact,'重新下载后可再生成。');assert.ok(!node.textContent.includes('旧原因'));assert.ok(node.children.every(child=>child.tag==='div'));
  assert.equal(node.dataset.rating,'4');assert.equal(node.dataset.assessmentState,'explicit');
});

test('suggestion surfaces retain the score and stale state separately from operation meaning',()=>{
  const f=fixture();
  for(let rating=1;rating<=5;rating++)assert.equal(f.render({kind:'file',assessment:{state:'explicit',rating}}).dataset.rating,String(rating));
  const stale=f.render({kind:'service',action:'stop_service',assessment:{state:'explicit',rating:5,stale:true,reason:'先前的观察'}});
  assert.equal(stale.dataset.rating,'5');assert.equal(stale.dataset.stale,'true');assert.match(f.fields(stale).suggestion,/需要重新核对/);assert.equal(f.fields(stale).reason,'先前的观察');
  assert.equal(f.render({assessment:{state:'skipped',rating:null}}).dataset.rating,'');
});

test('actual dark and light rating tokens contrast with their real surfaces and inactive scale outlines stay distinguishable',()=>{
  const css=readFileSync(join(__dirname,'..','web','hierarchy.css'),'utf8'),base=readFileSync(join(__dirname,'..','web','style.css'),'utf8');
  const block=(source,theme)=>{
    const match=source.match(theme==='dark'?/:root,\[data-theme="dark"\]\s*\{([^}]+)\}/:/\[data-theme="light"\]\s*\{([^}]+)\}/);assert.ok(match);
    return Object.fromEntries([...match[1].matchAll(/(--[\w-]+)\s*:\s*(#[0-9a-f]{6})/gi)].map(match=>[match[1],match[2]]));
  };
  const rgb=hex=>[1,3,5].map(start=>parseInt(hex.slice(start,start+2),16));
  const luminance=color=>color.map(value=>value/255).map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4).reduce((sum,value,index)=>sum+value*[.2126,.7152,.0722][index],0);
  const contrast=(foreground,background)=>{const a=luminance(foreground),b=luminance(background);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
  for(const theme of ['dark','light']){
    const colors=block(css,theme),surfaces=block(base,theme);assert.equal(new Set([1,2,3,4,5].map(level=>colors['--rating-'+level])).size,5);
    for(let level=1;level<=5;level++)for(const background of [colors[`--rating-${level}-bg`],surfaces['--panel'],surfaces['--surface-soft'],surfaces['--canvas']])assert.ok(contrast(rgb(colors['--rating-'+level]),rgb(background))>=4.5,`${theme} score ${level} must remain readable`);
    for(const name of ['--panel','--surface-soft'])assert.ok(contrast(rgb(surfaces['--border-strong']),rgb(surfaces[name]))>=3,`${theme} unfilled segment boundary`);
    assert.ok(contrast(rgb(surfaces['--amber']),rgb(surfaces['--warning-bg']))>=4.5);assert.ok(contrast(rgb(surfaces['--ink']),rgb(surfaces['--warning-bg']))>=4.5);
    const ratio=Number(css.match(/\[data-advice-part="reason"\][^{]*\{[^}]*var\(--surface-soft\) (\d+)%/)[1])/100;
    const reason=rgb(surfaces['--surface-soft']).map((value,index)=>value*ratio+rgb(surfaces['--panel'])[index]*(1-ratio));assert.ok(contrast(rgb(surfaces['--muted']),reason)>=4.5);
  }
  assert.match(css,/\[data-stale="true"\][^{]*\{\s*--rating-ink:var\(--rating-3\);\s*--rating-bg:var\(--rating-3-bg\)/);
  assert.match(css,/\.cleanup-rating-segment\[data-active="true"\]\s*\{background:currentColor;border-color:currentColor\}/);
  assert.match(css,/@media \(forced-colors: active\)[\s\S]*\.cleanup-rating-segment\s*\{[^}]*forced-color-adjust:none;[^}]*background:Canvas;[^}]*\}[\s\S]*\.cleanup-rating-segment\[data-active="true"\]\s*\{[^}]*background:CanvasText;/);
});

test('missing score, skipped judgement, absent advice and raw local explanation remain distinct',()=>{
  const f=fixture();
  const unscored=f.fields(f.render({assessment:{state:'explicit',rating:null,purpose:'已读过的资料'}}));assert.match(unscored.suggestion,/已分析.*尚未给出/);assert.equal(unscored.purpose,'已读过的资料');
  assert.match(f.fields(f.render({assessment:{state:'skipped',rating:null}})).suggestion,/已跳过/);
  assert.match(f.fields(f.render({assessment:{state:'no_advice',rating:null}})).suggestion,/暂未形成/);
  const unknown=f.fields(f.render({kind:'service',action:'stop_service',purpose:'系统服务类型',reason:'Windows本机状态',assessment:{state:'unanalysed',rating:null}}));
  assert.match(unknown.suggestion,/尚未分析/);assert.equal(unknown.purpose,'本机说明：系统服务类型');assert.equal(unknown.reason,'本机说明：Windows本机状态');assert.match(unknown.impact,/尚未说明/);
  const compact=f.render({kind:'service',assessment:{state:'unanalysed',rating:null}},{compactUnknown:true});assert.equal(compact.tag,'p');assert.equal(compact.children.length,0);assert.match(compact.textContent,/尚未分析/);
  assert.equal(f.render({assessment:{state:'explicit',rating:null}},{compactUnknown:true}).children.length,4,'an analysed but unrated object retains all explanations');
});

test('non-file scores do not invent deletion or infer an action from mere capabilities',()=>{
  const f=fixture();
  for(const kind of ['service','startup','process','software']){
    const value=f.fields(f.render({kind,actions:['stop_service'],assessment:{state:'explicit',rating:5,conclusion:'recommend'}}));
    assert.equal(value.suggestion,'倾向调整，具体操作待确认。');assert.ok(!value.suggestion.includes('删除'));
  }
  const specific=f.fields(f.render({kind:'service',action:'stop_service',assessment:{state:'explicit',rating:5,conclusion:'recommend',impact:'仅停止当前服务，不更改启动方式。'}}));
  assert.equal(specific.suggestion,'建议停止服务。');assert.match(specific.impact,/不更改启动方式/);
});

test('an operation suggestion without a score stays unscored and a fresh direct legacy record is readable without a stale unanalysed overlay',()=>{
  const f=fixture(),operation=f.fields(f.render({kind:'startup',action:'disable_startup',provenance:{source:'llm'},reason:'操作原因'}));
  assert.match(operation.suggestion,/已有操作建议.*禁用开机启动.*尚未评分/);assert.equal(operation.purpose,'暂未说明用途。');
  const fresh=f.fields(f.render({kind:'process',purpose:'模型说明用途',reason:'模型分析原因',impact:'会中断当前工作',cleanup_rating:4,conclusion:'review'},{direct:true}));
  assert.equal(fresh.purpose,'模型说明用途');assert.equal(fresh.reason,'模型分析原因');assert.equal(fresh.impact,'会中断当前工作');assert.equal(fresh.suggestion,'倾向调整，具体操作待确认。');
});
