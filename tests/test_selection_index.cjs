'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const environment={window:{}};
runInNewContext(readFileSync(join(__dirname,'..','web','files.js'),'utf8'),environment);
const selection=environment.window.PanxuFilteredSelection;
const plain=value=>JSON.parse(JSON.stringify(value));
const reference=(rules,items)=>{
 const values=items.map(item=>selection.mark(rules,item));
 const checked=values.length>0&&values.every(value=>value.selected);
 return {checked,indeterminate:values.some(value=>value.indeterminate)||!checked&&values.some(value=>value.selected)};
};

test('indexed aggregate preserves nearest-parent rules, exclusions, roots and independent application identities',()=>{
 const file=(id,path,kind='file')=>({id,path,kind}),root=file('root','C:\\','directory'),folder=file('folder','C:\\Owned','directory'),except=file('except','C:\\Owned\\Keep','directory'),leaf=file('leaf','C:\\Owned\\Keep\\Again.tmp');
 const sibling=file('sibling','C:\\Owned-Other\\a.tmp'),linux=file('linux','/','directory'),linuxLeaf=file('linux-leaf','/owned/file.tmp');
 const process={id:'process-1',kind:'process',path:'C:\\same.exe'},second={...process,id:'process-2'};
 const rules=new Map();[[root,true],[folder,true],[except,false],[leaf,true],[linux,false],[linuxLeaf,true],[process,true]].forEach(([item,checked])=>selection.write(rules,item,checked));
 for(const items of [[],[root],[folder],[except],[leaf],[sibling],[linux],[linuxLeaf],[process],[second],[folder,except,leaf,sibling,process,second,linuxLeaf]])assert.deepEqual(plain(selection.aggregate(rules,items)),reference(rules,items));
});

test('indexed aggregate matches the original per-item rule semantics over deterministic mixed trees',()=>{
 let seed=0x4c495353;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/2**32;};
 for(let trial=0;trial<250;trial++){
   const rules=new Map(),items=[];
   for(let index=0;index<100;index++){
     const kind=random()<.12?'startup':random()<.4?'directory':'file',depth=1+Math.floor(random()*5);
     const components=Array.from({length:depth},()=>String(Math.floor(random()*6)));
     const item={id:'trial-'+index,kind,path:(random()<.1?'/owned/':'E:\\Owned\\')+components.join('\\')};items.push(item);
     if(random()<.65)selection.write(rules,item,random()<.5);
   }
   const candidates=items.filter(()=>random()<.7);assert.deepEqual(plain(selection.aggregate(rules,candidates)),reference(rules,candidates),'tree trial '+trial);
 }
});
