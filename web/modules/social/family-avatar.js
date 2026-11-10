import {tText,tHtml} from "../../core/i18n.js";
import {$} from '../../core/ui.js';
import {prepareAvatar,avatarDataUrl,avatarHint} from '../../core/avatar.js';
import {spaceAvatar} from '../../core/space-avatar.js';

export function editFamilyAvatar(ctx,space,saved){
  const photoLabel=(!space.template||space.template==='family')?tText("social:familyPhoto"):tText("social:spacePhoto");
  const dialog=ctx.modal(`<h2>${photoLabel}</h2><form class="family-avatar-form"><button type="button" class="family-avatar-picker" aria-label="${tHtml("social:select", {value0:(photoLabel.toLowerCase())})}">${spaceAvatar(space,ctx.state.me)}</button><input type="file" data-family-photo accept="image/png,image/jpeg,image/webp,image/gif,image/mpo,.mpo" hidden><button type="button" class="text-link" data-family-photo-remove ${space.avatar_url?'':'hidden'}>${tHtml("social:removePhoto")}</button><p class="hint">${avatarHint}</p><p role="status" data-family-photo-status></p><button type="submit" class="primary" disabled>${tHtml("social:save")}</button></form>`);
  const form=$('form',dialog),pick=$('.family-avatar-picker',form),file=$('[data-family-photo]',form),remove=$('[data-family-photo-remove]',form),status=$('[data-family-photo-status]',form),button=$('[type=submit]',form);
  let prepared=null,removed=false,busy=false;
  const update=()=>{pick.disabled=remove.disabled=busy;button.disabled=busy||(!prepared&&!removed);};
  const preview=url=>{
    pick.innerHTML=spaceAvatar({...space,avatar_url:null},ctx.state.me);
    if(url){const img=document.createElement('img');img.src=url;img.alt=tText("social:previewPhoto");pick.querySelector('.person-avatar').replaceChildren(img);}
    remove.hidden=!url;
  };
  pick.onclick=()=>file.click();
  file.onchange=async()=>{
    const selected=file.files[0];file.value='';if(!selected||busy)return;
    busy=true;status.textContent=tText("social:preparingPhoto");update();
    try{const blob=await prepareAvatar(selected),url=await avatarDataUrl(blob);if(!dialog.isConnected)return;prepared=blob;removed=false;preview(url);status.textContent=tText("social:photoReadyToSave");}
    catch(error){if(dialog.isConnected)status.textContent=error.message;}
    finally{busy=false;if(dialog.isConnected)update();}
  };
  remove.onclick=()=>{prepared=null;removed=!!space.avatar_url;preview(null);status.textContent=removed?tText("social:thePhotoWillBeDeletedAfterSaving"):'';update();};
  form.onsubmit=async event=>{
    event.preventDefault();if(busy||(!prepared&&!removed))return;busy=true;status.textContent=tText("social:saving");update();
    try{await ctx.api('/social/spaces/'+space.id+'/avatar',removed?'DELETE':'POST',removed?undefined:prepared);if(!dialog.isConnected)return;$('.modal-close',dialog).click();await saved();}
    catch(error){if(dialog.isConnected)status.textContent=error.message;}
    finally{busy=false;if(dialog.isConnected)update();}
  };
}
