'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {runInNewContext}=require('node:vm');
const source=readFileSync(join(__dirname,'..','web','files.js'),'utf8');
function fixture(){
 const calls=[],document={body:{isConnected:true}},root={isConnected:true,contains:target=>target===content},button={isConnected:true};
 const content={isConnected:true,attrs:{},setAttribute(name,value){this.attrs[name]=value;},getAttribute(name){return this.attrs[name];},focus(options){document.activeElement=this;calls.push(['focus',options]);},scrollIntoView(options){calls.push(['scroll',options]);}};
 document.activeElement=button;const environment={window:{},document};runInNewContext(source.slice(0,source.indexOf('// A user decision')),environment);
 return {ui:environment.window.PanxuListNavigation,document,root,content,button,calls};
}
test('a paging intent moves focus and scroll instantly once without adding a tab stop',()=>{
 const f=fixture(),intent=f.ui.capture(f.root);assert.equal(f.ui.reveal(f.content,intent,'第2页'),true);assert.equal(f.content.attrs.tabindex,'-1');assert.equal(f.content.attrs['aria-label'],'第2页');assert.equal(f.document.activeElement,f.content);assert.equal(f.calls[0][1].preventScroll,true);assert.equal(f.calls[1][1].behavior,'instant');
 assert.equal(f.ui.reveal(f.content,intent,'重复'),false);assert.equal(f.ui.reveal(f.content,null),false);assert.equal(f.calls.length,2);
});
test('newer page intents, disconnected lists and a user focus change suppress obsolete reveal',()=>{
 const f=fixture(),old=f.ui.capture(f.root),latest=f.ui.capture(f.root);assert.equal(f.ui.reveal(f.content,old),false);
 f.document.activeElement={isConnected:true};assert.equal(f.ui.reveal(f.content,latest),false);assert.equal(f.calls.length,0);
 f.document.activeElement=f.document.body;f.root.isConnected=false;assert.equal(f.ui.reveal(f.content,latest),false);assert.equal(f.calls.length,0);
});
