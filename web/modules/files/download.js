import {tText,tHtml} from "../../core/i18n.js";
import {csrfCookie,refreshSession} from '../../core/auth.js';
import {abortable} from '../../core/request.js';

const pending=new Map();
const messages={
  401:tText("files:sessionEndedSignInAndRetryTheDownload"),
  403:tText("files:youDoNotHaveAccessToThisFile"),
  409:tText('server:fileDownloadRetryChanged'),
  410:tText('server:fileDownloadLimitReached'),
  404:tText("files:theFileOrLinkIsNoLongerAvailable"),
  423:tText("files:accessToThePublicationIsBlockedOpenThe"),
  429:tText("files:tooManyRequestsWaitAMomentAndRetry"),
};

export function downloadFile(path,ctx){
  if(ctx.canDownload?.()===false)return Promise.resolve(false);
  if(pending.has(path))return pending.get(path);
  const task=abortable(async signal=>{
    // HEAD checks access and storage without fetching the file contents.
    const check=()=>fetch(path,{method:'HEAD',credentials:'same-origin',cache:'no-store',signal});
    let response=await check();
    if(response.status===401&&csrfCookie()&&await abortable(()=>refreshSession(),{signal}))response=await check();
    if(!response.ok){
      const code=response.headers.get('X-DrG-Error-Code');
      const message=code==='fileStorageTemporarilyUnavailable'?tText('server:fileStorageTemporarilyUnavailable'):
        messages[response.status]||tText("files:failedToDownloadTheFileRetry");
      throw Object.assign(new Error(message),{status:response.status,code});
    }
    if(ctx.canDownload?.()===false)return false;
    const grant=response.headers.get('X-DrG-Download-Grant'),target=new URL(path,location.origin);
    if(grant)target.searchParams.set('download_grant',grant);
    const link=document.createElement('a');
    link.href=target.href;link.download='';link.hidden=true;
    document.body.append(link);link.click();link.remove();
    if(grant)ctx.onDownloadStarted?.(grant);
    return true;
  },{timeoutMs:30000,message:tText("files:failedToVerifyAccessInTimeRetryThe")}).catch(error=>{
    ctx.onDownloadError?.(error);
    if(error.status===401&&ctx.modal){
      const dialog=ctx.modal(`<h2>${tHtml("files:signIn")}</h2><p>${tHtml("files:sessionEndedAfterLoggingInRetryTheDownload")}</p><button class="primary" data-download-login>${tHtml("files:signIn2")}</button>`);
      dialog.querySelector('[data-download-login]').onclick=()=>{
        if(ctx.login){dialog.querySelector('.modal-close').click();ctx.login();}
        else location.reload();
      };
    }else if(error.code==='fileStorageTemporarilyUnavailable'&&ctx.modal){
      const dialog=ctx.modal(`<h2>${tHtml('server:fileStorageTemporarilyUnavailable')}</h2><button class="primary" data-download-retry>${tHtml('app:retry')}</button>`);
      dialog.querySelector('[data-download-retry]').onclick=()=>{
        dialog.querySelector('.modal-close').click();
        downloadFile(path,ctx);
      };
    }else ctx.toast(error.name==='TypeError'?tText("files:failedToConnectToTheServerCheckYour"):error.message);
    return false;
  }).finally(()=>pending.delete(path));
  pending.set(path,task);
  return task;
}

export function bindDownload(link,ctx){
  link.addEventListener('click',event=>{
    event.preventDefault();
    if(link.getAttribute('aria-disabled')==='true')return;
    downloadFile(link.getAttribute('href'),ctx);
  });
}
