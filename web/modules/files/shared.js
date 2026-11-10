import {tText,tHtml,locale} from "../../core/i18n.js";
import './shared.css';
import {$,esc,bytes,date,formBind} from '../../core/ui.js';
import {driveBrand,driveRow,fileType,fileCategory,icon,tool,driveMenu,closeDriveMenu} from './drive-ui.js';
import {fileArtwork} from './file-art.js';
import {mediaPreview} from './preview.js';
import {fileEditor} from './file-editor.js';
import {uploadFile,uploadPanel} from './upload-panel.js';
import {withUploadRateRetry,UploadStoppedError} from './upload-retry.js';
import {downloadFile,bindDownload} from './download.js';

export async function mountSharedFiles(root,ctx,token){
 const prefix='/shared-files/'+encodeURIComponent(token),page=document.createElement('main');page.className='shared-drive-page';
 let parent=null,sequence=0,layout='list',sort='name',expiryTimer,busy=false,transfer=null,refreshBudget=()=>{},canDownload=()=>true,lastBudget=0,budgetVisible=false;
 const budgetTimers=new Set(),pendingBudgets=new Set(),activeBudgets=new Set(),clearBudget=()=>{budgetTimers.forEach(clearTimeout);budgetTimers.clear();pendingBudgets.clear();activeBudgets.clear();};
 const wakeBudget=()=>{if(document.hidden||!page.isConnected)return;if(pendingBudgets.size){for(const ticket of pendingBudgets)refreshBudget(ticket);}else if(budgetVisible&&Date.now()-lastBudget>=15000)refreshBudget();};
 document.addEventListener('visibilitychange',wakeBudget);window.addEventListener('focus',wakeBudget);window.addEventListener('online',wakeBudget);
 root.replaceChildren(page);
 const dialogs=()=>document.querySelectorAll('[data-shared-file-dialog]');
 const sharedCtx={...ctx,canDownload:()=>canDownload(),onDownloadStarted:grant=>refreshBudget(grant),onDownloadError:error=>{if(error.status===410)refreshBudget();},modal:markup=>{const el=ctx.modal(markup);el.dataset.sharedFileDialog='';return el;}};
 const dispose=new MutationObserver(()=>{if(!page.isConnected){clearTimeout(expiryTimer);clearBudget();document.removeEventListener('visibilitychange',wakeBudget);window.removeEventListener('focus',wakeBudget);window.removeEventListener('online',wakeBudget);closeDriveMenu();dialogs().forEach(el=>$('.modal-close',el)?.click());dispose.disconnect();}});dispose.observe(root,{childList:true});
 const header=()=>`<header class="drive-workspace-header shared-drive-header"><h1><a href=/ aria-label="DrG Drive">${driveBrand()}</a></h1><span class=shared-drive-badge>${tHtml("files:sharedAccess", {value0:(icon('shared'))})}</span></header>`;
 const download=file=>downloadFile('/api'+prefix+(file.kind==='folder'?'/download.zip?ids='+file.id:'/'+file.id+'/content'),sharedCtx);
 const load=async()=>{
  const seq=++sequence;clearTimeout(expiryTimer);clearBudget();refreshBudget=()=>{};closeDriveMenu();
  if(!page.children.length)page.innerHTML=`${header()}<p class=drive-loading role=status>${tHtml("files:openingPublication")}</p>`;
  try{
   const data=await ctx.api(prefix+(parent?'?parent_id='+encodeURIComponent(parent):''));if(!page.isConnected||sequence!==seq)return;
   let grant=data.permission==='edit'?await ctx.api(prefix+'/edit-session','POST'):null;if(!page.isConnected||sequence!==seq)return;
   const canUpload=data.permission==='edit'&&data.current.kind==='folder',crumbs=data.breadcrumbs||[data.root],selected=new Set();
   const exhausted=()=>data.download_limit!=null&&data.used>=data.download_limit;
   canDownload=()=>{if(!exhausted())return true;ctx.toast(tText('files:shareExhausted'));return false;};
   const permission=data.permission==='edit'?tText("files:canBeEdited"):tText("files:readOnly");
   page.innerHTML=`${header()}<section class="drive-panel shared-drive-panel" data-layout="${layout}"><aside class=drive-sidebar><p class=shared-drive-caption>${tHtml("files:publication")}</p><nav class=files-view-tabs aria-label="${tHtml("files:sharedFolder")}"><button data-shared-root aria-current=page>${icon(data.root.kind==='folder'?'folder':'file')}<span>${esc(data.root.name)}</span></button></nav><div class=shared-drive-access>${icon(data.permission==='edit'?'rename':'preview')}<strong>${permission}</strong><p>${data.permission==='edit'?tText("files:youCanDownloadFilesAddNewOnesAnd"):tText("files:youCanOpenAndDownloadFilesFromThis")}</p>${data.expires?`<span>${tHtml("files:accessTo")}</span><time>${date(data.expires)}</time>`:`<span>${tHtml("files:noExpiry")}</span>`}</div></aside><div class=drive-main><div class=drive-toolbar>${tool('data-drive-layout aria-haspopup=menu','list',tText("files:displayMode"))}<div class=shared-drive-primary><a class="shared-drive-download" href="/api${prefix}/download.zip">${icon('download')}<span>${tHtml("files:downloadAll")}</span></a><span class=shared-drive-budget data-download-budget role=status hidden></span>${canUpload?`<label class="drive-tool file-upload-label" title="${tHtml("files:uploadFiles")}" aria-label="${tHtml("files:uploadFiles")}" tabindex=0 role=button>${icon('upload')}<input type=file multiple data-shared-upload></label>`:''}</div><div class=drive-selection-tools>${tool('data-shared-selected','download',tText("files:downloadSelected"),true)}${tool('data-shared-properties','properties',tText("files:propertiesOfSelected"),true)}${tool('data-shared-more aria-haspopup=menu','more',tText("files:otherActions"),true)}</div></div><header class=drive-heading><div>${crumbs.length>1?`<button class=drive-back data-shared-back aria-label="${tHtml("files:backToParent")}">${icon('back')}</button>`:icon(data.current.kind==='folder'?'folder':'file')}<h2>${esc(data.current.name)}</h2></div><p data-file-summary>${tHtml("files:objects", {value0:(data.items.length)})}</p><button class=drive-sort data-files-sort aria-haspopup=menu>${tHtml("files:byName")}</button></header><p class="hint drive-hint shared-drive-summary">${icon(data.permission==='edit'?'rename':'preview')}<span>${permission}</span><span class=shared-drive-separator>·</span><span>${data.expires?tText("files:until2", {value0:(date(data.expires))}):tText("files:noExpiry")}</span></p><p class=shared-drive-budget-hint data-download-hint hidden></p><div class=drive-columns><span>${tHtml("files:name")}</span><span>${tHtml("files:type")}</span><span>${tHtml("files:size")}</span><span>${tHtml("files:lastModified")}</span><span></span></div><div class=file-list></div><footer class=drive-footer><nav class=file-crumbs aria-label="${tHtml("files:folderPath")}">${crumbs.map((file,index)=>`${index?'<span>›</span>':''}<button data-shared-folder="${file.id}">${index?'':icon('shared')}${esc(file.name)}</button>`).join('')}</nav><span class=shared-drive-footer-note>${tHtml("files:sharedAccessViaDrgDrive")}</span></footer></div></section>`;
   const panel=$('.drive-panel',page),list=$('.file-list',page);
   if(transfer){panel.classList.add('has-transfer');panel.append(transfer.el);}
   const navigate=id=>{if(busy)return;parent=id===data.root.id?null:id;load();};
   const single=()=>data.items.find(file=>selected.has(file.id));
   const sync=()=>{
    list.querySelectorAll('[data-file-row]').forEach(row=>{const checked=selected.has(row.dataset.fileRow);row.classList.toggle('is-selected',checked);$('[data-select-file]',row).checked=checked;});
    $('[data-file-summary]',page).textContent=tText("files:objects2", {value0:(selected.size?(""+tText("files:selected", {value0:(selected.size)})+" "):''), value1:(data.items.length)});
    $('[data-shared-selected]',page).disabled=!selected.size||busy||exhausted();for(const attr of ['properties','more'])$('[data-shared-'+attr+']',page).disabled=selected.size!==1||busy;
   };
   const showBudget=()=>{
    const badge=$('[data-download-budget]',page),hint=$('[data-download-hint]',page),limited=data.download_limit!=null;
    budgetVisible=limited;badge.hidden=hint.hidden=!limited;panel.classList.toggle('has-download-limit',limited);
    badge.classList.toggle('is-exhausted',exhausted());
    badge.textContent=exhausted()?tText('files:shareExhaustedShort'):tText('files:shareRemaining',{count:data.download_limit||0,remaining:Math.max(0,(data.download_limit||0)-data.used),limit:data.download_limit});
    hint.textContent=tText(exhausted()?'files:shareExhausted':'files:shareRecipientHint');
    $('.shared-drive-download',page).setAttribute('aria-disabled',String(exhausted()));
    dialogs().forEach(el=>el.querySelectorAll('[data-shared-download],a[href^="/api'+prefix+'/"]').forEach(button=>{if(button.tagName==='BUTTON')button.disabled=exhausted();else button.setAttribute('aria-disabled',String(exhausted()));}));
    sync();
   };
   refreshBudget=(ticket)=>{
    const key=ticket||'';if(activeBudgets.has(key))return;activeBudgets.add(key);
    if(ticket)pendingBudgets.add(ticket);
    const check=async(attempt=0)=>{
     if(!page.isConnected||sequence!==seq||document.hidden){activeBudgets.delete(key);return;}
     try{
      const next=await ctx.api(prefix+'/budget'+(ticket?'?download_grant='+encodeURIComponent(ticket):''));
      if(!page.isConnected||sequence!==seq){activeBudgets.delete(key);return;}
      // Parallel native downloads can finish their checks in a different order.
      if(next.version>=data.version){Object.assign(data,{...next,used:Math.max(data.used,next.used)});showBudget();lastBudget=Date.now();}
      if(!ticket||next.started||exhausted()){pendingBudgets.delete(ticket);activeBudgets.delete(key);return;}
     }catch(error){if([401,403,404,423].includes(error.status)){pendingBudgets.delete(ticket);activeBudgets.delete(key);return;}}
     if(ticket&&attempt<6){const timer=setTimeout(()=>{budgetTimers.delete(timer);check(attempt+1);},Math.min(8000,500*2**attempt));budgetTimers.add(timer);}else{pendingBudgets.delete(ticket);activeBudgets.delete(key);}
    };
    // HEAD does not spend a download. Wait for the actual GET's receipt.
    const timer=setTimeout(()=>{budgetTimers.delete(timer);check();},ticket?300:0);budgetTimers.add(timer);
   };
   showBudget();lastBudget=Date.now();
   const properties=file=>sharedCtx.modal(`<div class=shared-drive-file-card><span class="drive-file-icon is-${fileCategory(file)}">${fileArtwork({...file,id:null},fileCategory(file))}</span><h2>${esc(file.name)}</h2><p>${esc(fileType(file))}${file.kind==='file'?' · '+bytes(file.size):''}</p></div><dl class=drive-properties><dt>${tHtml("files:modified")}</dt><dd>${date(file.updated)}</dd><dt>${tHtml("files:access")}</dt><dd>${permission}</dd>${data.expires?`<dt>${tHtml("files:linkTo")}</dt><dd>${date(data.expires)}</dd>`:''}</dl><button class="primary shared-drive-file-download" data-shared-download ${exhausted()?'disabled':''}>${tHtml("files:download2", {value0:(icon('download')), value1:(file.kind==='folder'?(" "+tText("files:folder2")+""):'')})}</button>`).querySelector('[data-shared-download]').addEventListener('click',()=>download(file));
   const open=file=>{
    if(busy)return;if(file.kind==='folder'){navigate(file.id);return;}if(exhausted()){ctx.toast(tText('files:shareExhausted'));return;}
    if(/^(image|video|audio)\//.test(file.mime||'')){mediaPreview(file,sharedCtx,menu,{prefix});return;}
    if(file.mime?.startsWith('text/')||fileCategory(file)==='code'){
     fileEditor(file,sharedCtx,load,{prefix,write:data.permission==='edit',refreshGrant:async()=>{grant=await ctx.api(prefix+'/edit-session','POST');return {'X-CSRF-Token':grant.csrf,'X-File-Edit-Token':grant.edit_token};}});return;
    }
    properties(file);
   };
   const menu=(file,anchor)=>driveMenu(anchor,[{label:file.kind==='folder'?tText("files:open"):tText("files:view"),icon:file.kind==='folder'?'chevron':'preview',attr:'data-shared-preview',action:()=>open(file)},{label:tText("files:properties"),icon:'properties',attr:'data-shared-info',action:()=>properties(file)},{separator:true},{label:file.kind==='folder'?tText("files:downloadFolder"):tText("files:downloadFile"),icon:'download',attr:'data-shared-download',action:()=>download(file)}]);
   const render=()=>{
    panel.dataset.layout=layout;
    const items=[...data.items].sort((a,b)=>sort==='updated'?b.updated-a.updated:sort==='size'?b.size-a.size:(a.kind===b.kind?0:a.kind==='folder'?-1:1)||(sort==='kind'?fileType(a).localeCompare(fileType(b),locale()):0)||a.name.localeCompare(b.name,locale(),{numeric:true}));
    list.innerHTML=items.map(file=>driveRow(file,false,{shared:true,prefix,draggable:false})).join('')||`<div class="file-empty drive-empty">${icon('folder')}<strong>${tHtml("files:folderIsCurrentlyEmpty")}</strong><span>${canUpload?tText("files:youCanAddFilesToIt"):tText("files:filesWillAppearHereWhenTheOwnerAdds")}</span></div>`;
    list.querySelectorAll('[data-file-row]').forEach(row=>{
     const file=items.find(file=>file.id===row.dataset.fileRow),select=()=>{selected.has(file.id)?selected.delete(file.id):selected.add(file.id);sync();};
     $('[data-select-file]',row).onchange=select;
     row.onclick=event=>{if(!busy&&!event.target.closest('button,input')){if(!event.metaKey&&!event.ctrlKey)selected.clear();select();}};
     $('[data-shared-open]',row).onclick=()=>open(file);
     $('[data-file-menu]',row).onclick=event=>{selected.clear();selected.add(file.id);sync();menu(file,event.currentTarget);};
     row.oncontextmenu=event=>{event.preventDefault();selected.clear();selected.add(file.id);sync();menu(file,$('[data-file-menu]',row));};
    });
    list.querySelectorAll('[data-drive-thumbnail]').forEach(img=>{img.onload=()=>img.parentElement.classList.add('has-thumbnail');img.onerror=()=>img.remove();});sync();
   };
   render();
   $('[data-shared-root]',page).onclick=()=>navigate(data.root.id);
   $('[data-shared-back]',page)?.addEventListener('click',()=>navigate(crumbs.at(-2).id));
   page.querySelectorAll('[data-shared-folder]').forEach(button=>button.onclick=()=>navigate(button.dataset.sharedFolder));
   const layoutButton=$('[data-drive-layout]',page),updateLayout=()=>layoutButton.innerHTML=icon(layout)+icon('layoutChevron');updateLayout();
   layoutButton.onclick=()=>driveMenu(layoutButton,[['grid',tText("files:grid")],['list',tText("files:list")]].map(([value,label])=>({label,icon:value,checked:layout===value,attr:'data-layout-option='+value,action:()=>{layout=value;updateLayout();render();}})),tText("files:showAs"));
   const sorting=$('[data-files-sort]',page),sortLabels={name:tText("files:byName"),updated:tText("files:byDate"),kind:tText("files:byType"),size:tText("files:bySize")};
   sorting.textContent=sortLabels[sort];sorting.onclick=()=>driveMenu(sorting,Object.entries(sortLabels).map(([value])=>({label:({name:tText("files:name"),updated:tText("files:date"),kind:tText("files:type"),size:tText("files:size")})[value],icon:({name:'file',updated:'recent',kind:'properties',size:'archive'})[value],checked:sort===value,attr:'data-sort-option='+value,action:()=>{sort=value;sorting.textContent=sortLabels[sort];render();}})),tText("files:sorting"));
   bindDownload($('.shared-drive-download',page),sharedCtx);
   $('[data-shared-selected]',page).onclick=()=>{if(selected.size)downloadFile('/api'+prefix+'/download.zip?ids='+encodeURIComponent([...selected].join(',')),sharedCtx);};
   $('[data-shared-properties]',page).onclick=()=>{if(single())properties(single());};
   $('[data-shared-more]',page).onclick=event=>{if(single())menu(single(),event.currentTarget);};
   const input=$('[data-shared-upload]',page);
   if(input){
    input.parentElement.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();input.click();}};
    input.onchange=async()=>{
     if(busy)return;const files=[...input.files];if(!files.length)return;
     if(files.some(file=>file.size>data.max_file_bytes)){ctx.toast(tText("files:fileExceeds", {value0:(bytes(data.max_file_bytes))}));input.value='';return;}
     busy=true;panel.querySelectorAll('button,input').forEach(el=>el.disabled=true);
     transfer?.el.remove();transfer=uploadPanel(files.map(file=>({name:file.name,size:file.size,mime:file.type})),()=>page.isConnected,()=>{transfer.el.remove();transfer=null;panel.classList.remove('has-transfer');});panel.classList.add('has-transfer');panel.append(transfer.el);
     let error;
     try{
      for(const file of files){
       if(transfer.isStopped())break;transfer.start();
       grant=await ctx.api(prefix+'/edit-session','POST');
       await withUploadRateRetry(()=>uploadFile(prefix+'/upload?parent_id='+data.current.id+'&name='+encodeURIComponent(file.name),file,{'X-CSRF-Token':grant.csrf,'X-File-Edit-Token':grant.edit_token},transfer.progress),transfer);transfer.complete();
      }
     }catch(e){if(!(e instanceof UploadStoppedError)){error=e;ctx.toast(e.message);}}finally{transfer.finish(error);busy=false;if(page.isConnected)await load();}
    };
   }
   if(data.expires)expiryTimer=setTimeout(()=>{dialogs().forEach(el=>$('.modal-close',el)?.click());load();},Math.min(2147483647,Math.max(1000,data.expires*1000-Date.now())));
  }catch(error){
   if(!page.isConnected||sequence!==seq)return;transfer?.el.remove();transfer=null;busy=false;
   const locked=error.status===423,login=error.status===401;
   page.innerHTML=header()+`<section class=shared-drive-gate><div class=shared-drive-gate-icon>${icon(locked||login?'lock':'shared')}</div><h2>${locked?tText("files:publicationIsPasswordProtected"):login?tText("files:enterPasswordToAccess"):tText("files:publicationIsUnavailable")}</h2><p>${esc(error.status>=500?tText("files:failedToOpenPublicationTryAgainLater"):error.message)}</p>${locked?`<form data-file-unlock><label>${tHtml("files:password")}<input name=password type=password required maxlength=256 autocomplete=current-password placeholder="${tHtml("files:enterPassword")}"></label><button class=primary type=submit>${tHtml("files:openPublication")}</button></form>`:login?`<button class=primary data-file-login>${tHtml("files:signIn2")}</button>`:`<a class=shared-drive-home href=/>${tHtml("files:goToHome")}</a>`}</section>`;
   $('[data-file-login]',page)?.addEventListener('click',()=>ctx.login());
   const form=$('[data-file-unlock]',page);if(form)formBind(form,async values=>{await ctx.api(prefix+'/unlock','POST',{password:values.get('password')});await load();});
  }
 };
 await load();
}
