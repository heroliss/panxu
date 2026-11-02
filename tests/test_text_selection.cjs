'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','tasks.js'),'utf8');

// Only owned strings and DOM/event boundaries. No system clipboard is read.
function fixture({selectionApi=true}={}){
  class Element{
    constructor(tag,cls='',parent=null){this.tag=tag;this.className=cls;this.parentElement=parent;}
    closest(selector){
      for(let node=this;node;node=node.parentElement){
        if(selector.split(',').some(value=>{
          const [tag,cls]=value.trim().split('.');
          return node.tag===tag&&(!cls||node.className.split(' ').includes(cls));
        }))return node;
      }
      return null;
    }
    contains(node){for(let value=node;value;value=value.parentElement)if(value===this)return true;return false;}
  }
  const handlers=new Map();let selection=null;
  const document={addEventListener(name,run,capture){assert.equal(capture,true);handlers.set(name,[...(handlers.get(name)||[]),run]);}};
  if(selectionApi)document.getSelection=()=>selection;
  const window={};runInNewContext(source,{window,document});
  function dispatch(name,target,extra={}){
    const event={target,button:0,detail:1,defaultPrevented:false,stopped:false,
      preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra};
    for(const run of handlers.get(name)||[]){run(event);if(event.stopped)break;}
    return event;
  }
  function range(control,{text='OWNED-FIXTURE-TEXT',start=0,end=10}={}){
    const node=control.textNode ||= {parentElement:control};
    return {isCollapsed:false,anchorNode:node,anchorOffset:start,focusNode:node,focusOffset:end,toString:()=>text};
  }
  return {Element,handlers,window,document,dispatch,range,
    select(value){selection=value;},selected:()=>selection};
}

for(const [tag,cls] of [['button','file-name-button'],['label','task-item-title'],['summary','']]){
  test(`a new selection on ${tag} suppresses its own activation and leaves the selection intact`,()=>{
    const f=fixture(),control=new f.Element(tag,cls),text=new f.Element('span','',control);
    f.dispatch('pointerdown',text);const selected=f.range(text);f.select(selected);
    const click=f.dispatch('click',text);
    assert.equal(click.defaultPrevented,true);assert.equal(click.stopped,true);
    assert.equal(f.selected(),selected,'copy still uses the unmodified browser selection');
    assert.equal(f.dispatch('click',text).defaultPrevented,false,'only this pointer gesture is consumed');
  });
}

test('an unchanged old selection on the same target never disables a normal click',()=>{
  const f=fixture(),name=new f.Element('button','file-name-button');
  f.select(f.range(name));f.dispatch('pointerdown',name);
  f.dispatch('pointermove',name,{clientX:90,clientY:80});
  assert.equal(f.dispatch('click',name).defaultPrevented,false);
});

test('existing text elsewhere does not block any button or label',()=>{
  const f=fixture(),paragraph=new f.Element('p'),name=new f.Element('button','file-name-button'),confirm=new f.Element('button','primary');
  f.select(f.range(paragraph));
  for(const target of [name,confirm,new f.Element('label')]){
    f.dispatch('pointerdown',target);assert.equal(f.dispatch('click',target).defaultPrevented,false);
  }
});

test('dragging without a nonempty range never cancels a click',()=>{
  const f=fixture(),name=new f.Element('button','file-name-button');
  f.dispatch('pointerdown',name);f.dispatch('pointermove',name,{clientX:90,clientY:80});
  assert.equal(f.dispatch('click',name).defaultPrevented,false);
  f.dispatch('pointerdown',name);f.select(f.range(name,{text:''}));
  assert.equal(f.dispatch('click',name).defaultPrevented,false);
});

test('cross-target gestures and ordinary action buttons retain their handlers',()=>{
  const f=fixture(),first=new f.Element('button','file-name-button'),second=new f.Element('button','file-name-button'),action=new f.Element('button','primary');
  f.dispatch('pointerdown',first);f.select(f.range(first));
  assert.equal(f.dispatch('click',second).defaultPrevented,false);
  f.dispatch('pointerdown',action);f.select(f.range(action));
  assert.equal(f.dispatch('click',action).defaultPrevented,false);
});

test('checkbox/input activation and keyboard activation stay native',()=>{
  const f=fixture(),label=new f.Element('label'),input=new f.Element('input','',label);
  f.dispatch('pointerdown',input);f.select(f.range(label));
  assert.equal(f.dispatch('click',input).defaultPrevented,false);
  f.dispatch('pointerdown',label);f.select(f.range(label,{end:14}));
  assert.equal(f.dispatch('click',label,{detail:0}).defaultPrevented,false);
});

test('cancelled pointer gestures and right clicks cannot intercept a later action',()=>{
  const f=fixture(),name=new f.Element('button','file-name-button');
  f.dispatch('pointerdown',name);f.dispatch('pointercancel',name);f.select(f.range(name));
  assert.equal(f.dispatch('click',name).defaultPrevented,false);
  f.dispatch('pointerdown',name,{button:2});f.select(f.range(name,{end:14}));
  assert.equal(f.dispatch('click',name,{button:2}).defaultPrevented,false);
});

test('the guard installs once and never captures copy, keys or the clipboard',()=>{
  const f=fixture();f.window.PanxuTextSelection.install(f.document);f.window.PanxuTextSelection.install({});
  assert.deepEqual([...f.handlers.keys()].sort(),['click','pointercancel','pointerdown']);
  for(const rows of f.handlers.values())assert.equal(rows.length,1);
  const unsupported=fixture({selectionApi:false}),name=new unsupported.Element('button','file-name-button');
  unsupported.dispatch('pointerdown',name);assert.equal(unsupported.dispatch('click',name).defaultPrevented,false);
});
