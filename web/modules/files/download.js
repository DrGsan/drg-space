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
    // HEAD checks the same file/selection permissions without reading the file
    // or buffering a potentially large ZIP. The final GET remains a native download.
    const check=()=>fetch(path,{method:'HEAD',credentials:'same-origin',cache:'no-store',signal});
    let response=await check();
    if(response.status===401&&csrfCookie()&&await abortable(()=>refreshSession(),{signal}))response=await check();
    if(!response.ok)throw Object.assign(new Error(messages[response.status]||tText("files:failedToDownloadTheFileRetry")),{status:response.status});
    const grant=response.headers.get('X-DrG-Download-Grant'),target=new URL(path,location.origin);
    if(grant)target.searchParams.set('download_grant',grant);
    location.assign(target.href);
    if(grant)ctx.onDownloadStarted?.(grant);
    return true;
  },{timeoutMs:30000,message:tText("files:failedToVerifyAccessInTimeRetryThe")}).catch(error=>{
    ctx.onDownloadError?.(error);
    if(error.status===401){
      const dialog=ctx.modal(`<h2>${tHtml("files:signIn")}</h2><p>${tHtml("files:sessionEndedAfterLoggingInRetryTheDownload")}</p><button class="primary" data-download-login>${tHtml("files:signIn2")}</button>`);
      dialog.querySelector('[data-download-login]').onclick=()=>{
        if(ctx.login){dialog.querySelector('.modal-close').click();ctx.login();}
        else location.reload();
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
