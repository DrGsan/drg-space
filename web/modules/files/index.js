import {fileShare} from './sharing.js';
import {tText,tHtml,locale} from "../../core/i18n.js";
import './drive.css';
import {mobileApp} from '../../core/mobile.js';
import {fileEditor} from './file-editor.js';
import {captureDrop,readDrop} from './drop.js';
import {uploadPanel,uploadFile} from './upload-panel.js';
import {withUploadRateRetry,UploadStoppedError} from './upload-retry.js';
import {mediaPreview,sendFileCopy} from './preview.js';
import {downloadFile} from './download.js';
import {watchFileDeadlines} from './deadline-refresh.js';
import {driveShell,driveStorage,driveRow,driveAccessUntil,driveSharingBadges,fileType,icon,driveBrand,driveMenu,closeDriveMenu} from './drive-ui.js';
import {inboxController} from '../notifications/index.js';
import {fromFileList,fromDirectory,uploadFolder} from './folder-upload.js';
import {$,esc,date,formBind,bytes} from '../../core/ui.js';
import {mountVault,vaultController,moveIntoVault} from '../security/vault.js';
export {bytes};
export async function mountFiles(host,ctx){
  let parent=ctx.state.fileFolder||null,view=ctx.state.fileTrash?'trash':ctx.state.fileView||'active',sequence=0,busy=false;
  let sort=view==='recent'?'updated':'name',layout=ctx.state.fileLayout||'list',transfer=null,deadlineWatch;
  const heading=document.createElement('header');heading.className='drive-workspace-header';
  heading.innerHTML='<button type=button id=files-main-menu aria-label="'+(mobileApp()?tText("files:backToSettings"):tText("files:mainMenu"))+'">'+icon('back')+'<span>'+(mobileApp()?tText("files:back"):tText("files:mainMenu"))+'</span></button><h1>'+driveBrand()+'</h1>'+(ctx.notesInboxButton||'');
  $('#files-main-menu',heading).onclick=()=>ctx.navigate(mobileApp()?'settings':'home');
  const lifecycle=new MutationObserver(()=>{if(!heading.isConnected){deadlineWatch?.destroy();lifecycle.disconnect();}});
  lifecycle.observe(host.closest('.workspace')?.parentElement||host,{childList:true});
  const show=markup=>{
    closeDriveMenu();host.replaceChildren(heading);host.insertAdjacentHTML('beforeend',markup);inboxController.attach(ctx);
    const workspace=host.closest('.workspace'),nav=$('.files-view-tabs',host);
    if(workspace&&nav){
      $('.drive-mobile-tabs',workspace)?.remove();
      const bottom=nav.cloneNode(true);bottom.className='mobile-app-tabs drive-mobile-tabs';
      $('[data-files-view=trash]>span',bottom).textContent=tText("files:trash");
      bottom.querySelectorAll('[data-files-view]').forEach(button=>{button.dataset.mobileFilesView=button.dataset.filesView;button.removeAttribute('data-files-view');});
      bottom.querySelector('.file-trash-count')?.remove();workspace.append(bottom);
    }
    if(transfer){const panel=$('.drive-panel',host);if(panel){panel.classList.add('has-transfer');panel.append(transfer.el);}}
  };
  const load=async()=>{
    if(sequence&&!heading.isConnected)return;
    deadlineWatch?.destroy();deadlineWatch=null;
    const seq=++sequence;ctx.state.fileFolder=parent;ctx.state.fileTrash=view==='trash';ctx.state.fileView=view;
    vaultController.destroy();
    show(`<p class=drive-loading>${tHtml("files:uploadingFiles")}</p>`);
    try{
      const trash=view==='trash',data=await ctx.api(trash?'/files/trash':'/files?view='+(view==='secret'?'active':view)+(view==='active'&&parent?'&parent_id='+encodeURIComponent(parent):''));
      if(trash){const status=await ctx.api('/secret-vault');data.secret_enabled=status.enabled;}
      if(!heading.isConnected||seq!==sequence)return;busy=false;
      const selected=new Set(),showError=e=>ctx.toast(e.message),targetParent=view==='active'?parent:null;
      show(driveShell(data,view,parent));
      if(view==='secret'){
        const bottom=host.closest('.workspace')?.querySelector('.drive-mobile-tabs');
        [...host.querySelectorAll('[data-files-view]'),...(bottom?.querySelectorAll('[data-mobile-files-view]')||[])].forEach(b=>b.onclick=()=>{view=b.dataset.filesView||b.dataset.mobileFilesView;parent=null;load();});
        if(data.secret_enabled)mountVault($('.drive-main',host),ctx,'files');
        else{view='active';load();}
        return;
      }
      const panel=$('.drive-panel',host),list=$('.file-list',host),sorting=$('[data-files-sort]',host);
      const sortLabels={name:tText("files:byName"),updated:tText("files:byDate"),size:tText("files:bySize"),kind:tText("files:byType")};
      const updateSort=()=>sorting.textContent=sortLabels[sort];updateSort();
      const status=message=>{const el=$('[data-file-status]',host);if(el)el.textContent=message;};
      const bottom=host.closest('.workspace')?.querySelector('.drive-mobile-tabs');
      const lock=value=>{busy=value;panel.setAttribute('aria-busy',String(value));panel.querySelectorAll('button,input,select').forEach(el=>{if(!el.closest('.drive-transfer'))el.disabled=value;});bottom?.querySelectorAll('button').forEach(el=>el.disabled=value);if(!value){syncSelection();deadlineWatch?.resume();}};
      const navigate=id=>{if(busy)return;parent=id||null;view='active';load();};
      [...host.querySelectorAll('[data-files-view]'),...(bottom?.querySelectorAll('[data-mobile-files-view]')||[])].forEach(b=>b.onclick=()=>{if(busy)return;view=b.dataset.filesView||b.dataset.mobileFilesView;parent=null;sort=view==='recent'?'updated':'name';load();});
      host.querySelectorAll('[data-folder]').forEach(b=>b.onclick=()=>navigate(b.dataset.folder));
      const visibleItems=()=>[...data.items].sort((a,b)=>{
        if(sort==='updated')return b.updated-a.updated||a.name.localeCompare(b.name,locale());
        if(sort==='size')return b.size-a.size||a.name.localeCompare(b.name,locale());
        return (a.kind===b.kind?0:a.kind==='folder'?-1:1)||(sort==='kind'?fileType(a).localeCompare(fileType(b),locale()):0)||a.name.localeCompare(b.name,locale(),{numeric:true});
      });
      const refreshItems=async(quiet=false)=>{
        let next;
        try{next=await ctx.api(trash?'/files/trash':'/files?view='+view+(view==='active'&&parent?'&parent_id='+encodeURIComponent(parent):''));}
        catch(error){if(quiet&&error.status===404&&view==='active'&&parent){parent=data.breadcrumbs.at(-2)?.id||null;await load();return;}throw error;}
        if(seq!==sequence||!list.isConnected||quiet&&(busy||editing))return;
        const previous=new Map(data.items.map(f=>[f.id,f]));
        data.items=next.items.map(f=>Object.assign(previous.get(f.id)||{},{delete_at:null,retention_version:null,retention_blocked:false,shared_until:undefined},f));
        for(const id of selected)if(!next.items.some(f=>f.id===id))selected.delete(id);
        Object.assign(data,{quota:next.quota,refresh_at:next.refresh_at,server_time:next.server_time});deadlineWatch?.set(data.refresh_at??null,data.server_time);render();
        $('.drive-storage',host).innerHTML=driveStorage(data.quota);
        const count=$('.file-trash-count',host);if(count)count.textContent=trash?data.items.length:next.trash_count||0;
      };
      const syncSelection=()=>{
        if(seq!==sequence)return;
        list.querySelectorAll('[data-file-row]').forEach(row=>{const checked=selected.has(row.dataset.fileRow);row.classList.toggle('is-selected',checked);const input=$('[data-select-file]',row);if(input)input.checked=checked;});
        $('[data-file-summary]',host).textContent=tText("files:objects2", {value0:(selected.size?(""+tText("files:selected", {value0:(selected.size)})+" "):''), value1:(visibleItems().length)});
        for(const attr of ['download','delete','share','more','send']){const button=$('[data-'+attr+'-selected]',host);if(button)button.disabled=busy||(['share','more','send'].includes(attr)?selected.size!==1:!selected.size);}
        const send=$('[data-send-selected]',host);if(send&&selected.size===1)send.disabled=busy||data.items.find(f=>selected.has(f.id))?.kind==='folder';
      };
      const runTrash=async(f,action)=>{
        if(busy)return;
        if(action==='permanent'&&!confirm(tText("files:deletePermanentlyRestoringWillNotBePossible", {value0:(f.name), value1:(f.kind==='folder'?(" "+tText("files:andAllContent")+""):''), value2:(f.kind==='folder'?tText("files:folder2"):tText("files:file3"))})))return;
        lock(true);try{await ctx.api('/files/'+f.id+'/'+action,action==='restore'?'POST':'DELETE');ctx.toast(action==='restore'?tText("files:recovered", {value0:(f.name)}):tText("files:permanentlyDeleted", {value0:(f.name)}));await load();}catch(error){showError(error);}finally{lock(false);}
      };
      const move=async(items,target)=>{
        if(busy)return;
        lock(true);try{const result=await ctx.api('/files/move','POST',{items:items.map(f=>({id:f.id,version:f.version})),parent_id:target});ctx.toast(result.retention_changes?.length?tText('files:shareMoveDelay'):tText("files:movedObjects", {value0:(result.moved)}));await load();}catch(error){showError(error);}finally{lock(false);}
      };
      const chooseMove=f=>{
        let destination=parent,browseSequence=0;
        const el=ctx.modal(`<h2>${tHtml("files:move", {value0:(esc(f.name))})}</h2><div data-move-folders></div><button class=primary data-confirm-move>${tHtml("files:moveHere")}</button>`);
        const browse=async id=>{
          const request=++browseSequence;const confirm=$('[data-confirm-move]',el);confirm.disabled=true;
          try{const d=await ctx.api('/files'+(id?'?parent_id='+encodeURIComponent(id):''));if(!el.isConnected||request!==browseSequence)return;destination=id;
            $('[data-move-folders]',el).innerHTML=`<nav class=file-crumbs><button data-move-to="">${tHtml("files:myFiles")}</button>${d.breadcrumbs.map(x=>'<button data-move-to="'+x.id+'">› '+esc(x.name)+'</button>').join('')}</nav><div class=drive-move-list>${d.items.filter(x=>x.kind==='folder'&&x.id!==f.id).map(x=>'<button data-move-to="'+x.id+'">'+icon('folder')+esc(x.name)+'</button>').join('')}</div>`;
            el.querySelectorAll('[data-move-to]').forEach(b=>b.onclick=()=>browse(b.dataset.moveTo||null));
            confirm.disabled=false;
          }catch(e){showError(e);}
        };
        $('[data-confirm-move]',el).onclick=async()=>{$('.modal-close',el).click();await move([f],destination);};browse(destination);
      };
      const download=f=>downloadFile(f.kind==='folder'?'/api/files/download.zip?ids='+f.id:'/api/files/'+f.id+'/content',ctx);
      const open=f=>f.kind==='folder'?navigate(f.id):/^(image|video|audio)\//.test(f.mime||'')?mediaPreview(f,ctx,menu):fileEditor(f,ctx,load);
      const remove=async(items,confirmRemoval=true)=>{
        if(busy||!items.length||confirmRemoval&&!confirm(items.length===1?tText("files:moveToThe7DayTrash", {value0:(items[0].name), value1:(items[0].kind==='folder'?(" "+tText("files:andAllContent")+""):'')}):tText("files:moveSelectedObjectsToThe7DayTrash", {value0:(items.length)})))return;
        lock(true);try{for(const f of items)await ctx.api('/files/'+f.id,'DELETE');ctx.toast(tText("files:movedToTrash", {value0:(items.length)}));}catch(error){showError(error);}finally{await load();lock(false);}
      };
      const duplicate=async f=>{
        if(busy)return;
        if(data.quota.limit_bytes!==null&&f.size>data.quota.limit_bytes-data.quota.used_bytes){showError(Error(tText("files:notEnoughSpaceToCopyTheFile")));return;}
        lock(true);status(tText("files:creatingAFileCopy"));
        try{
          const response=await fetch('/api/files/'+f.id+'/content',{cache:'no-store'});if(!response.ok)throw Error(tText("files:failedToReadTheFile"));
          const dot=f.name.lastIndexOf('.'),extension=dot>0&&f.name.length-dot<20?f.name.slice(dot):'',stem=extension?f.name.slice(0,dot):f.name;
          const suffix=(" "+tText("files:copy", {value0:(extension)})+""),name=stem.slice(0,255-suffix.length)+suffix;
          await ctx.api('/files/upload?name='+encodeURIComponent(name)+(f.parent_id?'&parent_id='+encodeURIComponent(f.parent_id):''),'POST',await response.blob());ctx.toast(tText("files:copyCreated", {value0:(name)}));
        }catch(error){showError(error);}finally{busy=false;if(heading.isConnected)await load();}
      };
      const properties=f=>ctx.modal(`<h2>${tHtml("files:properties")}</h2><dl class=drive-properties><dt>${tHtml("files:name")}</dt><dd>${esc(f.name)}</dd><dt>${tHtml("files:type")}</dt><dd>${esc(fileType(f))}</dd><dt>${tHtml("files:size")}</dt><dd>${f.kind==='folder'?'—':bytes(f.size)}</dd><dt>${tHtml("files:created")}</dt><dd>${date(f.created)}</dd><dt>${tHtml("files:modified")}</dt><dd>${date(f.updated)}</dd>${f.delete_at?`<dt>${tHtml('files:shareRetentionTitle')}</dt><dd>${tHtml('files:shareDeleteAt',{date:esc(date(f.delete_at))})}</dd>`:''}<dt>${tHtml("files:shared")}</dt><dd>${f.shared?tText("files:activeLink"):tText("files:noActiveLinks")}</dd></dl>`);
      let editing=false;
      const inlineName=f=>{
        if(busy||editing)return;editing=true;closeDriveMenu();
        const draft=!f,item=f||{id:'new-folder',kind:'folder',name:tText("files:newFolder"),updated:Date.now()/1000};
        if(draft){list.querySelector('.drive-empty')?.remove();list.insertAdjacentHTML('afterbegin',driveRow(item,false));}
        const row=list.querySelector('[data-file-row="'+item.id+'"]');row.draggable=false;row.classList.add('is-editing');
        const input=document.createElement('input');input.className='drive-name-input';input.maxLength=255;input.value=item.name;input.setAttribute('aria-label',draft?tText("files:folderName"):tText("files:newName"));
        // The editor is a sibling of the open button so it is never nested in a button.
        $('.file-open',row).hidden=true;$('.drive-name',row).append(input);
        let saving=false,cancelled=false;
        const cancel=()=>{if(saving)return;cancelled=true;editing=false;render();};
        const save=async()=>{
          if(saving||cancelled||!input.isConnected)return;
          const name=input.value.trim();if(!name){cancel();return;}if(!draft&&name===f.name){cancel();return;}
          saving=true;lock(true);
          try{
            const result=draft?await ctx.api('/files/folders','POST',{name,parent_id:targetParent}):await ctx.api('/files/'+f.id,'PATCH',{name,version:f.version});
            if(seq!==sequence||!list.isConnected)return;
            const {quota,...item}=result;if(quota){data.quota=quota;$('.drive-storage',host).innerHTML=driveStorage(quota);}
            if(draft)data.items.push(item);else Object.assign(f,item);
            editing=false;render();
          }
          catch(error){showError(error);saving=false;lock(false);input.focus();}
          finally{if(saving)lock(false);}
        };
        input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();save();}if(e.key==='Escape'){e.preventDefault();cancel();}};
        input.onblur=()=>save();input.focus();input.select();
      };
      const menu=(f,anchor,closePreview)=>{
        if(busy)return;
        const entries=[
          ...(data.secret_enabled?[{label:tText("files:toSecureStorage"),icon:'lock',action:async()=>{try{closePreview?.();if(await moveIntoVault(ctx,'files',f))await load();}catch(error){showError(error);}}}]:[]),
          ...(!closePreview?[{label:tText("files:open"),icon:'chevron',attr:'data-file-open',action:()=>open(f)}]:[]),
          ...(!closePreview&&f.kind==='file'?[{label:tText("files:view"),icon:'preview',attr:'data-file-preview-action',action:()=>open(f)}]:[]),
          {label:tText("files:properties"),icon:'properties',attr:'data-file-properties',action:()=>properties(f)},
          {separator:true},{label:tText("files:download", {value0:(f.kind==='folder'?(" "+tText("files:zipFolder")+""):(" "+tText("files:copy2")+""))}),icon:'download',attr:'data-file-download',action:()=>download(f)},
          {separator:true},{label:tText("files:share2"),icon:'share',attr:'data-file-share',action:()=>fileShare(f,{...ctx,onFilesChanged:updateSharing})},
          ...(f.kind==='file'&&typeof navigator.canShare==='function'?[{label:tText("files:sendCopy2"),icon:'mail',attr:'data-file-send',action:()=>sendFileCopy(f,ctx)}]:[]),
          {separator:true},...(f.kind==='file'&&!closePreview?[{label:tText("files:duplicate"),icon:'file',attr:'data-file-duplicate',action:()=>duplicate(f)}]:[]),{label:tText("files:rename"),icon:'rename',attr:'data-file-rename',action:()=>{closePreview?.();inlineName(f);}},
          {label:tText("files:moveToFolder"),icon:'move',attr:'data-file-move',action:()=>{closePreview?.();chooseMove(f);}},
          {separator:true},{label:tText("files:toTrash"),icon:'trash',attr:'data-file-delete',danger:true,action:async()=>{closePreview?.();await remove([f],false);}}
        ];
        driveMenu(anchor,entries);
      };
      const sharingKey=f=>JSON.stringify([f.shared,f.shared_until,f.retention_version,f.delete_at,f.retention_blocked]);
      const patchSharingRow=(row,f)=>{
        if(!row||row.dataset.sharingKey===sharingKey(f))return;
        const expiry=row.querySelector('.drive-expiry');if(expiry&&f.shared){expiry.title=driveAccessUntil(f);expiry.innerHTML='<span class=drive-expiry-label>'+tHtml('files:shareAccessUntil')+' </span>'+esc(driveAccessUntil(f));}
        row.querySelectorAll('.drive-shared,.drive-temporary').forEach(badge=>badge.remove());
        row.querySelector('.file-open')?.insertAdjacentHTML('beforeend',driveSharingBadges(f));row.dataset.sharingKey=sharingKey(f);
      };
      const updateSharing=({id,shares,retention})=>{
        if(seq!==sequence||!list.isConnected)return;
        // Reuse the dialog responses; keep the workspace, thumbnails and selection mounted.
        const schedules=new Map(retention.map(f=>[f.id,f]));
        const added=retention.map(f=>f.delete_at).filter(Number.isFinite);if(added.length)data.refresh_at=Math.min(data.refresh_at??Infinity,...added);
        for(const f of data.items){
          const schedule=schedules.get(f.id);
          if(f.id!==id&&!schedule)continue;
          if(f.id===id){
            const active=shares;f.shared=active.length>0;
            f.shared_until=active.reduce((end,s)=>end===null||s.expires===null?null:Math.max(end,s.expires),0);
          }
          Object.assign(f,{delete_at:schedule?.delete_at??null,retention_version:schedule?.version??null,retention_blocked:!!schedule&&schedule.delete_at===null});
          const row=list.querySelector('[data-file-row="'+f.id+'"]');
          patchSharingRow(row,f);
          if(view==='shared'&&!f.shared){row?.remove();selected.delete(f.id);}
        }
        if(view==='shared')data.items=data.items.filter(f=>f.shared);
        if(!data.items.length)render();else{syncSelection();planRefresh();}
      };
      let dragged=[];
      const render=()=>{
        editing=false;const items=visibleItems();panel.dataset.layout=trash?'list':layout;
        const filesById=new Map(items.map(f=>[f.id,f]));
        if(items.length){
          const existing=new Map([...list.children].filter(row=>row.dataset.fileRow).map(row=>[row.dataset.fileRow,row]));
          const rows=items.map(f=>{
            const old=existing.get(f.id);if(old&&old.dataset.fileVersion===String(f.version??0)&&!old.classList.contains('is-editing')){patchSharingRow(old,f);return old;}
            const template=document.createElement('template');template.innerHTML=driveRow(f,trash,{showExpiry:view==='shared'});
            const row=template.content.firstElementChild;row.dataset.fileVersion=String(f.version??0);row.dataset.sharingKey=sharingKey(f);return row;
          });
          const keep=new Set(rows);for(const row of [...list.children])if(!keep.has(row))row.remove();
          let cursor=list.firstElementChild;for(const row of rows){if(row===cursor)cursor=cursor.nextElementSibling;else list.insertBefore(row,cursor);}
        }else list.innerHTML='<div class="file-empty drive-empty">'+icon(trash?'trash':'folder')+'<strong>'+(trash?tText("files:trashIsEmpty"):view==='shared'?tText("files:noSharedFiles"):view==='recent'?tText("files:noFilesYet"):tText("files:folderIsEmpty"))+'</strong><span>'+(trash?tText("files:deletedObjectsWillAppearHere"):view==='shared'?tText("files:createALinkViaTheFileOrFolder"):tText("files:dragAndDropFilesAndFoldersOrClick"))+'</span></div>';
        list.querySelectorAll('[data-file-row]').forEach(row=>{
          const f=filesById.get(row.dataset.fileRow);
          if(trash){$('[data-file-restore]',row).onclick=()=>runTrash(f,'restore');$('[data-file-permanent]',row).onclick=()=>runTrash(f,'permanent');return;}
          const select=()=>{selected.has(f.id)?selected.delete(f.id):selected.add(f.id);syncSelection();};
          $('[data-select-file]',row).onchange=select;
          row.onclick=e=>{if(!busy&&!e.target.closest('button,input')){if(!e.metaKey&&!e.ctrlKey)selected.clear();select();}};
          $('[data-open-file]',row).onclick=()=>{if(!busy&&!editing)open(f);};
          $('[data-file-menu]',row).onclick=e=>{selected.clear();selected.add(f.id);syncSelection();menu(f,e.currentTarget);};
          row.oncontextmenu=e=>{e.preventDefault();selected.clear();selected.add(f.id);syncSelection();menu(f,$('[data-file-menu]',row));};
          row.ondragstart=e=>{
            if(busy||e.target.closest('[data-file-menu]')){e.preventDefault();return;}
            if(!selected.has(f.id)){selected.clear();selected.add(f.id);syncSelection();}
            dragged=data.items.filter(x=>selected.has(x.id));e.dataTransfer.setData('application/x-drg-files',JSON.stringify(dragged.map(x=>x.id)));e.dataTransfer.effectAllowed='move';
          };
          row.ondragend=()=>{dragged=[];clearDrop();};
        });
        list.querySelectorAll('[data-drive-thumbnail]').forEach(img=>{img.onload=()=>img.parentElement.classList.add('has-thumbnail');img.onerror=()=>img.remove();});
        syncSelection();planRefresh();
      };
      const planRefresh=()=>{
        if(trash)return;
        const deadlines=[data.refresh_at,...data.items.flatMap(f=>[f.delete_at,f.shared_until])].filter(Number.isFinite);
        deadlineWatch?.set(deadlines.length?Math.min(...deadlines):null);
      };
      if(!trash){deadlineWatch=watchFileDeadlines({connected:()=>heading.isConnected&&seq===sequence,blocked:()=>busy||editing,refresh:()=>refreshItems(true)});deadlineWatch.set(data.refresh_at??null,data.server_time);}
      sorting.onclick=()=>driveMenu(sorting,Object.entries(sortLabels).map(([value,label])=>({label:({name:tText("files:name"),updated:tText("files:date"),size:tText("files:size"),kind:tText("files:type")})[value],icon:({name:'file',updated:'recent',size:'archive',kind:'properties'})[value],checked:sort===value,attr:'data-sort-option='+value,action:()=>{sort=value;updateSort();render();}})),tText("files:sorting"));
      const layoutButton=$('[data-drive-layout]',host);
      const updateLayout=()=>layoutButton.innerHTML=icon(layout==='grid'&&!trash?'grid':'list')+icon('layoutChevron');updateLayout();layoutButton.disabled=trash;
      layoutButton.onclick=()=>driveMenu(layoutButton,[['grid',tText("files:grid")],['list',tText("files:list")]].map(([value,label])=>({label,icon:value,checked:layout===value,attr:'data-layout-option='+value,action:()=>{layout=value;ctx.state.fileLayout=value;updateLayout();render();}})),tText("files:showAs"));render();
      const external=e=>[...(e.dataTransfer?.types||[])].includes('Files');
      if(trash){
        panel.ondragover=e=>{if(external(e)){e.preventDefault();e.dataTransfer.dropEffect='none';}};
        panel.ondrop=e=>{if(external(e)){e.preventDefault();ctx.toast(tText("files:toUploadOpenBrowseOrTheDesiredFolder"));}};
        return;
      }
      $('[data-download-selected]',host).onclick=()=>{if(selected.size)downloadFile('/api/files/download.zip?ids='+encodeURIComponent([...selected].join(',')),ctx);};
      const single=()=>data.items.find(f=>selected.has(f.id));
      $('[data-share-selected]',host).onclick=()=>{if(selected.size===1)fileShare(single(),{...ctx,onFilesChanged:updateSharing});};
      $('[data-more-selected]',host).onclick=e=>{if(selected.size===1)menu(single(),e.currentTarget);};
      $('[data-delete-selected]',host).onclick=()=>remove(data.items.filter(f=>selected.has(f.id)));
      const send=$('[data-send-selected]',host);if(send)send.onclick=()=>{if(selected.size===1)sendFileCopy(single(),ctx);};
      $('[data-new-folder]',host).onclick=async()=>{if(view!=='active'){parent=null;view='active';await load();$('[data-new-folder]',host)?.click();return;}inlineName();};
      const uploadLabel=$('.file-upload-label',host);uploadLabel.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('[data-upload-files]',host).click();}};
      const runUpload=async(plans,target)=>{
        if(busy)return;lock(true);let error;
        try{
          let total=0;
          for(const item of plans)for(const file of item.folder?item.folder.files.map(x=>x.file):[item.file]){if(file.size>data.max_file_bytes)throw Error(tText("files:fileIsTooLargeMaximum", {value0:(file.name), value1:(bytes(data.max_file_bytes))}));total+=file.size;}
          if(data.quota.limit_bytes!==null&&total>data.quota.limit_bytes-data.quota.used_bytes)throw Error(tText("files:notEnoughAccountSpaceForSelectedFiles"));
          const entries=plans.flatMap(item=>item.folder?(item.folder.files.length?item.folder.files.map(x=>({kind:'file',name:item.folder.name+'/'+x.path,size:x.file.size,mime:x.file.type})):[{kind:'folder',name:item.folder.name,size:0}]):[{kind:'file',name:item.file.name,size:item.file.size,mime:item.file.type}]);
          if(!entries.length)return;
          transfer?.el.remove();transfer=uploadPanel(entries,()=>heading.isConnected,()=>{transfer?.el.remove();transfer=null;$('.drive-panel',host)?.classList.remove('has-transfer');});
          panel.classList.add('has-transfer');panel.append(transfer.el);
          for(const item of plans){
            if(transfer.isStopped())break;
            if(item.folder)await uploadFolder(item.folder,ctx,target,data,transfer);
            else{transfer.start();await withUploadRateRetry(()=>uploadFile('/files/upload?name='+encodeURIComponent(item.file.name)+(target?'&parent_id='+encodeURIComponent(target):''),item.file,{},transfer.progress),transfer);transfer.complete();}
          }
        }catch(e){if(!(e instanceof UploadStoppedError)){error=e;showError(e);}}finally{transfer?.finish(error);try{if(heading.isConnected&&seq===sequence)await refreshItems();}catch(e){showError(e);}finally{lock(false);}}
      };
      const upload=async(captured,target)=>{if(busy)return;lock(true);try{status(tText("files:readingFilesAndFolders"));const plans=await readDrop(captured);lock(false);await runUpload(plans,target);}catch(error){showError(error);lock(false);}};
      $('[data-upload-files]',host).onchange=e=>runUpload([...e.target.files].map(file=>({file})),targetParent);
      const directory=$('[data-upload-directory]',host),folderButton=$('[data-upload-folder]',host);
      const startFolder=plan=>runUpload([{folder:plan}],targetParent);
      directory.onchange=()=>{try{startFolder(fromFileList(directory.files));}catch(e){showError(e);directory.value='';}};
      folderButton.onclick=async()=>{
        if(typeof window.showDirectoryPicker==='function'&&window.isSecureContext){
          folderButton.disabled=true;try{const handle=await window.showDirectoryPicker({mode:'read'});await startFolder(await fromDirectory(handle));}catch(e){if(e.name!=='AbortError')showError(e);}finally{folderButton.disabled=false;}
        }else directory.click();
      };
      const overlay=$('.drive-drop-overlay',host);
      const clearDrop=()=>{overlay.hidden=true;panel.querySelectorAll('.is-drop-target').forEach(el=>el.classList.remove('is-drop-target'));};
      const dropTarget=e=>e.target.closest('[data-drop-folder],[data-folder],[data-files-view=active]');
      const destination=e=>{const el=dropTarget(e);return el?.hasAttribute('data-drop-folder')?el.dataset.dropFolder:el?.hasAttribute('data-folder')?el.dataset.folder||null:el?.matches('[data-files-view=active]')?null:targetParent;};
      const accepts=e=>!busy&&(dragged.length||[...(e.dataTransfer?.types||[])].includes('Files'));
      panel.ondragover=e=>{
        if(busy&&external(e)){e.preventDefault();e.dataTransfer.dropEffect='none';return;}
        if(!accepts(e))return;e.preventDefault();e.dataTransfer.dropEffect=dragged.length?'move':'copy';clearDrop();const target=dropTarget(e);
        if(target)target.classList.add('is-drop-target');
        if(!dragged.length){overlay.hidden=false;$('[data-drop-label]',overlay).textContent=tText("files:toFolder", {value0:(target?.textContent.trim()||(view==='active'&&parent?data.breadcrumbs.at(-1)?.name:tText("files:myFiles")))});}
      };
      panel.ondragleave=e=>{if(!panel.contains(e.relatedTarget))clearDrop();};
      panel.ondrop=e=>{
        if(busy&&external(e)){e.preventDefault();return;}
        if(!accepts(e))return;e.preventDefault();const target=destination(e);clearDrop();
        if(dragged.length){const items=dragged;dragged=[];move(items,target);}
        else{try{const captured=captureDrop(e.dataTransfer);upload(captured,target);}catch(error){showError(error);}}
      };
    }catch(e){if(host.isConnected&&seq===sequence){if(e.status===404&&view==='active'&&parent){parent=null;await load();return;}show('<p class=drive-loading>'+esc(e.message)+'</p>');}}
  };
  await load();
}
export {fileShare} from './sharing.js';
export {mountSharedFiles} from './shared.js';
