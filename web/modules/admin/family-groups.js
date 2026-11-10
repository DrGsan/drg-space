import {tText,tHtml} from "../../core/i18n.js";
import {connectionLabel,connectionDescription,botLabel,checkedMarkup,targetMarkup} from '../family-photos/connection-status.js';
import {copyGroup,openExports} from './family-exports.js';
import {$,esc,bytes,date} from '../../core/ui.js';
import {catalog,labelTableRows} from '../../core/catalog.js';

const base='/family-photos/admin';
const label=g=>g.phase?tText("admin:deleting"):g.delete_at?tText("admin:waitingToDelete"):g.enabled!==false?tText("admin:enabled"):tText("admin:disabled");
const remaining=(deadline,stamp)=>{const seconds=Math.max(0,deadline-stamp);return seconds?tText("admin:daysHoursMinutes", {value0:(Math.floor(seconds/86400)), value1:(Math.floor(seconds%86400/3600)), value2:(Math.floor(seconds%3600/60))}):tText("admin:waitingForCleanup");};

function exportActions(group,status,stamp){
  if(!group.enabled||!status?.photo_exports)return '';
  const ready=group.export_ready&&group.export_ready.expires>stamp?group.export_ready:null;
  const preparing=group.export_preparing;
  if(ready)return `<a class="text-link" data-fp-download href="${esc(ready.download_url)}" download>${tHtml("admin:downloadArchive")}</a><button type="button" class="text-link" data-fp-action="${preparing?'export':'export-new'}">${preparing?tText("admin:archiveIsBeingPrepared"):tText("admin:prepareNewArchive")}</button>`;
  return `<button type="button" class="text-link" data-fp-action="export">${preparing?tText("admin:archiveIsBeingPrepared"):tText("admin:prepareArchive")}</button>`;
}

export function mountFamilyGroups(host,ctx,data,reload){
  let timer,closed=false,exportDialog;
  const view={},dialogs=new Set();
  const track=dialog=>{dialogs.add(dialog);dialog.addEventListener('close',()=>dialogs.delete(dialog),{once:true});return dialog;};
  const showExport=(group,prepareNew=false)=>{exportDialog?.destroy();exportDialog=openExports(ctx,group,{prepareNew});track(exportDialog.dialog);};
  const hasDialog=()=>{for(const dialog of dialogs)if(!dialog.isConnected)dialogs.delete(dialog);return dialogs.size>0;};
  let offset=(data.server_time||Math.floor(Date.now()/1000))-Math.floor(Date.now()/1000);
  const now=()=>Math.floor(Date.now()/1000)+offset;
  const supported=!!data.status?.group_management;
  host.innerHTML=`<div class="admin-account-heading"><h2>${tHtml("admin:groups")}</h2>${supported?`<button type="button" class="primary compact" data-fp-add>${tHtml("admin:addGroup")}</button>`:''}</div><p class="hint">${tHtml("admin:disablingHidesPhotosAndMessagesAndStopsSynchronization")}</p><div data-fp-group-table></div><p role="status" data-fp-group-status></p>`;
  const status=$('[data-fp-group-status]',host);
  function draw(groups){
    const target=$('[data-fp-group-table]',host);
    target.innerHTML=`<div data-fp-group-catalog><div class="admin-accounts-scroll" role="region" tabindex="0" aria-label="${tHtml("admin:familyPhotoGroups")}"><table class="admin-accounts-table fp-management-table"><thead><tr><th scope="col">${tHtml("admin:group")}</th><th scope="col">${tHtml("admin:status")}</th><th scope="col">${tHtml("admin:photosVideos")}</th><th scope="col">${tHtml("admin:mediaVolume")}</th><th scope="col">${tHtml("admin:delete")}</th><th scope="col">${tHtml("admin:actions")}</th></tr></thead><tbody>${groups.map(g=>`<tr data-fp-managed-group="${esc(g.id)}"><td><strong>${esc(g.title)}</strong><small class="admin-table-secondary">${g.copy_created?tText("admin:copyFrom", {value0:(date(g.copy_created))}):'Telegram ID: '+esc(g.telegram_id||tText("admin:htmlArchive"))}</small>${g.telegram_id&&g.telegram_id!==g.id?`<small class="admin-table-secondary">${tHtml("admin:archive", {value0:(esc(g.id))})}</small>`:''}</td><td><span class="admin-role-badge">${label(g)}</span><small class="admin-table-secondary">${g.connection?esc(connectionLabel(g.connection)):g.enabled!==false?(g.copy_created?(g.copy_complete?tText("admin:savedHistory"):tText("admin:incompleteCopy")):(g.telegram_id?tText("admin:synchronization", {value0:(esc(g.state||tText("admin:waiting")))}):tText("admin:offlineArchive"))):g.copy_created?tText("admin:copyHidden"):tText("admin:synchronizationStopped")}</small>${g.connection?`<small class="admin-table-secondary">${esc(connectionDescription(g.connection))}</small>${checkedMarkup(g.connection)}${targetMarkup(g.connection)}${g.enabled!==false&&!g.delete_at?`<small class="admin-table-secondary">${esc(botLabel(g))}</small>`:''}`:''}</td><td>${g.photos??'—'} / ${g.videos??'—'}</td><td>${typeof g.media_bytes==='number'?bytes(g.media_bytes):'—'}</td><td>${g.delete_at?`${date(g.delete_at)}<small class="admin-table-secondary" data-fp-deadline="${g.delete_at}">${g.phase?tText("admin:fileCleanup"):remaining(g.delete_at,now())}</small>`:'—'}</td><td><div class="fp-group-actions">${!supported?tText("admin:updateFamilyPhoto"):g.phase?tText("admin:cleanupInProgress"):g.delete_at?(g.delete_at>now()?`<button type="button" class="text-link" data-fp-action="cancel">${tHtml("admin:cancelDeletion")}</button>`:tText("admin:waitingForCleanup")):`<button type="button" class="text-link" data-fp-action="${g.enabled?'disable':'enable'}">${g.enabled?tText("admin:disable"):tText("admin:enable")}</button>${exportActions(g,data.status,now())}${data.status?.group_copies&&!g.copy_source&&g.copy_created==null?`<button type="button" class="text-link" data-fp-action="copy">${tHtml("admin:createACopy")}</button>`:''}<button type="button" class="text-link danger" data-fp-action="delete">${tHtml("admin:delete2")}</button>`}</div></td></tr>`).join('')||`<tr><td colspan="6" class="admin-account-empty">${tHtml("admin:noConnectedGroupsYet")}</td></tr>`}</tbody></table></div></div>`;
    target.querySelectorAll('[data-fp-target-group]').forEach(button=>button.onclick=()=>{ctx.state.familyPhotoGroup=button.dataset.fpTargetGroup;ctx.navigate('family_photos');});
    const table=$('table',target);labelTableRows(table);
    if(groups.length)catalog($('[data-fp-group-catalog]',target),{items:[...target.querySelectorAll('[data-fp-managed-group]')],search:row=>row.textContent,label:tText("admin:searchForGroup"),view});
    target.querySelectorAll('[data-fp-action]').forEach(button=>button.onclick=()=>{
      button.focus({preventScroll:true});
      const group=groups.find(g=>g.id===button.closest('[data-fp-managed-group]').dataset.fpManagedGroup),action=button.dataset.fpAction;
      if(action==='copy'){track(copyGroup(ctx,group,reload));}
      else if(action==='export'||action==='export-new'){showExport(group,action==='export-new');}
      else if(action==='delete'){
        const dialog=track(ctx.modal(`<h2>${tHtml("admin:deleteGroupIn7Days")}</h2><p><strong>${esc(group.title)}</strong></p><p class="hint">${tHtml("admin:theGroupWillDisappearFromPhotosAndMessages")}</p><form data-fp-delete-form><label>${tHtml("admin:enterTheArchiveIdToConfirm", {value0:(esc(group.id))})}<input name="confirmation" autocomplete="off" required></label><button type="submit" class="primary danger">${tHtml("admin:scheduleDeletion")}</button><p role="status"></p></form>`));
        const form=$('form',dialog);form.onsubmit=async e=>{e.preventDefault();const notice=$('[role=status]',form);if(form.elements.confirmation.value!==group.id){notice.textContent=tText("admin:idDoesNotMatch");return;}const submit=$('button',form);submit.disabled=true;try{await act(group,action,{confirmation:group.id});$('.modal-close',dialog).click();}catch(error){notice.textContent=error.message;}finally{submit.disabled=false;}};
      }else{button.disabled=true;act(group,action,{}).catch(error=>{status.textContent=error.message;button.disabled=false;});}
    });
  }
  async function act(group,action,body){
    status.textContent=tText("admin:saving");
    try{await ctx.api(`${base}/groups/${encodeURIComponent(group.id)}/${action}`,'POST',body);await reload();}
    catch(error){status.textContent=error.message;throw error;}
  }
  function add(){
    const dialog=track(ctx.modal(`<h2>${tHtml("admin:addFamilyGroup")}</h2><p class="hint">${tHtml("admin:selectAGroupAvailableToTheConnectedTelegram")}</p><form data-fp-source-form><label>${tHtml("admin:telegramGroup")}<select name="telegram_id" required disabled><option value="">${tHtml("admin:loadingList")}</option></select></label><button type="button" class="secondary" data-fp-discover>${tHtml("admin:refreshList")}</button><button type="submit" class="primary" disabled>${tHtml("admin:addGroup")}</button><p role="status"></p></form>`));
    const form=$('form',dialog),select=$('select',form),notice=$('[role=status]',form),submit=$('[type=submit]',form);let poll;
    const known=new Set(data.groups.map(g=>g.telegram_id).filter(Boolean));
    const refreshButton=$('[data-fp-discover]',form);
    let loading=false,waiting=false,optionsKey='',deferred=null,adding=false;
    function options(items){
      const signature=JSON.stringify(items.map(g=>[g.id,g.title]));
      if(signature===optionsKey){deferred=null;return;}
      if(document.activeElement===select){deferred=items;return;}
      const selected=select.value;
      select.innerHTML=`<option value="">${tHtml("admin:selectGroup")}</option>${items.map(g=>`<option value="${esc(g.id)}">${esc(g.title)} · ${esc(g.id)}</option>`).join('')}`;
      if(items.some(g=>g.id===selected))select.value=selected;
      select.disabled=!items.length;submit.disabled=!items.length||adding;
      optionsKey=signature;deferred=null;
    }
    select.onblur=()=>{if(deferred)options(deferred);};
    async function discover(refresh=false){
      if(loading||refresh&&waiting||!dialog.isConnected)return;
      clearTimeout(poll);loading=true;refreshButton.disabled=true;
      try{
        const result=await ctx.api(base+'/discovery',refresh?'POST':'GET',refresh?{}:undefined);
        if(!dialog.isConnected)return;
        const items=result.items.filter(g=>!known.has(g.id));options(items);
        waiting=result.requested>result.checked&&!result.error;
        notice.textContent=result.error?tText("admin:failedToRetrieveListFromTelegramPleaseRetry"):waiting?tText("admin:retrievingListFromTelegramTheWorkerMustBe"):items.length?tText("admin:verifyTheNameAndIdOfTheSelected"):tText("admin:noNewAvailableGroupsRefreshTheListOr");
        if(waiting)poll=setTimeout(()=>discover(),2000);
      }catch(error){waiting=false;if(dialog.isConnected)notice.textContent=error.message;}
      finally{loading=false;if(dialog.isConnected){refreshButton.disabled=waiting;refreshButton.textContent=waiting?tText("admin:refreshing"):tText("admin:refreshList");}}
    }
    refreshButton.onclick=()=>discover(true);
    form.onsubmit=async e=>{e.preventDefault();if(adding||!select.value)return;adding=true;submit.disabled=true;try{await ctx.api(base+'/groups','POST',{telegram_id:select.value});clearTimeout(poll);$('.modal-close',dialog).click();await reload();}catch(error){notice.textContent=error.message;adding=false;submit.disabled=false;}};
    discover(true);
  }
  if(supported)$('[data-fp-add]',host).onclick=add;
  draw(data.groups);
  const tick=async()=>{
    if(!host.isConnected){closed=true;return;}
    host.querySelectorAll('[data-fp-deadline]').forEach(el=>el.textContent=remaining(Number(el.dataset.fpDeadline),now()));
    try{
      if(!hasDialog()&&host.closest('.preference-row')?.open){const fresh=await ctx.api(base);if(!closed&&host.isConnected&&!hasDialog()){offset=(fresh.server_time||now())-Math.floor(Date.now()/1000);draw(fresh.groups);}}
    }catch(error){if(host.isConnected)status.textContent=error.message;}
    if(!closed)timer=setTimeout(tick,10000);
  };
  timer=setTimeout(tick,10000);
  const stop=()=>{closed=true;clearTimeout(timer);exportDialog?.destroy();for(const dialog of dialogs)$('.modal-close',dialog)?.click();dialogs.clear();};
  stop.openExport=gid=>{const group=data.groups.find(g=>g.id===gid);if(group)showExport(group);};
  return stop;
}
