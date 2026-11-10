import {tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
import {icon} from './drive-ui.js';
import {bindDownload} from './download.js';
const BLOB_LIMIT=256*1024*1024;

export async function mediaPreview(file,ctx,menu,options={}){
 const path=(options.prefix||'/files')+'/'+encodeURIComponent(file.id);
 const type=file.mime||'',tag=type.startsWith('image/')?'img':type.startsWith('video/')?'video':'audio';
 const el=ctx.modal(`<div class=drive-preview-toolbar><button class=drive-tool data-preview-close aria-label="${tHtml("files:close")}">${icon('close')}</button><strong>${esc(file.name)}</strong><a class=drive-tool href="/api${esc(path)}/content" aria-label="${tHtml("files:downloadFile")}" title="${tHtml("files:downloadFile")}">${icon('download')}</a><button class=drive-tool data-preview-menu aria-label="${tHtml("files:fileActions2")}" aria-haspopup=menu>${icon('more')}</button></div><div class=drive-preview-content data-file-preview><p role=status>${tHtml("files:loadingPreview")}</p></div>`);
 el.classList.add('drive-preview-dialog');
 bindDownload($('a',el),ctx);
 const controller=new AbortController();let objectUrl;
 const cleanup=new MutationObserver(()=>{if(!el.isConnected){controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);cleanup.disconnect();}});
 cleanup.observe(document.body,{childList:true});
 $('[data-preview-close]',el).onclick=()=>$('.modal-close',el).click();
 $('[data-preview-menu]',el).onclick=e=>menu(file,e.currentTarget,()=>$('.modal-close',el).click());
 if(file.size>BLOB_LIMIT){$('[data-file-preview]',el).textContent=tText("files:thisFileIsTooLargeToPreviewIn");return;}
 try{
  const response=await fetch('/api'+path+'/content'+(options.prefix?.startsWith('/shared-files/')?'?preview=true':''),{signal:controller.signal,cache:'no-store'});
  if(!response.ok)throw Error(response.status===410?tText('server:fileDownloadLimitReached'):tText("files:couldNotOpenFileTryDownloadingIt"));
  const blob=await response.blob();if(!el.isConnected)return;
  objectUrl=URL.createObjectURL(new Blob([blob],{type}));
  const media=document.createElement(tag);media.src=objectUrl;
  if(tag==='img')media.alt=file.name;else{media.controls=true;media.preload='metadata';}
  media.onerror=()=>{if(el.isConnected)$('[data-file-preview]',el).innerHTML=`<p>${tHtml("files:browserDoesNotSupportThisFormatDownloadThe")}</p>`;};
  $('[data-file-preview]',el).replaceChildren(media);
 }catch(error){if(el.isConnected)$('[data-file-preview]',el).textContent=error.message;}
}

export async function sendFileCopy(file,ctx){
 const el=ctx.modal(`<h2>${tHtml("files:sendCopy")}</h2><p>${esc(file.name)}</p><p data-send-status role=status>${tHtml("files:preparingFile")}</p><button class=primary data-send-copy disabled>${tHtml("files:sendCopy")}</button>`);
 const controller=new AbortController(),observer=new MutationObserver(()=>{if(!el.isConnected){controller.abort();observer.disconnect();}});observer.observe(document.body,{childList:true});
 if(file.size>BLOB_LIMIT){$('[data-send-status]',el).textContent=tText("files:forLargeFilesUseDownloadOrSharedAccess");return;}
 try{
  const response=await fetch('/api/files/'+encodeURIComponent(file.id)+'/content',{signal:controller.signal,cache:'no-store'});
  if(!response.ok)throw Error(tText("files:failedToRetrieveFile"));
  const copy=new File([await response.blob()],file.name,{type:file.mime||'application/octet-stream'});if(!el.isConnected)return;
  if(!navigator.canShare?.({files:[copy]}))throw Error(tText("files:thisFormatIsNotSupportedInTheBrowser"));
  $('[data-send-status]',el).textContent=tText("files:selectAnApplicationOrRecipientFromTheSystem");
  const button=$('[data-send-copy]',el);button.disabled=false;button.onclick=async()=>{try{await navigator.share({files:[copy]});$('.modal-close',el).click();}catch(e){if(e.name!=='AbortError')ctx.toast(e.message);}};
 }catch(error){if(el.isConnected)$('[data-send-status]',el).textContent=error.message;}
}
