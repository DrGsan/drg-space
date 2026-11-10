import {tText,tHtml} from "../../core/i18n.js";
import {adminWorkspace} from './access.js';
import {mountRegistration} from './invitations.js';
import {managedFriends} from './friends.js';
import {mountAdminStorage} from './storage.js';
import {sectionHeader} from '../../core/section-header.js';
import {preferenceSections,preferenceStatus} from '../../core/preferences.js';
import {managedAccount,managedPassword} from './accounts.js';
import {accountsTable,accountDetails} from './account-table.js';
import {mountFamilyPhotoAccess} from './family-photos.js';
import {mountAdminSecurity} from './security.js';
import './workspace.css';
import { $,esc,date,formBind,bytes } from '../../core/ui.js';
import { csrfCookie,refreshSession } from '../../core/auth.js';
const encoded=value=>btoa(String.fromCharCode(...new TextEncoder().encode(value)));
async function transfer(path,body,extra={},retry=true){
  const response=await fetch('/api/admin/backups/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':body instanceof Blob?'application/octet-stream':'application/json','X-CSRF-Token':csrfCookie(),...extra},body:body instanceof Blob?body:JSON.stringify(body)});
  if(response.status===401&&retry&&await refreshSession())return transfer(path,body,extra,false);
  if(!response.ok){let data;try{data=await response.json();}catch{}throw Object.assign(new Error(typeof data?.detail==='string'?data.detail:tText("admin:unableToPerformOperationWithCopy")),{adminUnlock:response.headers.get('X-Admin-Unlock')==='required'});}
  return response;
}
export function mountAdmin(host,ctx){
  if(!['owner','admin'].includes(ctx.state.me.role)){host.textContent=tText("admin:availableToAdministrator");return;}
  return adminWorkspace(host,ctx,mountAdminContent);
}
function mountAdminContent(host,ctx){
  if(!['owner','admin'].includes(ctx.state.me.role)){host.textContent=tText("admin:availableToAdministrator");return;}
  host.innerHTML=`<div class="page-heading dashboard-heading"><div><p class="eyebrow">${tHtml("admin:workspaceManagement")}</p><h1>${tHtml("admin:administration")}<span class="hello-dot">.</span></h1></div><button class="secondary" data-back-settings>${tHtml("admin:toSettings")}</button></div><section class="panel" data-registration-admin></section><section class="panel admin-family-photos" data-family-photo-access><p role="status">${tHtml("admin:openTheSectionToUploadPermissions")}</p></section><section class="panel" data-accounts-admin><div class="admin-account-heading"><h2>${tHtml("admin:accountsAndData")}</h2><button type="button" class="primary" data-create-account>${tHtml("admin:createAccount")}</button></div><div data-admin-stats></div><p class="hint">${tHtml("admin:accountSizeIsBelowItsRecordsInThe")}</p><div data-admin-users>${tHtml("admin:loadingAccounts")}</div></section><section class="panel admin-backups"><h2>${tHtml("admin:backup")}</h2><p class="muted">${tHtml("admin:oneEncryptedFileForAllUsersAccountsAnd")}</p><p class="hint">${tHtml("admin:theCopyPasswordWillBeNeededToRestore")}</p><div class="admin-backup-actions"><button class="primary" data-export-backup>${tHtml("admin:createBackup")}</button><button class="secondary" data-restore-backup>${tHtml("admin:restoreFromBackup")}</button></div><p class="hint" data-backup-limit></p><div data-restore-result></div></section>`;
  const isOwner=ctx.state.me.role==='owner';
  if(!isOwner)host.querySelectorAll('[data-family-photo-access],.admin-backups').forEach(panel=>panel.remove());
  sectionHeader(host,ctx);
  const security=document.createElement('section');security.className='panel admin-security';security.dataset.securityAdmin='';security.innerHTML=`<p role="status">${tHtml("admin:openTheSectionToUploadTheSecurityLog")}</p>`;
  if(isOwner)$('[data-registration-admin]',host).before(security);
  const groups=document.createElement('div');groups.className='admin-preference-groups';
  host.querySelectorAll(':scope > .panel').forEach(panel=>groups.append(panel));host.append(groups);
  preferenceSections(groups,[
    {id:'security',selector:'[data-security-admin]',group:tText("admin:access2"),title:tText("admin:portalSecurity"),description:tText("admin:loginAttemptsIncidentsLocksAndEmergencyShutdown"),icon:'shield-user'},
    {id:'registration',selector:'[data-registration-admin]',group:tText("admin:access2"),title:tText("admin:registrationAndInvitations"),description:tText("admin:openRegistrationAndLinksForNewParticipants"),icon:'users-round'},
    {id:'family-photos',selector:'[data-family-photo-access]',group:tText("admin:access2"),title:tText("admin:familyPhotos"),description:tText("admin:groupsAndUsersWithAccessToThePhoto"),icon:'users-round'},
    {id:'accounts',selector:'[data-accounts-admin]',group:tText("admin:users"),title:tText("admin:accountsAndData"),description:tText("admin:userProfilesContactsPasswordsAndQuotas"),icon:'shield-user'},
    {id:'backups',selector:'.admin-backups',group:tText("admin:websiteData"),title:tText("admin:backup"),description:tText("admin:createEncryptedCopyAndRestore"),icon:'folder-closed'},
  ],{userId:ctx.state.me.id,scope:'admin'});
  if(isOwner&&ctx.state.adminFamilyExportRequested)$('[data-preference-key=family-photos]',host).open=true;
  if(isOwner&&ctx.state.adminSecurityRequested){$('[data-preference-key=security]',host).open=true;delete ctx.state.adminSecurityRequested;}
  mountRegistration($('[data-registration-admin]',host),ctx);
  if(isOwner)mountAdminSecurity($('[data-security-admin]',host),ctx);
  if(isOwner)mountFamilyPhotoAccess($('[data-family-photo-access]',host),ctx);
  $('[data-back-settings]',host).onclick=()=>ctx.navigate('settings');
  const accountView={};
  const reload=async()=>{
    const result=await ctx.api('/admin/users');if(!host.isConnected)return;
    preferenceStatus($('[data-accounts-admin]',host),tText("admin:accounts", {value0:(result.users.length)}));
    $('[data-create-account]',host).onclick=()=>managedAccount(ctx,{families:result.families,reload});
    mountAdminStorage($('[data-admin-stats]',host),ctx,result);
    if(isOwner)$('[data-backup-limit]',host).textContent=tText("admin:copyLimitRestoringReplacesCurrentWebsiteDataThen", {value0:(bytes(result.backup_limit_bytes))});
    const openUser=user=>{
      const row=ctx.modal(accountDetails(user,{owner:isOwner}));row.classList.add('admin-account-details');
      const close=()=>$('.modal-close',row).click();
      $('[data-admin-friends]',row)?.addEventListener('click',()=>{close();managedFriends(ctx,user,reload);});
      $('[data-admin-profile]',row)?.addEventListener('click',()=>{close();managedAccount(ctx,{user,families:result.families,reload});});
      $('[data-admin-set-password]',row)?.addEventListener('click',()=>{close();managedPassword(ctx,user);});
      $('[data-admin-quota]',row)?.addEventListener('click',()=>{close();
        const el=ctx.modal(`<h2>${tHtml("admin:quota2", {value0:(esc(user.login))})}</h2><form data-quota-form><label class=check-label><input name=unlimited type=checkbox ${user.quota.limit_bytes===null?'checked':''}>${tHtml("admin:noStorageLimit")}</label><label>${tHtml("admin:storageGb")}<input name=gb type=number min=0 step=0.1 max=102400 value="${(user.quota.limit_bytes??1073741824)/1073741824}"></label><p class=hint>${tHtml("admin:ifTheNewQuotaIsSmallerThanThe")}</p><button class=primary>${tHtml("admin:saveQuota")}</button></form>`);
        formBind($('[data-quota-form]',el),async f=>{await ctx.api('/admin/users/'+user.id+'/quota','PUT',{limit_bytes:f.get('unlimited')?null:Math.round(Number(f.get('gb'))*1073741824)});$('.modal-close',el).click();await reload();});
      });
      $('[data-admin-password]',row)?.addEventListener('click',()=>{close();user.current?ctx.navigate('security'):action(user,'password');});
      $('[data-admin-2fa]',row)?.addEventListener('click',()=>{close();action(user,'2fa');});
      if(user.role!=='owner')$('[data-admin-delete]',row)?.addEventListener('click',()=>{close();action(user,'delete');});
      $('[data-admin-restore]',row)?.addEventListener('click',async()=>{try{await ctx.api('/admin/users/restore','POST',{user_id:user.id,confirmation:user.login});close();await reload();ctx.toast(tText("admin:accountDeletionCancelled"));}catch(error){ctx.toast(error.message);}});
    };
    accountsTable($('[data-admin-users]',host),result,accountView,openUser,(user,role)=>{
      const dialog=ctx.modal(`<h2>${role==='admin'?tText("admin:assignAdministrator"):tText("admin:removeAdministratorPrivileges")}</h2><p>${esc(user.login)} → ${role==='admin'?tText("admin:administrator"):tText("admin:user")}</p><p class="hint">${tHtml("admin:administratorManagesRegularUsersInvitationsAndQuotasAssignment")}</p><form data-role-form><button class="primary" type="submit">${tHtml("admin:confirmRoleChange")}</button></form>`);
      formBind($('[data-role-form]',dialog),async()=>{await ctx.api('/admin/users/'+user.id+'/role','PUT',{role,previous_role:user.role});$('.modal-close',dialog).click();await reload();ctx.toast(tText("admin:roleChangedUserSessionsTerminated"));});
    });
  };
  const action=(user,kind)=>{
    const title=kind==='password'?tText("admin:createResetLink"):kind==='2fa'?tText("admin:reset2Fa"):tText("admin:deleteAccount");
    const message=kind==='password'?tText("admin:aOneTimeLinkFor1HourIs"):kind==='2fa'?tText("admin:label2faKeyPasskeyRecoveryCodesAndAllSessions"):tText("admin:theAccountPersonalNotesWithHistoryRemindersSettings");
    const el=ctx.modal(`<h2>${title}</h2><p><strong>${esc(user.login)}</strong></p><p class="muted">${message}</p><form data-admin-action>${kind==='delete'?`<label>${tHtml("admin:enterLoginToDelete")}<input name="confirmation" autocomplete="off" required></label>`:''}<button type="submit" class="primary">${title}</button></form>`);
    formBind($('[data-admin-action]',el),async form=>{
      if(kind==='password'){
        const result=await ctx.api('/admin/users/'+user.id+'/password-reset','POST',Object.fromEntries(form));
        el.querySelector('[data-admin-action]').innerHTML=`<label>${tHtml("admin:passThisLinkToTheUser")}<input readonly value="${esc(result.url)}" aria-label="${tHtml("admin:passwordResetLink")}"></label><p class="hint">${tHtml("admin:validUntilRepeatingTheLinkCancelsThePrevious", {value0:(date(result.expires))})}</p>`;return;
      }
      if(kind==='delete')await ctx.api('/admin/users/delete','POST',{...Object.fromEntries(form),user_id:user.id});
      else await ctx.api('/admin/users/'+user.id+(kind==='password'?'/password':'/reset-2fa'),kind==='password'?'PUT':'POST',Object.fromEntries(form));
      $('.modal-close',el).click();
      if(user.current){location.assign('/');return;}
      await reload();ctx.toast(kind==='delete'?tText("admin:deletionScheduledFor24Hours"):kind==='2fa'?tText("admin:label2faResetANewConfigurationIsRequiredOn"):tText("admin:passwordChangedPreviousSessionsTerminated"));
    });
  };
  if(isOwner){
  $('[data-export-backup]',host).onclick=async()=>{
    let warning='';try{warning=(await ctx.api('/secret-vault')).site_backup_warning;}catch(error){ctx.toast(error.message);return;}
    const el=ctx.modal(`<h2>${tHtml("admin:createBackup")}</h2><p class="muted">${tHtml("admin:theCopyContainsDataForAllAccountsCreate")}</p><form data-export-form><label>${tHtml("admin:backupPassword")}<input type="password" name="backup_password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label>${tHtml("admin:reEnterBackupPassword")}<input type="password" name="repeat" minlength="12" maxlength="256" autocomplete="new-password" required></label><button class="primary" type="submit">${tHtml("admin:downloadBackup")}</button><p class="hint" data-export-status></p></form>`);
    if(warning)$('[data-export-form]',el).insertAdjacentHTML('beforebegin','<p class="vault-warning">'+esc(warning)+'</p>');
    formBind($('[data-export-form]',el),async form=>{
      if(form.get('backup_password')!==form.get('repeat'))throw new Error(tText("admin:backupPasswordsDoNotMatch"));
      $('[data-export-status]',el).textContent=tText("admin:creatingAndEncryptingBackup");
      try{
        const response=await ctx.adminTransfer(()=>transfer('export',{backup_password:form.get('backup_password')}));
        const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
        link.href=url;link.download='drg-site-'+new Date().toISOString().replaceAll(':','-')+'.drgbackup';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
        $('.modal-close',el).click();ctx.toast(tText("admin:backupDownloaded"));
      }finally{if(el.isConnected)$('[data-export-status]',el).textContent='';}
    });
  };
  $('[data-restore-backup]',host).onclick=()=>{
    const el=ctx.modal(`<h2>${tHtml("admin:restoreFromBackup")}</h2><p class="muted">${tHtml("admin:currentDataForAllUsersWillBeReplaced")}</p><form data-restore-form><label>${tHtml("admin:backupFile")}<input type="file" name="file" accept=".drgbackup" required></label><label>${tHtml("admin:backupPassword")}<input type="password" name="backup_password" minlength="12" maxlength="256" autocomplete="off" required></label><label>${tHtml("admin:enterRestoreToReplaceData")}<input name="confirmation" autocomplete="off" pattern="RESTORE" required></label><button class="primary" type="submit">${tHtml("admin:replaceDataFromBackup")}</button><p class="hint" data-restore-status></p></form>`);
    formBind($('[data-restore-form]',el),async form=>{
      const file=form.get('file');if(!file?.size)throw new Error(tText("admin:selectBackupFile"));
      $('[data-restore-status]',el).textContent=tText("admin:checkingFileAndRestoringDataDoNotClose");
      try{
        const response=await ctx.adminTransfer(()=>transfer('restore',file,{'X-Backup-Password-B64':encoded(form.get('backup_password')),'X-Restore-Confirmation':form.get('confirmation')}));
        const result=await response.json();$('.modal-close',el).click();
        ctx.restored(result.login);
      }finally{if(el.isConnected)$('[data-restore-status]',el).textContent='';}
    });
  };
  }
  reload().catch(error=>{if(host.isConnected)$('[data-admin-users]',host).textContent=error.message;});
}
