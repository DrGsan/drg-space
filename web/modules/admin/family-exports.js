import {tText,tHtml} from "../../core/i18n.js";
import {$,esc,bytes,date} from '../../core/ui.js';
import {labelTableRows} from '../../core/catalog.js';

import './family-exports.css';

const base='/family-photos/admin/exports';
const labels={queued:tText("admin:inQueue"),running:tText("admin:preparing"),ready:tText("admin:ready"),cleaning:tText("admin:clearing"),cleaned:tText("admin:deleted")};
const errors={media_type_corrected:tText("admin:contentUpdatedGifsExcludedPleasePrepareTheArchive"),cancelled:tText("admin:cancelled"),expired:tText("admin:expired"),interrupted:tText("admin:preparationInterruptedByRestart"),preparation_failed:tText("admin:failedToPrepareArchive"),group_unavailable:tText("admin:groupDisabledOrDeleted")};

export function openExports(ctx,group,{prepareNew=false}={}){
  const dialog=ctx.modal(`<h2>${tHtml("admin:prepareArchive")}</h2><p><strong>${esc(group.title)}</strong></p><p class="hint">${tHtml("admin:weWillSaveThePhotosAlreadyUploadedBy")}</p><button type="button" class="secondary" data-fp-new-export hidden>${tHtml("admin:prepareNewArchive")}</button><form data-fp-export-form><label><input type="checkbox" name="videos"> ${tHtml("admin:enableVideo")}</label><button class="primary" type="submit">${tHtml("admin:prepareArchive")}</button><p role="status" data-fp-export-status></p></form><div data-fp-export-jobs></div>`);
  dialog.classList.add('fp-export-dialog');
  const form=$('form',dialog),notice=$('[data-fp-export-status]',dialog),jobs=$('[data-fp-export-jobs]',dialog);
  let timer,requestId=null,requestBody=null,closed=false;
  const newButton=$('[data-fp-new-export]',dialog);let showForm=prepareNew;
  newButton.onclick=()=>{showForm=true;form.hidden=false;newButton.hidden=true;};
  form.addEventListener('change',()=>{requestId=null;requestBody=null;});
  const refresh=async()=>{
    clearTimeout(timer);
    if(closed||!dialog.isConnected)return;
    try{
      const result=await ctx.api(base);
      if(closed||!dialog.isConnected)return;
      const items=result.items.filter(job=>job.groups?.length===1&&job.groups[0]===group.id);
      const active=items.some(job=>['queued','running'].includes(job.state));
      const ready=items.some(job=>job.state==='ready'&&job.expires>result.server_time);
      form.hidden=!showForm&&(active||ready);newButton.hidden=showForm||active||!ready;
      $('h2',dialog).textContent=active?tText("admin:archiveIsBeingPrepared"):ready&&!showForm?tText("admin:archiveIsReady"):tText("admin:prepareArchive");
      jobs.innerHTML=`<div class="admin-accounts-scroll" role="region" tabindex="0" aria-label="${tHtml("admin:preparedArchives")}"><table class="admin-accounts-table"><thead><tr><th>${tHtml("admin:created")}</th><th>${tHtml("admin:status")}</th><th>${tHtml("admin:filesSize")}</th><th>${tHtml("admin:availableUntil")}</th><th>${tHtml("admin:actions")}</th></tr></thead><tbody>${items.sort((a,b)=>b.created-a.created).map(job=>`<tr><td>${date(job.created)}</td><td>${labels[job.state]||tText("admin:pending")}${job.error?`<small class="admin-table-secondary">${esc(errors[job.error]||tText("admin:cleanupAfterFailure"))}</small>`:''}${job.incomplete?`<small class="admin-table-secondary">${tHtml("admin:incompleteArchivePartOfTheHistoryOrFiles")}</small>`:''}</td><td>${job.done} / ${job.count}<small class="admin-table-secondary">${bytes(job.bytes+(job.current_bytes||0))} / ${bytes(job.total_bytes)}${job.state==='ready'?' · ZIP '+bytes(job.size):''}</small>${job.current_name?`<small class="admin-table-secondary">${esc(job.current_name)}</small>`:''}</td><td>${job.expires?date(job.expires):'—'}</td><td>${job.state==='ready'&&job.expires>result.server_time?`<a class="text-link" href="${esc(job.download_url)}" download>${tHtml("admin:downloadZip")}</a> `:''}${['queued','running','ready'].includes(job.state)?`<button class="text-link" type="button" data-fp-export-cancel="${esc(job.id)}">${job.state==='ready'?tText("admin:deleteArchive"):tText("admin:cancel")}</button>`:job.state==='cleaned'&&job.error!=='expired'?`<span class="hint">${tHtml("admin:canBePreparedAgain")}</span>`:''}</td></tr>`).join('')||`<tr><td colspan="5">${tHtml("admin:noPreparedArchivesYet")}</td></tr>`}</tbody></table></div>`;
      labelTableRows($('table',jobs));
      jobs.querySelectorAll('[data-fp-export-cancel]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{await ctx.api(base+'/'+button.dataset.fpExportCancel+'/cancel','POST',{});await refresh();}catch(error){notice.textContent=error.message;button.disabled=false;}});
    }catch(error){if(dialog.isConnected)notice.textContent=error.message;}
    finally{if(!closed&&dialog.isConnected)timer=setTimeout(refresh,2000);}
  };
  form.onsubmit=async event=>{
    event.preventDefault();
    const button=$('[type=submit]',form);button.disabled=true;
    // Keep the idempotency key across an ambiguous network failure.
    if(!requestId){requestId=crypto.randomUUID().replaceAll('-','');requestBody={groups:[group.id],videos:form.elements.videos.checked,request_id:requestId};}
    try{const job=await ctx.api(base,'POST',requestBody);notice.textContent=tText("admin:requestAcceptedFilesApproximatelyYouCanCloseThe", {value0:(job.count), value1:(bytes(job.total_bytes))});requestId=null;requestBody=null;showForm=false;await refresh();}
    catch(error){notice.textContent=error.message;}
    finally{button.disabled=false;}
  };
  const stop=()=>{closed=true;clearTimeout(timer);};
  dialog.addEventListener('close',stop,{once:true});
  refresh();
  return {dialog,destroy(){stop();$('.modal-close',dialog)?.click();}};
}

export function copyGroup(ctx,group,onCreated){
  const dialog=ctx.modal(`<h2>${tHtml("admin:createACopyOfTheGroup")}</h2><p><strong>${esc(group.title)}</strong></p><p class="hint">${tHtml("admin:weWillSaveTheCurrentHistoryAndAlready")}</p>${!group.history_complete||group.media_pending?`<p class="hint">${tHtml("admin:historyOrFilesAreStillBeingLoadedThe")}</p>`:''}<form data-fp-copy-form><button class="primary" type="submit" data-fp-confirm-copy>${tHtml("admin:createACopy")}</button><p role="status"></p></form>`);
  const requestId=crypto.randomUUID().replaceAll('-','');
  const form=$('[data-fp-copy-form]',dialog);
  form.onsubmit=async event=>{
    event.preventDefault();
    const button=$('[data-fp-confirm-copy]',form);button.disabled=true;
    try{await ctx.api(`/family-photos/admin/groups/${encodeURIComponent(group.id)}/copy`,'POST',{request_id:requestId});$('.modal-close',dialog).click();ctx.toast(tText("admin:copyCreated"));await onCreated();}
    catch(error){$('[role=status]',dialog).textContent=error.message;}
    finally{button.disabled=false;}
  };
  return dialog;
}
