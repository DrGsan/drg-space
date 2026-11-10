import {translateStaticPage,locale} from './core/i18n.js';
import {mountPublicLanguage} from './modules/localization/index.js';
translateStaticPage();mountPublicLanguage(document.querySelector('main'));
import {tText} from "./core/i18n.js";
const button=document.querySelector('#continue'),status=document.querySelector('#status');
document.querySelector('#server').textContent=location.host;
const decode=value=>Uint8Array.from(atob(value.replaceAll('-','+').replaceAll('_','/')+'='.repeat((4-value.length%4)%4)),c=>c.charCodeAt(0));
const encode=value=>btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
let ceremony;
try{
 const payload=location.hash.slice(1);history.replaceState(null,'',location.pathname);
 if(payload.length>65536||!/^[A-Za-z0-9_-]+$/.test(payload))throw Error(tText("auth:invalidAttempt"));
 ceremony=JSON.parse(new TextDecoder().decode(decode(payload)));
 if(!/^[A-Za-z0-9_-]{43}$/.test(ceremony.state)||ceremony.options?.rp?.id!==location.hostname||!ceremony.options.challenge||!ceremony.options.user?.id)throw Error(tText("auth:attemptRefersToADifferentServer"));
}catch{ceremony=null;button.disabled=true;status.textContent=tText("auth:startAddingPasskeyInTheDrgAppSettings");}
button.onclick=async()=>{
 if(!ceremony)return;button.disabled=true;status.textContent=tText("auth:confirmKeyCreationOnTheDevice");
 try{
  if(!window.isSecureContext||!navigator.credentials?.create)throw Error(tText("auth:thisDeviceDoesNotSupportCreatingAPasskey"));
  const options=ceremony.options;
  const credential=await navigator.credentials.create({publicKey:{...options,challenge:decode(options.challenge),user:{...options.user,id:decode(options.user.id)},excludeCredentials:(options.excludeCredentials||[]).map(item=>({...item,id:decode(item.id)}))}});
  if(!credential)throw Error(tText("auth:creationCancelled"));
  const proof={id:credential.id,rawId:encode(credential.rawId),type:credential.type,response:{clientDataJSON:encode(credential.response.clientDataJSON),attestationObject:encode(credential.response.attestationObject),transports:credential.response.getTransports?.()||[]},clientExtensionResults:credential.getClientExtensionResults?.()||{}};
  const bytes=new TextEncoder().encode(JSON.stringify(proof));if(bytes.length>65536)throw Error(tText("auth:confirmationIsTooLarge"));
  const callback=new URL('drg-family://passkey-created');callback.searchParams.set('state',ceremony.state);callback.searchParams.set('credential',encode(bytes));
  status.textContent=tText("auth:returningToTheApp");location.replace(callback.href);ceremony=null;
 }catch(error){status.textContent=error.name==='NotAllowedError'?tText("auth:creationCancelledOrConfirmationTimeHasExpiredReturn"):error.message;button.disabled=false;}
};
