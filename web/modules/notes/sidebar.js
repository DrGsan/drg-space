import {tText,tHtml} from "../../core/i18n.js";
import {$} from '../../core/ui.js';
export const sidebarSections={favorites:tText("notes:favourites"),shared:tText("notes:shared"),private:tText("notes:personal"),recent:tText("notes:recent"),secret:tText("notes:private")};
const defaults=()=>({order:Object.keys(sidebarSections),hidden:['recent']});
export function readSidebar(user){
  try{
    const value=JSON.parse(localStorage.getItem('drg-notes-sidebar:'+user));
    if(!value||!Array.isArray(value.order))return defaults();
    const config={order:[...new Set([...value.order.filter(k=>Object.hasOwn(sidebarSections,k)),...Object.keys(sidebarSections)])],hidden:Array.isArray(value.hidden)?value.hidden.filter(k=>Object.hasOwn(sidebarSections,k)):[]};
    saveSidebar(user,config);
    return config;
  }catch{return defaults();}
}
export function saveSidebar(user,value){try{localStorage.setItem('drg-notes-sidebar:'+user,JSON.stringify(value));return true;}catch{return false;}}
export function customizeSidebar(host,config,{change,done,secret=false}){
  const render=()=>{
    host.innerHTML=`<div class="sidebar-customizer"><p class="hint">${tHtml("notes:panelSections")}</p>${config.order.filter(key=>key!=='secret'||secret).map((key,index,list)=>`<div class="sidebar-setting-row" draggable="true" data-section="${key}"><span class="sidebar-grip" aria-hidden="true"><svg viewBox="0 0 10 16" width="10" height="16" fill="currentColor">${[3,8,13].flatMap(y=>[2,8].map(x=>`<circle cx="${x}" cy="${y}" r="1.2"/>`)).join('')}</svg></span><label><input type="checkbox" data-visible="${key}" ${config.hidden.includes(key)?'':'checked'}>${sidebarSections[key]}</label><button class="icon-btn" data-up="${key}" aria-label="${tHtml("notes:above", {value0:(sidebarSections[key])})}" ${index===0?'disabled':''}>↑</button><button class="icon-btn" data-down="${key}" aria-label="${tHtml("notes:below", {value0:(sidebarSections[key])})}" ${index===list.length-1?'disabled':''}>↓</button></div>`).join('')}<button class="text-link" data-sidebar-reset>${tHtml("notes:resetSettings")}</button><p class="hint">${tHtml("notes:forYourAccountInThisBrowser")}</p><button class="primary" data-sidebar-done>${tHtml("notes:done")}</button></div>`;
    const update=()=>{change(config);render();};
    const move=(key,offset)=>{const list=config.order.filter(key=>key!=='secret'||secret),index=list.indexOf(key),target=index+offset;if(target<0||target>=list.length)return;const a=config.order.indexOf(key),b=config.order.indexOf(list[target]);[config.order[a],config.order[b]]=[config.order[b],config.order[a]];update();};
    host.querySelectorAll('[data-visible]').forEach(input=>input.onchange=()=>{config.hidden=config.hidden.filter(k=>k!==input.dataset.visible);if(!input.checked)config.hidden.push(input.dataset.visible);update();});
    host.querySelectorAll('[data-up]').forEach(b=>b.onclick=()=>move(b.dataset.up,-1));
    host.querySelectorAll('[data-down]').forEach(b=>b.onclick=()=>move(b.dataset.down,1));
    host.querySelectorAll('[data-section]').forEach(row=>{
      row.ondragstart=e=>{e.dataTransfer.setData('application/drg-sidebar-section',row.dataset.section);e.dataTransfer.effectAllowed='move';};
      row.ondragover=e=>{if(e.dataTransfer.types.includes('application/drg-sidebar-section'))e.preventDefault();};
      row.ondrop=e=>{e.preventDefault();const from=config.order.indexOf(e.dataTransfer.getData('application/drg-sidebar-section')),to=config.order.indexOf(row.dataset.section);if(from<0||to<0)return;config.order.splice(to,0,config.order.splice(from,1)[0]);update();};
    });
    $('[data-sidebar-reset]',host).onclick=()=>{Object.assign(config,defaults());update();};
    $('[data-sidebar-done]',host).onclick=done;
  };render();
}
