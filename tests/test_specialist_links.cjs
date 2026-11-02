'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const {join}=require('node:path');
function fixture(){
  const window={};
  const document={createElement:()=>({addEventListener(type,fn){this[type]=fn;}})};
  runInNewContext(readFileSync(join(__dirname,'..','web','specialist-links.js'),'utf8'),{window,document});
  return window.PanxuSpecialistLinks;
}
test('ordinary prose mentioning tools never creates an operation',()=>{
  const ui=fixture();assert.equal(ui.create({id:'x',reason:'运行清理工具或任意命令'}),null);
});
test('link only navigates with an object reference and does not forward commands or parameters',()=>{
  const ui=fixture(),opened=[];ui.setOpener(value=>opened.push(value));
  const button=ui.create({id:'x',signature:'sig',assessment:{source_ref:{task_id:'source'},specialist:{tool_id:'pagefile',parameters:{command:'untrusted'},mode:'replace'}}});
  assert.equal(button.textContent,'打开专项工具');assert.equal(opened.length,0);
  let stopped=0;button.click({preventDefault(){stopped++;},stopPropagation(){stopped++;}});
  assert.equal(stopped,2);assert.deepEqual(JSON.parse(JSON.stringify(opened)),[{item_id:'x',signature:'sig',task_id:'source'}]);
});
test('stale recommendations cannot launch an old tool link',()=>{
  const ui=fixture();for(const assessment of [{stale:true},{state:'stale'}])assert.equal(ui.create({id:'x',assessment:{...assessment,specialist:{tool_id:'pagefile'}}}),null);
});
test('inherited advice keeps the host source task rather than a guessed command',()=>{
  const value=fixture().reference({id:'child',signature:'child-sig',task_id:'other',assessment:{inherited_from:{task_id:'parent-task'},specialist:{tool_id:'uv_cache'}}});
  assert.equal(value.task_id,'parent-task');assert.equal(value.item_id,'child');assert.equal(value.signature,'child-sig');
});
test('extra suggestions use a view label without promising execution',()=>{
  const ui=fixture();assert.equal(ui.create({id:'x',extra_suggestion_id:'choice'}).textContent,'查看专项方案');
});
