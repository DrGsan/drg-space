import {tHtml} from "./i18n.js";
import {esc} from './ui.js';
import './avatar-presets.css';
// The catalog comes from the server so web and native clients share artwork.
export function mountAvatarPresets(host,ctx,{selected=()=>'',choose}){
  let categories=[],category='nature',busy=false;
  const update=()=>{
    if(!host.isConnected)return;
    const items=categories.find(item=>item.id===category)?.items||[];
    host.innerHTML=`<div class="avatar-preset-tabs" role="group" aria-label="${tHtml("common:avatarSelection")}">${categories.map(item=>`<button type="button" class="secondary compact" data-avatar-category="${item.id}" aria-pressed="${item.id===category}">${esc(item.name)}</button>`).join('')}</div><div class="avatar-preset-grid" role="group" aria-label="${tHtml("common:readyAvatars")}">${items.map(item=>`<button type="button" data-avatar-preset="${item.id}" aria-label="${esc(item.name)}" aria-pressed="${selected()===item.id}" ${busy?'disabled':''}><img src="${esc(item.url)}" alt="" loading="lazy" width="80" height="80"><span>${esc(item.name)}</span></button>`).join('')}</div>`;
    host.querySelectorAll('[data-avatar-category]').forEach(button=>button.onclick=()=>{category=button.dataset.avatarCategory;update();});
    host.querySelectorAll('[data-avatar-preset]').forEach(button=>button.onclick=async()=>{busy=true;update();try{await choose(button.dataset.avatarPreset);}catch(error){ctx.toast(error.message);}finally{busy=false;update();}});
  };
  ctx.api('/avatar-presets').then(data=>{categories=data.categories;category=selected().split('-')[0]||'nature';update();}).catch(error=>{if(host.isConnected)host.textContent=error.message;});
  return {refresh:update,setBusy(value){busy=value;update();}};
}
