'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','files.js'),'utf8');
const start=source.indexOf('  function locationLabels('),end=source.indexOf('  function select(',start);
assert.ok(start>=0 && end>start);
const labels=runInNewContext(source.slice(start,end)+'\nlocationLabels');

test('file positions keep the drive and parent context without repeating the filename',()=>{
  const rows=[{id:'a',name:'setup.exe',path:'C:\\Users\\fixture\\Downloads\\setup.exe'}];
  assert.equal(labels(rows).get('a'),'C:\\…\\fixture\\Downloads');
  assert.equal(rows[0].path,'C:\\Users\\fixture\\Downloads\\setup.exe');
});
test('same names with identical short tails receive enough parent context to distinguish them',()=>{
  const rows=['ProjectA','ProjectB'].map((name,index)=>({id:String(index),name:'build.zip',path:`E:\\Projects\\${name}\\output\\old\\build.zip`}));
  const result=labels(rows);
  assert.notEqual(result.get('0'),result.get('1'));
  assert.match(result.get('0'),/ProjectA/);assert.match(result.get('1'),/ProjectB/);
});
test('root files and network shares retain their location identity',()=>{
  const rows=[{id:'root',name:'file.txt',path:'E:\\file.txt'},{id:'network',name:'file.txt',path:'\\\\server\\share\\parent\\file.txt'}];
  assert.equal(labels(rows).get('root'),'E:\\');
  assert.equal(labels(rows).get('network'),'\\\\server\\share\\parent');
});
