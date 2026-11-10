import {tText,tHtml,locale} from "../../core/i18n.js";
import {$,esc,date} from '../../core/ui.js';
import {preferenceStatus} from '../../core/preferences.js';
import './security.css';

const BASE='/admin/security';
const risks={info:tText("admin:information"),warning:tText("admin:attention"),high:tText("admin:highRisk"),critical:tText("admin:criticalRisk")};
const modes={normal:tText("admin:normalAccess"),logins_closed:tText("admin:newLoginsAreClosed"),lockdown:tText("admin:emergencyClosure")};
const modeHelp={
  normal:tText("admin:theServiceIsWorkingInNormalModeAutomatic"),
  logins_closed:tText("admin:newNormalLoginsRegistrationAndRestorationAreClosed"),
  lockdown:tText("admin:theServiceIsClosedForUsersNormalApis"),
};
const outcomes={failure:tText("admin:decline"),success:tText("admin:success"),blocked:tText("admin:blocked"),info:tText("admin:details")};
const actions={login:tText("admin:loginWithPassword"),'2fa':tText("admin:label2faLoginConfirmation"),enroll:tText("admin:label2faSetup"),confirm:tText("admin:label2faVerification"),activate:tText("admin:enable2Fa"),'disable-2fa':tText("admin:disable2Fa"),'change-password':tText("admin:passwordChange"),'share-password':tText("admin:publicationPassword"),'admin-password':tText("admin:administratorConfirmation"),'passkey-login':tText("admin:loginWithPasskey"),'passkey-register':tText("admin:createPasskey"),'passkey-manage':tText("admin:managePasskey"),register:tText("admin:registrationOrInvitation"),setup:tText("admin:initialSetup"),'personal-data-password':tText("admin:personalArchivePassword")};
Object.assign(actions,{'admin-confirm':tText("admin:administratorConfirmation"),'security-control':tText("admin:confirmationOfProtectionManagement"),'security-export':tText("admin:exportLog"),'incident-review':tText("admin:incidentVerification"),'ip-ban':tText("admin:ipBlocking"),'ip-unban':tText("admin:removeIpBlocking"),'session-revoke':tText("admin:sessionEnd"),'service-mode':tText("admin:changeServiceMode"),manual:tText("admin:manualBlocking")});
Object.assign(actions,{'vault-enabled':tText("admin:enableSecretStorage"),'vault-unlocked':tText("admin:openSecretSection"),'vault-locked':tText("admin:blockSecretSection"),'vault-auto-locked':tText("admin:automaticBlockingOfSecretSection"),'vault-pin-failed':tText("admin:incorrectPin"),'vault-pin-blocked':tText("admin:blockingAfterIncorrectPins"),'vault-pin-changed':tText("admin:changePin"),'vault-unblocked':tText("admin:removePinAttempts"),'vault-deleted':tText("admin:deleteSecretStorage"),'vault-item-deleted':tText("admin:deleteSecretElement"),'vault-moved-in':tText("admin:moveToSecretStorage"),'vault-moved-out':tText("admin:moveFromSecretStorage"),'vault-unlock':tText("admin:pinVerification"),'vault-security':tText("admin:confirmActionWithStorage")});
const eventNames={site_role_changed:tText("admin:websiteRoleChanged"),auth_failure:tText("admin:accessVerificationError"),auth_success:tText("admin:accessVerificationComplete"),login_success:tText("admin:loginSuccessful"),ban_created:tText("admin:automaticIpBlocking"),request_blocked:tText("admin:requestBlocked"),rate_limited:tText("admin:requestFrequencyExceeded"),csrf_rejected:tText("admin:csrfRequestFailed"),origin_rejected:tText("admin:requestSourceRejected"),access_denied:tText("admin:accessDenied"),scan_detected:tText("admin:suspiciousScanning"),refresh_replay:tText("admin:reusingSessionToken"),admin_action:tText("admin:administratorAction"),account_security_changed:tText("admin:accountProtectionSettingsChanged"),session_rejected:tText("admin:sessionInvalid")};
const detailNames={role:tText("admin:newSiteRole"),previous_role:tText("admin:previousSiteRole"),reason:tText("admin:reason"),status:tText("admin:httpStatus"),method:tText("admin:requestMethod"),mode:tText("admin:mode"),previous_mode:tText("admin:previousMode"),duration:tText("admin:durationSec"),until:tText("admin:to"),actor:tText("admin:initiator"),stage:tText("admin:loginStage"),target:tText("admin:goal"),target_type:tText("admin:goalType"),target_ip:tText("admin:ipForAction"),target_user_id:tText("admin:targetAccountId"),target_session:tText("admin:targetSessionId"),action:tText("admin:action"),count:tText("admin:count"),strikes:tText("admin:retries"),revoked:tText("admin:sessionsCompleted"),retry_after:tText("admin:retryAfterSec")};
const stages={full:tText("admin:loginSuccessful"),pending:tText("admin:awaiting2Fa"),enrollment:tText("admin:enableProtection")};
const stamp=value=>value?date(value):'—';
const number=value=>Number(value||0).toLocaleString(locale());
const risk=value=>`<span class="as-risk as-risk-${Object.hasOwn(risks,value)?value:'info'}">${esc(risks[value]||value||risks.info)}</span>`;
const pair=(label,value)=>`<div><dt>${esc(label)}</dt><dd>${esc(value??'—')}</dd></div>`;
const fieldValue=value=>typeof value==='object'?JSON.stringify(value):String(value??'—');
const durationOptions=(manual=false)=>`<option value="900">${tHtml("admin:label15Minutes")}</option><option value="3600" selected>${tHtml("admin:label1Hour")}</option><option value="14400">${tHtml("admin:label4Hours")}</option><option value="86400">${tHtml("admin:label24Hours")}</option>${manual?`<option value="0">${tHtml("admin:untilManuallyDisabled")}</option>`:''}`;
const empty=message=>`<p class="as-empty">${esc(message)}</p>`;

function eventRow(event){
  const action=actions[event.action]||event.action;
  const title=event.event==='secret_vault'?tText("admin:secureStorage"):event.event==='auth_success'&&event.action==='login'?tText("admin:passwordAcceptedLoginVerificationInProgress"):event.event==='auth_failure'&&event.action==='2fa'?tText("admin:secondFactorLoginError"):eventNames[event.event]||event.event;
  const details=Object.entries(event.details||{}).map(([key,value])=>pair(detailNames[key]||key,key==='until'?stamp(value):key==='mode'||key==='previous_mode'?(modes[value]||value):key==='stage'?(stages[value]||value):fieldValue(value))).join('');
  return `<details class="as-event"><summary><span class="as-event-copy"><strong>${esc(title)}</strong><span>${esc(stamp(event.last_seen))} · ${esc(event.ip||tText("admin:ipAddressUnknown"))}${event.subject?' · '+esc(event.subject):''}</span></span><span class="as-event-tags">${risk(event.severity)}${event.count>1?`<span class="as-count">×${number(event.count)}</span>`:''}</span></summary><div class="as-event-body">${event.event==='auth_success'?`<p class="hint">${tHtml("admin:separateVerificationCompletedASeparateEventLoginSuccessful")}</p>`:''}<dl class="as-facts">${pair(tText("admin:result"),outcomes[event.outcome]||event.outcome)}${action?pair(tText("admin:operation"),action):''}${pair(tText("admin:firstEvent"),stamp(event.first_seen))}${pair(tText("admin:lastEvent"),stamp(event.last_seen))}${event.route?pair(tText("admin:requestAddress"),event.route):''}${event.agent?pair(tText("admin:browserClient"),event.agent):''}${details}</dl>${event.ip&&event.ip!=='unknown'?`<button type="button" class="text-link" data-as-ban-ip="${esc(event.ip)}">${tHtml("admin:blockIp")}</button>`:''}</div></details>`;
}

export function mountAdminSecurity(host,ctx){
  if(ctx.state.me.role!=='owner'){host.textContent=tText("admin:availableOnlyToPortalAdministrator");return;}
  const disclosure=host.closest('.preference-row');
  let started=false,overview=null,days=1,tab='events',offset=0,requestId=0,overviewRequest=0;
  let filters={event:'',outcome:'',severity:'',ip:'',subject:''},incidentStatus='open';
  const live=()=>host.isConnected;

  function confirmation({title,description,fields='',submit,run,onReady}){
    if(!ctx.state.me.two_factor){
      const dialog=ctx.modal(`<h2>${tHtml("admin:twoFactorAuthenticationIsRequired")}</h2><p class="muted">${tHtml("admin:changingServiceModeBlockingAndSessionsRequiresThe")}</p><button type="button" class="primary" data-as-enable-factor>${tHtml("admin:setUp2Fa")}</button>`);
      $('[data-as-enable-factor]',dialog).onclick=()=>{$('.modal-close',dialog).click();ctx.navigate('security');};return dialog;
    }
    const dialog=ctx.modal(`<h2>${esc(title)}</h2><p class="muted">${esc(description)}</p><form data-as-confirm>${fields}<label>${tHtml("admin:reason")}<input name="reason" maxlength="300" required autocomplete="off" placeholder="${tHtml("admin:brieflyDescribeTheReason")}"></label><div class="as-proof"><p class="hint">${tHtml("admin:confirmActionWithOwnerSDataIfThe")}</p><label>${tHtml("admin:administratorPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label><label>${tHtml("admin:new2FaCode")}<input name="code" inputmode="numeric" pattern="[0-9]{6}" autocomplete="one-time-code" minlength="6" maxlength="6" required></label></div><p class="as-form-status" role="alert" data-as-form-status></p><button class="primary" type="submit">${esc(submit)}</button></form>`);
    dialog.classList.add('admin-security-dialog');
    const form=$('[data-as-confirm]',dialog),button=$('[type=submit]',form),notice=$('[data-as-form-status]',form);
    form.onsubmit=async event=>{
      event.preventDefault();if(button.disabled)return;
      const data=Object.fromEntries(new FormData(form));
      form.elements.password.value='';if(form.elements.code)form.elements.code.value='';
      button.disabled=true;notice.textContent=tText("admin:applying");
      try{await run(data);if(dialog.isConnected)$('.modal-close',dialog).click();await refresh();}
      catch(error){if(dialog.isConnected){notice.textContent=error.message;form.elements.password.focus();}}
      finally{delete data.password;delete data.code;if(dialog.isConnected)button.disabled=false;}
    };
    onReady?.(dialog,form);return dialog;
  }

  function ban(ip=''){
    confirmation({title:tText("admin:temporarilyBlockIp"),description:tText("admin:accessToThePortalFromThisIpWill"),fields:`<label>${tHtml("admin:ipAddress")}<input name="ip" value="${esc(ip)}" maxlength="64" required autocomplete="off" placeholder="${tHtml("admin:forExample203011310")}"></label><label>${tHtml("admin:blockingPeriod")}<select name="duration">${durationOptions()}</select></label>`,submit:tText("admin:blockIp"),run:async data=>{await ctx.api(BASE+'/bans','POST',{...data,duration:Number(data.duration)});ctx.toast(tText("admin:ipAddressTemporarilyBlocked"));}});
  }

  function mode(){
    const current=overview?.mode?.mode||'normal';
    confirmation({title:tText("admin:accessToTheService"),description:tText("admin:theModeAppliesToTheEntirePortalThe"),fields:`<label>${tHtml("admin:mode")}<select name="mode">${Object.entries(modes).map(([value,label])=>`<option value="${value}" ${value===current?'selected':''}>${label}</option>`).join('')}</select></label><p class="as-mode-explanation" data-as-mode-explanation></p><label data-as-duration>${tHtml("admin:restrictionPeriod")}<select name="duration">${durationOptions(true)}</select></label><p class="hint" data-as-duration-help>${tHtml("admin:afterTheSelectedPeriodTheServiceWillAutomatically")}</p>`,submit:tText("admin:applyMode"),run:async data=>{await ctx.api(BASE+'/mode','POST',{...data,duration:data.mode==='normal'?0:Number(data.duration)});ctx.toast(tText("admin:accessModeHasBeenChanged"));},onReady(dialog,form){
      const explainDuration=()=>{$('[data-as-duration-help]',dialog).textContent=form.elements.duration.value==='0'?tText("admin:theServiceWillRemainRestrictedUntilThisMode"):tText("admin:afterTheSelectedPeriodTheServiceWillAutomatically");};
      const update=()=>{const value=form.elements.mode.value;form.elements.duration.value=value==='lockdown'?'0':'3600';$('[data-as-mode-explanation]',dialog).textContent=modeHelp[value];$('[data-as-duration]',dialog).hidden=value==='normal';$('[data-as-duration-help]',dialog).hidden=value==='normal';explainDuration();};
      form.elements.mode.onchange=update;form.elements.duration.onchange=explainDuration;update();
    }});
  }

  function bindEventActions(container){container.querySelectorAll('[data-as-ban-ip]').forEach(button=>button.onclick=()=>ban(button.dataset.asBanIp));}

  function renderOverview(){
    const data=overview,c=data.counts||{},state=data.mode||{mode:'normal'},modeName=modes[state.mode]||state.mode;
    preferenceStatus(host,state.mode==='normal'?(c.open_incidents?tText("admin:incidents", {value0:(number(c.open_incidents))}):tText("admin:normalAccess")):modeName);
    $('[data-as-overview]',host).innerHTML=`<div class="as-mode as-mode-${state.mode==='normal'?'normal':'restricted'}"><div><span class="as-kicker">${tHtml("admin:accessToService")}</span><strong>${esc(modeName)}</strong><p>${esc(modeHelp[state.mode]||'')}${state.mode!=='normal'?' '+(state.until?tText("admin:upTo", {value0:(esc(stamp(state.until)))}):tText("admin:untilManualDisable")):''}</p>${state.reason?`<p>${tHtml("admin:reason2", {value0:(esc(state.reason))})}</p>`:''}</div><button type="button" class="${state.mode==='normal'?'secondary':'primary'}" data-as-mode>${tHtml("admin:changeMode")}</button></div><div class="as-metrics" aria-label="${tHtml("admin:securitySummary")}"><div><strong>${number(c.failed)}</strong><span>${tHtml("admin:failedAttempts")}</span></div><div><strong>${number(c.successful)}</strong><span>${tHtml("admin:successfulLogins")}</span></div><div><strong>${number(c.open_incidents)}</strong><span>${tHtml("admin:openIncidents")}</span></div><div><strong>${number(c.active_bans)}</strong><span>${tHtml("admin:activeBlocks")}</span></div></div><p class="hint as-summary-note">${tHtml("admin:eventsInThePeriodBlockedRequestsBlocksAnd", {value0:(number(c.events)), value1:(number(c.blocked))})}</p>${data.health?.ok===false?`<div class="as-health" role="alert"><strong>${tHtml("admin:logContainsErrors")}</strong><p>${tHtml("admin:failedEntriesLastErrorTheLogMayBe", {value0:(number(data.health.failed_writes)), value1:(esc(stamp(data.health.last_failure)))})}</p></div>`:''}`;
    $('[data-as-mode]',host).onclick=mode;
    $('[data-as-retention]',host).textContent=tText("admin:eventsAreLoggedAfterUpdatingDetailsAreStored", {value0:(data.policy?.event_days||365), value1:(data.policy?.incident_days||365), value2:(number(data.policy?.max_events||50000)), value3:(number(data.policy?.max_incidents||5000))});
  }

  async function loadOverview(){
    const id=++overviewRequest;
    try{const data=await ctx.api(BASE+'?days='+days);if(!live()||id!==overviewRequest)return;overview=data;renderOverview();$('[data-as-error]',host).textContent='';}
    catch(error){if(live()&&id===overviewRequest)$('[data-as-error]',host).textContent=tText("admin:failedToUpdateSummary", {value0:(error.message)});}
  }

  function query(extra={}){
    const values={days,...filters,...extra};return new URLSearchParams(Object.entries(values).filter(([,value])=>value!==''&&value!==undefined)).toString();
  }
  function pagination(total,limit){
    return `<div class="as-pagination"><span>${total?tText("admin:of", {value0:(number(offset+1)), value1:(number(Math.min(offset+limit,total))), value2:(number(total))}):tText("admin:noEntries")}</span><div><button type="button" class="secondary" data-as-prev ${offset?'':'disabled'} aria-label="${tHtml("admin:previousPage")}">${tHtml("admin:back")}</button><button type="button" class="secondary" data-as-next ${offset+limit<total?'':'disabled'} aria-label="${tHtml("admin:nextPage")}">${tHtml("admin:next")}</button></div></div>`;
  }
  function bindPagination(container,total,limit){
    $('[data-as-prev]',container)?.addEventListener('click',()=>{offset=Math.max(0,offset-limit);loadTab();});
    $('[data-as-next]',container)?.addEventListener('click',()=>{if(offset+limit<total){offset+=limit;loadTab();}});
  }

  function renderTabShell(){
    const area=$('[data-as-view]',host);
    if(tab==='events'){
      area.innerHTML=`<form class="as-filters" data-as-filters><label>${tHtml("admin:result")}<select name="outcome"><option value="">${tHtml("admin:allResults")}</option>${Object.entries(outcomes).map(([value,label])=>`<option value="${value}" ${filters.outcome===value?'selected':''}>${label}</option>`).join('')}</select></label><label>${tHtml("admin:level")}<select name="severity"><option value="">${tHtml("admin:allLevels")}</option>${Object.entries(risks).map(([value,label])=>`<option value="${value}" ${filters.severity===value?'selected':''}>${label}</option>`).join('')}</select></label><label>${tHtml("admin:event")}<select name="event"><option value="">${tHtml("admin:allEvents")}</option>${Object.entries(eventNames).map(([value,label])=>`<option value="${value}" ${filters.event===value?'selected':''}>${label}</option>`).join('')}</select></label><label>${tHtml("admin:ipAddress")}<input name="ip" value="${esc(filters.ip)}" maxlength="64" placeholder="${tHtml("admin:anyAddress")}"></label><label>${tHtml("admin:accountTarget")}<input name="subject" value="${esc(filters.subject)}" maxlength="100" placeholder="${tHtml("admin:anyAccount")}"></label><div class="as-filter-actions"><button type="submit" class="secondary">${tHtml("admin:apply")}</button><button type="button" class="text-link" data-as-reset>${tHtml("admin:reset")}</button></div></form><div class="as-list-heading"><p class="hint">${tHtml("admin:expandTheEventToSeeTheStageReason")}</p><button type="button" class="text-link" data-as-export>${tHtml("admin:downloadJson")}</button></div><div data-as-list aria-live="polite"></div>`;
      $('[data-as-filters]',area).onsubmit=event=>{event.preventDefault();filters=Object.fromEntries(new FormData(event.currentTarget));offset=0;loadTab();};
      $('[data-as-reset]',area).onclick=()=>{filters={event:'',outcome:'',severity:'',ip:'',subject:''};offset=0;renderTabShell();loadTab();};
      $('[data-as-export]',area).onclick=async event=>{
        const button=event.currentTarget;button.disabled=true;
        try{
          const data=await ctx.api(BASE+'/events/export?'+query());
          const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),link=document.createElement('a');
          link.href=url;link.download='drg-security-'+new Date().toISOString().slice(0,10)+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
          ctx.toast(data.truncated?tText("admin:first5000RecordsDownloadedRefineFiltersForThe"):tText("admin:logDownloaded"));
        }catch(error){ctx.toast(error.message);}finally{button.disabled=false;}
      };
    }else if(tab==='incidents'){
      area.innerHTML=`<div class="as-list-heading"><label>${tHtml("admin:status2")}<select data-as-incident-status><option value="open" ${incidentStatus==='open'?'selected':''}>${tHtml("admin:open3")}</option><option value="reviewed" ${incidentStatus==='reviewed'?'selected':''}>${tHtml("admin:viewed")}</option><option value="" ${incidentStatus===''?'selected':''}>${tHtml("admin:all")}</option></select></label><p class="hint">${tHtml("admin:relatedEventsForTheEntireRetentionPeriodMarking")}</p></div><div data-as-list aria-live="polite"></div>`;
      $('[data-as-incident-status]',area).onchange=event=>{incidentStatus=event.target.value;offset=0;loadTab();};
    }else{
      area.innerHTML=`<div class="as-list-heading"><p class="hint">${tab==='bans'?tText("admin:activeAutomaticAndManualLocksARestrictionOn"):tText("admin:allAccountSessionsIncludingIncompleteLoginsTheCurrent")}</p>${tab==='bans'?`<button type="button" class="secondary" data-as-add-ban>${tHtml("admin:blockIp")}</button>`:''}</div><div data-as-list aria-live="polite"></div>`;
      $('[data-as-add-ban]',area)?.addEventListener('click',()=>ban());
    }
  }

  async function incident(id){
    const dialog=ctx.modal(`<h2>${tHtml("admin:securityIncident")}</h2><div data-as-incident-content role="status">${tHtml("admin:loadingDetails")}</div>`);dialog.classList.add('admin-security-dialog');
    const body=$('[data-as-incident-content]',dialog);
    try{
      const data=await ctx.api(BASE+'/incidents/'+encodeURIComponent(id));if(!dialog.isConnected)return;
      const item=data.incident;
      body.innerHTML=`<div class="as-incident-title">${risk(item.severity)}<strong>${esc(item.title)}</strong></div><dl class="as-facts">${pair(tText("admin:status2"),item.status==='reviewed'?tText("admin:viewed2"):tText("admin:open4"))}${pair(tText("admin:start"),stamp(item.first_seen))}${pair(tText("admin:lastEvent"),stamp(item.last_seen))}${pair(tText("admin:events"),number(item.event_count))}${pair(tText("admin:ipAddresses"),number(item.ip_count))}${item.ip?pair('IP',item.ip):''}${item.subject?pair(tText("admin:accountTarget"),item.subject):''}</dl><div class="as-actions"><button type="button" class="secondary" data-as-review>${item.status==='reviewed'?tText("admin:openAgain"):tText("admin:markAsViewed")}</button>${item.ip?`<button type="button" class="text-link" data-as-incident-ban>${tHtml("admin:blockIp")}</button>`:''}</div><p class="as-form-status" role="alert" data-as-review-status></p><h3>${tHtml("admin:relatedEvents")}</h3><div class="as-incident-events">${(data.events||[]).map(eventRow).join('')||empty(tText("admin:detailedEventsHaveBeenDeletedDueToRetention"))}</div>`;
      bindEventActions(body);$('[data-as-incident-ban]',body)?.addEventListener('click',()=>ban(item.ip));
      $('[data-as-review]',body).onclick=async event=>{
        const button=event.currentTarget;button.disabled=true;
        try{await ctx.api(BASE+'/incidents/'+encodeURIComponent(id)+'/review','POST',{status:item.status==='reviewed'?'open':'reviewed'});if(dialog.isConnected)$('.modal-close',dialog).click();await refresh();}
        catch(error){if(dialog.isConnected){$('[data-as-review-status]',body).textContent=error.message;button.disabled=false;}}
      };
    }catch(error){if(dialog.isConnected)body.textContent=error.message;}
  }

  async function loadTab(){
    const id=++requestId,list=$('[data-as-list]',host),kind=tab;
    if(!list)return;list.setAttribute('aria-busy','true');list.innerHTML=`<p class="as-empty" role="status">${tHtml("admin:loading")}</p>`;
    try{
      const path=kind==='events'?'/events?'+query({limit:50,offset}):kind==='incidents'?'/incidents?'+new URLSearchParams({status:incidentStatus,limit:50,offset}):'/'+kind;
      const data=await ctx.api(BASE+path);if(!live()||id!==requestId)return;
      if(kind==='events'){
        list.innerHTML=(data.events.map(eventRow).join('')||empty(tText("admin:noEventsFoundBasedOnTheSelectedCriteria")))+pagination(data.total,data.limit||50);
        bindEventActions(list);bindPagination(list,data.total,data.limit||50);
      }else if(kind==='incidents'){
        list.innerHTML=(data.incidents.map(item=>`<button type="button" class="as-incident" data-as-incident="${esc(item.id)}"><span class="as-event-copy"><strong>${esc(item.title)}</strong><span>${esc(stamp(item.last_seen))}${item.subject?' · '+esc(item.subject):''}</span><span>${tHtml("admin:eventsIp", {value0:(number(item.event_count)), value1:(number(item.ip_count))})}</span></span>${risk(item.severity)}<span class="as-chevron" aria-hidden="true">›</span></button>`).join('')||empty(incidentStatus==='open'?tText("admin:noOpenIncidents"):tText("admin:noIncidentsWithThisStatus")))+pagination(data.total,data.limit||50);
        list.querySelectorAll('[data-as-incident]').forEach(button=>button.onclick=()=>incident(button.dataset.asIncident));bindPagination(list,data.total,data.limit||50);
      }else if(kind==='bans'){
        list.innerHTML=data.bans.map(item=>`<article class="as-resource"><div><strong>${esc(item.ip)}</strong><span>${tHtml("admin:until", {value0:(item.kind==='ip'?tText("admin:allWebsiteAccess"):esc(actions[item.action]||item.action||tText("admin:accessCheck"))), value1:(esc(stamp(item.until)))})}</span>${item.reason?`<span>${esc(item.reason)}</span>`:''}</div><button type="button" class="text-link" data-as-unban="${esc(item.id)}">${tHtml("admin:unlock")}</button></article>`).join('')||empty(tText("admin:noActiveLocks"));
        list.querySelectorAll('[data-as-unban]').forEach(button=>button.onclick=()=>{
          const item=data.bans.find(item=>String(item.id)===button.dataset.asUnban);
          confirmation({title:tText("admin:removeIpLock"),description:tText("admin:accessFromWillBeRestoredAutomaticProtectionWill", {value0:(item.ip)}),submit:tText("admin:removeLock"),run:async proof=>{await ctx.api(BASE+'/bans/'+encodeURIComponent(item.id),'DELETE',proof);ctx.toast(tText("admin:ipLockRemoved"));}});
        });
      }else{
        list.innerHTML=data.sessions.map(item=>`<article class="as-resource"><div><strong>${esc(item.login)} ${item.current?`<span class="as-current">${tHtml("admin:thisSession")}</span>`:''}</strong><span>${esc(stages[item.stage]||item.stage)} · ${esc(item.ip||tText("admin:ipNotRecorded"))}</span><span>${esc(item.name||item.device||tText("admin:clientNotRecorded"))}${item.name?' · '+esc(item.device||''):''}</span><span>${tHtml("admin:createdActivityUntil", {value0:(esc(stamp(item.created))), value1:(esc(stamp(item.last_seen))), value2:(esc(stamp(item.expires)))})}</span></div>${item.current?'':`<button type="button" class="text-link danger" data-as-revoke="${esc(item.id)}">${tHtml("admin:endSession")}</button>`}</article>`).join('')||empty(tText("admin:noActiveSessions"));
        list.querySelectorAll('[data-as-revoke]').forEach(button=>button.onclick=()=>{
          const item=data.sessions.find(item=>String(item.id)===button.dataset.asRevoke);
          confirmation({title:tText("admin:endSession2"),description:tText("admin:theAccountSessionForOnDeviceWillBe", {value0:(item.login), value1:(item.name||item.device||tText("admin:unknown"))}),submit:tText("admin:endSession2"),run:async proof=>{await ctx.api(BASE+'/sessions/'+encodeURIComponent(item.id)+'/revoke','POST',proof);ctx.toast(tText("admin:sessionEnded"));}});
        });
      }
      if((kind==='bans'||kind==='sessions')&&data.total>data[kind].length){const note=document.createElement('p');note.className='hint';note.textContent=tText("admin:showingOutOfTheRemainingRecordsWillAppear", {value0:(number(data[kind].length)), value1:(number(data.total))});list.prepend(note);}
    }catch(error){
      if(!live()||id!==requestId)return;
      list.innerHTML=`<p class="as-form-status" role="alert">${esc(error.message)}</p><button type="button" class="secondary" data-as-retry>${tHtml("admin:retry")}</button>`;
      $('[data-as-retry]',list).onclick=loadTab;
    }finally{if(live()&&id===requestId)list.setAttribute('aria-busy','false');}
  }

  async function refresh(){
    if(!live())return;
    const button=$('[data-as-refresh]',host);button.disabled=true;
    try{await Promise.allSettled([loadOverview(),loadTab()]);}finally{if(live())button.disabled=false;}
  }
  function start(){
    if(started||disclosure&&!disclosure.open)return;started=true;
    host.innerHTML=`<div class="as-heading"><div><h2>${tHtml("admin:portalSecurity")}</h2><p class="hint">${tHtml("admin:logSuspiciousActivityAndAccessManagementOnlyFor")}</p></div><button type="button" class="text-link" data-as-refresh>${tHtml("admin:update")}</button></div><div class="as-period"><label>${tHtml("admin:summaryAndLogPeriod")}<select data-as-days><option value="1">${tHtml("admin:perDay")}</option><option value="7">${tHtml("admin:per7Days")}</option><option value="30">${tHtml("admin:per30Days")}</option><option value="365">${tHtml("admin:perYear")}</option></select></label></div><p class="as-form-status" role="alert" data-as-error></p><div data-as-overview><p role="status">${tHtml("admin:loadingSummary")}</p></div><div class="as-tabs" role="tablist" aria-label="${tHtml("admin:securitySections")}">${[['events',tText("admin:log")],['incidents',tText("admin:incidents2")],['bans',tText("admin:lockouts")],['sessions',tText("admin:sessions")]].map(([value,label])=>`<button type="button" id="as-tab-${value}" role="tab" data-as-tab="${value}" aria-controls="as-security-view" aria-selected="${tab===value}" tabindex="${tab===value?'0':'-1'}">${label}</button>`).join('')}</div><div id="as-security-view" role="tabpanel" aria-labelledby="as-tab-events" data-as-view></div><p class="hint as-retention" data-as-retention></p>`;
    $('[data-as-refresh]',host).onclick=refresh;
    $('[data-as-days]',host).onchange=event=>{days=Number(event.target.value);offset=0;refresh();};
    const buttons=[...host.querySelectorAll('[data-as-tab]')];
    buttons.forEach((button,index)=>{
      button.onclick=()=>{tab=button.dataset.asTab;offset=0;buttons.forEach(item=>{item.setAttribute('aria-selected',String(item===button));item.tabIndex=item===button?0:-1;});$('[data-as-view]',host).setAttribute('aria-labelledby',button.id);renderTabShell();loadTab();};
      button.onkeydown=event=>{const next=event.key==='ArrowRight'?(index+1)%buttons.length:event.key==='ArrowLeft'?(index+buttons.length-1)%buttons.length:event.key==='Home'?0:event.key==='End'?buttons.length-1:null;if(next===null)return;event.preventDefault();buttons[next].focus();buttons[next].click();};
    });
    renderTabShell();refresh();
  }
  disclosure?.addEventListener('toggle',start);start();
}
