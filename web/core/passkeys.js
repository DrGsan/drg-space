import {tText,tHtml} from "./i18n.js";
import {$,esc,date,formBind} from './ui.js';
import './passkeys.css';
export const passkeysSupported=()=>window.isSecureContext&&!!window.PublicKeyCredential&&!!navigator.credentials?.create&&!!navigator.credentials?.get;
export function fromBase64url(value){const raw=atob(value.replaceAll('-','+').replaceAll('_','/')+'='.repeat((4-value.length%4)%4));return Uint8Array.from(raw,c=>c.charCodeAt(0));}
export function toBase64url(value){return btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');}
function options(value){return {...value,challenge:fromBase64url(value.challenge),...(value.user?{user:{...value.user,id:fromBase64url(value.user.id)}}:{}),...(value.excludeCredentials?{excludeCredentials:value.excludeCredentials.map(key=>({...key,id:fromBase64url(key.id)}))}:{}),...(value.allowCredentials?{allowCredentials:value.allowCredentials.map(key=>({...key,id:fromBase64url(key.id)}))}:{})};}
export function serializeCredential(credential){
 const response=credential.response;
 return {id:credential.id,rawId:toBase64url(credential.rawId),type:credential.type,response:{clientDataJSON:toBase64url(response.clientDataJSON),...(response.attestationObject?{attestationObject:toBase64url(response.attestationObject),transports:response.getTransports?.()||[]}:{}),...(response.authenticatorData?{authenticatorData:toBase64url(response.authenticatorData),signature:toBase64url(response.signature),userHandle:response.userHandle?toBase64url(response.userHandle):null}:{})},clientExtensionResults:credential.getClientExtensionResults?.()||{}};
}
function message(error){return error.name==='NotAllowedError'?tText("common:verificationCancelledOrTimeoutOccurredYouCanTry"):error.name==='InvalidStateError'?tText("common:thisPasskeyAlreadyExistsChooseADifferentKey"):error.name==='SecurityError'?tText("common:openTheWebsiteAtItsPrimarySecureAddress"):error.message||tText("common:unableToUsePasskey");}
export async function authenticatePasskey(api,{signal}={}){
 const start=await api('/passkeys/options','POST',{},false,{}, {signal});
 const credential=await navigator.credentials.get({publicKey:options(start.options),signal});
 if(!credential)throw new Error(tText("common:passkeyVerificationCancelled"));
 return api('/passkeys/verify','POST',{credential:serializeCredential(credential),csrf:start.ceremony_csrf},false,{}, {signal});
}
export const passkeyError=message;
export function mountPasskeySettings(host,ctx){
 host.innerHTML=`<h2>Passkey</h2><p class="muted">${tHtml("common:quickLoginWithFaceIdTouchIdOr")}</p><div data-passkey-list role="status">${tHtml("common:loadingKeys")}</div><button class="secondary" data-passkey-add type="button">${tHtml("common:addPasskey")}</button><p class="hint" data-passkey-support></p>`;
 const supported=passkeysSupported();$('[data-passkey-add]',host).hidden=!supported;
 $('[data-passkey-support]',host).textContent=supported?tText("common:youCanSaveMultipleKeysTheSameWebsite"):tText("common:toAddAPasskeyOpenTheWebsiteIn");
 const factor=()=>ctx.state.me.two_factor?`<label>${tHtml("common:label2faOrRecoveryCode")}<input name="code" autocomplete="one-time-code" maxlength="100" required></label>`:'';
 async function reload(){
  const data=await ctx.api('/security/passkeys');if(!host.isConnected)return;
  $('[data-passkey-list]',host).innerHTML=data.items.map(key=>`<article class="passkey-row"><div><strong>${esc(key.name)}</strong><small>${tHtml("common:added", {value0:(date(key.created)), value1:(key.last_used?(" "+tText("common:login", {value0:(date(key.last_used))})+""):'')})}</small><small>${esc(key.rp_id)}</small></div><button type="button" class="text-link" data-passkey-rename="${esc(key.id)}">${tHtml("common:name")}</button><button type="button" class="text-link danger" data-passkey-delete="${esc(key.id)}">${tHtml("common:delete")}</button></article>`).join('')||`<p class="hint">${tHtml("common:passkeysHaveNotYetBeenAdded")}</p>`;
  $('[data-passkey-add]',host).disabled=data.items.length>=data.limit;
  host.querySelectorAll('[data-passkey-rename]').forEach(button=>button.onclick=()=>{
   const key=data.items.find(item=>item.id===button.dataset.passkeyRename),el=ctx.modal(`<h2>${tHtml("common:passkeyName")}</h2><form data-passkey-rename-form><label>${tHtml("common:name")}<input name="name" maxlength="80" required value="${esc(key.name)}"></label><button type="submit" class="primary">${tHtml("common:saveName2")}</button></form>`);
   formBind($('[data-passkey-rename-form]',el),async form=>{await ctx.api('/security/passkeys/'+key.id,'PATCH',{name:form.get('name')});$('.modal-close',el).click();await reload();});
  });
  host.querySelectorAll('[data-passkey-delete]').forEach(button=>button.onclick=()=>{
   const key=data.items.find(item=>item.id===button.dataset.passkeyDelete),el=ctx.modal(`<h2>${tHtml("common:deletePasskey")}</h2><p>${esc(key.name)}</p><p class="hint">${tHtml("common:thisKeyWillNoLongerOpenTheAccount")}</p><form data-passkey-delete-form><label>${tHtml("common:currentPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>${factor()}<button type="submit" class="primary">${tHtml("common:deleteKey")}</button></form>`);
   formBind($('[data-passkey-delete-form]',el),async form=>{await ctx.api('/security/passkeys/'+key.id+'/delete','POST',Object.fromEntries(form));$('.modal-close',el).click();await reload();});
  });
 }
 $('[data-passkey-add]',host).onclick=()=>{
  const el=ctx.modal(`<h2>${tHtml("common:addPasskey")}</h2><p class="hint">${tHtml("common:confirmAccountSettingsThenSaveTheKeyIn")}</p><form data-passkey-register><label>${tHtml("common:keyName")}<input name="name" maxlength="80" required placeholder="${tHtml("common:forExampleIphone")}" value="${tHtml('common:myPasskey')}"></label><label>${tHtml("common:currentPassword")}<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>${factor()}<button type="submit" class="primary">${tHtml("common:createPasskey")}</button><p data-passkey-register-status class="hint" role="status"></p></form>`);
  const controller=new AbortController(),observer=new MutationObserver(()=>{if(!el.isConnected){controller.abort();observer.disconnect();}});observer.observe(document.body,{childList:true});
  formBind($('[data-passkey-register]',el),async form=>{
   try{
    $('[data-passkey-register-status]',el).textContent=tText("common:confirmKeyCreationOnTheDevice");
    const start=await ctx.api('/security/passkeys/options','POST',Object.fromEntries(form));if(!el.isConnected)return;
    const credential=await navigator.credentials.create({publicKey:options(start.options),signal:controller.signal});if(!credential||!el.isConnected)return;
    await ctx.api('/security/passkeys/verify','POST',{credential:serializeCredential(credential),csrf:start.ceremony_csrf});
    $('.modal-close',el).click();await reload();ctx.toast(tText("common:passkeyAdded"));
   }catch(error){if(el.isConnected)$('[data-passkey-register-status]',el).textContent=message(error);}
  });
 };
 reload().catch(error=>{if(host.isConnected)$('[data-passkey-list]',host).textContent=error.message;});
}
