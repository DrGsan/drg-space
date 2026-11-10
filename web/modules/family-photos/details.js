import {tText,tHtml,locale} from "../../core/i18n.js";
import {esc} from '../../core/ui.js';
export function telegramPerson(person){
 const name=person.name||person.sender||tText("family_photos:telegramParticipant"),initials=name.replace(/^@/,'').split(/\s+/).slice(0,2).map(word=>Array.from(word)[0]||'').join('').toLocaleUpperCase(locale());
 return `<span class="person-avatar fp-sender-avatar" aria-label="${tHtml("family_photos:avatar", {value0:(esc(name))})}"><span>${esc(initials||'?')}</span>${person.avatar_url?`<img data-telegram-avatar="${esc(person.avatar_url)}" alt="" hidden>`:''}</span>`;
}
export function customEmoji(entry,fallback='✨'){
 if(entry.state!=='ready')return esc(fallback);
 if(entry.animation_url)return `<span class="fp-inline-emoji" data-fp-animation="emoji-${esc(entry.document_id)}-${entry.offset||0}" data-animation-url="${esc(entry.animation_url)}" data-emoji-fallback="${esc(fallback)}" role="img" aria-label="${esc(fallback)}">${esc(fallback)}</span>`;
 const source=entry.thumbnail_url||entry.url;
 return source?`<img class="fp-inline-emoji" src="${esc(source)}" alt="${esc(fallback)}" loading="lazy">`:esc(fallback);
}
export function telegramText(message){
 const text=message.text||'';let offset=0,result='';
 for(const entry of [...(message.custom_emojis||[])].slice(0,200).sort((a,b)=>a.offset-b.offset)){
   if(!Number.isInteger(entry.offset)||!Number.isInteger(entry.length)||entry.offset<offset||entry.length<1||entry.offset+entry.length>text.length)continue;
   result+=esc(text.slice(offset,entry.offset))+customEmoji(entry,text.slice(entry.offset,entry.offset+entry.length));offset=entry.offset+entry.length;
 }
 return result+esc(text.slice(offset));
}
export function telegramAvatars(signal){
 const cached=new Map();let closed=false;signal?.addEventListener('abort',()=>destroy(),{once:true});
 function hydrate(host){
   host.querySelectorAll('img[data-telegram-avatar]').forEach(img=>{
     const address=img.dataset.telegramAvatar;let url;
     try{url=new URL(address,location.origin);if(url.origin!==location.origin||!/^\/api\/family-photos\/groups\/[^/]+\/people\/-?\d{1,20}\/avatar$/.test(url.pathname))return;}catch{return;}
     if(!cached.has(address))cached.set(address,fetch(address,{credentials:'same-origin',signal,redirect:'error'}).then(async response=>{if(!response.ok)throw Error();const blob=await response.blob();if(blob.size>2*1024*1024||!['image/jpeg','image/png','image/webp'].includes(blob.type))throw Error();if(closed)return null;return URL.createObjectURL(blob);}).catch(()=>{setTimeout(()=>{if(!closed)cached.delete(address);},30000);return null;}));
     cached.get(address).then(src=>{if(src&&!closed&&img.isConnected&&img.dataset.telegramAvatar===address){img.onload=()=>{img.hidden=false;};img.src=src;if(img.complete&&img.naturalWidth)img.hidden=false;}});
   });
 }
 function destroy(){if(closed)return;closed=true;for(const promise of cached.values())promise.then(url=>{if(url)URL.revokeObjectURL(url);});cached.clear();}
 return {hydrate,destroy};
}
