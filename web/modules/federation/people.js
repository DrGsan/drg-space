import {tText,tHtml} from "../../core/i18n.js";
import {$,esc,formBind} from '../../core/ui.js';
import {contacts,ownContact,addContact,validateContact,operate,knownContact,contactFingerprint} from './client.js';
import {callsController} from '../chat/calls.js';
import './federation.css';

const statuses={confirmed:tText("federation:cardConfirmed"),outgoing:tText("federation:invitationSent"),incoming:tText("federation:invitingYou"),accepted:tText("federation:inFriends"),blocked:tText("federation:communicationDisabled")};
const labels=contact=>({name:contact.display_name||contact.login||contact.actor_id,origin:contact.origin||contact.server?.origin||contact.peer_id});
function errorText(error){return error.status===404?tText("federation:thisServerDoesNotCurrentlyCommunicateWithOther"):error.message;}
export async function mountFederationPeople(host,ctx,{refresh=()=>{}}={}){
  host.innerHTML=`<section class="preference-group preference-rows federation-people"><h2 class="preference-group-title">${tHtml("federation:otherDrgServers")}</h2><div class="federation-empty"><p class="muted">${tHtml("federation:checkingConnection")}</p></div></section>`;
  try{
    const info=await ctx.api('/federation/info');if(!host.isConnected)return;
    if(!info.enabled){
      host.innerHTML=`<section class="preference-group preference-rows federation-people"><h2 class="preference-group-title">${tHtml("federation:otherDrgServers")}</h2><div class="federation-empty"><p>${tHtml("federation:communicateAndWorkWithFriendsWhoHaveTheir")}</p><p class="hint">${ctx.state.me.role==='owner'?tText("federation:enableInterServerCommunicationEachServerAndDevice"):tText("federation:communicationWithOtherServersIsCurrentlyDisabledBy")}</p>${ctx.state.me.role==='owner'?`<button class="secondary compact" data-enable-federation>${tHtml("federation:enableCommunicationWithOtherServers")}</button>`:''}</div></section>`;
      $('[data-enable-federation]',host)?.addEventListener('click',async e=>{e.target.disabled=true;try{await protectedAction(ctx,()=>ctx.api('/admin/federation/enable','POST',{enabled:true}));await mountFederationPeople(host,ctx,{refresh});}catch(error){ctx.toast(errorText(error));e.target.disabled=false;}});return;
    }
    const people=await contacts(ctx);if(!host.isConnected)return;
    host.innerHTML=`<section class="preference-group preference-rows federation-people"><div class="federation-heading"><h2 class="preference-group-title">${tHtml("federation:otherDrgServers")}</h2><div><button class="text-link" data-own-contact>${tHtml("federation:myCard")}</button><button class="secondary compact" data-add-contact>${tHtml("federation:addAFriendFromAnotherServer")}</button></div></div><div class="federation-contacts">${people.length?people.map((contact,index)=>{const {name,origin}=labels(contact);return `<article class="panel federation-person" data-federation-person="${index}"><div><strong>${esc(name)}</strong><small>${esc(origin)}</small><span class="hint">${esc(statuses[contact.status]||contact.status)}</span></div><div class="federation-person-actions">${contact.status==='confirmed'?`<button class="secondary compact" data-federation-invite>${tHtml("federation:inviteToFriends")}</button>`:''}${contact.status==='incoming'?`<button class="primary compact" data-federation-accept>${tHtml("federation:acceptInvitation")}</button>`:''}${contact.status==='accepted'?`<button class="primary compact" data-federation-notes>${tHtml("federation:sharedNotes")}</button><button class="secondary compact" data-federation-call>${tHtml("federation:call")}</button>`:''}${contact.status!=='blocked'?`<button class="text-link danger-link" data-federation-revoke>${tHtml("federation:disableCommunication")}</button>`:''}</div></article>`;}).join(''):`<div class="federation-empty"><p class="muted">${tHtml("federation:confirmedFriendsFromOtherServersWillAppearHere")}</p></div>`}</div><p class="hint">${tHtml("federation:askYourFriendToSendYouTheirCard")}</p><p class="error" data-federation-error role="alert"></p></section>`;
    $('[data-own-contact]',host).onclick=()=>contactDialog(ctx);
    const settings=document.createElement('button');settings.className='text-link';settings.textContent=tText("federation:devicesAndCommunication");settings.dataset.federationSettings='';$('[data-own-contact]',host).before(settings);settings.onclick=()=>connectionDialog(ctx,info,()=>mountFederationPeople(host,ctx,{refresh}));
    $('[data-add-contact]',host).onclick=()=>importContactDialog(ctx,()=>mountFederationPeople(host,ctx,{refresh}));
    for(const row of host.querySelectorAll('[data-federation-person]')){
      const contact=people[Number(row.dataset.federationPerson)];
      const action=async(button,work)=>{button.disabled=true;try{await work();await mountFederationPeople(host,ctx,{refresh});await refresh();}catch(error){const message=$('[data-federation-error]',host);if(message)message.textContent=errorText(error);button.disabled=false;}};
      $('[data-federation-invite]',row)?.addEventListener('click',e=>action(e.currentTarget,()=>operate(ctx,contact.peer_id,contact.actor_id,'invite',{})));
      $('[data-federation-accept]',row)?.addEventListener('click',e=>action(e.currentTarget,()=>operate(ctx,contact.peer_id,contact.actor_id,'accept',{invitation:contact.invitation})));
      $('[data-federation-notes]',row)?.addEventListener('click',()=>{ctx.state.federationContact=contact;ctx.state.selected=null;ctx.state.notesList=true;ctx.navigate('notes');});
      $('[data-federation-call]',row)?.addEventListener('click',()=>callsController.startFederated?.(contact));
      $('[data-federation-revoke]',row)?.addEventListener('click',e=>{if(confirm(tText("federation:disableCommunicationWithThisPersonNewAccessTo")))action(e.currentTarget,()=>ctx.api('/federation/contacts/'+contact.peer_id+'/'+contact.actor_id+'/revoke','POST',{}));});
    }
  }catch(error){if(host.isConnected)host.innerHTML=`<section class="preference-group preference-rows federation-people"><h2 class="preference-group-title">${tHtml("federation:otherDrgServers")}</h2><div class="federation-empty"><p class="hint">${esc(errorText(error))}</p></div></section>`;}
}
function connectionDialog(ctx,info,changed){
  const owner=ctx.state.me.role==='owner',devices=info.devices||[],peers=info.peers||[];
  const dialog=ctx.modal(`<h2>${tHtml("federation:devicesAndInterServerCommunication")}</h2><p class="hint">${tHtml("federation:aRevokedDeviceWillNoLongerOpenShared")}</p><div data-federation-device-list></div>${owner?`<h3>${tHtml("federation:connectedServers")}</h3><div data-federation-peer-list></div><hr><button class="secondary danger-link" data-disable-federation>${tHtml("federation:disableCommunicationWithOtherServers")}</button>`:''}<p class="error" data-connection-error role="alert"></p>`);
  const run=async(button,action)=>{button.disabled=true;try{await protectedAction(ctx,action);$('.modal-close',dialog).click();await changed();}catch(error){$('[data-connection-error]',dialog).textContent=errorText(error);button.disabled=false;}};
  $('[data-federation-device-list]',dialog).innerHTML=devices.length?devices.map((item,index)=>{const card=item.card||item;return `<div class="federation-device-row"><code class="federation-fingerprint">${esc(card.device_id)}</code><span class="hint">${item.active===false?tText("federation:revoked"):tText("federation:confirmed")}</span>${item.active!==false?`<button class="text-link danger-link" data-revoke-device="${index}">${tHtml("federation:revokeDevice")}</button>`:''}</div>`;}).join(''):`<p class="hint">${tHtml("federation:deviceKeysAreNotYetAvailableOpenMy")}</p>`;
  dialog.querySelectorAll('[data-revoke-device]').forEach(button=>button.onclick=()=>{if(!confirm(tText("federation:revokeThisDeviceSKeyItWillNo")))return;const item=devices[Number(button.dataset.revokeDevice)],card=item.card||item;run(button,()=>ctx.api('/federation/devices/'+card.device_id+'/revoke','POST',{}));});
  if(owner){
    $('[data-federation-peer-list]',dialog).innerHTML=peers.length?peers.map((peer,index)=>`<div class="federation-device-row"><strong>${esc(peer.origin||peer.server_id)}</strong><code class="federation-fingerprint">${esc(peer.key_id)}</code><span class="hint">${peer.active===false?tText("federation:disabled"):tText("federation:connected")}</span>${peer.active!==false?`<button class="text-link danger-link" data-revoke-peer="${index}">${tHtml("federation:disableServer")}</button>`:''}</div>`).join(''):`<p class="hint">${tHtml("federation:connectedServersWillAppearAfterTheFriendS")}</p>`;
    dialog.querySelectorAll('[data-revoke-peer]').forEach(button=>button.onclick=()=>{if(!confirm(tText("federation:disableThisServerForAllWebsiteUsersAnd")))return;const peer=peers[Number(button.dataset.revokePeer)];run(button,()=>ctx.api('/admin/federation/peers/'+peer.server_id+'/revoke','POST',{}));});
    $('[data-disable-federation]',dialog).onclick=event=>{if(confirm(tText("federation:disableInterServerNotesInvitationsAndCallsFor")))run(event.currentTarget,()=>ctx.api('/admin/federation/enable','POST',{enabled:false}));};
  }
}
export async function contactDialog(ctx){
  const dialog=ctx.modal(`<h2>${tHtml("federation:myDrgCard")}</h2><p class="muted">${tHtml("federation:preparingAPublicCardForThisDevice")}</p>`);
  try{
    const value=await ownContact(ctx),fingerprint=await contactFingerprint(value);if(!dialog.isConnected)return;
    const content=$('.modal-content',dialog)||dialog.querySelector('section')||dialog;
    // Keep the modal's existing close button; replace only our own content.
    const body=document.createElement('div');body.className='federation-card-dialog';
    body.innerHTML=`<h2>${tHtml("federation:myDrgCard")}</h2><p>${tHtml("federation:passItToAFriendTheCardOnly")}</p><p class="hint">${tHtml("federation:cardFingerprintVerifyWithAFriend")}</p><code class="federation-fingerprint" data-contact-fingerprint>${esc(fingerprint)}</code><p class="hint">${tHtml("federation:heConfirmsTheServerAddressCertificateAndKeys")}</p><details><summary>${tHtml("federation:deviceFingerprint")}</summary><code class="federation-fingerprint">${esc(value.actor.device_id)}</code></details><label>${tHtml("federation:contactCard")}<textarea readonly rows="7" data-contact-card aria-label="${tHtml("federation:publicDrgCard")}"></textarea></label><button class="primary" data-copy-card>${tHtml("federation:copyCard")}</button><p class="hint" data-copy-status role="status"></p>`;
    dialog.querySelectorAll('h2,p').forEach(el=>{if(!el.closest('.modal-close'))el.remove();});content.append(body);
    $('[data-contact-card]',body).value=JSON.stringify(value);
    $('[data-copy-card]',body).onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(value));$('[data-copy-status]',body).textContent=tText("federation:cardCopied");}catch{$('[data-contact-card]',body).select();$('[data-copy-status]',body).textContent=tText("federation:manuallyCopyTheCard");}};
  }catch(error){ctx.toast(errorText(error));$('.modal-close',dialog)?.click();}
}
export function importContactDialog(ctx,changed){
  const dialog=ctx.modal(`<h2>${tHtml("federation:friendFromAnotherServer")}</h2><form data-contact-import><label>${tHtml("federation:friendSCard")}<textarea name="card" rows="6" required maxlength="100000" placeholder="${tHtml("federation:insertDrgCard")}"></textarea></label><button type="button" class="secondary" data-check-contact>${tHtml("federation:verifyCard")}</button><div data-contact-preview hidden></div><p class="error" role="alert" data-contact-error></p><button class="primary" type="submit" disabled data-confirm-contact>${tHtml("federation:confirmContact")}</button></form>`);
  let value,previousDeviceId,checkSequence=0;
  $('[data-check-contact]',dialog).onclick=async()=>{
    const sequence=++checkSequence,raw=$('textarea',dialog).value,error=$('[data-contact-error]',dialog);error.textContent='';value=null;$('[data-confirm-contact]',dialog).disabled=true;
    try{
      const candidate=await validateContact(raw),fingerprint=await contactFingerprint(candidate),local=await knownContact(ctx,candidate.server.server_id,candidate.actor.actor_id),remote=(await contacts(ctx)).find(item=>item.peer_id===candidate.server.server_id&&item.actor_id===candidate.actor.actor_id);if(sequence!==checkSequence||!dialog.isConnected)return;value=candidate;previousDeviceId=remote?.card?.device_id||local?.device_id;
      if(previousDeviceId===value.actor.device_id)previousDeviceId=undefined;
      $('[data-contact-preview]',dialog).hidden=false;$('[data-contact-preview]',dialog).innerHTML=`<p><strong>${esc(value.profile?.display_name||value.profile?.login||value.actor.actor_id)}</strong><br><small>${esc(value.server.origin)}</small></p><p class="hint">${tHtml("federation:cardFingerprintVerifyWithAFriend")}</p><code class="federation-fingerprint" data-contact-fingerprint>${esc(fingerprint)}</code><p class="hint">${tHtml("federation:heConfirmsTheServerAddressCertificateAndKeys2")}</p>${previousDeviceId?`<p class="hint">${tHtml("federation:previousDeviceFingerprint")}</p><code class="federation-fingerprint">${esc(previousDeviceId)}</code>`:''}<details><summary>${previousDeviceId?tText("federation:newDeviceFingerprint"):tText("federation:friendSDeviceFingerprint")}</summary><code class="federation-fingerprint">${esc(value.actor.device_id)}</code></details><label class="checkbox-row"><input type="checkbox" name="verified" required>${tHtml("federation:iVerifiedTheAddressAndCardFingerprintWith", {value0:(previousDeviceId?(" "+tText("federation:andIConfirmTheAdditionalDevice")+""):'')})}</label>`;
      $('[data-confirm-contact]',dialog).disabled=false;
    }catch(problem){if(sequence===checkSequence)error.textContent=errorText(problem);}
  };
  $('textarea',dialog).oninput=()=>{++checkSequence;value=null;$('[data-contact-preview]',dialog).hidden=true;$('[data-confirm-contact]',dialog).disabled=true;};
  formBind($('[data-contact-import]',dialog),async fields=>{if(!value||!fields.get('verified'))throw new Error(tText("federation:firstVerifyAndConfirmYourFriendSCard"));await protectedAction(ctx,()=>addContact(ctx,value,{expectedOldDeviceId:previousDeviceId}));$('.modal-close',dialog).click();await changed();ctx.toast(tText("federation:cardVerifiedNowSendAnInvitationToYour"));});
}
export async function protectedAction(ctx,action){
  try{return await action();}catch(error){
    if(!error.adminUnlock)throw error;
    await new Promise((resolve,reject)=>{
      const dialog=ctx.modal(`<h2>${tHtml("federation:confirmServerControl")}</h2><form data-federation-unlock><label>${tHtml("federation:ownerSPassword")}<input type="password" name="password" autocomplete="current-password" maxlength="256" required></label><p class="error" data-unlock-error role="alert"></p><button class="primary" type="submit">${tHtml("federation:confirm")}</button></form>`);let done=false;
      dialog.addEventListener('close',()=>{if(!done)reject(new Error(tText("federation:controlConfirmationCancelled")));},{once:true});
      $('[data-federation-unlock]',dialog).onsubmit=async event=>{event.preventDefault();const input=$('[name=password]',dialog),button=$('[type=submit]',dialog);button.disabled=true;try{await ctx.api('/admin/access/unlock','POST',{password:input.value});done=true;$('.modal-close',dialog).click();resolve();}catch(problem){$('[data-unlock-error]',dialog).textContent=problem.message;}finally{input.value='';button.disabled=false;}};
      $('[name=password]',dialog).focus();
    });
    return action();
  }
}
