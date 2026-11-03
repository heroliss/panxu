'use strict';

// Page identity, scope navigation and list controls share one stable surface.
// Slots own the actual controls; state, requests and actions stay in each page.
window.PanxuWorkspaceHeader = (() => {
  const slots=new WeakMap();
  let observer=null;
  function dispose(){observer?.disconnect();observer=null;}
  function node(tag,cls,text){const value=document.createElement(tag);value.className=cls;if(text!==undefined)value.textContent=String(text);return value;}
  function create(title,description,actions=[]){
    dispose();
    const root=node('header','page-heading workspace-page-header'),line=node('div','workspace-heading-line'),copy=node('div','workspace-heading-copy');
    copy.append(node('h1','',title));if(description)copy.append(node('p','workspace-heading-description',description));
    const actionHost=node('div','heading-actions workspace-header-actions');actionHost.append(...actions);actionHost.hidden=!actions.length;
    line.append(copy,actionHost);root.append(line);
    const navigation=node('div','workspace-header-navigation'),controls=node('div','workspace-header-controls');navigation.hidden=true;controls.hidden=true;root.append(navigation,controls);
    slots.set(root,{navigation,controls,actions:actionHost});
    if(typeof ResizeObserver==='function'){observer=new ResizeObserver(()=>{if(root.isConnected&&root.parentElement?.id==='workspace')root.parentElement.style.setProperty('--workspace-header-height',root.getBoundingClientRect().height+'px');});observer.observe(root);}
    return root;
  }
  function set(root,name,...items){const host=slots.get(root)?.[name];if(!host)return null;host.replaceChildren(...items.filter(Boolean));host.hidden=!host.children.length;return host;}
  function append(root,name,...items){const host=slots.get(root)?.[name];if(!host)return null;host.append(...items.filter(Boolean));host.hidden=!host.children.length;return host;}
  function switches(group,{kind='view',label='查看方式'}={}){
    group.classList.add(kind==='category'?'workspace-category-switch':'workspace-view-switch');
    group.setAttribute('role','group');group.setAttribute('aria-label',label);return group;
  }
  function reveal(header,target){
    const scroller=header?.parentElement;if(!header?.isConnected||!target?.isConnected||scroller?.id!=='workspace'||!scroller.contains(target))return false;
    const top=target.getBoundingClientRect().top,edge=header.getBoundingClientRect().bottom;
    scroller.scrollTop=Math.max(0,scroller.scrollTop+top-edge-12);return true;
  }
  return {create,set,append,switches,reveal,dispose};
})();
