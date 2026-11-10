import {locale,tText,tHtml} from "../../core/i18n.js";
import {$,esc,bytes} from '../../core/ui.js';
import {csrfCookie,refreshSession} from '../../core/auth.js';
import {icon,fileCategory} from './drive-ui.js';
import {fileArtwork} from './file-art.js';

// Authentication refresh is separate from the queue's explicit 429 backoff.
export async function uploadFile(path,file,headers={},progress=()=>{},retry=true,signal){
 try{return await new Promise((resolve,reject)=>{
  if(signal?.aborted){reject(Error(tText("files:fileTransferAborted")));return;}
  const request=new XMLHttpRequest();request.open('POST','/api'+path);
  const abort=()=>request.abort();signal?.addEventListener('abort',abort,{once:true});request.onloadend=()=>signal?.removeEventListener('abort',abort);
  for(const [key,value] of Object.entries({'Accept-Language':locale(),'Content-Type':'application/octet-stream','X-CSRF-Token':csrfCookie(),...headers}))request.setRequestHeader(key,value);
  request.upload.onprogress=e=>{if(e.lengthComputable)progress(e.loaded,e.total);};
  request.upload.onload=()=>progress(file.size,file.size,true);
  request.onload=()=>{let data;try{data=JSON.parse(request.responseText);}catch{}
   if(request.status>=200&&request.status<300&&data)resolve(data);
   else reject(Object.assign(Error(typeof data?.detail==='string'?data.detail:tText("files:failedToUploadFile", {value0:(request.status)})),{status:request.status,retryAfter:request.getResponseHeader('Retry-After')}));
  };
  request.onerror=()=>reject(Error(tText("files:connectionLostRefreshTheFolderBeforeReUploading")));
  request.onabort=()=>reject(Error(tText("files:fileTransferAborted")));
  request.send(file);
 });}catch(error){if(error.status===401&&retry&&await refreshSession())return uploadFile(path,file,headers,progress,false,signal);throw error;}
}

export function uploadPanel(entries,alive,close){
 const el=document.createElement('aside');el.className='drive-transfer';el.setAttribute('aria-label',tText("files:uploadingFiles2"));
 el.innerHTML=`<div class=drive-transfer-top><button class=text-link data-upload-stop>${tHtml("files:stopUpload")}</button><button class=text-link data-upload-close hidden>${tHtml("files:done")}</button></div><div class=drive-transfer-heading>${icon('upload')}<h3>${tHtml("files:uploadingToDrgDrive")}</h3><progress data-upload-progress max=100 value=0 aria-label="${tHtml("files:overallUploadProgress")}"></progress><p data-folder-upload-status role=status>${tHtml("files:preparing")}</p><p data-upload-current></p><p class=hint data-upload-hint>${tHtml("files:doNotCloseThisPageWhileFilesAre")}</p></div><div class=drive-transfer-list>${entries.map((f,i)=>`<div class=drive-transfer-item data-transfer-item="${i}"><span class="drive-file-icon is-${fileCategory(f)}">${fileArtwork(f,fileCategory(f))}</span><div><strong title="${esc(f.name)}">${esc(f.name.split('/').at(-1))}</strong><small>${bytes(f.size)}</small></div><span data-transfer-state>${tHtml("files:inQueue")}</span></div>`).join('')}</div>`;
 let stopped=false,index=-1,completed=0,activeBytes=0,done=0,frame=0,ring;
 const total=entries.reduce((n,f)=>n+f.size,0),status=$('[data-folder-upload-status]',el),stop=$('[data-upload-stop]',el),closing=$('[data-upload-close]',el),current=$('[data-upload-current]',el),bar=$('[data-upload-progress]',el),hint=$('[data-upload-hint]',el);
 const rows=[...el.querySelectorAll('.drive-transfer-item')],states=rows.map(row=>$('[data-transfer-state]',row));
 const setStatus=text=>{if(status.textContent!==text)status.textContent=text;};
 const paint=()=>{
  frame=0;bar.value=total?Math.min(100,100*(completed+activeBytes)/total):100*done/Math.max(1,entries.length);
  if(ring){const size=entries[index].size,percent=size?Math.min(100,Math.round(100*activeBytes/size)):0;ring.setAttribute('aria-valuenow',percent);$('.drive-ring-progress',ring).setAttribute('stroke-dasharray',percent+' 100');}
 };
 const flush=()=>{cancelAnimationFrame(frame);paint();};
 stop.onclick=()=>{stopped=true;stop.disabled=true;setStatus(tText("files:stopAfterTheCurrentFile"));};
 closing.onclick=close;
 return {el,isStopped:()=>stopped||!alive(),
  start(){
   index++;activeBytes=0;rows[index]?.classList.add('is-uploading');
   states[index].innerHTML=`<svg class=drive-upload-ring viewBox="0 0 24 24" role=progressbar aria-label="${tHtml("files:uploadingFile")}" aria-valuemin=0 aria-valuemax=100 aria-valuenow=0><circle class=drive-ring-track cx=12 cy=12 r="9"/><circle class=drive-ring-progress cx=12 cy=12 r=9 pathLength=100 stroke-dasharray="0 100"/></svg>`;
   ring=states[index].firstElementChild;current.textContent=entries[index].name.split('/').at(-1);current.title=entries[index].name;
   if(!stopped)setStatus(tText("files:objectOf", {value0:(index+1), value1:(entries.length)}));flush();
  },
  progress(loaded,size,finishing){activeBytes=Math.max(activeBytes,Math.min(loaded,entries[index].size));if(!frame)frame=requestAnimationFrame(paint);if(finishing&&!stopped)setStatus(tText("files:finishingUpload"));},
  wait(seconds){activeBytes=0;setStatus(tText("files:serverPausedContinueWithSeconds", {value0:(seconds)}));flush();},
  resume(){if(!stopped)setStatus(index<0?tText("files:preparing"):tText("files:objectOf", {value0:(index+1), value1:(entries.length)}));},
  complete(){completed+=entries[index].size;activeBytes=0;done++;rows[index].classList.remove('is-uploading');states[index].innerHTML=icon('check');ring=null;flush();},
  finish(error){
   const interrupted=stopped||!alive()||error;stop.hidden=true;closing.hidden=false;
   const message=error?error.message:interrupted?tText("files:uploadStoppedUploadedFilesSaved"):tText("files:completeObjectsUploadedOutOf", {value0:(done), value1:(entries.length)});
   setStatus(message);status.title=message;hint.textContent=interrupted?tText("files:uploadedFilesSaved"):tText("files:allFilesSavedToDrgDrive");
   states.forEach((node,i)=>{if(i===index&&ring){node.textContent=error?tText("files:error"):tText("files:notUploaded");rows[i].classList.remove('is-uploading');}else if(i>index)node.textContent=tText("files:notUploaded");});
   ring=null;flush();if(!interrupted)bar.value=100;
  }
 };
}
