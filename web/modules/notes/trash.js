import {tText,tHtml,locale} from "../../core/i18n.js";
import {$,esc,date} from '../../core/ui.js';
import {pageIcon} from '../../core/page-assets.js';
import {pageMenuIcon} from './page-menu.js';
import './trash.css';

export function trashPopover({anchor,notes,spaces,scope,ctx,onRestore,onHistory,onRemove,onOpen,onClose}){
 const panel=document.createElement('section');panel.className='note-trash-popover';panel.setAttribute('role','dialog');panel.setAttribute('aria-label',tText("notes:trash"));
 panel.innerHTML=`<header><h2>${tHtml("notes:trash")}</h2><button type="button" class="icon-btn" data-trash-close aria-label="${tHtml("notes:emptyTrash")}">×</button></header><input type="search" data-trash-search aria-label="${tHtml("notes:searchInTrash")}" placeholder="${tHtml("notes:searchPagesInTrash")}"><div class="note-trash-filters"><select data-trash-scope aria-label="${tHtml("notes:trashWorkspace")}"><option value="all">${tHtml("notes:allWorkspaces")}</option><option value="private">${tHtml("notes:personalAndSharedWithFriends")}</option>${spaces.filter(s=>s.kind==='family').map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select><select data-trash-period aria-label="${tHtml("notes:deletionPeriod")}"><option value="0">${tHtml("notes:forAnyTime")}</option><option value="1">${tHtml("notes:forToday")}</option><option value="7">${tHtml("notes:forAWeek")}</option></select></div><div class="note-trash-list" data-trash-list></div><footer>${tHtml("notes:pagesInTheTrashAreAutomaticallyDeletedAfter")}</footer>`;
 document.body.append(panel);let closed=false,menu,busy=false;
 const events=new AbortController(),signal=events.signal,friendIds=new Set(spaces.filter(s=>s.kind==='friends').map(s=>s.id));
 $('[data-trash-scope]',panel).value=scope||'private';if(!$('[data-trash-scope]',panel).value)$('[data-trash-scope]',panel).value='all';
 function position(){
   const rect=anchor.getBoundingClientRect(),width=Math.min(440,innerWidth-16),height=Math.min(600,innerHeight-24);
   panel.style.width=width+'px';panel.style.maxHeight=height+'px';
   panel.style.left=Math.max(8,Math.min(rect.left,innerWidth-width-8))+'px';
   panel.style.top=Math.max(8,Math.min(rect.bottom+6,innerHeight-panel.offsetHeight-8))+'px';
 }
 function close(focus=true){if(closed)return;closed=true;events.abort();menu?.remove();panel.remove();anchor.setAttribute('aria-expanded','false');if(focus&&anchor.isConnected)anchor.focus();onClose?.();}
 const area=n=>spaces.find(s=>s.id===n.space_id)?.name||tText("notes:personalNotes");
 const text=n=>'# '+n.title+'\n\n'+n.body;
 function download(n){const url=URL.createObjectURL(new Blob([text(n)],{type:'text/markdown;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=(n.title.replace(/[^\p{L}\p{N} _-]/gu,'').slice(0,100)||tText("notes:note3"))+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 async function action(n,kind){
   menu?.remove();menu=null;if(busy)return;
   if(kind==='copy'){try{await navigator.clipboard.writeText(text(n));ctx.toast(tText("notes:contentCopied"));}catch{ctx.toast(tText("notes:couldNotCopyContent"));}return;}
   if(kind==='export'){download(n);return;}
   if(kind==='history'){close(false);onHistory(n);return;}
   if(kind==='remove'&&!confirm(tText("notes:deletePageAndSubpagesPermanently")))return;
   busy=true;panel.setAttribute('aria-busy','true');
   try{if(kind==='restore'){await onRestore(n);close(false);}else{await onRemove(n);notes=await ctx.api('/notes?trash=true');draw();}}
   catch(error){ctx.toast(error.message);}finally{busy=false;panel.removeAttribute('aria-busy');}
 }
 function actions(n,button){
   if(menu?.dataset.noteId===n.id){menu.remove();menu=null;return;}
   menu?.remove();menu=document.createElement('div');menu.className='note-trash-actions';menu.dataset.noteId=n.id;menu.setAttribute('role','menu');menu.setAttribute('aria-label',tText("notes:actionsForDeletedPage"));
   const entries=[['restore','back',tText("notes:restore"),n.role!=='editor'],['history','history',tText("notes:history"),n.role!=='editor'],['export','export',tText("notes:export"),false],['copy','copy',tText("notes:copy2"),false],['remove','trash',tText("notes:permanentlyDelete"),n.role!=='editor']];
   menu.innerHTML=entries.map(([id,icon,label,disabled])=>`<button type="button" role="menuitem" data-trash-action="${id}" ${disabled?'disabled':''} class="${id==='remove'?'danger':''}">${pageMenuIcon(icon)}<span>${label}</span></button>`).join('');document.body.append(menu);
   const rect=button.getBoundingClientRect(),bounds=panel.getBoundingClientRect(),below=innerHeight-rect.bottom-12,above=rect.top-12;
   const side=bounds.right+menu.offsetWidth+14<=innerWidth?bounds.right+6:bounds.left-menu.offsetWidth-6>=8?bounds.left-menu.offsetWidth-6:null;
   menu.style.maxHeight=Math.max(60,side===null?Math.max(above,below):innerHeight-16)+'px';menu.style.overflowY='auto';
   menu.style.left=(side??Math.max(8,Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8)))+'px';
   menu.style.top=(side!==null?Math.max(8,Math.min(rect.top,innerHeight-menu.offsetHeight-8)):Math.max(8,below>=menu.offsetHeight||below>=above?rect.bottom+4:rect.top-menu.offsetHeight-4))+'px';
   menu.querySelectorAll('button').forEach(el=>el.onclick=()=>action(n,el.dataset.trashAction));menu.querySelector('button:not(:disabled)')?.focus();
   menu.onkeydown=e=>{const buttons=[...menu.querySelectorAll('button:not(:disabled)')];if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const i=buttons.indexOf(document.activeElement);buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:buttons.length-1))%buttons.length]?.focus();}if(e.key==='Escape'){e.preventDefault();e.stopPropagation();menu.remove();menu=null;button.focus();}};
 }
 function draw(){
   menu?.remove();menu=null;
   const query=$('[data-trash-search]',panel).value.trim().toLocaleLowerCase(locale()),selected=$('[data-trash-scope]',panel).value,days=Number($('[data-trash-period]',panel).value);
   const filtered=notes.filter(n=>(!query||n.title.toLocaleLowerCase(locale()).includes(query))&&(selected==='all'||selected==='private'?selected==='all'||!n.space_id||friendIds.has(n.space_id)||n.external:n.space_id===selected)&&(!days||n.deleted>=Date.now()/1000-days*86400));
   $('[data-trash-list]',panel).innerHTML=filtered.map(n=>`<div class="note-trash-row" data-trash-note="${esc(n.id)}"><button type="button" class="note-trash-title" data-trash-preview="${esc(n.id)}"><span class="note-trash-icon">${pageIcon({...n,icon_url:n.icon_url?.startsWith('/api/notes/')?n.icon_url+'?trash=true':n.icon_url})}</span><span><strong>${esc(n.title)}</strong><small>${esc(area(n))} · ${esc(date(n.deleted))}</small></span></button><button type="button" class="icon-btn" data-trash-menu="${esc(n.id)}" aria-haspopup="menu" aria-label="${tHtml("notes:actions", {value0:(esc(n.title))})}">${pageMenuIcon('dots')}</button></div>`).join('')||'<p class="note-trash-empty">'+(notes.length?tText("notes:pagesNotFound"):tText("notes:trashIsEmpty"))+'</p>';
   panel.querySelectorAll('[data-trash-menu]').forEach(el=>el.onclick=()=>actions(notes.find(n=>n.id===el.dataset.trashMenu),el));
   panel.querySelectorAll('[data-trash-preview]').forEach(el=>el.onclick=()=>{const n=notes.find(n=>n.id===el.dataset.trashPreview);close(false);onOpen(n);});
   position();
 }
 anchor.setAttribute('aria-expanded','true');$('[data-trash-close]',panel).onclick=()=>close();
 $('[data-trash-search]',panel).oninput=draw;panel.querySelectorAll('select').forEach(el=>el.onchange=draw);
 document.addEventListener('pointerdown',e=>{if(menu&&!menu.contains(e.target)&&!e.target.closest('[data-trash-menu]')){menu.remove();menu=null;}if(!panel.contains(e.target)&&!anchor.contains(e.target)&&!menu?.contains(e.target))close(false);},{signal,capture:true});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close();}},{signal});
 window.addEventListener('resize',position,{signal});window.visualViewport?.addEventListener('resize',position,{signal});
 draw();$('[data-trash-search]',panel).focus();return close;
}
