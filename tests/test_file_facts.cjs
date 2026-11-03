'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{readFileSync}=require('node:fs'),{runInNewContext}=require('node:vm');
function fixture(){
  class Element{
    constructor(tag){this.tag=tag;this.className='';this.children=[];this.attrs={};this._text='';}
    set textContent(value){this._text=String(value);this.children=[];}get textContent(){return this._text+this.children.map(item=>item.textContent).join('');}
    append(...children){this.children.push(...children);}setAttribute(key,value){this.attrs[key]=value;}
  }
  const window={};runInNewContext(readFileSync('web/files.js','utf8'),{window,document:{createElement:tag=>new Element(tag)}});
  return {facts:window.PanxuFileFacts,intent:window.PanxuDisposition,Element};
}
test('filesystem creation and modification remain distinct from model suggestion generation',()=>{
  const {facts}=fixture(),line=facts.render({kind:'file',name:'notes.txt',size_bytes:32,details:{timestamps:{created_at:'2022-05-06T12:00:00+00:00',modified_at:'2023-07-08T12:00:00+00:00'}},assessment:{state:'explicit',provenance:{source:'llm',generated_at:'2026-11-03T12:00:00+00:00'}}});
  assert.match(line.textContent,/文件创建：2022/);assert.match(line.textContent,/文件修改：2023/);assert.match(line.textContent,/建议生成：2026/);assert.match(line.textContent,/大小：32 B/);
});
test('unknown filesystem time never borrows the suggestion date and malformed legacy time stays unknown',()=>{
  const {facts}=fixture(),line=facts.render({kind:'file',name:'notes',size_bytes:null,created_at:'2026-02-31T00:00:00Z',modified_at:'2023-01-01',provenance:{source:'llm',generated_at:'2026-11-03T12:00:00Z'}});
  assert.match(line.textContent,/文件创建：未取得/);assert.match(line.textContent,/文件修改：未取得/);assert.match(line.textContent,/建议生成：2026/);assert.match(line.textContent,/大小：未取得/);assert.equal(facts.stamp('2024-02-29T12:00:00Z')!==null,true);
});
test('partial folder bytes are a lower bound and directory modification does not claim child modification',()=>{
  const {facts}=fixture(),line=facts.render({kind:'directory',name:'project',size_bytes:9,size_kind:'observed_lower_bound',details:{timestamps:{modified_at:'2023-07-08T12:00:00Z'}}});
  assert.match(line.textContent,/大小：至少 9 B/);assert.match(line.textContent,/目录修改：2023/);assert.match(line.children.find(part=>part.textContent.startsWith('目录修改')).title,/不代表全部子文件/);
  assert.equal(facts.sizeLabel({kind:'directory',size_bytes:null}),'未统计');assert.equal(facts.sizeLabel({kind:'file',size_bytes:0}),'0 B');
});
test('personal intent has a visible label without replacing the original interactive select',()=>{
  const {intent,Element}=fixture(),select=new Element('select');select.title='倾向会提供给 AI；执行仍需确认。';const label=intent.labelled(select);
  assert.equal(label.tag,'label');assert.equal(label.children[0].textContent,'我的意愿');assert.equal(label.children[1],select);assert.match(label.title,/执行仍需确认/);
});
