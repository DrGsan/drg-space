import {tText,tHtml} from "../../core/i18n.js";
import {sectionHeader} from '../../core/section-header.js';
import {mountPasskeySettings} from '../../core/passkeys.js';
import { esc, date, $,formBind } from "../../core/ui.js";
import { mountEnrollment } from './enrollment.js';
import { requireStrongPassword } from '../../core/passwords.js';
import {mountVaultSettings} from './vault.js';



export async function mountSecurity(host, ctx) {
  const enabled=ctx.state.me.two_factor;
  host.innerHTML = `<div class="page-heading dashboard-heading"><div><p class="eyebrow">${tHtml("security:accountAccess")}</p><h1>${tHtml("security:security")}<span class="hello-dot">.</span></h1></div><button class="secondary" data-back-settings>${tHtml("security:toSettings")}</button></div><div class="security-grid"><section class="panel" data-passkey-settings></section><section class="panel"><h2>${tHtml("security:passwordChange")}</h2><p class="hint">${tHtml("security:atLeast12CharactersLowercaseAndUppercaseLetters")}</p><form data-change-password><label>${tHtml("security:currentPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label><label>${tHtml("security:newPassword")}<input name="new_password" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label>${tHtml("security:reEnterNewPassword")}<input name="repeat" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label>${tHtml("security:passwordHintOptional")}<input name="password_hint" maxlength="200" placeholder="${tHtml("security:doNotEnterThePasswordItself")}"></label>${enabled?`<label>${tHtml("security:label2faOrRecoveryCode")}<input name="code" autocomplete="one-time-code" maxlength="100" required></label>`:''}<button class="primary" type="submit">${tHtml("security:changePassword")}</button></form></section><section class="panel"><h2>${tHtml("security:twoFactorAuthentication")}</h2><span class="tag">${enabled?tText("security:label2faEnabled"):tText("security:label2faDisabled")}</span><p class="muted">${enabled?tText("security:passwordLoginIsVerifiedWithAnAuthenticatorCode"):tText("security:loginIsCurrentlyProtectedByPasswordConnectAn")}</p><button class="secondary" data-toggle-2fa ${enabled&&ctx.state.me.role==='admin'?tText("security:disabledTitle2FaIsMandatoryForTheWebsite"):''}>${enabled?tText("security:disable2Fa"):tText("security:enable2Fa")}</button><p class="hint">${tHtml("security:whenReEnabledANewKeyAndNew")}</p></section><section class="panel security-sessions"><h2>${tHtml("security:activeSessions")}</h2><p class="muted">${tHtml("security:sessionIsExtendedUponActivity7DaysOf")}</p><button class="secondary" data-revoke-others>${tHtml("security:logOutOfAllOtherSessions")}</button><div data-sessions>${tHtml("security:loadingSessions")}</div></section>${ctx.state.me.role === "owner" ? `<section class="panel security-bans"><h2>${tHtml("security:portalSecurity")}</h2><p class="muted">${tHtml("security:loginLogsIncidentsAndLockoutsAreAvailableIn")}</p><button class="secondary" data-admin-security>${tHtml("security:openAdmin")}</button></section>` : ''}</div>`;
  sectionHeader(host,ctx);
  const vault=document.createElement('section');vault.className='panel';vault.dataset.vaultSettings='';
  $('.security-grid',host).insertBefore(vault,$('.security-sessions',host));
  mountVaultSettings(vault,ctx).catch(error=>ctx.toast(error.message));
  mountPasskeySettings($('[data-passkey-settings]',host),ctx);
  $('[data-back-settings]',host).onclick=()=>ctx.navigate('settings');
  const updated=async()=>{ctx.state.me=await ctx.api('/me');ctx.renderWorkspace();};
  formBind($('[data-change-password]',host),async form=>{
    requireStrongPassword(form.get('new_password'));
    if(form.get('new_password')!==form.get('repeat'))throw new Error(tText("security:newPasswordsDoNotMatch"));
    const data=Object.fromEntries(form);delete data.repeat;
    await ctx.api('/security/password','PUT',data);await updated();ctx.toast(tText("security:passwordChangedAllOtherSessionsHaveBeenTerminated"));
  });
  $('[data-toggle-2fa]',host).onclick=()=>{
    if(!enabled){
      const modal=ctx.modal(`<h2>${tHtml("security:enable2Fa")}</h2><p class="muted">${tHtml("security:anyTotpAppWillWork2FasAegisGoogle")}</p><div data-setup-2fa></div>`);
      mountEnrollment($('[data-setup-2fa]',modal),ctx,async()=>{$('.modal-close',modal).click();await updated();ctx.toast(tText("security:label2faIsEnabled"));});
      return;
    }
    const modal=ctx.modal(`<h2>${tHtml("security:disable2Fa")}</h2><p class="muted">${tHtml("security:afterDisablingPasswordLoginWillNotRequireA")}</p><form data-disable-2fa><label>${tHtml("security:currentPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label><label>${tHtml("security:label2faOrRecoveryCode")}<input name="code" autocomplete="one-time-code" maxlength="100" required></label><button class="primary" type="submit">${tHtml("security:disable2Fa")}</button></form>`);
    formBind($('[data-disable-2fa]',modal),async form=>{await ctx.api('/security/2fa/disable','POST',Object.fromEntries(form));$('.modal-close',modal).click();await updated();ctx.toast(tText("security:label2faIsDisabled"));});
  };
  $('[data-revoke-others]',host).onclick=async()=>{
    const button=$('[data-revoke-others]',host);button.disabled=true;
    try{await ctx.api('/security/sessions/revoke-others','POST');await sessions();ctx.toast(tText("security:allOtherSessionsHaveBeenTerminated"));}
    catch(error){ctx.toast(error.message);}finally{button.disabled=false;}
  };
  const sessions = async () => {
    const rows = await ctx.api("/security/sessions");
    if (!host.isConnected) return;
    $("[data-sessions]", host).innerHTML = rows.map((row) =>
      `<div class="share-row"><div class="session-details"><strong>${esc(row.name||row.device||tText("security:unknownDevice"))}</strong>${row.name?`<span class="small muted">${esc(row.device||'')}</span>`:''}<span class="small muted">${row.current?(""+tText("security:currentSession")+" "):''}${row.online?(""+tText("security:online")+" "):''}${row.last_seen?tText("security:lastActivity", {value0:(date(row.last_seen))}):tText("security:activityNotRecorded")}</span><span class="small muted">IP: ${esc(row.ip||tText("security:noData"))}</span><span class="small muted">${tHtml("security:createdUntil", {value0:(date(row.created)), value1:(date(row.expires))})}</span></div><div class="session-actions"><button class="text-link" data-name-session="${esc(row.id)}">${tHtml("security:name")}</button>${row.current?`<span class="tag">${tHtml("security:thisSession")}</span>`:`<button class="text-link danger" data-revoke-session="${esc(row.id)}" ${row.can_revoke?'':tText("security:disabledTitleUseAnOldDeviceOrWait")}>${tHtml("security:endSession")}</button>`}</div></div>`
    ).join("");
    host.querySelectorAll('[data-name-session]').forEach(button=>{button.onclick=()=>{
      const row=rows.find(item=>item.id===button.dataset.nameSession);if(!row)return;
      const modal=ctx.modal(`<h2>${tHtml("security:sessionName")}</h2><p class="muted">${tHtml("security:anEmptyNameWillReturnAnAutomaticName", {value0:(esc(row.device||tText("security:device")))})}</p><form data-session-name><label>${tHtml("security:name")}<input name="name" maxlength="80" value="${esc(row.name||'')}" autocomplete="off" placeholder="${tHtml("security:forExampleAHomeMacbook")}"></label><button class="primary" type="submit">${tHtml("security:save")}</button></form>`);
      formBind($('[data-session-name]',modal),async form=>{await ctx.api('/security/sessions/'+encodeURIComponent(row.id),'PATCH',{name:form.get('name')});$('.modal-close',modal).click();await sessions();ctx.toast(tText("security:sessionNameSaved"));});
    };});
    host.querySelectorAll("[data-revoke-session]").forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        try {
          await ctx.api("/security/sessions/" + button.dataset.revokeSession, "DELETE");
          await sessions();
          ctx.toast(tText("security:sessionRevoked"));
        } catch (error) { ctx.toast(error.message); button.disabled = false; }
      };
    });
  };
  host.querySelector('[data-admin-security]')?.addEventListener('click',()=>{ctx.state.adminSecurityRequested=true;ctx.navigate('admin');});
  const results = await Promise.allSettled([sessions()]);
  for (const result of results) {
    if (result.status === "rejected" && host.isConnected) ctx.toast(result.reason.message);
  }
}
