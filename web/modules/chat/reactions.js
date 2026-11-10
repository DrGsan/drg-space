import {tText,tHtml} from "../../core/i18n.js";
import {esc} from '../../core/ui.js';
import {personAvatar,personName} from '../../core/people.js';
import {telegramPerson,customEmoji} from '../family-photos/details.js';
import './reactions.css';
export const reactionEmojis=['👍','❤️','😂','🔥','👏','😢','😮','🎉','🤔','👀','✅','🙏','😁','😍','💯','🤝','🥰','💔','😎','😡','👎','👌','😱','🫡'];
function groups(message,me){
 const result=new Map();
 for(const row of [...(message.reactions||[]),...(message.site_reactions||[])]){
   const emoji=reactionEmojis.find(value=>value.replaceAll('\uFE0F','')===(row.emoji||'').replaceAll('\uFE0F',''))||row.emoji;
   const id=row.document_id?'custom:'+row.document_id:emoji;if(!id)continue;
   let group=result.get(id);if(!group){group={id,emoji:emoji||'✨',asset:row.custom_emoji,count:0,people:[],mine:false};result.set(id,group);}
   group.count+=row.count||0;
   for(const person of row.people||[]){const source=row.source||'site';if(group.people.some(previous=>previous.source===source&&previous.id===person.id))group.count=Math.max(0,group.count-1);else group.people.push({...person,source});}
   if(row.source!=='telegram'&&(row.people||[]).some(person=>person.id===me))group.mine=true;
 }
 return [...result.values()];
}
const label=person=>person.source==='telegram'?person.name||tText("chat:telegramParticipant"):personName(person);
const avatar=person=>person.source==='telegram'?telegramPerson(person):personAvatar(person);
export function reactionsMarkup(message,me,{readOnly=false}={}){
 return `<div class="chat-reactions">${groups(message,me).map(group=>`<span class="chat-reaction ${group.mine?'is-mine':''}"><button type="button" ${readOnly?'data-reaction-people':'data-reaction-toggle'}="${esc(group.id)}" ${readOnly?'':`aria-pressed="${group.mine}"`} aria-label="${tHtml("chat:reaction", {value0:(esc(group.emoji)), value1:(group.count)})}" title="${esc(group.people.map(label).join(', ')||tText("chat:telegramReaction"))}">${group.asset?customEmoji(group.asset,group.emoji):esc(group.emoji)}${group.people.length?'':`<span>${group.count}</span>`}</button>${group.people.length?`<button type="button" class="chat-reaction-people" data-reaction-people="${esc(group.id)}" aria-label="${tHtml("chat:whoReactedWith", {value0:(esc(group.emoji))})}">${group.people.slice(0,3).map(avatar).join('')}</button>`:''}</span>`).join('')}</div>`;
}
export function bindReactions(host,ctx,{getMessage,endpoint,onChange,onPeople,canReact=()=>true,telegramMirror=()=>false}={}){
 const events=new AbortController(),pending=new Set();let picker;
 function close(){picker?.remove();picker=null;}
 function people(message,id){
   const group=groups(message,ctx.state.me.id).find(entry=>entry.id===id);if(!group)return;
   const dialog=ctx.modal(`<h2>${tHtml("chat:reaction2", {value0:(esc(group.emoji))})}</h2><div class="reaction-person-list">${group.people.map(person=>`<div>${avatar(person)}<span><strong>${esc(label(person))}</strong><small>${person.source==='telegram'?'Telegram':'DrG Space'}</small></span></div>`).join('')}${group.count>group.people.length?`<p class="hint">${tHtml("chat:moreTelegramDidNotSendTheFullList", {value0:(group.count-group.people.length)})}</p>`:''}</div>`);onPeople?.(dialog);
 }
 async function toggle(message,emoji){
   if(!canReact()){close();return;}
   if(emoji.startsWith('custom:')){people(message,emoji);return;}
   if(pending.has(message.id))return;pending.add(message.id);close();
   try{const data=await ctx.api(endpoint(message.id),'POST',{emoji});if(host.isConnected)onChange(message.id,data);}catch(error){ctx.toast(error.message);}finally{pending.delete(message.id);}
 }
 function open(message,anchor){
   if(!canReact())return;
   close();picker=document.createElement('div');picker.className='chat-reaction-picker';picker.setAttribute('role','dialog');picker.setAttribute('aria-label',tText("chat:chooseAReaction"));
   picker.innerHTML=`<div>${reactionEmojis.map(emoji=>`<button type="button" data-choose-reaction="${emoji}" aria-label="${tHtml("chat:reactWith", {value0:(emoji)})}">${emoji}</button>`).join('')}</div><small>${endpoint(message.id).startsWith('/family-photos/')?(telegramMirror()?tText("chat:theTelegramBotShowsTheMostPopularAvailable"):tText("chat:siteReactionsNotSentToTelegram")):tText("chat:youCanChooseUpToThreeReactions")}</small>`;document.body.append(picker);
   const rect=anchor.getBoundingClientRect();picker.style.left=Math.max(8,Math.min(rect.right-picker.offsetWidth,innerWidth-picker.offsetWidth-8))+'px';picker.style.top=Math.max(8,Math.min(rect.bottom+5,innerHeight-picker.offsetHeight-8))+'px';
   picker.onkeydown=e=>{if(!['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const buttons=[...picker.querySelectorAll('button')],i=buttons.indexOf(document.activeElement),step=e.key==='ArrowDown'?8:e.key==='ArrowUp'?-8:e.key==='ArrowLeft'?-1:1;buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+step+buttons.length)%buttons.length]?.focus();};
   picker.querySelectorAll('button').forEach(button=>button.onclick=()=>toggle(message,button.dataset.chooseReaction));picker.querySelector('button')?.focus();
 }
 host.addEventListener('click',e=>{
   const button=e.target.closest('[data-reaction-add],[data-reaction-toggle],[data-reaction-people]');if(!button)return;e.stopPropagation();
   const article=button.closest('[data-message-id],[data-fp-message-id]'),id=Number(article?.dataset.messageId||article?.dataset.fpMessageId),message=getMessage(id);if(!message)return;
   if(!canReact()){if(!button.hasAttribute('data-reaction-add'))people(message,button.dataset.reactionPeople||button.dataset.reactionToggle);return;}
   if(button.hasAttribute('data-reaction-add'))open(message,button);else if(button.dataset.reactionPeople)people(message,button.dataset.reactionPeople);else if(!button.dataset.reactionToggle.startsWith('custom:')&&!reactionEmojis.includes(button.dataset.reactionToggle))open(message,button);else void toggle(message,button.dataset.reactionToggle);
 },{signal:events.signal});
 document.addEventListener('pointerdown',e=>{if(picker&&!picker.contains(e.target)&&!e.target.closest('[data-reaction-add]'))close();},{signal:events.signal});
 document.addEventListener('keydown',e=>{if(picker&&e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}},{signal:events.signal});
 return {open,menu(message,anchor){if(!canReact())return;return {emojis:['😁','👍','🔥','❤️','😢','😱','💯'],choose:emoji=>toggle(message,emoji),more:()=>open(message,anchor)};},destroy(){close();events.abort();}};
}

export function hasReactions(message){return [...(message.reactions||[]),...(message.site_reactions||[])].some(row=>row.count>0);}
export function reactionFooter(message,me,footer,options){return `<div class="chat-message-bottom">${reactionsMarkup(message,me,options)}${footer}</div>`;}

export function botReactionNotice(message){
 const sync=message.bot_reaction_sync;if(!sync||sync.state==='delivered'&&!sync.error&&!sync.limited)return '';
 const text=sync.limited?tText("chat:theseReactionsAreOnlyAvailableOnTheSite"):sync.error==='message_unavailable'?tText("chat:theMessageIsNotAvailableForReactionIn"):sync.error==='reaction_unavailable'?tText("chat:thisReactionIsNotAvailableInTelegramSelecting"):sync.error==='telegram_pause'?tText("chat:telegramHasPausedSendingTheReactionWeWill"):sync.error?tText("chat:reactionSavedOnTheSiteWeWillRetry"):tText("chat:reactionSavedWaitingToSendToTelegram");
 return `<small class="fp-delivery-status fp-reaction-status" role="status">${text}</small>`;
}
