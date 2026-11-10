import {tText,locale} from "../../core/i18n.js";
import {personName,personNickname,personSearch,namedPeople} from '../../core/person-names.js';
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const messageTime = stamp => new Date(stamp * 1000).toLocaleTimeString(locale(), {hour:'2-digit',minute:'2-digit'});
export const messageDay = stamp => new Date(stamp * 1000).toLocaleDateString(locale(), {day:'numeric',month:'long',year:'numeric'});
// Compare calendar days in the device timezone; elapsed hours break at DST.
const calendarDay = date => Date.UTC(date.getFullYear(),date.getMonth(),date.getDate())/86400000;
export function chatDayLabel(stamp,now=Date.now()/1000){
  const date=new Date(stamp*1000),today=new Date(now*1000);
  if(calendarDay(date)===calendarDay(today))return tText("chat:today");
  const parts=new Intl.DateTimeFormat(locale(),{day:'2-digit',month:'long',...(date.getFullYear()===today.getFullYear()?{}:{year:'numeric'})}).formatToParts(date);
  return parts.filter(part=>['day','month','year'].includes(part.type)).map(part=>part.value).join(' ');
}
export function roomDate(stamp,now=Date.now()/1000){
  const date=new Date(stamp*1000),today=new Date(now*1000);
  if(!Number.isFinite(date.getTime()))return '';
  const age=calendarDay(today)-calendarDay(date);
  if(age===0)return messageTime(stamp);
  if(age>0&&age<7)return new Intl.DateTimeFormat(locale(),{weekday:'short'}).format(date);
  const short=String(date.getDate()).padStart(2,'0')+'.'+String(date.getMonth()+1).padStart(2,'0');
  return date.getFullYear()===today.getFullYear()?short:short+'.'+String(date.getFullYear()).slice(-2).padStart(2,'0');
}
export function sameMessageGroup(previous,message){
  if(!previous||!message)return false;
  const sender=message.sender_id||message.sender;
  const elapsed=message.created-previous.created;
  return !!sender&&sender===(previous.sender_id||previous.sender)&&elapsed>=0&&elapsed<300&&
    calendarDay(new Date(previous.created*1000))===calendarDay(new Date(message.created*1000));
}
export const canEditMessage = (message,userId,now=Date.now()/1000) => message.sender_id===userId && now<message.editable_until;
export function messagePreview(message) {
  if (!message) return tText("chat:noMessagesYet");
  return presentMentions(message.body?.trim(),message.mentions||[]) || (message.attachments?.length ? message.attachments.map(file => file.kind==='animation'?'GIF':file.kind==='image'?tText("chat:photo"):file.kind==='video_note'?tText('chat:videoNote'):file.kind==='voice'||file.kind==='audio'?tText('chat:voiceMessage'):file.kind==='video'?tText("chat:video"):file.kind==='sticker'?tText("chat:sticker"):file.name).join(', ') : tText("chat:message"));
}
export function filterRooms(rooms, filter, query='') {
  const needle=query.trim().toLocaleLowerCase(locale());
  return rooms.filter(room => (!needle || `${room.name} ${(room.members||[]).map(person=>personSearch(person)).join(' ')}`.toLocaleLowerCase(locale()).includes(needle)) &&
    (filter==='all'||filter==='unread'&&room.unread>0||filter==='favorites'&&room.favorite||filter==='direct'&&room.kind==='friends'||filter==='groups'&&room.kind==='family'))
    .sort((a,b)=>Number(b.pinned)-Number(a.pinned)||(b.last_message?.created||0)-(a.last_message?.created||0)||(b.last_message?.id||0)-(a.last_message?.id||0)||a.name.localeCompare(b.name,locale()));
}
export function unreadRoomCounts(rooms) {
  const counts={all:0,unread:0,favorites:0,direct:0,groups:0};
  for(const room of rooms){
    if(!Number.isSafeInteger(room.unread)||room.unread<=0)continue;
    counts.all++;counts.unread++;
    if(room.favorite)counts.favorites++;
    if(room.kind==='friends')counts.direct++;
    if(room.kind==='family')counts.groups++;
  }
  return counts;
}
export function messageTextMarkup(text,mentions=[]) {
  // Links are passive: no external preview request and no HTML from messages.
  text=String(text??'');
  const members=new Map((mentions?.length?mentions:namedPeople()).map(p=>[p.login.toLowerCase(),p]));
  const names=[...members.keys()].sort((a,b)=>b.length-a.length).map(name=>name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const mentionPattern=names.length?new RegExp('(?<![\\w@])@('+names.join('|')+')(?![\\w@-]|\\.[\\w.-])','gi'):null;
  const plain=value=>{let result='',offset=0;if(mentionPattern)for(const match of value.matchAll(mentionPattern)){const p=members.get(match[1].toLowerCase());result+=escape(value.slice(offset,match.index))+`<button type="button" class="chat-mention" data-chat-mention="${escape(p.id)}" title="${escape(personName(p))}">${escape(personNickname(p)?'@'+personName(p):match[0])}</button>`;offset=match.index+match[0].length;}return result+escape(value.slice(offset));};
  const pattern=/https?:\/\/[^\s<>"'\u0000-\u001f]+/gi;
  let html='',offset=0;
  for (const match of String(text||'').matchAll(pattern)) {
    const candidate=match[0].replace(/[.,!?;:)\]}]+$/,'');
    html+=plain(text.slice(offset,match.index));
    try {
      const url=new URL(candidate);
      if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw new Error('Invalid link');
      html+=`<a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${escape(candidate)}</a>`;
    } catch { html+=escape(candidate); }
    html+=escape(match[0].slice(candidate.length));
    offset=match.index+match[0].length;
  }
  return html+plain(String(text||'').slice(offset));
}

export function presentMentions(text,mentions=[]){
  const people=new Map((mentions.length?mentions:namedPeople()).map(p=>[p.login?.toLowerCase(),p]).filter(([login])=>login));
  const aliases=[...people.keys()].sort((a,b)=>b.length-a.length).map(login=>login.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  if(!aliases.length)return text||'';
  const pattern=new RegExp('(?<![\\w@])@('+aliases.join('|')+')(?![\\w@-]|\\.[\\w.-])','gi');
  return String(text||'').split(/(https?:\/\/[^\s<>"']+)/gi).map((part,i)=>i%2?part:part.replace(pattern,(raw,login)=>{const person=people.get(login.toLowerCase());return personNickname(person)?'@'+personName(person):raw;})).join('');
}
