import {tText,tHtml} from "../../core/i18n.js";
import {esc,bytes,date} from '../../core/ui.js';
import {icon} from './icons.js';
import {fileArtwork} from './file-art.js';
export {icon};
export const driveBrand=()=>'<span class="drive-wordmark"><img src="/assets/icons/drg-d8-logo.png" width="23" height="23" alt=""><span>DrG <b>Drive</b></span></span>';
export function fileCategory(f){
 if(f.kind==='folder')return 'folder';
 const ext=f.name.split('.').at(-1).toLowerCase();
 if(f.mime?.startsWith('image/'))return 'image';
 if(f.mime?.startsWith('video/'))return 'video';
 if(f.mime?.startsWith('audio/'))return 'audio';
 if(ext==='pdf')return 'pdf';
 if(/^(xlsx?|ods|csv|tsv|numbers)$/.test(ext))return 'sheet';
 if(/^(zip|rar|7z|gz|tar|bz2|xz)$/.test(ext))return 'archive';
 if(/^(py|js|mjs|ts|tsx|jsx|json|html|css|sh|sql|yaml|yml|xml)$/.test(ext))return 'code';
 return 'file';
}
export function fileType(f){
 const category=fileCategory(f),ext=f.name.includes('.')?f.name.split('.').at(-1).toUpperCase():'';
 return ({folder:tText("files:folder"),pdf:tText("files:pdfDocument"),image:tText("files:image", {value0:(ext)}),video:tText("files:video", {value0:(ext)}),audio:tText("files:audio", {value0:(ext)}),sheet:tText("files:table", {value0:(ext)}),archive:tText("files:archive", {value0:(ext)}),code:tText("files:sourceCode", {value0:(ext)})})[category]||(f.mime?.startsWith('text/')?tText("files:textDocument"):ext&&ext.length<12?tText("files:file", {value0:(ext)}):tText("files:file2"));
}
export const tool=(attr,symbol,label,disabled=false)=>`<button type=button class=drive-tool ${attr} title="${label}" aria-label="${label}" ${disabled?'disabled':''}>${icon(symbol)}</button>`;
export const driveStorage=quota=>`<strong>${tHtml("files:storage")}</strong><span>${bytes(quota.used_bytes)}${quota.limit_bytes===null?(" "+tText("files:unlimited")+""):(" "+tText("files:from", {value0:(bytes(quota.limit_bytes))})+"")}</span>${quota.limit_bytes!==null?`<meter min=0 max="${Math.max(1,quota.limit_bytes)}" value="${quota.used_bytes}" aria-label="${tHtml("files:usedSpace")}"></meter>`:''}<small>${tHtml("files:personalWorkspace")}</small>`;
export function driveShell(data,view,parent){
 const trash=view==='trash';
 const nav=[['recent','recent',tText("files:recent")],['active','folder',tText("files:overview")],['shared','shared',tText("files:shared")],['trash','trash',tText("files:recentlyDeleted")]];
 if(data.secret_enabled)nav.splice(3,0,['secret','lock',tText("files:private")]);
 return `<section class="panel files-panel drive-panel" data-drive-view="${view}"><aside class=drive-sidebar><nav class=files-view-tabs aria-label="${tHtml("files:fileSections")}">${nav.map(([key,symbol,label])=>`<button data-files-view="${key}" aria-current="${view===key?'page':'false'}">${icon(symbol)}<span>${label}</span>${key==='trash'?`<span class=file-trash-count>${trash?data.items.length:data.trash_count||0}</span>`:''}</button>`).join('')}</nav><div class=drive-storage>${driveStorage(data.quota)}</div></aside><div class=drive-main>${driveContent(data,view,parent)}</div></section>`;
}
export function driveContent(data,view,parent){
 const trash=view==='trash',titles={active:parent?data.breadcrumbs.at(-1)?.name:'DrG Drive',recent:tText("files:recent"),shared:tText("files:shared"),trash:tText("files:recentlyDeleted"),secret:parent?data.breadcrumbs.at(-1)?.name:tText("files:private")};
 return ` <div class=drive-toolbar>${tool('data-drive-layout aria-haspopup="menu"','list',tText("files:displayMode"))}<div class=drive-upload-tools>${trash?'':`<label class="drive-tool file-upload-label" title="${tHtml("files:uploadFiles")}" aria-label="${tHtml("files:uploadFiles")}" tabindex=0 role=button>${icon('upload')}<input type=file multiple data-upload-files></label>${tool('data-new-folder','folderPlus',tText("files:newFolder"))}${tool('data-upload-folder','folder',tText("files:uploadFolder"))}<input type=file webkitdirectory multiple data-upload-directory hidden>`}</div>${trash?'':`<div class=drive-selection-tools>${tool('data-download-selected','download',tText("files:downloadSelected"),true)}${view==='secret'?'':tool('data-share-selected','share',tText("files:share"),true)}${view!=='secret'&&typeof navigator.canShare==='function'?tool('data-send-selected','mail',tText("files:sendCopy"),true):''}${tool('data-delete-selected','trash',view==='secret'?tText("files:deletePermanently"):tText("files:toTrash"),true)}${tool('data-more-selected aria-haspopup="menu"','more',tText("files:otherActions"),true)}</div>`}</div>
 <header class=drive-heading><div>${parent&&!trash&&['active','secret'].includes(view)?`<button class=drive-back data-folder="${data.breadcrumbs.at(-2)?.id||''}" aria-label="${tHtml("files:backToParent")}">${icon('back')}</button>`:icon(trash?'trash':view==='active'?'cloud':view==='secret'?'lock':view)}<h2>${esc(titles[view])}</h2></div><p data-file-summary>${tHtml("files:objects", {value0:(data.items.length)})}</p><button class=drive-sort data-files-sort aria-haspopup=menu>${tHtml("files:byName")}</button></header>
 <p class="hint drive-hint">${trash?tText("files:filesAndFoldersAreStoredInTheTrash", {value0:(data.retention_days||7)}):tText("files:dragFilesAndFoldersHerePerFile", {value0:(bytes(data.max_file_bytes)), value1:(view==='secret'?(" "+tText("files:withoutSharedAccessHistoryAndTrash")+""):view!=='active'?(" "+tText("files:uploadToOverview")+""):'')})}</p>
 <div class=drive-columns><span>${tHtml("files:name")}</span><span>${tHtml("files:type")}</span><span>${tHtml("files:size")}</span><span>${trash?tText("files:delete"):tText("files:lastModified")}</span>${view==='shared'?`<span class=drive-expiry-heading>${tHtml("files:shareAccessUntil")}</span>`:''}<span></span></div><div class=file-list></div><div class=drive-footer><nav class=file-crumbs aria-label="${tHtml("files:folderPath")}">${trash?'':`<button data-folder="">${icon(view==='secret'?'lock':'cloud')}${view==='secret'?tText("files:private"):'DrG Drive'}</button>${(data.breadcrumbs||[]).map(f=>`<span>›</span><button data-folder="${f.id}">${esc(f.name)}</button>`).join('')}`}</nav><p data-file-status role=status class=hint></p></div><div class=drive-drop-overlay hidden>${icon('upload')}<strong>${tHtml("files:dragToUpload")}</strong><span data-drop-label>${tHtml("files:toCurrentFolder")}</span></div>`;
}
export const driveAccessUntil=f=>f.shared_until===null?tText("files:noExpirationDate"):date(f.shared_until);
export function driveSharingBadges(f){
 return `${f.shared?`<span class=drive-shared title="${tHtml("files:activeLink")}" aria-label="${tHtml("files:activeLink")}">${icon('shared')}</span>`:''}${f.retention_version?`<span class=drive-temporary title="${f.delete_at?tHtml('files:shareDeleteAt',{date:esc(date(f.delete_at))}):tHtml(f.kind==='folder'?'files:shareFolderWaiting':'files:shareRetentionBlocked')}" aria-label="${tHtml('files:shareRetentionTitle')}">${icon('recent')}</span>`:''}`;
}
export function driveRow(f,trash,options={}){
 const category=fileCategory(f),name=`<span class="drive-file-icon is-${category}">${fileArtwork(trash?{...f,id:null}:f,category,options.prefix)}</span><strong>${esc(f.name)}</strong>${driveSharingBadges(f)}`;
 return `<article class="file-row drive-row ${trash?'file-trash-row':''}" data-file-row="${f.id}" data-file-kind="${f.kind}" ${trash||options.draggable===false?'':`draggable=true ${f.kind==='folder'?`data-drop-folder="${f.id}"`:''}`}><div class=drive-name>${trash?`<div class="file-open file-trash-name">${name}</div>`:`<input type=checkbox data-select-file="${f.id}" aria-label="${tHtml("files:select", {value0:(esc(f.name))})}"><button class=file-open ${options.shared?'data-shared-open':'data-open-file'}="${f.id}" title="${esc(f.name)}">${name}</button>`}</div><span class=drive-type>${esc(fileType(f))}</span><span class=drive-size>${f.kind==='folder'?'—':bytes(f.size)}</span>${trash?`<div class=file-trash-details><span>${tHtml("files:deleted", {value0:(date(f.deleted))})}</span><span>${tHtml("files:willBeDeleted", {value0:(date(f.purge_at))})}</span></div><div class=file-trash-actions><button class="text-link" data-file-restore="${f.id}">${tHtml("files:restore")}</button><button class="text-link danger" data-file-permanent="${f.id}">${tHtml("files:deletePermanently2")}</button></div>`:`<span class=drive-date>${date(f.updated)}</span>${options.showExpiry?`<span class=drive-expiry title="${esc(driveAccessUntil(f))}"><span class=drive-expiry-label>${tHtml("files:shareAccessUntil")} </span>${esc(driveAccessUntil(f))}</span>`:''}<button class="icon-btn drive-menu" data-file-menu="${f.id}" aria-haspopup=menu aria-label="${tHtml("files:fileActions", {value0:(esc(f.name))})}">${icon('moreFilled')}</button>`}</article>`;
}
let activeMenu;
export const closeDriveMenu=()=>activeMenu?.close();
export function driveMenu(anchor,entries,title=''){
 closeDriveMenu();
 // A body child can be reinserted by an autofill extension after PIN entry.
 // Reinsertion resets native popover state in Safari, without a close event.
 const el=document.createElement('div'),events=new AbortController();el.className='drive-popover';el.setAttribute('role','menu');
 const close=()=>{events.abort();el.remove();anchor.setAttribute('aria-expanded','false');if(activeMenu?.el===el)activeMenu=null;};
 el.innerHTML=(title?`<p>${esc(title)}</p>`:'')+entries.map((e,i)=>e.separator?'<hr>':`<button type=button role="${typeof e.checked==='boolean'?'menuitemradio':'menuitem'}" ${typeof e.checked==='boolean'?`aria-checked="${e.checked}"`:''} ${e.attr||''} data-menu-index="${i}" class="${e.danger?'danger':''}">${icon(e.icon)}<span>${esc(e.label)}</span>${e.checked?icon('check'):''}</button>`).join('');
 document.body.append(el);activeMenu={el,close};anchor.setAttribute('aria-expanded','true');
 const place=()=>{if(!anchor.isConnected){close();return;}const box=anchor.getBoundingClientRect(),rect=el.getBoundingClientRect();el.style.left=Math.max(8,Math.min(box.left,innerWidth-rect.width-8))+'px';el.style.top=Math.max(8,Math.min(box.bottom+6,innerHeight-rect.height-8))+'px';};
 place();
 el.querySelectorAll('button').forEach(b=>b.onclick=()=>{const action=entries[Number(b.dataset.menuIndex)].action;close();anchor.focus({preventScroll:true});action?.();});
 const keys=e=>{const buttons=[...el.querySelectorAll('button')],index=buttons.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(index+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus({preventScroll:true});}if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();anchor.focus({preventScroll:true});}if(e.key==='Tab')close();};
 document.addEventListener('keydown',keys,{capture:true,signal:events.signal});
 document.addEventListener('pointerdown',event=>{if(!el.contains(event.target))close();},{capture:true,signal:events.signal});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)close();},{signal:events.signal});
 window.addEventListener('resize',place,{signal:events.signal});window.addEventListener('scroll',place,{capture:true,signal:events.signal});
 el.querySelector('button')?.focus({preventScroll:true});return el;
}
