import {tText,tHtml,locale} from "../../core/i18n.js";
import {$,esc,date,bytes} from '../../core/ui.js';
import {driveContent,driveRow,driveMenu,closeDriveMenu,icon,fileType} from './drive-ui.js';
import {captureDrop,readDrop} from './drop.js';
import {fromFileList,fromDirectory} from './folder-upload.js';
import {uploadPanel} from './upload-panel.js';
import {withUploadRateRetry} from './upload-retry.js';
import {fileEditor} from './file-editor.js';

// The regular Drive templates are shared; authorization and destructive actions
// stay with the vault controller. No ordinary asset URL is used for previews.
export function mountSecretDrive(vault){
 const host=vault.host,ctx=vault.ctx,all=vault.items;
 let disposed=false,busy=false,selected=new Set(),sort='name',dragged=[],transfer,thumbnails;
 let layout=ctx.state.fileLayout||'list';
 const breadcrumbs=[];let current=all.find(f=>f.id===vault.parent),seen=new Set();
 while(current&&!seen.has(current.id)){seen.add(current.id);breadcrumbs.unshift(current);current=all.find(f=>f.id===current.parent_id);}
 const items=all.filter(f=>f.parent_id===vault.parent),data={items,breadcrumbs,max_file_bytes:vault.fileLimit};
 host.innerHTML=driveContent(data,'secret',vault.parent);
 const panel=host.closest('.drive-panel'),list=$('.file-list',host);
 const quota=vault.fileQuota,storage=$('.drive-storage',panel);if(quota&&storage){$('span',storage).textContent=bytes(quota.used_bytes)+(quota.limit_bytes===null?(" "+tText("files:unlimited")+""):(" "+tText("files:from", {value0:(bytes(quota.limit_bytes))})+""));const meter=$('meter',storage);if(meter){meter.max=Math.max(1,quota.limit_bytes||1);meter.value=quota.used_bytes;}}
 if(vault.transferPanel){panel.append(vault.transferPanel.el);panel.classList.add('has-transfer');}
 $('[data-delete-selected]',host).insertAdjacentHTML('beforebegin',`<button class="drive-tool" data-vault-lock title="${tHtml("files:block")}" aria-label="${tHtml("files:block")}">${icon('lock')}</button>`);vault.bindHeader();
 const alive=()=>!disposed&&!vault.disposed&&!vault.locked&&host.isConnected;
 const safe=async fn=>{if(busy||!alive())return;try{await fn();}catch(error){if(alive()){lock(false);ctx.toast(error.message);}}};
 const reload=()=>alive()?vault.list():Promise.resolve();
 const navigate=id=>{lock(true);vault.parent=id||null;return reload();};
 const modal=html=>vault.databaseContext().modal(html);
 const sync=()=>{list.querySelectorAll('[data-file-row]').forEach(row=>{const value=selected.has(row.dataset.fileRow);row.classList.toggle('is-selected',value);$('[data-select-file]',row).checked=value;});$('[data-file-summary]',host).textContent=tText("files:objects2", {value0:(selected.size?(""+tText("files:selected", {value0:(selected.size)})+" "):''), value1:(items.length)});for(const action of ['download','delete','more'])$('[data-'+action+'-selected]',host).disabled=busy||(action==='more'?selected.size!==1:!selected.size);};
 const lock=value=>{busy=value;panel.setAttribute('aria-busy',String(value));for(const el of panel.querySelectorAll('button,input,select'))if(!el.closest('.drive-transfer')&&!el.matches('[data-vault-lock]'))el.disabled=value;for(const el of host.closest('.workspace')?.querySelectorAll('.drive-mobile-tabs button')||[])el.disabled=value;if(!value&&alive())sync();};
 const move=async(files,target)=>{lock(true);for(const file of files)await vault.api('/'+file.id,'PUT',{name:file.name,parent_id:target||null,version:file.version});await reload();};
 const remove=async files=>{if(!confirm(tText("files:deleteSelectedItemsAndNestedContentPermanentlyNo", {value0:(files.length)})))return;lock(true);for(const f of files)await vault.api('/items/'+f.id,'DELETE');await reload();};
 const rename=file=>{
  const draft=!file,item=file||{id:'new-folder',kind:'folder',name:tText("files:newFolder"),size:0,updated:Date.now()/1000};
  if(draft){list.querySelector('.drive-empty')?.remove();list.insertAdjacentHTML('afterbegin',driveRow(item,false,{prefix:false}));}
  const row=list.querySelector('[data-file-row="'+item.id+'"]');row.draggable=false;row.classList.add('is-editing');$('.file-open',row).hidden=true;
  const input=document.createElement('input');input.className='drive-name-input';input.maxLength=255;input.value=item.name;input.setAttribute('aria-label',draft?tText("files:folderName"):tText("files:newName"));$('.drive-name',row).append(input);
  let saving=false,cancelled=false;
  const save=async()=>{if(saving||cancelled||!alive())return;const name=input.value.trim();if(!name||!draft&&name===file.name){cancelled=true;render();return;}saving=true;input.disabled=true;try{await vault.api(draft?'/folders':'/'+file.id,draft?'POST':'PUT',{name,parent_id:draft?vault.parent:file.parent_id,...draft?{}:{version:file.version}});await reload();}catch(error){ctx.toast(error.message);saving=false;input.disabled=false;input.focus();}};
  input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();save();}if(e.key==='Escape'){e.preventDefault();cancelled=true;render();}};input.onblur=save;input.focus();input.select();
 };
 const chooseMove=file=>{
  const el=modal(`<h2>${tHtml("files:move", {value0:(esc(file.name))})}</h2><div class=drive-move-list><button data-secret-target="">${tHtml("files:secret", {value0:(icon('lock'))})}</button>${all.filter(f=>f.kind==='folder'&&f.id!==file.id).map(f=>'<button data-secret-target="'+f.id+'">'+icon('folder')+esc(f.name)+'</button>').join('')}</div>`);
  el.querySelectorAll('[data-secret-target]').forEach(b=>b.onclick=()=>safe(async()=>{await move([file],b.dataset.secretTarget);$('.modal-close',el).click();}));
 };
 const open=file=>safe(async()=>{
  if(file.kind==='folder'){await navigate(file.id);return;}
  if(/^(image|video|audio)\//.test(file.mime||'')){await vault.preview(file);return;}
  await fileEditor(file,{...ctx,modal,api:(path,method,body)=>vault.api(path.replace(/^\/files/,''),method,body)},async()=>{const data=await vault.api('/items');if(alive()){Object.assign(file,data.items.find(f=>f.id===file.id));render();}},{download:()=>vault.download(file.id)});
 });
 const menu=(file,anchor)=>driveMenu(anchor,[
  {label:tText("files:open"),icon:'chevron',attr:'data-file-open',action:()=>open(file)},
  {label:tText("files:properties"),icon:'properties',attr:'data-file-properties',action:()=>modal(`<h2>${tHtml("files:properties")}</h2><dl class=drive-properties><dt>${tHtml("files:name")}</dt><dd>${esc(file.name)}</dd><dt>${tHtml("files:type")}</dt><dd>${esc(fileType(file))}</dd><dt>${tHtml("files:size")}</dt><dd>${bytes(file.size)}</dd><dt>${tHtml("files:created")}</dt><dd>${date(file.created)}</dd><dt>${tHtml("files:modified")}</dt><dd>${date(file.updated)}</dd></dl>`)},
  {separator:true},{label:tText("files:download", {value0:(file.kind==='folder'?(" "+tText("files:zipFolder")+""):'')}),icon:'download',attr:'data-file-download',action:()=>vault.download(file.id)},
  ...(file.kind==='file'?[{label:tText("files:duplicate"),icon:'file',attr:'data-file-duplicate',action:()=>safe(async()=>{await vault.api('/'+file.id+'/duplicate','POST',{version:file.version});await reload();})}]:[]),
  {label:tText("files:rename"),icon:'rename',attr:'data-file-rename',action:()=>rename(file)},
  {label:tText("files:moveToFolder"),icon:'move',attr:'data-file-move',action:()=>chooseMove(file)},
  {label:tText("files:moveToRegularSection"),icon:'move',attr:'data-vault-out',action:()=>vault.moveOut(file)},
  {separator:true},{label:tText("files:deletePermanently"),icon:'trash',attr:'data-file-delete',danger:true,action:()=>safe(()=>remove([file]))}
 ]);
 function render(){
  if(!alive())return;
  panel.dataset.layout=layout;
  const rows=[...items].sort((a,b)=>sort==='updated'?b.updated-a.updated:sort==='size'?b.size-a.size:(a.kind===b.kind?0:a.kind==='folder'?-1:1)||(sort==='kind'?fileType(a).localeCompare(fileType(b),locale()):0)||a.name.localeCompare(b.name,locale(),{numeric:true}));
  list.innerHTML=rows.map(f=>driveRow(f,false,{prefix:false})).join('')||`<div class="file-empty drive-empty">${icon('folder')}<strong>${tHtml("files:folderIsEmpty")}</strong><span>${tHtml("files:dragAndDropFilesAndFoldersOrClick")}</span></div>`;
  thumbnails?.disconnect();
  thumbnails=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){thumbnails.unobserve(entry.target);const img=entry.target,id=img.dataset.secretThumbnail,file=items.find(f=>f.id===id),key='thumbnail:'+id+':'+file.version;const apply=url=>{if(alive()&&img.isConnected){img.src=url;img.parentElement.classList.add('has-thumbnail');}};const known=vault.urls.get(key);if(known)apply(known);else vault.binary('/'+id+'/thumbnail').then(r=>r.blob()).then(blob=>apply(vault.remember(key,URL.createObjectURL(blob)))).catch(()=>img.remove());}});
  list.querySelectorAll('[data-file-row]').forEach(row=>{
   const picture=items.find(f=>f.id===row.dataset.fileRow);if(picture.mime?.startsWith('image/')&&picture.size<=32*1024**2){const img=document.createElement('img');img.className='drive-thumbnail';img.dataset.secretThumbnail=picture.id;img.alt='';$('.drive-file-icon',row).append(img);thumbnails.observe(img);}
   const file=items.find(f=>f.id===row.dataset.fileRow),select=()=>{selected.has(file.id)?selected.delete(file.id):selected.add(file.id);sync();};
   $('[data-select-file]',row).onchange=select;row.onclick=e=>{if(!e.target.closest('button,input'))select();};
   $('[data-open-file]',row).onclick=()=>open(file);$('[data-file-menu]',row).onclick=e=>{if(busy)return;selected.clear();selected.add(file.id);sync();menu(file,e.currentTarget);};
   row.oncontextmenu=e=>{e.preventDefault();if(busy)return;selected.clear();selected.add(file.id);sync();menu(file,$('[data-file-menu]',row));};
   row.ondragstart=e=>{if(busy||e.target.closest('[data-file-menu]')){e.preventDefault();return;}if(!selected.has(file.id)){selected.clear();selected.add(file.id);sync();}dragged=items.filter(f=>selected.has(f.id));e.dataTransfer.setData('application/x-drg-secret-files',JSON.stringify(dragged.map(f=>f.id)));e.dataTransfer.effectAllowed='move';};
   row.ondragend=()=>{dragged=[];clearDrop();};
  });sync();
 }
 const sorting=$('[data-files-sort]',host),labels={name:tText("files:byName"),updated:tText("files:byDate"),size:tText("files:bySize"),kind:tText("files:byType")};
 sorting.textContent=labels[sort];sorting.onclick=()=>driveMenu(sorting,Object.keys(labels).map(key=>({label:labels[key],icon:'list',checked:sort===key,attr:'data-sort-option='+key,action:()=>{sort=key;sorting.textContent=labels[sort];render();}})));
 const layoutButton=$('[data-drive-layout]',host);layoutButton.onclick=()=>driveMenu(layoutButton,['list','grid'].map(value=>({label:value==='list'?tText("files:list"):tText("files:grid"),icon:value,checked:layout===value,attr:'data-layout-option='+value,action:()=>{layout=value;ctx.state.fileLayout=value;render();}})));
 $('[data-new-folder]',host).onclick=()=>safe(()=>rename());
 $('[data-delete-selected]',host).onclick=()=>safe(()=>remove(items.filter(f=>selected.has(f.id))));
 $('[data-download-selected]',host).onclick=()=>safe(async()=>{await vault.download([...selected]);});
 $('[data-more-selected]',host).onclick=e=>{const file=items.find(f=>selected.has(f.id));if(file)menu(file,e.currentTarget);};
 host.querySelectorAll('[data-folder]').forEach(b=>b.onclick=()=>safe(()=>navigate(b.dataset.folder)));
 const runUpload=async(plans,target)=>{
  if(busy||!alive())return;lock(true);let error;
  const entries=plans.flatMap(item=>item.folder?(item.folder.files.length?item.folder.files.map(f=>({name:item.folder.name+'/'+f.path,size:f.file.size,mime:f.file.type})):[{name:item.folder.name,size:0,kind:'folder'}]):[{name:item.file.name,size:item.file.size,mime:item.file.type}]);
  if(!entries.length){lock(false);return;}
  vault.transferPanel?.el.remove();transfer=uploadPanel(entries,alive,()=>{transfer.el.remove();vault.transferPanel=null;panel.classList.remove('has-transfer');});vault.transferPanel=transfer;panel.append(transfer.el);panel.classList.add('has-transfer');
  try{
   for(const item of plans){
    if(transfer.isStopped())break;
    if(item.folder){
     const mapping={};for(const path of item.folder.folders){if(transfer.isStopped())break;const parts=path.split('/');const folder=await vault.api('/folders','POST',{name:path?parts.at(-1):item.folder.name,parent_id:path?mapping[parts.slice(0,-1).join('/')]:target});mapping[path]=folder.id;}
     if(!item.folder.files.length){transfer.start();transfer.complete();}
     for(const entry of item.folder.files){if(transfer.isStopped())break;transfer.start();await withUploadRateRetry(()=>vault.upload(entry.file,mapping[entry.parent],transfer.progress),transfer);transfer.complete();}
    }else{transfer.start();await withUploadRateRetry(()=>vault.upload(item.file,target,transfer.progress),transfer);transfer.complete();}
   }
  }catch(e){error=e;if(alive())ctx.toast(e.message);}
  finally{transfer.finish(error);if(alive()){lock(false);await reload();}}
 };
 $('[data-upload-files]',host).onchange=e=>runUpload([...e.target.files].map(file=>({file})),vault.parent);
 $('.file-upload-label',host).onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('[data-upload-files]',host).click();}};
 const directory=$('[data-upload-directory]',host);directory.onchange=()=>{try{runUpload([{folder:fromFileList(directory.files)}],vault.parent);}catch(error){ctx.toast(error.message);}};
 $('[data-upload-folder]',host).onclick=()=>safe(async()=>{if(typeof window.showDirectoryPicker==='function'&&window.isSecureContext){try{await runUpload([{folder:await fromDirectory(await window.showDirectoryPicker({mode:'read'}))}],vault.parent);}catch(error){if(error.name!=='AbortError')throw error;}}else directory.click();});
 const overlay=$('.drive-drop-overlay',host),external=e=>[...(e.dataTransfer?.types||[])].includes('Files');
 const clearDrop=()=>{overlay.hidden=true;host.querySelectorAll('.is-drop-target').forEach(el=>el.classList.remove('is-drop-target'));};
 const dropTarget=e=>e.target.closest('[data-drop-folder],[data-folder]');
 const destination=e=>{const target=dropTarget(e);return target?.hasAttribute('data-drop-folder')?target.dataset.dropFolder:target?.hasAttribute('data-folder')?target.dataset.folder||null:vault.parent;};
 host.ondragover=e=>{if(!dragged.length&&!external(e))return;e.preventDefault();e.dataTransfer.dropEffect=busy?'none':dragged.length?'move':'copy';if(busy)return;clearDrop();dropTarget(e)?.classList.add('is-drop-target');if(external(e))overlay.hidden=false;};
 host.ondragleave=e=>{if(!host.contains(e.relatedTarget))clearDrop();};
 host.ondrop=e=>{if(!dragged.length&&!external(e))return;e.preventDefault();if(busy)return;const target=destination(e);clearDrop();if(dragged.length){const files=dragged;dragged=[];safe(()=>move(files,target));}else{const captured=captureDrop(e.dataTransfer);safe(async()=>runUpload(await readDrop(captured),target));}};
 lock(false);render();
 return {obscure(hidden){closeDriveMenu();if(vault.transferPanel)vault.transferPanel.el.hidden=hidden;},destroy(){lock(false);thumbnails?.disconnect();vault.transferPanel?.el.remove();disposed=true;closeDriveMenu();host.ondragover=host.ondragleave=host.ondrop=null;panel.classList.remove('has-transfer');}};
}
