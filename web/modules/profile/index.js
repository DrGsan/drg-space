import {tText,tHtml} from "../../core/i18n.js";
import {mountAvatarPresets} from '../../core/avatar-presets.js';
import {prepareAvatar,avatarHint} from '../../core/avatar.js';
import {$,esc,formBind} from '../../core/ui.js';
export const displayName=me=>me.settings.profile?.display_name||me.login;
export function avatarMarkup(me){
  const id=me.settings.profile?.avatar_id||me.settings.profile?.avatar_preset;
  return id?`<img src="/api/account/avatar?v=${encodeURIComponent(id)}" alt="${tHtml("profile:avatar", {value0:(esc(displayName(me)))})}">`:esc(displayName(me).slice(0,1).toUpperCase());
}
export function mountProfileSettings(host,ctx){
  host.innerHTML=`<h2>${tHtml("profile:profile")}</h2><div class="profile-avatar avatar">${avatarMarkup(ctx.state.me)}</div><div data-avatar-presets></div><label>${tHtml("profile:avatar2")}<input data-avatar-file type=file accept="image/png,image/jpeg,image/webp,image/gif,image/mpo,.mpo"></label><p class="hint">${tHtml("profile:squareThumbnailForGifTheFirstFrame", {value0:(avatarHint)})}</p><button class="secondary" data-remove-avatar>${tHtml("profile:removeAvatar")}</button><p data-avatar-status role=status class=hint></p>`;
  host.querySelector('h2').insertAdjacentHTML('afterend',`<form data-profile-form><label>${tHtml("profile:displayName")}<input name="display_name" maxlength="80" value="${esc(ctx.state.me.settings.profile?.display_name||'')}" placeholder="${esc(ctx.state.me.login)}"></label><p class="hint">${tHtml("profile:forGreetingsAndNewEveningsLoginToEnter", {value0:(esc(ctx.state.me.login))})}</p><button class="secondary compact" type="submit">${tHtml("profile:saveName")}</button><p data-profile-status class="hint" role="status"></p></form>`);
  if(ctx.state.me.role==='member'){
    host.insertAdjacentHTML('beforeend',`<hr><h3>${tHtml("profile:deleteAccount")}</h3><p class="hint">${tHtml("profile:deleteAfter24HoursYouCanCancelThe")}</p><button type="button" class="text-link danger" data-delete-own-account>${tHtml("profile:deleteMyAccount")}</button>`);
    $('[data-delete-own-account]',host).onclick=()=>{
      const dialog=ctx.modal(`<h2>${tHtml("profile:deleteAccount")}</h2><p>${tHtml("profile:theAccountAndPersonalDataWillBeDeleted")}</p><form data-delete-account><label>${tHtml("profile:yourPassword")}<input type="password" name="password" autocomplete="current-password" required></label><label>${tHtml("profile:enterYourLogin")}<input name="confirmation" autocomplete="off" required></label><button type="submit" class="primary">${tHtml("profile:scheduleDeletion")}</button></form>`);
      formBind($('[data-delete-account]',dialog),async form=>{
        await ctx.api('/account/deletion','POST',Object.fromEntries(form));
        $('.modal-close',dialog).click();ctx.state.me=await ctx.api('/me');await ctx.loadWorkspace();
      });
    };
  }
  const input=$('[data-avatar-file]',host),remove=$('[data-remove-avatar]',host),status=$('[data-avatar-status]',host);
  const presets=mountAvatarPresets($('[data-avatar-presets]',host),ctx,{selected:()=>ctx.state.me.settings.profile?.avatar_preset||'',choose:async preset=>{input.disabled=remove.disabled=true;try{ctx.state.me.settings.profile=await ctx.api('/account/avatar/preset','PUT',{preset});status.textContent=tText("profile:avatarSaved");}finally{input.disabled=false;refresh();}}});
  const refresh=()=>{host.querySelector('.avatar').innerHTML=avatarMarkup(ctx.state.me);const avatar=document.querySelector('.user .avatar');if(avatar)avatar.innerHTML=avatarMarkup(ctx.state.me);const name=document.querySelector('.user strong');if(name)name.textContent=displayName(ctx.state.me);document.querySelectorAll('[data-profile-display-name]').forEach(el=>el.textContent=displayName(ctx.state.me));document.querySelectorAll('[data-profile-avatar]').forEach(el=>el.innerHTML=avatarMarkup(ctx.state.me));remove.disabled=!(ctx.state.me.settings.profile?.avatar_id||ctx.state.me.settings.profile?.avatar_preset);presets.refresh();};
  formBind($('[data-profile-form]',host),async f=>{ctx.state.me.settings.profile=await ctx.api('/account/profile','PUT',{display_name:f.get('display_name')});$('[data-profile-status]',host).textContent=tText("profile:nameSaved");refresh();});
  input.onchange=async()=>{const file=input.files[0];if(!file)return;input.disabled=remove.disabled=true;presets.setBusy(true);status.textContent=tText("profile:loading2");try{const avatar=await prepareAvatar(file);ctx.state.me.settings.profile=await ctx.api('/account/avatar','POST',avatar);status.textContent=tText("profile:avatarSaved");}catch(e){status.textContent=e.message;}finally{input.disabled=false;input.value='';presets.setBusy(false);refresh();}};
  remove.onclick=async()=>{remove.disabled=input.disabled=true;presets.setBusy(true);try{await ctx.api('/account/avatar','DELETE');delete ctx.state.me.settings.profile?.avatar_id;delete ctx.state.me.settings.profile?.avatar_preset;status.textContent=tText("profile:avatarRemoved");}catch(e){status.textContent=e.message;}finally{input.disabled=false;presets.setBusy(false);refresh();}};
  refresh();
}
