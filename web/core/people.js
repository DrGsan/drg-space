import {tText,tHtml,locale} from "./i18n.js";
import {esc} from './ui.js';
import {cachedAvatar,currentAvatar} from './avatar-cache.js';
import {personName,rememberPeople,knownPerson} from './person-names.js';
export {personName,personSearch,personNickname} from './person-names.js';
export function isPersonOnline(person,now=Date.now()/1000){return !!person?.online&&(!person.last_seen||Math.max(0,now-person.last_seen)<90);}
export function presenceText(person,now=Date.now()/1000,{precise=false}={}){
  if(isPersonOnline(person,now))return tText("common:online");
  if(precise)return person?.last_seen?tText("common:wasOnline", {value0:(new Date(person.last_seen*1000).toLocaleString(locale(),{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}))}):tText("common:noActivityData");
  const age=person?.last_seen?Math.max(0,now-person.last_seen):Infinity;
  if(age<3600)return tText("common:was", {value0:(new Intl.RelativeTimeFormat(locale(),{numeric:'always'}).format(-Math.max(1,Math.floor(age/60)),'minute'))});
  if(age<86400)return tText("common:recently");
  return tText("common:offline");
}
export function personAvatar(person){
  rememberPeople([person]);
  const avatar=currentAvatar(person);
  return `<span class="person-avatar" data-person-avatar="${esc(person?.id||'')}" aria-label="${tHtml("common:avatar", {value0:(esc(personName(person)))})}"><span>${esc(personName(person).slice(0,1).toUpperCase())}</span>${person?.id?`<span class="person-online-dot" data-person-online="${esc(person.id)}" aria-label="${tHtml("common:online")}" ${isPersonOnline(person)?'':'hidden'}></span>`:''}${avatar?`<img data-avatar-source="${esc(avatar)}" ${cachedAvatar(avatar)?`src="${esc(cachedAvatar(avatar))}"`:''} alt="">`:''}</span>`;
}
export function personPresence(person,{precise=false}={}){return `<span class="person-presence ${isPersonOnline(person)?'is-online':''}" data-person-presence="${esc(person.id)}" ${precise?'data-presence-format="exact"':''} title="${person.last_seen?esc(new Date(person.last_seen*1000).toLocaleString(locale())):''}">${esc(presenceText(person,Date.now()/1000,{precise}))}</span>`;}
document.addEventListener('error',e=>{if(e.target instanceof HTMLImageElement&&e.target.closest('.person-avatar'))e.target.remove();},true);

export function personLabel(person){rememberPeople([person]);const p=knownPerson(person);return p.id?`<span data-person-label="${esc(p.id)}">${esc(personName(p))}</span>`:esc(personName(p));}
