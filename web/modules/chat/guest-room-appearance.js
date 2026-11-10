import {tText,tHtml} from "../../core/i18n.js";
import {$,esc} from '../../core/ui.js';
import {prepareAvatar,avatarDataUrl,avatarHint} from '../../core/avatar.js';
import {chatIcon} from './icons.js';

export function guestRoomAvatar(room,classes=''){
  return `<span class="guest-room-avatar ${classes}">${room.avatar_url?`<img src="${esc(room.avatar_url)}" alt="" draggable="false">`:chatIcon('users')}</span>`;
}

export function mountGuestRoomEditor(host,ctx,room,{signal,saved}={}){
  host.innerHTML=`<form class="guest-room-editor"><div class="guest-room-editor-main"><div class="guest-room-photo"><button type="button" class="guest-avatar-picker" data-guest-pick-avatar aria-label="${tHtml("chat:selectCallAvatar")}">${guestRoomAvatar(room)}<span class="guest-avatar-edit" aria-hidden="true">${chatIcon('image')}</span></button><input type="file" data-guest-avatar-file accept="image/png,image/jpeg,image/webp,image/gif,image/mpo,.mpo" hidden><button type="button" class="text-link" data-guest-remove-avatar ${room.avatar_url?'':'hidden'}>${tHtml("chat:removePhoto")}</button></div><label>${tHtml("chat:callName")}<input name="title" data-guest-title value="${esc(room.title)}" maxlength="80" required autocomplete="off" placeholder="${tHtml("chat:forExampleFamilyNight")}"></label></div><p class="hint guest-avatar-hint">${avatarHint}</p><div class="guest-room-editor-actions"><button type="submit" class="secondary compact" data-guest-save disabled>${tHtml("chat:save")}</button><span class="hint" data-guest-settings-status role="status"></span></div></form>`;
  const form=$('form',host),input=$('[data-guest-title]',host),file=$('[data-guest-avatar-file]',host),pick=$('[data-guest-pick-avatar]',host),remove=$('[data-guest-remove-avatar]',host),button=$('[data-guest-save]',host),status=$('[data-guest-settings-status]',host);
  let avatar=null,removed=false,preparing=false,pending;
  const dirty=()=>input.value.trim()!==room.title||avatar!==null||removed;
  const update=()=>{
    button.disabled=preparing||!!pending||!dirty();input.disabled=!!pending;pick.disabled=remove.disabled=preparing||!!pending;
    host.closest('[data-guest-invitation]').querySelectorAll('[data-guest-enter],[data-guest-copy]').forEach(b=>b.disabled=preparing||!!pending);
  };
  const preview=url=>{pick.innerHTML=guestRoomAvatar({avatar_url:url})+`<span class="guest-avatar-edit" aria-hidden="true">${chatIcon('image')}</span>`;remove.hidden=!url;};
  input.oninput=()=>{status.textContent=dirty()?tText("chat:changesNotSaved"):'';update();};
  pick.onclick=()=>file.click();
  file.onchange=async()=>{
    const selected=file.files[0];file.value='';if(!selected)return;
    preparing=true;status.textContent=tText("chat:preparingPhoto");update();
    try{
      const data=await avatarDataUrl(await prepareAvatar(selected));if(!host.isConnected)return;
      avatar=data.split(',')[1];removed=false;preview(data);status.textContent=tText("chat:changesNotSaved");
    }catch(error){if(host.isConnected)status.textContent=error.message;}
    finally{preparing=false;if(host.isConnected)update();}
  };
  remove.onclick=()=>{avatar=null;removed=!!room.avatar_url;preview(null);status.textContent=dirty()?tText("chat:changesNotSaved"):'';update();};
  async function save(){
    if(pending)return pending;if(preparing)return false;if(!dirty())return true;
    if(!form.reportValidity())return false;
    pending=(async()=>{
      status.textContent=tText("chat:saving");
      try{
        const result=await ctx.api('/calls/guest-rooms/'+room.id,'PUT',{title:input.value.trim(),...(avatar!==null?{avatar}:{}),remove_avatar:removed},true,{}, {signal,timeoutMs:12000});
        if(!host.isConnected)return false;
        Object.assign(room,result);input.value=room.title;avatar=null;removed=false;preview(room.avatar_url);status.textContent=tText("chat:saved");saved?.(room);document.dispatchEvent(new Event('drg-guest-call-change'));return true;
      }catch(error){if(host.isConnected)status.textContent=error.message;return false;}
    })();update();
    try{return await pending;}finally{pending=null;if(host.isConnected)update();}
  }
  form.onsubmit=e=>{e.preventDefault();save();};
  return {save,get dirty(){return dirty();}};
}
