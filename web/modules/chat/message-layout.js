import {formatNumber} from '../../core/i18n.js';
import {tText,tHtml} from "../../core/i18n.js";
import {esc,date} from '../../core/ui.js';
import {chatIcon} from './icons.js';
import {messageTime} from './format.js';
import {voiceMarkup,videoNoteMarkup} from './recording-playback.js';

export const attachmentSize=bytes=>bytes<1024*1024?tText("chat:kb", {value0:(Math.ceil(bytes/1024))}):tText("chat:mb", {value0:(formatNumber((bytes/1024/1024),{minimumFractionDigits:1,maximumFractionDigits:1}))});
export function attachmentMarkup(file){
  if(file.kind==='video_note')return videoNoteMarkup(file);
  if(['audio','voice'].includes(file.kind))return voiceMarkup(file);
  return `<div class="chat-attachment" data-chat-attachment-id="${esc(file.id)}"><a href="${esc(file.url)}${file.url.includes('?')?'&':'?'}download=1" download><span>↓ ${esc(file.name)}</span><small>${attachmentSize(file.size)}</small></a></div>`;
}

// Native rooms and Telegram history share the same timestamp/receipt markup.
export function messageFooter(message,mine=false,read=false){
  return `<span class="chat-message-footer">${message.edited?`<span class="chat-message-edited" title="${esc(tText("chat:modified2", {value0:(date(message.edited))}))}">${tHtml("chat:modified")}</span>`:''}<time datetime="${new Date(message.created*1000).toISOString()}" title="${esc(date(message.created))}">${messageTime(message.created)}</time>${mine?`<span title="${read?tText("chat:read"):tText("chat:sentToServer")}" aria-label="${read?tText("chat:read"):tText("chat:sent")}" class="${read?'is-read':''}">${chatIcon(read?'checks':'check')}</span>`:''}</span>`;
}
