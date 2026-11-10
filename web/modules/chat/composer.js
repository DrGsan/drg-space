import {tText,tHtml} from "../../core/i18n.js";
import {chatIcon} from './icons.js';
import {esc} from '../../core/ui.js';

// Keep an already open keyboard when tapping Send. Keyboard navigation can
// still focus the button, and submitting with a closed keyboard does not open it.
export function keepComposerFocus(form,input){
  const keep=event=>{
    if(event.button===0&&event.target.closest('button[type="submit"]')&&document.activeElement===input)event.preventDefault();
  };
  // Cancelling pointerdown also suppresses the tap's click in WebKit.
  form.addEventListener('mousedown',keep);
}

// Native readonly/disabled can dismiss the iOS keyboard. Freeze the pending
// text through input events instead, without changing focus or editability.
export function lockComposer(input){
  const value=input.value,start=input.selectionStart,end=input.selectionEnd;
  const before=event=>event.preventDefault();
  const restore=event=>{
    input.value=value;input.setSelectionRange(start,end);
    event.stopImmediatePropagation();
  };
  input.setAttribute('aria-busy','true');
  input.addEventListener('beforeinput',before);
  input.addEventListener('input',restore,true);
  return ()=>{
    input.removeAttribute('aria-busy');
    input.removeEventListener('beforeinput',before);
    input.removeEventListener('input',restore,true);
  };
}

const emojis=['😀','🙂','😊','😂','🥰','😍','🤔','😎','😭','😅','👍','👎','❤️','🔥','🎉','🙏','👋','✅','☕','🍕','💪','✨','🎂','🌞'];
const iconButton=(name,label,attr='',extra='')=>`<button type="button" class="chat-icon-button ${extra}" ${attr} aria-label="${esc(label)}" title="${esc(label)}">${chatIcon(name)}</button>`;

export function chatComposerMarkup({formId='send-message',textareaId='message-body',telegram=false}={}){
  return `<form id="${esc(formId)}" class="chat-compose ${telegram?'fp-chat-compose':''}" ${telegram?'data-fp-compose hidden':''}>${iconButton('clip',tText("chat:attachFiles"),'data-attach-chat')}<div class="chat-compose-content"><div class="chat-mentions" data-chat-mentions role="listbox" aria-label="${tHtml("chat:conversationParticipants")}" hidden></div><div data-chat-drafts class="chat-drafts"></div><div class="chat-reply-draft" data-chat-reply hidden></div><div class="chat-reply-draft chat-edit-draft" data-chat-edit hidden></div><div class="chat-composer-shell"><label class="sr-only" for="${esc(textareaId)}">${tHtml("chat:message")}</label><textarea id="${esc(textareaId)}" name="${telegram?'text':'body'}" maxlength="${telegram?3500:4000}" rows="1" placeholder="${tHtml("chat:message2")}"></textarea>${iconButton('smile',tText("chat:addEmojis"),'data-chat-emoji')}<div class="chat-emoji-picker" data-chat-emoji-panel hidden>${emojis.map(e=>`<button type="button" data-insert-emoji="${e}" aria-label="${tHtml("chat:insert", {value0:(e)})}">${e}</button>`).join('')}</div></div></div><input data-chat-files type="file" multiple hidden><div class="chat-recording-actions" data-recording-actions><button type="button" class="chat-icon-button chat-recording-hold" data-recording-hold aria-label="${tHtml("chat:voiceMessage")}">${chatIcon('mic')}</button><button type="button" class="chat-icon-button chat-recording-switch" data-recording-switch aria-label="${tHtml("chat:switchToVideoNote")}">${chatIcon('video')}</button></div><button type="submit" class="primary chat-send" aria-label="${tHtml("chat:send")}" title="${tHtml("chat:send")}">${chatIcon('send')}</button><div class="chat-compose-notice"><span data-chat-upload-status role="status"></span><button type="button" class="text-link" data-cancel-chat-upload hidden>${tHtml("chat:cancelUpload")}</button><span class="chat-help">${tHtml("chat:filesUpTo50MbEnterSend")}</span></div></form>`;
}
