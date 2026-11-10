import {tText,tHtml} from '../../core/i18n.js';
import {$,esc,date,formBind} from '../../core/ui.js';
import {preferenceStatus} from '../../core/preferences.js';
import './invitations.css';

export async function mountRegistration(host,ctx){
  host.registrationCleanup?.();
  let timer,sequence=0,alive=true,refresh=()=>{};
  const panel=host.closest('details');
  const visible=()=>{if(alive&&!document.hidden&&host.getClientRects().length)refresh();};
  const observer=new MutationObserver(records=>{
    if(!host.isConnected)cleanup();
    else if(records.some(record=>record.type==='attributes'&&record.attributeName==='hidden'&&record.target.contains(host)))visible();
  });
  const cleanup=()=>{alive=false;clearTimeout(timer);observer.disconnect();document.removeEventListener('visibilitychange',visible);window.removeEventListener('focus',visible);panel?.removeEventListener('toggle',visible);};
  host.registrationCleanup=cleanup;
  observer.observe(document.getElementById('app'),{childList:true,subtree:true,attributes:true,attributeFilter:['hidden']});
  document.addEventListener('visibilitychange',visible);window.addEventListener('focus',visible);panel?.addEventListener('toggle',visible);
  try{
    const policy=await ctx.api('/admin/registration');if(!alive||!host.isConnected)return;
    preferenceStatus(host,policy.enabled?tText('admin:open'):tText('admin:closed'));
    host.innerHTML=`<h2>${tHtml('admin:registrationAndInvitations')}</h2><p class="muted">${policy.enabled?tText('admin:openRegistrationIsEnabledAnyVisitorCanCreate'):tText('admin:registrationIsClosedYouCanInviteAPerson')}</p><div class="admin-account-actions"><button class="secondary" data-registration-toggle ${ctx.state.me.role==='owner'?'':'hidden'}>${policy.enabled?tText('admin:disableRegistration'):tText('admin:enableRegistration')}</button><button class="primary" data-invite-member>${tHtml('admin:inviteAPerson')}</button></div><p class="hint">${tHtml('admin:invitationIsOneTimeAndValidForOne')}</p><h3>${tHtml('admin:yourInvitations')}</h3><p class="hint">${tHtml('admin:independentInvitationsHint')}</p><div class="registration-invitations" data-invitation-list></div>`;
    $('[data-registration-toggle]',host).onclick=()=>{
      const el=ctx.modal(`<h2>${tHtml('admin:registration',{value0:policy.enabled?tText('admin:close'):tText('admin:open2')})}</h2><form data-policy-proof><button class="primary" type="submit">${tHtml('admin:apply')}</button></form>`);
      formBind($('[data-policy-proof]',el),async()=>{await ctx.api('/admin/registration','PUT',{enabled:!policy.enabled});$('.modal-close',el).click();await mountRegistration(host,ctx);});
    };
    const reload=async()=>{
      const request=++sequence,result=await ctx.api('/invites');if(!alive||!host.isConnected||request!==sequence)return;
      clearTimeout(timer);
      const invitations=result.invitations.filter(item=>item.status==='active');
      const stamp=result.server_time??Date.now()/1000;
      if(invitations.length)timer=setTimeout(visible,Math.max(100,Math.min(2147483647,(Math.min(...invitations.map(item=>item.expires))-stamp)*1000+100)));
      const list=$('[data-invitation-list]',host);
      list.innerHTML=invitations.length?invitations.map(item=>`<article class="registration-invitation" data-invitation-id="${esc(item.id)}"><div class="invitation-heading"><strong>${item.login?tHtml('admin:invitationFor',{login:item.login}):tHtml('admin:invitationAnyone')}</strong><span class="invitation-status" data-status="${esc(item.status)}">${tHtml('admin:invitationActive')}</span></div><p class="hint">${tHtml('admin:invitationExpiry',{date:date(item.expires)})}${item.add_creator_as_friend?` · ${tHtml('admin:invitationWithFriendship')}`:''}</p>${item.url?`<input readonly type="url" value="${esc(item.url)}" aria-label="${tHtml('admin:invitationLink')}" data-invitation-url>`:''}<div class="invitation-actions">${item.url?`<button class="secondary compact" data-copy-invitation>${tHtml('admin:copyInvitation')}</button>`:''}${item.can_revoke?`<button class="text-link" data-revoke-invitation>${tHtml('admin:revokeInvitation')}</button>`:''}</div></article>`).join(''):`<p class="muted">${tHtml('admin:noInvitations')}</p>`;
      for(const item of invitations){
        const row=list.querySelector(`[data-invitation-id="${item.id}"]`);
        $('[data-copy-invitation]',row)?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(item.url);ctx.toast(tText('admin:invitationCopied'));}catch{$('[data-invitation-url]',row).select();}});
        $('[data-revoke-invitation]',row)?.addEventListener('click',()=>{
          const el=ctx.modal(`<h2>${tHtml('admin:revokeInvitationTitle')}</h2><p>${tHtml('admin:revokeInvitationHint')}</p><form data-revoke-invitation-form><button class="primary" type="submit">${tHtml('admin:revokeInvitation')}</button></form>`);
          formBind($('[data-revoke-invitation-form]',el),async()=>{let alreadyRemoved=false;try{await ctx.api('/invites/'+item.id,'DELETE');}catch(error){if(error.status!==404)throw error;alreadyRemoved=true;}$('.modal-close',el).click();row.remove();await reload();ctx.toast(tText(alreadyRemoved?'admin:invitationAlreadyRemoved':'admin:invitationRevokedNotice'));});
        });
      }
    };
    refresh=()=>reload().catch(error=>{if(alive&&host.isConnected)ctx.toast(error.message);});
    $('[data-invite-member]',host).onclick=()=>{
      const el=ctx.modal(`<h2>${tHtml('admin:inviteAPerson')}</h2><p class="hint">${tHtml('admin:invitationIsOneTimeAndValidForOne')}</p><form data-create-invitation><label class="check-label"><input type="checkbox" name="add_creator_as_friend">${tHtml('admin:addCreatorAsFriend')}</label><p class="hint">${tHtml('admin:invitationFriendshipHint')}</p><button class="primary" type="submit">${tHtml('admin:createInvitation')}</button></form>`);
      formBind($('[data-create-invitation]',el),async form=>{await ctx.api('/invites','POST',{add_creator_as_friend:form.has('add_creator_as_friend')});$('.modal-close',el).click();await reload();});
    };
    await reload();
  }catch(error){cleanup();if(host.isConnected)host.textContent=error.message;}
}
