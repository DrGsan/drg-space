import {tText,tHtml,locale} from "../../core/i18n.js";
import {messageFooter as footer,attachmentMarkup,attachmentSize as sizeLabel} from './message-layout.js';
import {reactionsMarkup,bindReactions,hasReactions,reactionFooter} from './reactions.js';
import {inlineVideos} from './video-preview.js';
import {editNickname} from '../../core/nickname-editor.js';
import {deletionScreen} from '../social/lifecycle.js';
import {mobileApp} from '../../core/mobile.js';
import {mentionQuery,mentionCandidates} from './mentions.js';
import {readSession,writeSession} from '../../core/navigation.js';
import {roomMuted} from './notification-settings.js';
import {watchTelegramRooms,mountTelegramChat,telegramAvatar,openFamilyViewer} from '../family-photos/index.js';
import './workspace.css';
import './media.css';
import {albumMarkup,mediaFrame,visualMedia,fitAlbums,prepareVideos} from './media.js';
import {bindChatViewport} from './viewport.js';
import {keepComposerFocus,lockComposer,chatComposerMarkup} from './composer.js';
import {guestInvitation} from './guest-calls.js';
import {guestRoomAvatar} from './guest-room-appearance.js';
import {callsController} from './calls.js';
import {callDate,callLabel,callDuration} from './call-format.js';
import {previewFile} from './uploads.js';
import {personName,personAvatar,personPresence,personLabel,personSearch} from '../../core/people.js';
import {$,esc,date,formBind} from '../../core/ui.js';
import {connectRoom} from '../../core/realtime.js';
import {inboxController} from '../notifications/index.js';
import {chatIcon} from './icons.js';
import {bindMessagePress,messageMenu} from './message-menu.js';
import {attachmentMenu} from './attachment-menu.js';
import {cameraCapture} from './camera.js';
import {voiceRecording,recordingActions} from './voice-recording.js';
import {renderMarkup} from '../../core/dom.js';
import {edgePagination} from '../../core/edge-pagination.js';
import {spaceAvatar} from '../../core/space-avatar.js';
import {filterRooms,unreadRoomCounts,messageDay,chatDayLabel,roomDate,sameMessageGroup,messagePreview,messageTextMarkup,presentMentions,canEditMessage} from './format.js';

let active;
export const chatController={destroy(){active?.destroy();active=null;},async flush(){return active?.flush()??true;}};
const chatTimers=[[0,tText("chat:never")],[86400,tText("chat:label1Day")],[604800,tText("chat:label1Week")],[2592000,tText("chat:label1Month")]];
const maxFile=50*1024*1024;
const iconButton=(name,label,attr='',extra='')=>`<button type="button" class="chat-icon-button ${extra}" ${attr} aria-label="${esc(label)}" title="${esc(label)}">${chatIcon(name)}</button>`;
export async function mountChat(host,ctx,{listOnly=true}={}){chatController.destroy();active=new ChatWorkspace(host,ctx);active.listOnly=listOnly;if(listOnly&&mobileApp())active.tab='chats';await active.init();}
class ChatWorkspace {
  constructor(host,ctx){this.host=host;this.ctx=ctx;this.disposed=false;this.sequence=0;this.roomRevision=0;this.messages=[];this.spaces=[];this.telegramSpaces=[];this.filter='all';this.query='';this.tab=readSession('drg-chat-tab:'+ctx.state.me.id)||'chats';this.callHistory=[];this.last=0;this.oldest=null;this.more=false;this.cleanups=[];this.pending=[];this.drafts=ctx.state.chatDrafts?.userId===ctx.state.me.id?ctx.state.chatDrafts.items:new Map();ctx.state.chatDrafts={userId:ctx.state.me.id,items:this.drafts};this.readSent=new Map();this.boundMedia=new WeakSet();this.mediaPreviews=new Map();this.mediaAbort=new AbortController();this.reads={};this.typingPeers=new Map();this.typingRooms={};}
  async init(){
    this.host.innerHTML=`<div class="chat-shell"><div class="chat-welcome"><p role="status">${tHtml("chat:loadingConversations")}</p></div></div>`;
    try{
      const data=await this.ctx.api('/chat/rooms');if(this.disposed)return;this.spaces=data.items;this.ctx.state.spaces=this.spaces;if(this.sid&&this.spaces.find(s=>s.id===this.sid)?.deletion&&$('#message-body',this.host)){await this.open(this.sid);}
      this.host.innerHTML=`<div class="chat-shell"><div class="chat-workspace"><aside class="chat-sidebar" tabindex="-1"><div class="chat-nav-head"><button id="chat-main-menu" class="chat-main-menu" aria-label="${tHtml("chat:mainMenu")}" title="${tHtml("chat:mainMenu")}">${chatIcon('back')}<span>${tHtml("chat:home")}</span></button>${this.ctx.notesInboxButton||''}</div><button type="button" class="chat-push-setup" data-chat-push hidden>${tHtml("chat:enableNotificationsWhenTheAppIsClosed")}</button><div class="chat-sidebar-heading"><h1>${tHtml("chat:messages")}</h1><label class="mobile-chat-section"><span class="sr-only">${tHtml("chat:messagesSection")}</span><select data-mobile-chat-section aria-label="${tHtml("chat:messagesSection")}"><option value="chats">${tHtml("chat:messages")}</option><option value="contacts">${tHtml("chat:contacts")}</option><option value="calls">${tHtml("chat:calls")}</option></select><span aria-hidden="true">⌄</span></label>${iconButton('search',tText("chat:searchConversations"),'data-open-room-search aria-expanded="false" aria-controls="chat-list-search"','mobile-room-search')}${iconButton('compose',tText("chat:newConversation"),'data-new-chat')}</div><label class="chat-search" id="chat-list-search">${chatIcon('search')}<input type="search" data-chat-room-search aria-label="${tHtml("chat:searchConversations")}" placeholder="${tHtml("chat:searchConversations")}" autocomplete="off">${iconButton('close',tText("chat:closeChatSearch"),'data-close-room-search','mobile-room-search')}</label><div class="chat-tabs" role="tablist" aria-label="${tHtml("chat:filterConversations")}">${[['all',tText("chat:all")],['unread',tText("chat:new")],['favorites',tText("chat:favourites")],['direct',tText("chat:personal")],['groups',tText("chat:groups")]].map(([id,title])=>`<button type="button" role="tab" data-chat-filter="${id}" aria-selected="${id==='all'}">${title}</button>`).join('')}</div><div class="chat-room-list" data-chat-rooms role="navigation" aria-label="${tHtml("chat:conversations")}"></div><footer class="chat-sidebar-footer chat-section-tabs" role="tablist" aria-label="${tHtml("chat:messagesSections")}">${[['contacts','contact',tText("chat:contacts")],['calls','phone',tText("chat:calls")],['chats','message',tText("chat:chats")],['settings','settings',tText("chat:settings")]].map(([id,icon,label])=>`<button type="button" role="tab" data-chat-section="${id}" aria-label="${label}" title="${label}" aria-selected="false" ${id==='calls'&&!(callsController.available||callsController.guestAvailable)?'hidden':''}>${chatIcon(icon)}<span>${label}</span></button>`).join('')}</footer></aside><main id="chat-main" class="chat-main"><div class="page-welcome chat-welcome chat-empty-selection"><p>${tHtml("chat:selectWhoYouWantToWriteTo")}</p></div></main></div></div>`;
      this.shell=$('.chat-shell',this.host);const pushButton=$('[data-chat-push]',this.host);this.ctx.push?.state().then(state=>{if(!this.disposed){pushButton.hidden=state.enabled;pushButton.textContent=state.supported&&state.configured?tText("chat:enableNotificationsWhenTheAppIsClosed"):tText("chat:configureNotificationsOnThisDevice");}}).catch(()=>{});pushButton.onclick=async()=>{pushButton.disabled=true;try{await this.ctx.push.enable();pushButton.hidden=true;this.ctx.toast(tText("chat:notificationsOnThisDeviceAreEnabled"));}catch(error){this.ctx.toast(error.message);this.ctx.navigate('settings');}finally{pushButton.disabled=false;}};$('#chat-main-menu',this.host).onclick=()=>this.ctx.navigate('home');
      this.host.querySelectorAll('[data-chat-section]').forEach(b=>b.onclick=()=>this.selectSection(b.dataset.chatSection));$('[data-new-chat]',this.host).onclick=()=>this.tab==='calls'?this.newCall():this.tab==='contacts'?this.addContact():this.newChat();
      $('[data-mobile-chat-section]',this.host).onchange=e=>this.selectSection(e.target.value);
      $('[data-open-room-search]',this.host).onclick=()=>this.listSearch(!this.shell.classList.contains('is-list-searching'),true);$('[data-close-room-search]',this.host).onclick=()=>this.listSearch(false,true);
      $('[data-chat-room-search]',this.host).oninput=e=>{this.query=e.target.value;this.drawRooms();};this.host.querySelectorAll('[data-chat-filter]').forEach(b=>b.onclick=()=>{this.filter=b.dataset.chatFilter;this.drawRooms();});
      const availability=()=>this.syncCallsAvailability();document.addEventListener('drg-call-availability',availability);this.cleanups.push(()=>document.removeEventListener('drg-call-availability',availability));
      this.selectSection(['chats','contacts','calls'].includes(this.tab)?this.tab:'chats');this.syncCallsAvailability();inboxController.attach(this.ctx);
      const callChanged=()=>{if(this.tab==='calls')this.refreshCalls();};for(const type of ['drg-call-change','drg-guest-call-change']){document.addEventListener(type,callChanged);this.cleanups.push(()=>document.removeEventListener(type,callChanged));}
      const namesChanged=()=>{if(this.disposed)return;this.syncRoomIdentity();this.drawRooms();if($('#chat-messages',this.host))this.drawMessages({preserve:true});this.drawReply();};document.addEventListener('drg-people-names-change',namesChanged);this.cleanups.push(()=>document.removeEventListener('drg-people-names-change',namesChanged));
      const presenceChanged=e=>{if(this.disposed)return;const users=new Map(e.detail.map(p=>[p.id,p]));for(const space of this.spaces)for(const person of space.members)if(users.has(person.id))Object.assign(person,users.get(person.id));if(this.tab==='contacts')this.drawContacts();this.syncRoomIdentity();this.drawTyping();};document.addEventListener('drg-presence-change',presenceChanged);this.cleanups.push(()=>document.removeEventListener('drg-presence-change',presenceChanged));
      const refresh=()=>{if(!this.disposed&&!document.hidden){this.scheduleRooms();this.catchup?.().catch(()=>{});this.markVisibleRead();}};
      for(const [target,type] of [[document,'drg-inbox-change'],[document,'visibilitychange'],[window,'focus'],[window,'online']]){target.addEventListener(type,refresh);this.cleanups.push(()=>target.removeEventListener(type,refresh));}
      this.typingClock=setInterval(()=>{this.drawTyping();this.expireMessages();if(this.roomDateDay!==new Date().toDateString()){this.drawRooms();this.drawMessages({preserve:true});}},1000);this.typingPoll=setInterval(()=>this.refreshTyping(),3000);
      const stopTyping=()=>{if(document.hidden||!document.hasFocus())this.stopTyping();};document.addEventListener('visibilitychange',stopTyping);window.addEventListener('blur',stopTyping);this.cleanups.push(()=>document.removeEventListener('visibilitychange',stopTyping),()=>window.removeEventListener('blur',stopTyping));
      this.poll=setInterval(()=>{if(!document.hidden){this.refreshRooms().catch(()=>{});this.catchup?.().catch(()=>{});}},20000);
      this.cleanups.push(bindChatViewport(this.shell));
      const keydown=e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(matchMedia('(max-width:760px)').matches)this.shell.classList.remove('room-open');if(mobileApp())this.listSearch(true);$('[data-chat-room-search]',this.host).focus();}if(e.key==='Escape'){if(mobileApp()&&!this.shell.classList.contains('room-open')&&this.shell.classList.contains('is-list-searching')){this.listSearch(false,true);return;}if(this.messageMenu){this.closeMessageMenu(true);return;}if(this.editing){this.cancelEdit();return;}this.closeSearch();this.closeMenus();}};this.host.addEventListener('keydown',keydown);this.cleanups.push(()=>this.host.removeEventListener('keydown',keydown));
      const outside=e=>{if(!e.target.closest('[data-chat-menu],[data-chat-menu-panel],[data-chat-emoji],[data-chat-emoji-panel],[data-attach-chat],[data-attach-menu],.chat-message-menu,[data-message-menu]'))this.closeMenus();};document.addEventListener('pointerdown',outside);this.cleanups.push(()=>document.removeEventListener('pointerdown',outside));
      const focused=()=>this.markVisibleRead();this.host.addEventListener('focusin',focused);this.cleanups.push(()=>this.host.removeEventListener('focusin',focused));
      const wanted=this.listOnly?null:this.ctx.state.chatSpace;
      const notificationChange=e=>{this.telegramSpaces=this.telegramSpaces.map(s=>s.id===e.detail?.id?{...s,mute_until:e.detail.mute_until}:s);this.drawRooms();};document.addEventListener('drg-chat-notifications',notificationChange);this.cleanups.push(()=>document.removeEventListener('drg-chat-notifications',notificationChange));
      if(wanted?.startsWith('telegram:')&&this.ctx.moduleEnabled('family_photos'))await this.open(wanted);
      else if(this.spaces.some(s=>s.id===wanted))await this.open(wanted);
      // The optional archive must not delay opening an ordinary conversation.
      watchTelegramRooms(this.ctx,items=>{
        if(this.disposed)return;this.telegramSpaces=items;this.drawRooms();
      }).then(watcher=>{if(this.disposed)watcher?.destroy();else this.telegramRooms=watcher;});
    }catch(e){if(!this.disposed){this.host.innerHTML=`<div class="chat-shell"><div class="chat-welcome"><h2>${tHtml("chat:failedToLoadChats")}</h2><p>${esc(e.message)}</p><button class="secondary" data-chat-retry>${tHtml("chat:retry")}</button><button class="text-link" id="chat-main-menu">${tHtml("chat:mainMenu")}</button></div></div>`;$('[data-chat-retry]',this.host).onclick=()=>mountChat(this.host,this.ctx,{listOnly:this.listOnly});$('#chat-main-menu',this.host).onclick=()=>this.ctx.navigate('home');}}
  }
  stopAudio(){this.host.querySelectorAll("audio").forEach(audio=>{audio.pause();audio.removeAttribute("src");audio.load();});}
  destroy(){this.stopAudio();this.reactions?.destroy();this.previews?.destroy();this.pagination?.destroy();this.attachmentContext?.close();this.cameraCapture?.close();this.voiceRecording?.close();this.recordingActions?.destroy();this.mediaAbort.abort();this.mediaViewer?.destroy();this.telegramRooms?.destroy();this.telegramChat?.destroy();this.stopTyping();clearInterval(this.typingClock);clearInterval(this.typingPoll);this.sendAbort?.abort();this.uploadBatch?.abort();this.closeMessageMenu();this.messagePress?.();this.saveDraft();this.disposed=true;this.sequence++;this.room?.destroy();clearInterval(this.poll);clearTimeout(this.roomTimer);clearTimeout(this.searchTimer);clearTimeout(this.readTimer);clearTimeout(this.catchupTimer);this.cancelDrafts();this.cleanups.forEach(fn=>fn());}
  async flush(){if(this.telegramChat)await this.telegramChat.flush();if(this.sendPromise){try{await this.sendPromise;}catch{return false;}}return true;}
  saveDraft(){const input=$('#message-body',this.host);if(this.sid&&input)this.drafts.set(this.sid,this.editing?{...this.editing.draft,editing:{message:this.editing.message,text:input.value}}:{text:input.value,reply:this.reply});}
  cancelDrafts(){for(const file of this.pending||[])this.ctx.api(`/chat/${this.sid}/attachments/${file.id}`,'DELETE').catch(()=>{});this.pending=[];}
  other(space){return space.members.find(person=>person.id!==this.ctx.state.me.id)||space.members[0]||this.ctx.state.me;}
  avatar(space){if(space.id?.startsWith('telegram:'))return telegramAvatar(space);return spaceAvatar(space,this.ctx.state.me);}
  allRooms(){return [...this.spaces,...this.telegramSpaces];}
  drawRooms(){
    if(this.disposed||!this.shell)return;
    this.roomDateDay=new Date().toDateString();
    for(const s of this.spaces)if(s.kind==='friends')s.name=personName(this.other(s));
    const rooms=this.allRooms(),counts=unreadRoomCounts(rooms);
    this.host.querySelectorAll('[data-chat-filter]').forEach(button=>{
      button.setAttribute('aria-selected',String(button.dataset.chatFilter===this.filter));
      const count=counts[button.dataset.chatFilter]||0;
      let badge=button.querySelector('.chat-tab-unread');
      if(!badge){button.setAttribute('aria-label',button.textContent);badge=document.createElement('span');badge.className='chat-tab-unread';badge.setAttribute('aria-hidden','true');button.append(badge);}
      badge.hidden=!count;
      const text=count>99?'99+':String(count);if(badge.textContent!==text)badge.textContent=text;
      button.setAttribute('aria-description',count?tText("chat:unreadChats", {value0:(count)}):tText("chat:noUnreadChats"));
      button.title=button.getAttribute('aria-label')+(count?(" "+tText("chat:unreadChats2", {value0:(count)})+""):'');
    });
    if(this.tab==='contacts'){this.drawContacts();return;}if(this.tab==='calls'){this.drawCalls();return;}
    const items=filterRooms(rooms,this.filter,this.query),list=$('[data-chat-rooms]',this.host);list.setAttribute('aria-label',tText("chat:conversations"));
    renderMarkup(list,items.map(s=>`<button type="button" class="chat-room ${s.id===this.sid?'active':''}" data-room="${esc(s.id)}" aria-pressed="${s.id===this.sid}" title="${esc(s.name)}"><span class="chat-room-avatar">${this.avatar(s)}</span><span class="chat-room-copy"><span class="chat-room-title"><strong>${esc(s.name)}</strong>${roomMuted(s)?`<span class="chat-room-muted" aria-label="${tHtml("chat:notificationsAreDisabled")}" title="${tHtml("chat:notificationsAreDisabled")}">${chatIcon('bellOff')}</span>`:''}${s.favorite?`<span class="chat-room-star" aria-label="${tHtml("chat:favourites")}">${chatIcon('star')}</span>`:''}</span><span class="chat-room-preview">${s.kind==='family'&&s.last_message?`${esc(personName(s.last_message.sender_profile||{login:s.last_message.sender}))}: `:''}${esc(messagePreview(s.last_message))}</span></span><span class="chat-room-meta">${s.last_message?`<time datetime="${new Date(s.last_message.created*1000).toISOString()}">${roomDate(s.last_message.created)}</time>`:''}${s.unread?`<span class="chat-room-unread" aria-label="${tHtml("chat:unread", {value0:(s.unread)})}">${s.unread>99?'99+':s.unread}</span>`:s.pinned?`<span class="chat-room-pin" aria-label="${tHtml("chat:pinned")}">${chatIcon('pin')}</span>`:''}</span></button>`).join('')||`<div class="chat-list-empty"><p>${this.query?tText("chat:chatNotFound"):this.filter==='favorites'?tText("chat:addChatToFavoritesUsingTheStarIcon"):this.filter==='unread'?tText("chat:noNewMessages"):rooms.length?tText("chat:thereAreNoChatsInThisSectionYet"):tText("chat:addAFriendOrCreateASpace")}</p>${!rooms.length?`<button class="text-link" id="chat-social">${tHtml("chat:peopleAndGroups")}</button>`:''}</div>`);
    list.querySelectorAll('[data-room]').forEach(b=>b.onclick=()=>this.open(b.dataset.room).catch(e=>this.ctx.toast(e.message)));const social=$('#chat-social',this.host);if(social)social.onclick=()=>this.ctx.navigate('social');this.drawTyping();
  }
  listSearch(open,focus=false){
    this.shell.classList.toggle('is-list-searching',open);$('[data-open-room-search]',this.host).setAttribute('aria-expanded',String(open));
    const input=$('[data-chat-room-search]',this.host);if(open){if(focus)input.focus();}else{if(document.activeElement===input)input.blur();input.value='';this.query='';this.drawRooms();if(focus)$('[data-open-room-search]',this.host).focus({preventScroll:true});}
  }
  syncCallsAvailability(){
    const available=callsController.available||callsController.guestAvailable;this.host.querySelectorAll('[data-chat-section="calls"]').forEach(button=>button.hidden=!available);this.host.querySelectorAll('[data-chat-call]').forEach(button=>button.hidden=!callsController.available);
    const option=$('[data-mobile-chat-section] option[value=calls]',this.host);option.disabled=!available;option.hidden=!available;
    if(available&&this.waitingCalls){this.waitingCalls=false;this.selectSection('calls');}
    else if(callsController.checked&&!available){this.waitingCalls=false;if(this.tab==='calls')this.selectSection('chats');else writeSession('drg-chat-tab:'+this.ctx.state.me.id,this.tab);}
  }
  selectSection(tab){
    this.waitingCalls=tab==='calls'&&!(callsController.available||callsController.guestAvailable)&&!callsController.checked;
    if(tab==='calls'&&!(callsController.available||callsController.guestAvailable))tab='chats';
    if(tab==='settings'){this.ctx.navigate('settings');return;}this.tab=tab;if(mobileApp())this.listSearch(false);$('[data-mobile-chat-section]',this.host).value=tab;if(!this.waitingCalls)writeSession('drg-chat-tab:'+this.ctx.state.me.id,tab);this.query='';
    const titles={chats:tText("chat:messages"),contacts:tText("chat:contacts"),calls:tText("chat:recentCalls")},search=$('[data-chat-room-search]',this.host),create=$('[data-new-chat]',this.host);
    $('.chat-sidebar-heading h1',this.host).textContent=titles[tab];search.value='';search.placeholder=tab==='contacts'?tText("chat:contactSearch"):tab==='calls'?tText("chat:callSearch"):tText("chat:searchConversations");search.setAttribute('aria-label',search.placeholder);$('[data-open-room-search]',this.host).setAttribute('aria-label',search.placeholder);
    create.setAttribute('aria-label',tab==='contacts'?tText("chat:addContact"):tab==='calls'?tText("chat:startANewCall"):tText("chat:newConversation"));create.title=create.getAttribute('aria-label');create.innerHTML=chatIcon(tab==='contacts'?'contact':tab==='calls'?'phone':'compose');
    $('.chat-tabs',this.host).hidden=tab!=='chats';this.host.querySelectorAll('[data-chat-section]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.chatSection===tab)));this.drawRooms();if(tab==='calls')this.refreshCalls();
  }
  contacts(){return this.spaces.filter(s=>s.kind==='friends').map(s=>({space:s,person:this.other(s)})).sort((a,b)=>Number(b.person.online)-Number(a.person.online)||personName(a.person).localeCompare(personName(b.person),locale()));}
  drawContacts(){
    const list=$('[data-chat-rooms]',this.host),needle=this.query.trim().toLocaleLowerCase(locale());list.setAttribute('aria-label',tText("chat:contacts"));
    renderMarkup(list,this.contacts().filter(({person})=>personSearch(person).includes(needle)).map(({space,person})=>`<button type="button" class="chat-contact" data-contact-room="${esc(space.id)}">${personAvatar(person)}<span><strong>${esc(personName(person))}</strong>${personPresence(person)}</span></button>`).join('')||`<div class="chat-list-empty"><p>${needle?tText("chat:contactNotFound"):tText("chat:addFriendsToChatAndCall")}</p><button type="button" class="text-link" data-add-contact>${tHtml("chat:addContact")}</button></div>`);
    list.querySelectorAll('[data-contact-room]').forEach(b=>b.onclick=()=>this.open(b.dataset.contactRoom).catch(e=>this.ctx.toast(e.message)));$('[data-add-contact]',list)?.addEventListener('click',()=>this.addContact());
  }
  addContact(){
    const d=this.ctx.modal(`<h2>${tHtml("chat:addContact")}</h2><p class="hint">${tHtml("chat:enterTheUserSLoginTheContactWill")}</p><form data-add-contact-form><label>${tHtml("chat:username")}<input name="login" minlength="3" maxlength="40" required autocomplete="off"></label><button class="primary" type="submit">${tHtml("chat:sendInvitation")}</button></form>`);
    formBind($('[data-add-contact-form]',d),async data=>{await this.ctx.api('/social/friends','POST',{login:data.login.trim()});d.querySelector('.modal-close').click();this.ctx.toast(tText("chat:invitationSent"));});
  }
  async refreshCalls(more=false){
    if(this.callsLoading||this.disposed)return;this.callsLoading=true;try{const [data,guests]=await Promise.all([this.ctx.api('/calls/history'+(more&&this.callHistory.length?'?before='+this.callHistory.at(-1).id:'')),this.ctx.api('/calls/guest-rooms')]);if(this.disposed)return;this.guestRooms=guests.items;this.callHistory=more?[...this.callHistory,...data.items]:data.items;this.callsMore=data.more;this.callsLoaded=true;this.callsError=null;if(this.tab==='calls')this.drawCalls();}catch(error){this.callsError=error.message;if(!this.disposed&&this.tab==='calls')this.drawCalls();}finally{this.callsLoading=false;}
  }
  drawCalls(){
    const list=$('[data-chat-rooms]',this.host),needle=this.query.trim().toLocaleLowerCase(locale());list.setAttribute('aria-label',tText("chat:recentCalls"));
    renderMarkup(list,`<button type="button" class="chat-new-call" data-new-call>${chatIcon('phone')}<span>${tHtml("chat:startANewCall")}</span></button>${(this.guestRooms||[]).map(room=>`<button type="button" class="chat-contact" data-guest-room="${esc(room.id)}">${guestRoomAvatar(room,'person-avatar guest-new-icon')}<span class="guest-room-label"><strong>${esc(room.title)}</strong><small>${tHtml("chat:inTheRoomUntil", {value0:(room.count), value1:(new Date(room.expires*1000).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'}))})}</small></span></button>`).join('')}<div class="chat-call-caption">${tHtml("chat:recentCalls")}</div>${this.callHistory.filter(call=>(personName(call.person)+' '+call.person.login).toLocaleLowerCase(locale()).includes(needle)).map(call=>`<button type="button" class="chat-contact chat-call-row ${!call.outgoing&&['missed','cancelled'].includes(call.status)?'missed':''}" data-recent-call="${esc(call.space_id)}" title="${tHtml("chat:call3", {value0:(esc(personName(call.person)))})}">${personAvatar(call.person)}<span><strong>${esc(personName(call.person))}</strong><small>${chatIcon(call.outgoing?'outgoing':'incoming')}${esc(callLabel(call))}${call.connected&&call.ended?' · '+callDuration(call.ended-call.connected):''}</small></span><time datetime="${new Date(call.created*1000).toISOString()}">${callDate(call.created)}</time></button>`).join('')}${!this.callHistory.length?`<div class="chat-list-empty"><p>${esc(this.callsError||(!this.callsLoaded?tText("chat:loadingCalls"):tText("chat:yourCallHistoryWillAppearHere")))}</p>${this.callsError?`<button type="button" class="text-link" data-retry-calls>${tHtml("chat:retry")}</button>`:''}</div>`:''}${this.callsMore?`<button type="button" class="text-link chat-call-more" data-more-calls>${tHtml("chat:showMore")}</button>`:''}`);
    $('[data-more-calls]',list)?.addEventListener('click',()=>this.refreshCalls(true));
    list.querySelectorAll('[data-guest-room]').forEach(b=>b.onclick=()=>guestInvitation(this.ctx,this.guestRooms.find(room=>room.id===b.dataset.guestRoom)));$('[data-new-call]',list).onclick=()=>this.newCall();$('[data-retry-calls]',list)?.addEventListener('click',()=>this.refreshCalls());list.querySelectorAll('[data-recent-call]').forEach(b=>b.onclick=()=>callsController.start(b.dataset.recentCall));
  }
  newCall(){
    const contacts=callsController.available?this.contacts():[];
    const d=this.ctx.modal(`<h2>${tHtml("chat:startANewCall")}</h2><p class="hint">${tHtml("chat:chooseWhoToCall")}</p><div class="chat-new-rooms"><button type="button" class="chat-room" data-new-guest-call ${!callsController.guestAvailable?'hidden':''}><span class="person-avatar guest-new-icon">${chatIcon('users')}</span><span class="guest-room-label"><strong>${tHtml("chat:guest")}</strong><small>${tHtml("chat:groupCallViaLink12Hours")}</small></span>${chatIcon('phone')}</button>${contacts.map(({space,person})=>`<button type="button" class="chat-room" data-call-contact="${esc(space.id)}">${personAvatar(person)}<strong>${esc(personName(person))}</strong>${chatIcon('phone')}</button>`).join('')||''}</div>${!callsController.guestAvailable&&!contacts.length?`<p class="hint">${tHtml('chat:noAvailableCallContacts')}</p>`:''}`);
    $('[data-new-guest-call]',d).onclick=()=>{d.querySelector('.modal-close').click();guestInvitation(this.ctx);};d.querySelectorAll('[data-call-contact]').forEach(b=>b.onclick=()=>{d.querySelector('.modal-close').click();callsController.start(b.dataset.callContact);});
  }
  scheduleRooms(){clearTimeout(this.roomTimer);this.roomTimer=setTimeout(()=>this.refreshRooms().catch(()=>{}),180);}
  async refreshRooms(){if(this.refreshing||this.disposed)return;this.refreshing=true;const revision=this.roomRevision;try{const data=await this.ctx.api('/chat/rooms');if(this.disposed||revision!==this.roomRevision)return;this.spaces=data.items;this.ctx.state.spaces=this.spaces;if(this.sid&&this.spaces.find(s=>s.id===this.sid)?.deletion&&$('#message-body',this.host)){await this.open(this.sid);}this.drawRooms();if(this.tab==='calls')this.refreshCalls();this.syncPreferenceButton();this.syncRoomIdentity();this.drawTyping();if(this.sid&&!this.sid.startsWith('telegram:')&&!this.spaces.some(s=>s.id===this.sid)){this.closeMessageMenu();this.mediaViewer?.destroy();this.previews?.destroy();this.room?.destroy();const input=$('#message-body',this.host);if(input)input.disabled=true;const status=$('#chat-status',this.host);if(status)status.textContent=tText("chat:accessIsRestricted");}}finally{this.refreshing=false;if(!this.disposed&&revision!==this.roomRevision)this.scheduleRooms();}}
  newChat(){
    const d=this.ctx.modal(`<h2>${tHtml("chat:newConversation")}</h2><p class="hint">${tHtml("chat:selectAContactOrWorkspaceChat")}</p><div class="chat-new-rooms">${filterRooms(this.allRooms(),'all').map(s=>`<button class="chat-room" data-new-room="${esc(s.id)}">${this.avatar(s)}<strong>${esc(s.name)}</strong></button>`).join('')||`<p>${tHtml("chat:noChatsYet")}</p>`}</div><button class="secondary" data-chat-social>${tHtml("chat:peopleAndGroups")}</button>`);
    d.querySelectorAll('[data-new-room]').forEach(b=>b.onclick=()=>{d.querySelector('.modal-close').click();this.open(b.dataset.newRoom).catch(e=>this.ctx.toast(e.message));});$('[data-chat-social]',d).onclick=()=>{d.querySelector('.modal-close').click();this.ctx.navigate('social');};
  }
  backToList(){
    this.stopAudio();this.closeMenus();this.cameraCapture?.close();this.voiceRecording?.close();this.recordingActions?.close();
    this.shell.classList.remove('room-open');
    $('.chat-sidebar',this.host).focus({preventScroll:true});
  }
  async openTelegram(gid){
    this.recordingActions?.destroy();this.voiceRecording?.close();this.cameraCapture?.close();
    this.previews?.destroy();this.previewHost=null;this.reactions?.destroy();this.reactionHost=null;
    if(this.disposed||!(await this.flush()))return;
    this.stopTyping();this.saveDraft();this.uploadBatch?.abort();this.closeMessageMenu();this.cancelDrafts();this.room?.destroy();this.typingPeers.clear();clearTimeout(this.readTimer);
    this.pagination?.destroy();this.telegramChat?.destroy();this.sequence++;this.catchup=null;clearTimeout(this.catchupTimer);clearTimeout(this.searchTimer);
    this.messagePress?.();this.sid='telegram:'+gid;this.ctx.state.chatSpace=this.sid;
    this.shell.classList.add('room-open');this.drawRooms();
    this.mediaViewer?.destroy();
    this.telegramChat=mountTelegramChat($('#chat-main',this.host),this.ctx,gid,()=>this.backToList());
  }
  async open(sid){
    this.stopAudio();
    if(sid.startsWith('telegram:'))return this.openTelegram(sid.slice(9));
    this.telegramChat?.destroy();this.telegramChat=null;
    if(this.disposed)return;if(!this.spaces.find(s=>s.id===sid)?.deletion&&this.sid===sid&&$('#message-body',this.host)&&!$('#message-body',this.host).disabled){this.shell.classList.add('room-open');this.markVisibleRead();return;}
    if(!(await this.flush())||this.disposed)return;this.mediaViewer?.destroy();this.stopTyping();this.saveDraft();this.uploadBatch?.abort();this.closeMessageMenu();this.messagePress?.();this.cancelDrafts();this.room?.destroy();this.typingPeers.clear();this.reads={};clearTimeout(this.searchTimer);clearTimeout(this.catchupTimer);this.catchup=null;const seq=++this.sequence,space=this.spaces.find(s=>s.id===sid);if(!space)return;
    this.pagination?.destroy();this.pagination=null;this.olderRequest=null;
    this.clearedThrough=0;this.retentionCutoff=0;this.sid=sid;this.ctx.state.chatSpace=sid;this.messages=[];this.last=0;this.oldest=null;this.searchQuery='';this.searchItems=[];this.reply=null;this.editing=null;this.retry=null;this.pending=[];this.uploading=false;this.catchingUp=false;this.syncCursor=0;this.wantedCatchup=false;this.shell.classList.add('room-open');this.drawRooms();

    $('#chat-main',this.host).innerHTML=`<header class="chat-header">${iconButton('back',tText("chat:toChatList"),'data-chat-back','chat-back')}<button type="button" class="chat-header-identity" data-chat-info>${this.avatar(space)}<span><h2>${esc(space.name)}</h2><span class="chat-header-subtitle">${space.kind==='friends'?personPresence(this.other(space)):tText("common:memberCount", {count:(space.members.length)})}<span id="chat-status" role="status">${tHtml("chat:connecting3")}</span></span></span></button><div class="chat-header-actions">${space.kind==='friends'?iconButton('phone',tText("chat:call4"),'data-chat-call '+(callsController.available?'':'hidden')):''}${iconButton('search',tText("chat:searchInConversation"),'data-open-chat-search')}${iconButton('star',space.favorite?tText("chat:removeChatFromFavorites"):tText("chat:addChatToFavorites"),`data-chat-favorite aria-pressed="${space.favorite}"`)}${iconButton('more',tText("chat:chatActions"),'data-chat-menu aria-expanded="false"')}<div class="chat-menu" data-chat-menu-panel hidden></div></div></header><p class="chat-retention chat-retention-inline">${tHtml("chat:last3MonthsHistoryOldMessagesAndAttachments")}</p><div class="chat-search-bar" data-conversation-search hidden><label>${chatIcon('search')}<input type="search" data-chat-search aria-label="${tHtml("chat:messageSearch")}" placeholder="${tHtml("chat:searchInConversation")}" maxlength="120" autocomplete="off"></label><span class="chat-search-count" role="status"></span>${iconButton('close',tText("chat:closeSearch"),'data-close-chat-search')}</div><div id="chat-messages" class="chat-messages" role="log" aria-label="${tHtml("chat:messageHistory")}" aria-live="polite"><p class="chat-empty" role="status">${tHtml("chat:loadingMessages")}</p></div>${chatComposerMarkup()}`;
    if(space.deletion){
      $('[data-chat-back]',this.host).onclick=()=>this.backToList();
      const identity=$('[data-chat-info]',this.host);identity.disabled=true;
      $('.chat-header-actions',this.host).replaceChildren();
      $('.chat-retention',this.host).remove();$('[data-conversation-search]',this.host).remove();
      $('#chat-status',this.host).textContent='';
      const composer=$('#message-body',this.host)?.closest('form');composer?.remove();
      // Keep navigation while never requesting or rendering restricted history.
      $('#chat-messages',this.host).setAttribute('role','region');
      deletionScreen($('#chat-messages',this.host),space,this.ctx,async()=>{await this.refreshRooms();await this.open(sid);});
      return;
    }
    $('[data-chat-call]',this.host)?.addEventListener('click',()=>callsController.start(sid));
    $('[data-chat-back]',this.host).onclick=()=>this.backToList();$('[data-chat-info]',this.host).onclick=()=>this.info();$('[data-chat-favorite]',this.host).onclick=()=>this.preference('favorite');$('[data-chat-menu]',this.host).onclick=()=>this.menu();
    $('[data-open-chat-search]',this.host).onclick=()=>{$('[data-conversation-search]',this.host).hidden=false;$('[data-chat-search]',this.host).focus();};$('[data-close-chat-search]',this.host).onclick=()=>this.closeSearch();$('[data-chat-search]',this.host).oninput=e=>{clearTimeout(this.searchTimer);this.searchSequence=(this.searchSequence||0)+1;const query=e.target.value;this.searchTimer=setTimeout(()=>this.search(query).catch(e=>this.ctx.toast(e.message)),280);};
    this.bindComposer(seq);const initial=await this.ctx.api(`/chat/${sid}/messages`);if(this.disposed||seq!==this.sequence)return;this.reads=initial.reads||{};this.historyVersion=initial.history_version||0;this.clock={server:initial.server_time,local:performance.now()};this.applyChatState(initial);this.more=initial.more;this.editCursor=initial.edit_cursor;this.syncCursor=initial.items.at(-1)?.id||0;this.merge(initial.items,initial.retention_cutoff,{follow:true});
    // Only complete HTTP history batches advance this cursor. A newer live
    // event or POST acknowledgement must never jump over a missing message.
    this.catchup=async()=>{if(this.disposed||seq!==this.sequence)return;if(this.catchingUp){this.wantedCatchup=true;return this.catchupPromise;}this.catchingUp=true;this.catchupPromise=(async()=>{do{this.wantedCatchup=false;let more=true;while(more&&!this.disposed&&seq===this.sequence){const d=await this.ctx.api(`/chat/${sid}/messages?after=${this.syncCursor}&edited_after=${this.editCursor}`);if(this.disposed||seq!==this.sequence)return;if((d.history_version||0)!==(this.historyVersion||0)){await this.reloadHistory(seq);break;}this.applyChatState(d);this.updateReads(d.reads);this.syncCursor=Math.max(this.syncCursor,d.items.at(-1)?.id||0);this.merge(d.items,d.retention_cutoff);this.merge(d.edits||[],null,{editsOnly:true});this.editCursor=Math.max(this.editCursor,d.edit_cursor||0);more=d.more||d.edit_more;}}while(this.wantedCatchup&&!this.disposed&&seq===this.sequence);})();try{await this.catchupPromise;}finally{if(seq===this.sequence){this.catchingUp=false;this.catchupPromise=null;}}};
    this.room=connectRoom(`chat/${sid}`,this.ctx,{validate:()=>this.catchup(),onStatus:status=>{if(!this.disposed&&seq===this.sequence){$('#chat-status',this.host).textContent=status==='online'?'':tText("chat:reconnecting");if(status==='online'&&document.activeElement===$('#message-body',this.host))this.sendTyping();}},onMessage:d=>{if(this.disposed||seq!==this.sequence)return;if(d.type==='chat_state'){this.applyChatState(d);if((d.history_version||0)!==(this.historyVersion||0))this.catchup().catch(e=>this.ctx.toast(e.message));}if(d.type==='reads')this.updateReads(d.reads);if(d.type==='presence'){if(d.state?.user?.id!==this.ctx.state.me.id&&d.state?.until>this.messageNow())this.typingPeers.set(d.client_id,d.state);else this.typingPeers.delete(d.client_id);this.drawTyping();}if(d.type==='ready')this.catchup().catch(()=>{});if(d.type==='message'||d.type==='message_edited'){this.merge([d.message],null,{editsOnly:d.type==='message_edited'});this.scheduleRooms();this.catchup().catch(()=>{});}},onError:e=>{if(this.disposed||seq!==this.sequence)return;if([401,403,404].includes(e.status)){this.closeMessageMenu();this.mediaViewer?.destroy();this.previews?.destroy();this.room?.destroy();$('#chat-status',this.host).textContent=tText("chat:accessIsRestricted");$('#message-body',this.host).disabled=true;}this.ctx.toast(e.message);}});
    this.pagination=edgePagination($('#chat-messages',this.host),{root:$('#chat-messages',this.host),canLoad:()=>!this.disposed&&seq===this.sequence&&!this.olderRequest&&!this.messageMenu&&(this.searchQuery?this.searchMore:this.more),load:()=>this.older(),onError:e=>this.ctx.toast(e.message)});this.pagination.update();
    this.bindMessageMenu();$('#chat-messages',this.host).onscroll=()=>{clearTimeout(this.readTimer);this.readTimer=setTimeout(()=>this.markVisibleRead(),180);};this.markVisibleRead();
  }
  async reloadHistory(seq=this.sequence){
    const sid=this.sid,first=this.messages[0]?.id;
    let page=await this.ctx.api(`/chat/${sid}/messages`),items=[...page.items],more=page.more;
    const version=page.history_version||0;
    while(first&&more&&items[0]?.id>first){
      const older=await this.ctx.api(`/chat/${sid}/messages?before=${items[0].id}`);
      if(this.disposed||seq!==this.sequence)return;
      if((older.history_version||0)!==version){this.wantedCatchup=true;return;}
      if(!older.items.length)break;
      items=[...older.items,...items];more=older.more;
    }
    if(this.disposed||seq!==this.sequence)return;
    this.closeMessageMenu();this.historyVersion=version;this.messages=[];this.searchItems=[];
    this.more=more;this.editCursor=page.edit_cursor||0;this.syncCursor=page.items.at(-1)?.id||0;
    this.reads=page.reads||{};this.applyChatState(page);
    if(this.reply&&!items.some(m=>m.id===this.reply.id)){this.reply=null;this.retry=null;this.drawReply();}
    if(this.editing&&!items.some(m=>m.id===this.editing.message.id))this.cancelEdit(false);
    this.merge(items,page.retention_cutoff,{preserve:true});
    if(this.searchQuery)await this.search(this.searchQuery);
    this.scheduleRooms();
  }
  deleteMessage(message){
    if(!message||message.sender_id!==this.ctx.state.me.id)return;
    const sid=this.sid,seq=this.sequence;this.closeMenus();
    const dialog=this.ctx.modal(`<h2>${tHtml("chat:deleteMessage")}</h2><p>${tHtml("chat:theMessageAndItsAttachmentsWillBeDeleted")}</p><div class="chat-clear-buttons"><button type="button" class="secondary" data-cancel-delete>${tHtml("chat:cancel")}</button><button type="button" class="danger" data-confirm-delete>${tHtml("chat:deleteForAll")}</button></div>`);
    dialog.querySelector('[data-cancel-delete]').onclick=()=>dialog.querySelector('.modal-close').click();
    dialog.querySelector('[data-confirm-delete]').onclick=async event=>{
      event.currentTarget.disabled=true;
      try{await this.ctx.api(`/chat/${sid}/messages/${message.id}`,'DELETE');dialog.querySelector('.modal-close').click();if(!this.disposed&&seq===this.sequence)await this.catchup();this.scheduleRooms();}
      catch(error){this.ctx.toast(error.message);event.currentTarget.disabled=false;}
    };
  }
  merge(items,cutoff=null,options={}){
    const map=new Map(this.messages.map(m=>[m.id,m])),updates=new Map();
    for(const incoming of items){
      const old=map.get(incoming.id);if(old&&(incoming.revision||0)<(old.revision||0)&&(incoming.reaction_revision||0)<=(old.reaction_revision||0))continue;
      const body=old&&(old.revision||0)>(incoming.revision||0)?old:incoming,reactions=old&&(old.reaction_revision||0)>(incoming.reaction_revision||0)?old:incoming;
      const m={...body,reactions:reactions.reactions||[],reaction_revision:reactions.reaction_revision||0};updates.set(m.id,m);if(!options.editsOnly||old)map.set(m.id,m);
    }
    const refreshReply=m=>{const source=updates.get(m.reply?.id);return source?{...m,reply:{...m.reply,sender:source.sender,sender_profile:source.sender_profile,sender_id:source.sender_id,expires:source.expires,body:source.body.slice(0,240),unavailable:false}}:m;};
    this.retentionCutoff=cutoff??this.retentionCutoff;
    this.messages=[...map.values()].filter(m=>this.messageAlive(m)&&(cutoff===null||m.created>cutoff)).map(refreshReply).sort((a,b)=>a.id-b.id);
    this.searchItems=(this.searchItems||[]).map(m=>{const updated=updates.get(m.id);return refreshReply(updated&&(updated.revision||0)>=(m.revision||0)?updated:m);}).filter(m=>this.messageAlive(m)&&(!this.searchQuery||m.body.toLocaleLowerCase(locale()).includes(this.searchQuery.toLocaleLowerCase(locale()))));
    if(this.editing?.draft.reply&&updates.has(this.editing.draft.reply.id))this.editing.draft.reply=updates.get(this.editing.draft.reply.id);
    if(this.reply&&updates.has(this.reply.id)){this.reply=updates.get(this.reply.id);this.drawReply();}
    this.last=this.messages.at(-1)?.id||0;this.oldest=this.messages[0]?.id;
    const space=this.spaces.find(s=>s.id===this.sid),latest=this.messages.at(-1),previous=space?.last_message,edited=updates.get(previous?.id);
    if(space&&(latest&&latest.id>(previous?.id||0)||edited&&(edited.revision||0)>(previous?.revision||0))){space.last_message=latest&&latest.id>(previous?.id||0)?latest:edited;this.roomRevision++;this.drawRooms();}
    this.drawMessages(options);
  }
  drawMessages({follow=false,preserve=false}={}){
    const list=$('#chat-messages',this.host);if(!list)return;const nearBottom=list.scrollHeight-list.scrollTop-list.clientHeight<100,rect=list.getBoundingClientRect();const anchor=[...list.querySelectorAll('[data-message-id]')].find(el=>el.getBoundingClientRect().bottom>rect.top);const anchorInfo=anchor?{id:anchor.dataset.messageId,offset:anchor.getBoundingClientRect().top-rect.top}:null;const source=this.searchQuery?this.searchItems:this.messages;const items=source.map(m=>({...m,attachments:(m.attachments||[]).map(f=>this.previewAttachment(f))}));let day='';
    const markup=items.map((m,index)=>{const grouped=sameMessageGroup(items[index-1],m),groupEnd=!sameMessageGroup(m,items[index+1]);const nextDay=messageDay(m.created),separator=nextDay!==day?`<div class="chat-day" data-chat-day="${m.id}:${esc(nextDay)}"><time>${esc(chatDayLabel(m.created))}</time></div>`:'';day=nextDay;const mine=m.sender_id===this.ctx.state.me.id||m.sender===this.ctx.state.me.login,direct=this.spaces.find(s=>s.id===this.sid)?.kind==='friends';const profile=this.spaces.find(s=>s.id===this.sid)?.members.find(p=>p.id===m.sender_id)||m.sender_profile||{login:m.sender};const visuals=(m.attachments||[]).filter(visualMedia),documents=(m.attachments||[]).filter(f=>!visualMedia(f));
      return `${separator}<article class="chat-message ${grouped?'grouped':''} ${groupEnd?'group-end':''} ${direct?'is-direct-message':''} ${mine?'mine':''} ${(m.attachments||[]).length===1&&m.attachments[0].kind==='video_note'&&!m.body?'is-circle-only':''} ${visuals.length?'has-visual-media':''} ${visuals.length&&!m.body&&!documents.length&&!m.reply?'media-only':''}" data-message-id="${m.id}" tabindex="0" aria-haspopup="menu">${!mine?`<div class="chat-message-avatar">${personAvatar(profile)}</div><div class="chat-message-meta"><strong>${personLabel(profile)}</strong></div>`:''}${m.reply?`<button type="button" class="chat-reply-quote" data-reply-target="${m.reply.id}" aria-label="${tHtml("chat:goToTheMessageYouRepliedTo")}"><strong>${m.reply.unavailable?tText("chat:messageUnavailable"):personLabel(m.reply.sender_profile||{id:m.reply.sender_id,login:m.reply.sender})}</strong><span>${esc(presentMentions(m.reply.body,m.reply.mentions||[])||tText("chat:attachment"))}</span></button>`:''}${visuals.length&&!m.body&&!documents.length?mediaFrame(albumMarkup(visuals,'data-chat-media-id'),footer(m,mine,this.isRead(m))):albumMarkup(visuals,'data-chat-media-id')}${m.body?`<p class="chat-message-text">${messageTextMarkup(m.body,m.mentions)}${!(m.attachments||[]).length&&!hasReactions(m)?footer(m,mine,this.isRead(m)):''}</p>`:''}<div class="chat-attachments">${documents.map(attachmentMarkup).join('')}</div>${visuals.length&&!m.body&&!documents.length?reactionsMarkup(m,this.ctx.state.me.id):hasReactions(m)?reactionFooter(m,this.ctx.state.me.id,footer(m,mine,this.isRead(m))):!m.body||(m.attachments||[]).length?footer(m,mine,this.isRead(m)):''}</article>`;
    }).join('');
    const changed=renderMarkup(list,`<div class="page-edge" data-page-edge role="status" ${(this.searchQuery?this.searchMore:this.more)?'':'hidden'}></div><div data-chat-search-results>${markup||`<p class="hint chat-empty">${this.searchQuery?this.searchMore?tText("chat:noMatchesFoundInTheViewedHistory"):tText("chat:messagesNotFound"):tText("chat:writeTheFirstMessage")}</p>`}</div>`);
    list.querySelectorAll('[data-reply-target]').forEach(b=>b.onclick=()=>this.jump(Number(b.dataset.replyTarget)).catch(e=>this.ctx.toast(e.message)));
    list.querySelectorAll('[data-chat-mention]').forEach(b=>b.onclick=()=>this.memberInfo(b.dataset.chatMention));
    const allMedia=[...new Map([...this.messages,...items].map(m=>[m.id,m])).values()].sort((a,b)=>a.id-b.id).flatMap(m=>(m.attachments||[]).map(f=>this.previewAttachment(f)).filter(visualMedia).map(f=>({...f,kind:f.kind==='image'?'photo':f.kind,state:'ready',created:m.created,sender:personName(m.sender_profile||{login:m.sender})})));
    if(this.mediaViewer?.isOpen()&&this.viewerFiles?.some(old=>!allMedia.some(f=>f.id===old.id)))this.mediaViewer.destroy();
    list.querySelectorAll('[data-chat-media-id]').forEach(b=>b.onclick=()=>{this.mediaViewer?.destroy();this.viewerFiles=allMedia;this.mediaViewer=openFamilyViewer(this.ctx,allMedia,allMedia.findIndex(f=>f.id===b.dataset.chatMediaId));});
    if(this.reactionHost!==list){this.reactions?.destroy();this.reactionHost=list;this.reactions=bindReactions(list,this.ctx,{getMessage:id=>[...this.messages,...(this.searchItems||[])].find(m=>m.id===id),endpoint:id=>`/chat/${this.sid}/messages/${id}/reactions`,onChange:(id,data)=>this.merge([data.message],null,{editsOnly:true,preserve:true})});}
    fitAlbums(list);if(this.previewHost!==list){this.previews?.destroy();this.previewHost=list;this.previews=inlineVideos(list);}this.previews.sync();this.resolveMedia(source);
    if(changed||follow){this.followMessages=!preserve&&!this.messageMenu&&(follow||(!this.searchQuery&&nearBottom));list.onwheel=list.ontouchstart=list.onkeydown=e=>{if(e.defaultPrevented)return;this.followMessages=false;this.closeMessageMenu();};const mediaReady=()=>{if(this.followMessages&&list.isConnected&&!this.disposed)list.scrollTop=list.scrollHeight;};list.querySelectorAll('img,video').forEach(m=>{if(this.boundMedia.has(m))return;this.boundMedia.add(m);m.addEventListener(m.tagName==='VIDEO'?'loadedmetadata':'load',mediaReady,{once:true});});}
    if((changed||follow)&&this.followMessages)list.scrollTop=list.scrollHeight;else if(changed&&anchorInfo){const target=list.querySelector(`[data-message-id="${anchorInfo.id}"]`);if(target)list.scrollTop+=target.getBoundingClientRect().top-rect.top-anchorInfo.offset;}clearTimeout(this.readTimer);this.readTimer=setTimeout(()=>this.markVisibleRead(),180);this.pagination?.update();
  }
  previewAttachment(file){return this.mediaPreviews.get(file.id)||file;}
  async resolveMedia(items){
    if(this.resolvingMedia||this.disposed)return;
    const pending=items.flatMap(m=>m.attachments||[]).filter(f=>f.preview_url&&!this.mediaPreviews.has(f.id));
    if(!pending.length)return;
    this.resolvingMedia=true;
    // Check existing originals lazily, with bounded concurrency and the same
    // room/message authorization as downloads. Failed probes stay documents.
    try{
      await Promise.all(Array.from({length:Math.min(3,pending.length)},async()=>{
        while(pending.length&&!this.disposed){
          const file=pending.shift();this.mediaPreviews.set(file.id,null);
          try{const checked=await this.ctx.api(file.preview_url.replace(/^\/api/,''),'GET',undefined,true,{}, {signal:this.mediaAbort.signal});if(!this.disposed)this.mediaPreviews.set(file.id,checked);}
          catch{/* The document link retains its normal permission checks. */}
        }
      }));
    }finally{this.resolvingMedia=false;if(!this.disposed)this.drawMessages();}
  }
  older(){
    const seq=this.sequence,sid=this.sid,query=this.searchQuery;
    if(this.olderRequest?.seq===seq&&this.olderRequest.query===query)return this.olderRequest.promise;
    if(this.disposed||!(query?this.searchMore:this.more))return Promise.resolve();
    const task={seq,query};this.olderRequest=task;
    task.promise=(async()=>{
      if(query){await this.search(query,true);return;}
      const version=this.historyVersion||0,cursor=this.oldest,data=await this.ctx.api(`/chat/${sid}/messages?before=${cursor}`);
      if(this.disposed||seq!==this.sequence||this.searchQuery!==query||version!==(this.historyVersion||0))return;
      if((data.history_version||0)!==version){await this.catchup();return;}
      this.more=data.more&&data.items.some(m=>m.id<cursor);this.merge(data.items,data.retention_cutoff,{preserve:true});
    })().finally(()=>{if(this.olderRequest===task)this.olderRequest=null;});
    return task.promise;
  }
  async jump(id){this.closeSearch();const seq=this.sequence;let batches=0;while(!this.messages.some(m=>m.id===id)&&this.more&&batches++<10){await this.older();if(this.disposed||seq!==this.sequence)return;}const target=this.host.querySelector(`[data-message-id="${id}"]`);if(!target){this.ctx.toast(tText("chat:messageDeletedOrInAnEarlierHistory"));return;}target.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});target.classList.add('is-highlighted');setTimeout(()=>target.classList.remove('is-highlighted'),1600);}
  async search(query,more=false){query=query.trim();if(!query){this.closeSearch(false);return;}this.searchQuery=query;if(!more)this.searchItems=[];const seq=this.sequence,searchSeq=(this.searchSequence||0)+1;this.searchSequence=searchSeq;$('.chat-search-count',this.host).textContent=tText("chat:searching");const cursor=more&&this.searchBefore?`&before=${this.searchBefore}`:'';const data=await this.ctx.api(`/chat/${this.sid}/search?q=${encodeURIComponent(query)}${cursor}`);if(this.disposed||seq!==this.sequence||searchSeq!==this.searchSequence)return;this.searchItems=(more?[...data.items,...this.searchItems]:data.items).filter(m=>this.messageAlive(m));this.searchMore=data.more;this.searchBefore=data.next_before;$('.chat-search-count',this.host).textContent=tText("chat:found", {value0:(this.searchItems.length), value1:(data.more?(" "+tText("chat:moreHistory")+""):'')});this.drawMessages({preserve:more});}
  closeSearch(hide=true){clearTimeout(this.searchTimer);this.searchSequence=(this.searchSequence||0)+1;this.searchQuery='';this.searchItems=[];const bar=$('[data-conversation-search]',this.host);if(bar){bar.hidden=hide;$('[data-chat-search]',bar).value='';}this.drawMessages({follow:true});}
  isRead(message){const others=this.spaces.find(s=>s.id===this.sid)?.members.filter(p=>p.id!==this.ctx.state.me.id)||[];return others.length>0&&others.every(p=>(this.reads[p.id]||0)>=message.id);}
  updateReads(reads){let changed=false;for(const [uid,id] of Object.entries(reads||{}))if(id>(this.reads[uid]||0)){this.reads[uid]=id;changed=true;}if(changed)this.drawMessages();}
  stopTyping(){clearTimeout(this.typingStop);if(this.typingSent){this.room?.send({type:'presence',state:null});this.typingSent=0;}}
  sendTyping(){const value=$('#message-body',this.host)?.value.trim();if(!value||this.editing||document.hidden){this.stopTyping();return;}if(!this.typingSent||Date.now()-this.typingSent>2500){if(this.room?.send({type:'presence',state:{typing:true}}))this.typingSent=Date.now();}clearTimeout(this.typingStop);this.typingStop=setTimeout(()=>this.stopTyping(),4000);}
  async refreshTyping(){if(this.disposed||document.hidden||this.refreshingTyping)return;this.refreshingTyping=true;try{const data=await this.ctx.api('/chat/typing');if(!this.disposed){this.typingRooms=data.rooms;this.clock={server:data.server_time,local:performance.now()};this.drawTyping();}}catch{}finally{this.refreshingTyping=false;}}
  drawTyping(){
    if(this.disposed)return;for(const [cid,state] of this.typingPeers)if(state.until<=this.messageNow())this.typingPeers.delete(cid);
    const users=[...new Map([...this.typingPeers.values()].map(s=>[s.user.id,s.user])).values()],text=users.length?(users.length===1?tText("chat:typing"):tText("chat:typing2")):'';
    const status=$('#chat-status',this.host);if(status){status.classList.toggle('chat-typing',!!text);if(text)status.textContent='••• '+(users.length>1?users.map(u=>u.name).join(', ')+' ':'')+text;else if(status.classList.contains('was-typing'))status.textContent='';status.classList.toggle('was-typing',!!text);}
    for(const room of this.spaces){const preview=this.host.querySelector(`[data-room="${room.id}"] .chat-room-preview`);if(!preview)continue;const peers=room.id===this.sid&&this.room?.online?users:(this.typingRooms[room.id]||[]).filter(p=>p.until>this.messageNow());preview.classList.toggle('chat-typing',!!peers.length);if(peers.length){preview.textContent='••• '+(peers.length===1?tText("chat:typing"):tText("chat:typing2"));preview.dataset.typing='1';}else if(preview.dataset.typing){delete preview.dataset.typing;preview.textContent=(room.kind==='family'&&room.last_message?personName(room.last_message.sender_profile||{login:room.last_message.sender})+': ':'')+messagePreview(room.last_message);}}
  }
  async markVisibleRead(){
    if(this.disposed||!this.sid||document.hidden||document.querySelector('.modal-overlay,.chat-context-layer.is-open,.voice-call:not(.is-minimized)')||!document.hasFocus()||!this.shell?.classList.contains('room-open')||this.reading)return;const list=$('#chat-messages',this.host);if(!list?.getClientRects().length)return;const rect=list.getBoundingClientRect();const visible=[...list.querySelectorAll('[data-message-id]')].filter(el=>{const r=el.getBoundingClientRect();return r.bottom>rect.top&&r.top<rect.bottom;});const id=Math.max(0,...visible.map(el=>Number(el.dataset.messageId)).filter(id=>id<=this.syncCursor));if(!id||id<=(this.readSent.get(this.sid)||0))return;
    const sid=this.sid;this.reading=true;try{await this.ctx.api(`/chat/${sid}/read`,'POST',{message_id:id});if(this.disposed)return;this.readSent.set(sid,id);document.dispatchEvent(new Event('drg-message-read'));this.scheduleRooms();}catch{}finally{this.reading=false;if(!this.disposed&&this.sid!==sid)this.markVisibleRead();}
  }
  info(){const s=this.spaces.find(s=>s.id===this.sid);if(!s)return;const d=this.ctx.modal(`<h2>${esc(s.name)}</h2><div class="chat-info-members">${s.members.map(m=>`<div>${personAvatar(m)}<span><strong>${personLabel(m)}</strong><small> (@${esc(m.login)})</small>${personPresence(m)}</span>${m.id!==this.ctx.state.me.id?`<button type="button" class="text-link" data-edit-name="${esc(m.id)}">${tHtml("chat:personalName")}</button>`:''}</div>`).join('')}</div><p class="hint">${tHtml("chat:messagesAndAttachmentsAreStoredFor3Months")}</p>`);d.querySelectorAll('[data-edit-name]').forEach(b=>b.onclick=()=>{const p=s.members.find(m=>m.id===b.dataset.editName);$('.modal-close',d).click();editNickname(this.ctx,p);});}
  syncRoomIdentity(){for(const s of this.spaces)if(s.kind==='friends')s.name=personName(this.other(s));const space=this.spaces.find(s=>s.id===this.sid),button=$('[data-chat-info]',this.host);if(!space||!button)return;const status=$('#chat-status',button)?.textContent||'';renderMarkup(button,`${this.avatar(space)}<span><h2>${esc(space.name)}</h2><span class="chat-header-subtitle">${space.kind==='friends'?personPresence(this.other(space)):tText("common:memberCount", {count:(space.members.length)})}<span id="chat-status" role="status">${esc(status)}</span></span></span>`);}
  syncPreferenceButton(){const space=this.spaces.find(s=>s.id===this.sid),b=$('[data-chat-favorite]',this.host);if(space&&b){b.setAttribute('aria-label',space.favorite?tText("chat:removeChatFromFavorites"):tText("chat:addChatToFavorites"));b.title=b.getAttribute('aria-label');b.setAttribute('aria-pressed',String(space.favorite));b.classList.toggle('is-active',space.favorite);}}
  async preference(key){const space=this.spaces.find(s=>s.id===this.sid);if(!space||this.savingPreference)return;this.savingPreference=true;this.roomRevision++;try{const data=await this.ctx.api(`/chat/${space.id}/preferences`,'PUT',{[key]:!space[key]});if(this.disposed)return;this.roomRevision++;const current=this.spaces.find(s=>s.id===space.id);if(current)Object.assign(current,data);this.drawRooms();if(this.tab==='calls')this.refreshCalls();this.syncPreferenceButton();this.closeMenus();}catch(e){this.ctx.toast(e.message);}finally{this.savingPreference=false;}}
  menu(){
    const p=$('[data-chat-menu-panel]',this.host),s=this.spaces.find(s=>s.id===this.sid);if(!p||!s)return;const open=p.hidden;this.closeMenus();p.hidden=!open;$('[data-chat-menu]',this.host).setAttribute('aria-expanded',String(open));p.setAttribute('role','menu');
    const manage=s.kind==='friends'||(s.manage??s.owner);
    p.innerHTML=`<button type="button" role="menuitem" data-room-info>${tHtml("chat:participantsAndDetails", {value0:(chatIcon('users'))})}</button><button type="button" role="menuitem" data-pin-chat>${chatIcon('pin')}${s.pinned?tText("chat:unpinChat"):tText("chat:pinChat")}</button><button type="button" role="menuitem" data-copy-chat-link>${tHtml("chat:copyLink", {value0:(chatIcon('copy'))})}</button><button type="button" role="menuitem" data-room-social>${tHtml("chat:peopleAndGroups2", {value0:(chatIcon('users'))})}</button><button type="button" role="menuitem" data-chat-notifications>${chatIcon(roomMuted(s)?'bellOff':'bell')}${roomMuted(s)?tText("chat:enableNotifications"):tText("chat:disableTemporarily")}</button>${manage?`<hr><button type="button" role="menuitem" data-auto-delete aria-haspopup="menu" aria-expanded="false">${chatIcon('timer')}<span>${tHtml("chat:automaticMessageDeletion")}</span>${chatIcon('down')}</button><div class="chat-timer-menu" data-chat-timers role="menu" aria-label="${tHtml("chat:automaticMessageDeletion")}" hidden>${chatTimers.map(([seconds,label])=>`<button type="button" role="menuitemradio" aria-checked="${seconds===(s.chat_ttl||0)}" data-chat-timer="${seconds}"><span>${label}</span>${seconds===(s.chat_ttl||0)?chatIcon('check'):''}</button>`).join('')}</div><button type="button" role="menuitem" class="chat-clear-history" data-clear-history>${tHtml("chat:clearHistory", {value0:(chatIcon('trash'))})}</button>`:''}`;
    $('[data-pin-chat]',p).onclick=()=>this.preference('pinned');$('[data-copy-chat-link]',p).onclick=()=>{this.copy(location.origin+'/#chat/'+this.sid);this.closeMenus();};$('[data-room-info]',p).onclick=()=>{this.closeMenus();this.info();};$('[data-room-social]',p).onclick=()=>this.ctx.navigate('social');
    $('[data-chat-notifications]',p).onclick=()=>{if(roomMuted(s))this.setNotifications(s,0);else this.notificationMenu(s);};
    $('[data-auto-delete]',p)?.addEventListener('click',e=>{const menu=$('[data-chat-timers]',p);menu.hidden=!menu.hidden;e.currentTarget.setAttribute('aria-expanded',String(!menu.hidden));if(!menu.hidden)menu.querySelector('[aria-checked="true"]').focus();});
    p.querySelectorAll('[data-chat-timer]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const state=await this.ctx.api(`/chat/${s.id}/timer`,'PUT',{seconds:Number(b.dataset.chatTimer)});if(this.sid===s.id)this.applyChatState(state);this.closeMenus();}catch(e){this.ctx.toast(e.message);}finally{b.disabled=false;}});
    $('[data-clear-history]',p)?.addEventListener('click',()=>this.confirmClear());
    p.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();this.closeMenus();$('[data-chat-menu]',this.host).focus();}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const buttons=[...p.querySelectorAll('button')].filter(b=>b.getClientRects().length&&!b.disabled),i=buttons.indexOf(document.activeElement);buttons[(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}};
    if(open)p.querySelector('button').focus();
  }
  notificationMenu(s){
    const p=$('[data-chat-menu-panel]',this.host);if(!p)return;
    p.innerHTML=`<button type="button" role="menuitem" data-notifications-back>${tHtml("chat:back", {value0:(chatIcon('back'))})}</button><hr>${[[3600,tText("chat:label1Hour")],[28800,tText("chat:label8Hours")],[86400,tText("chat:label1Day")],[604800,tText("chat:label7Days")]].map(([seconds,label])=>`<button type="button" role="menuitem" data-mute-seconds="${seconds}">${tHtml("chat:doNotDisturb", {value0:(label)})}</button>`).join('')}`;
    $('[data-notifications-back]',p).onclick=()=>{this.closeMenus();this.menu();};
    p.querySelectorAll('[data-mute-seconds]').forEach(b=>b.onclick=()=>this.setNotifications(s,Number(b.dataset.muteSeconds)));
    p.querySelector('button').focus();
  }
  async setNotifications(s,seconds){
    if(this.savingNotifications)return;this.savingNotifications=true;this.roomRevision++;
    const panel=$('[data-chat-menu-panel]',this.host);panel?.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{const data=await this.ctx.api(`/chat/${s.id}/notifications`,'PUT',{seconds});if(this.disposed)return;this.roomRevision++;const current=this.spaces.find(room=>room.id===s.id);if(current)Object.assign(current,data);this.drawRooms();this.closeMenus();this.ctx.toast(seconds?tText("chat:notificationsTemporarilyDisabled"):tText("chat:notificationsEnabled"));}
    catch(e){this.ctx.toast(e.message);}
    finally{this.savingNotifications=false;panel?.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  confirmClear(){
    const sid=this.sid;this.closeMenus();const d=this.ctx.modal(`<h2>${tHtml("chat:clearHistory2")}</h2><p>${tHtml("chat:allMessagesAndSentAttachmentsWillBeDeleted")}</p><div class="chat-clear-buttons"><button type="button" class="secondary" data-cancel-clear>${tHtml("chat:cancel")}</button><button type="button" class="primary" data-confirm-clear>${tHtml("chat:clear")}</button></div>`);
    $('[data-cancel-clear]',d).onclick=()=>d.querySelector('.modal-close').click();
    $('[data-confirm-clear]',d).onclick=async e=>{e.currentTarget.disabled=true;try{const state=await this.ctx.api(`/chat/${sid}/clear`,'POST');if(this.sid===sid)this.applyChatState(state);d.querySelector('.modal-close').click();this.scheduleRooms();}catch(error){this.ctx.toast(error.message);e.currentTarget.disabled=false;}};
  }
  messageAlive(m){return m.id>(this.clearedThrough||0)&&m.created>(this.retentionCutoff||0)&&(!m.expires||m.expires>this.messageNow());}
  applyChatState(state){
    const s=this.spaces.find(s=>s.id===this.sid);if(!s)return;
    if(Number.isInteger(state.chat_ttl)&&s.chat_ttl!==state.chat_ttl){s.chat_ttl=state.chat_ttl;this.roomRevision++;}
    const cleared=Math.max(this.clearedThrough||0,state.chat_cleared||0);if(cleared>(this.clearedThrough||0)){this.clearedThrough=cleared;s.chat_cleared=cleared;this.more=false;this.closeMessageMenu();this.expireMessages();}
  }
  expireMessages(){
    if(!this.sid)return;let changed=false;
    for(const key of ['messages','searchItems']){const old=this[key]||[];this[key]=old.filter(m=>this.messageAlive(m));if(old.length!==this[key].length)changed=true;}
    const s=this.spaces.find(s=>s.id===this.sid);if(s?.last_message&&!this.messageAlive(s.last_message)){s.last_message=this.messages.at(-1)||null;s.unread=0;this.roomRevision++;this.drawRooms();}
    if(this.reply&&!this.messageAlive(this.reply)){this.reply=null;this.retry=null;this.drawReply();}
    if(this.editing&&!this.messageAlive(this.editing.message))this.cancelEdit();
    for(const key of ['messages','searchItems'])for(const m of this[key]||[])if(m.reply&&!m.reply.unavailable&&(m.reply.id<=(this.clearedThrough||0)||!this.messages.some(source=>source.id===m.reply.id)&&m.reply.expires&&m.reply.expires<=this.messageNow())){m.reply={id:m.reply.id,unavailable:true,body:''};changed=true;}
    if(changed){this.closeMessageMenu();this.drawMessages();}
  }
  memberInfo(uid){const s=this.spaces.find(s=>s.id===this.sid),p=s?.members.find(p=>p.id===uid);if(!p)return;const d=this.ctx.modal(`<h2>${personLabel(p)}</h2><div class="chat-info-members"><div>${personAvatar(p)}<span><strong>@${esc(p.login)}</strong>${personPresence(p)}</span></div></div>${uid!==this.ctx.state.me.id?`<button type="button" class="secondary compact" data-personal-name>${tHtml("chat:personalName")}</button>`:''}`);$('[data-personal-name]',d)?.addEventListener('click',()=>{$('.modal-close',d).click();editNickname(this.ctx,p);});}
  hideMentions(){this.mentionState=null;const panel=$('[data-chat-mentions]',this.host);if(panel)panel.hidden=true;const body=$('#message-body',this.host);body?.removeAttribute('aria-activedescendant');body?.setAttribute('aria-expanded','false');}
  showMentions(){
    const s=this.spaces.find(s=>s.id===this.sid),body=$('#message-body',this.host),panel=$('[data-chat-mentions]',this.host);if(!body||!panel||!['family','friends'].includes(s?.kind)||body.selectionStart!==body.selectionEnd||this.sending||body.readOnly)return this.hideMentions();
    const query=mentionQuery(body.value,body.selectionStart),items=query?mentionCandidates(s.members,query.query):[];if(!items.length)return this.hideMentions();
    const index=this.mentionState?.query.start===query.start&&this.mentionState.query.query===query.query?Math.min(this.mentionState.index,items.length-1):0;this.mentionState={query,items,index};panel.hidden=false;
    body.setAttribute('aria-controls','chat-mention-options');body.setAttribute('aria-expanded','true');body.setAttribute('aria-autocomplete','list');panel.id='chat-mention-options';
    panel.innerHTML=items.map((p,i)=>`<button type="button" id="chat-mention-${i}" role="option" aria-selected="${i===index}" data-mention-index="${i}">${personAvatar(p)}<span><strong>${esc(personName(p))}</strong><small>@${esc(p.login)}</small></span></button>`).join('');
    panel.onpointerdown=e=>e.preventDefault();panel.querySelectorAll('[data-mention-index]').forEach(b=>b.onclick=()=>this.chooseMention(Number(b.dataset.mentionIndex)));this.highlightMention();
  }
  highlightMention(){const state=this.mentionState;if(!state)return;const panel=$('[data-chat-mentions]',this.host);panel.querySelectorAll('[role="option"]').forEach((b,i)=>b.setAttribute('aria-selected',String(i===state.index)));const selected=$('#chat-mention-'+state.index,panel);selected?.scrollIntoView({block:'nearest'});$('#message-body',this.host).setAttribute('aria-activedescendant',selected.id);}
  chooseMention(index){const state=this.mentionState,body=$('#message-body',this.host);if(!state||!state.items[index])return;const text='@'+state.items[index].login+' ';if(body.value.length-(state.query.end-state.query.start)+text.length>4000)return;body.setRangeText(text,state.query.start,state.query.end,'end');this.hideMentions();this.resizeComposer();this.sendTyping();body.focus();}
  mentionKey(e){if(!this.mentionState||e.isComposing)return false;if(['ArrowDown','ArrowUp','Enter','Tab','Escape'].includes(e.key)){e.preventDefault();e.stopPropagation();if(e.key==='Escape')this.hideMentions();else if(e.key==='Enter'||e.key==='Tab')this.chooseMention(this.mentionState.index);else{this.mentionState.index=(this.mentionState.index+(e.key==='ArrowDown'?1:-1)+this.mentionState.items.length)%this.mentionState.items.length;this.highlightMention();}return true;}return false;}
  closeMenus(){this.closeMessageMenu();this.attachmentContext?.close();this.attachmentContext=null;this.host.querySelectorAll('[data-chat-menu-panel],[data-chat-emoji-panel]').forEach(el=>el.hidden=true);$('[data-chat-menu]',this.host)?.setAttribute('aria-expanded','false');}
  async copy(text){try{await navigator.clipboard.writeText(text);this.ctx.toast(tText("chat:copied"));}catch{this.ctx.toast(tText("chat:couldNotCopySelectTheTextAndCopy"));}}
  drawReply(){const el=$('[data-chat-reply]',this.host);if(!el)return;el.hidden=!this.reply||!!this.editing;el.innerHTML=this.reply?`<div><strong>${tHtml("chat:reply", {value0:(esc(personName(this.reply.sender_profile||{login:this.reply.sender})))})}</strong><span>${esc(messagePreview(this.reply).slice(0,240))}</span></div>${iconButton('close',tText("chat:cancelReply"),'data-cancel-reply')}`:'';$('[data-cancel-reply]',el)?.addEventListener('click',()=>{this.reply=null;this.retry=null;this.drawReply();});}
  messageNow(){return this.clock?.server?this.clock.server+(performance.now()-this.clock.local)/1000:Date.now()/1000;}
  messageById(id){return this.messages.find(m=>m.id===id)||this.searchItems?.find(m=>m.id===id);}
  replyTo(message){if(!message)return;if(this.editing)return this.ctx.toast(tText("chat:firstSaveOrCancelEditingTheMessage"));this.reply=message;this.retry=null;this.drawReply();$('#message-body',this.host).focus();this.saveDraft();}
  bindMessageMenu(){
    this.messagePress?.();
    this.messagePress=bindMessagePress($('#chat-messages',this.host),'[data-message-id]',(article,x,y,touch)=>{
      if(!this.messageMenu)this.showMessageMenu(Number(article.dataset.messageId),x,y,article.querySelector('[data-message-menu]'),touch);
    });
  }
  closeMessageMenu(focus=false){this.messageContext?.close(focus);this.messageContext=null;this.messageMenu=null;clearTimeout(this.menuDeadline);}
  showMessageMenu(id,x,y,trigger,touch=false){
    const message=this.messageById(id),article=this.host.querySelector(`[data-message-id="${id}"]`);if(!message||!article)return;
    this.closeMenus();this.followMessages=false;
    const current=()=>this.messageById(id),editable=canEditMessage(message,this.ctx.state.me.id,this.messageNow());
    const actions=[{id:'reply',icon:'reply',label:tText("chat:reply2"),run:()=>this.replyTo(current())}];
    if(message.body)actions.push({id:'copy',icon:'copy',label:tText("chat:copyText"),run:()=>{const m=current();if(m)this.copy(m.body);}});
    if(editable)actions.push({id:'edit',icon:'compose',label:tText("chat:edit"),run:()=>{const m=current();if(m)this.startEdit(m);}});
    if(message.sender_id===this.ctx.state.me.id)actions.push({id:'delete',icon:'trash',label:tText("chat:delete"),destructive:true,run:()=>this.deleteMessage(current())});
    this.messageContext=messageMenu({article,trigger,x,y,touch,actions,reactions:this.reactions?.menu(current(),article),onClose:()=>{this.messageMenu=null;clearTimeout(this.menuDeadline);clearTimeout(this.readTimer);this.readTimer=setTimeout(()=>this.markVisibleRead(),200);}});
    this.messageMenu=this.messageContext.element;
    if(editable)this.menuDeadline=setTimeout(()=>this.messageMenu?.querySelector('[data-message-action="edit"]')?.remove(),Math.max(0,(message.editable_until-this.messageNow())*1000));
  }
  startEdit(message,restore=false){
    if(this.sending||this.uploading)return this.ctx.toast(tText("chat:waitForTheSendingOrUploadToComplete"));
    if(!restore&&!canEditMessage(message,this.ctx.state.me.id,this.messageNow()))return this.ctx.toast(tText("chat:youCanModifyYourMessageWithin24Hours"));
    if(this.editing){if(this.editing.message.id!==message.id)this.ctx.toast(tText("chat:firstSaveOrCancelTheCurrentEditing"));$('#message-body',this.host).focus();return;}
    this.closeMenus();const body=$('#message-body',this.host);this.editing={message,draft:{text:body.value,reply:this.reply}};body.value=message.body;$('[data-chat-upload-status]',this.host).textContent='';this.drawEdit();this.resizeComposer?.();body.focus();body.setSelectionRange(body.value.length,body.value.length);
  }
  drawEdit(){
    const edit=$('[data-chat-edit]',this.host),submit=$('.chat-send',this.host),editing=this.editing;if(!edit)return;edit.hidden=!editing;
    edit.innerHTML=editing?`<div><strong>${tHtml("chat:editing")}</strong><span>${esc(messagePreview(editing.message).slice(0,240))}</span></div>${iconButton('close',tText("chat:cancelEditing"),'data-cancel-edit')}`:'';
    $('[data-cancel-edit]',edit)?.addEventListener('click',()=>this.cancelEdit());$('[data-attach-chat]',this.host).disabled=!!editing;this.drawReply();$('[data-chat-drafts]',this.host).hidden=!!editing;
    const label=editing?tText("chat:saveChanges"):tText("chat:send");submit.innerHTML=chatIcon(editing?'check':'send');submit.setAttribute('aria-label',label);submit.title=label;$('#message-body',this.host).placeholder=editing?tText("chat:modifyTheMessage"):tText("chat:message2");$('.chat-help',this.host).textContent=editing?tText("chat:changesAvailable24HoursAfterSendingEnterSave"):tText("chat:filesUpTo50MbEnterSend");
  }
  cancelEdit(focus=true){if(!this.editing||this.sending)return;const draft=this.editing.draft;this.editing=null;this.reply=draft.reply;$('#message-body',this.host).value=draft.text;this.drawEdit();this.resizeComposer?.();if(focus)$('#message-body',this.host).focus();}
  async submitEdit(seq){
    const editing=this.editing,body=$('#message-body',this.host),status=$('[data-chat-upload-status]',this.host),text=body.value.trim();
    if(!text&&!editing.message.attachments?.length)return this.ctx.toast(tText("chat:theMessageTextCannotBeEmpty"));
    this.sending=true;const unlock=lockComposer(body);this.sendAbort=new AbortController();const cancel=$('[data-cancel-chat-upload]',this.host);cancel.textContent=tText("chat:cancelWaiting");cancel.hidden=false;$('[data-cancel-edit]',this.host).disabled=true;
    this.sendPromise=(async()=>{const saved=await this.ctx.api(`/chat/${this.sid}/messages/${editing.message.id}`,'PUT',{body:text,revision:editing.message.revision||0},true,{}, {signal:this.sendAbort.signal});if(this.disposed||seq!==this.sequence)return;this.merge([saved],null,{editsOnly:true});this.scheduleRooms();this.sending=false;this.cancelEdit(false);status.textContent=tText("chat:changesSaved");})();
    try{await this.sendPromise;}finally{unlock();this.sendPromise=null;this.sendAbort=null;this.sending=false;if(!this.disposed&&seq===this.sequence){$('[data-cancel-chat-upload]',this.host).hidden=true;const cancel=$('[data-cancel-edit]',this.host);if(cancel)cancel.disabled=false;}}
  }
  bindComposer(seq){
    const sid=this.sid,input=$('[data-chat-files]',this.host),form=$('#send-message',this.host),body=$('#message-body',this.host),attach=$('[data-attach-chat]',this.host),submit=$('button[type=submit]',form),status=$('[data-chat-upload-status]',this.host);const current=()=>!this.disposed&&seq===this.sequence;
    keepComposerFocus(form,body);
    const resize=this.resizeComposer=()=>{body.style.height='auto';body.style.height=Math.min(150,Math.max(24,body.scrollHeight))+'px';this.saveDraft();this.recordingActions?.update();};const saved=this.drafts.get(sid);if(saved){body.value=saved.text;this.reply=saved.reply;this.drawReply();}resize();if(saved?.editing){this.startEdit(saved.editing.message,true);body.value=saved.editing.text;resize();}body.oninput=()=>{resize();this.sendTyping();this.showMentions();};body.onclick=()=>this.showMentions();body.onkeyup=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key))this.showMentions();};body.onblur=()=>{this.stopTyping();this.hideMentions();};
    const cancel=file=>this.ctx.api(`/chat/${sid}/attachments/${file.id}`,'DELETE').catch(()=>{});
    const detach=files=>{const ids=new Set(files.map(f=>f.id));this.pending=this.pending.filter(f=>!ids.has(f.id));this.retry=null;for(const file of files)cancel(file);draftView();};
    const draftView=()=>{
      if(!current())return;const drafts=$('[data-chat-drafts]',this.host);
      drafts.innerHTML=this.pending.map(f=>`<span>${(f.kind==='image'||f.kind==='animation')?`<img src="${esc(f.url)}" alt="">`:['video','video_note'].includes(f.kind)?`<video muted playsinline preload="metadata" src="${esc(f.url)}" aria-label="${tHtml("chat:previewVideoBeforeSending")}"></video>`:''}<b>${esc(f.name)}</b><small>${sizeLabel(f.size)}</small><button type="button" data-remove-draft="${f.id}" aria-label="${tHtml("chat:removeAttachment", {value0:(esc(f.name))})}">×</button></span>`).join('')+(this.pending.length>1?`<button type="button" class="text-link" data-clear-drafts>${tHtml("chat:removeAllAttachments")}</button>`:'');
      prepareVideos(drafts);
      drafts.querySelectorAll('[data-remove-draft]').forEach(b=>b.onclick=()=>{if(this.sending)return;detach(this.pending.filter(f=>f.id===b.dataset.removeDraft));});
      $('[data-clear-drafts]',drafts)?.addEventListener('click',()=>{if(!this.sending){this.uploadBatch?.abort();detach([...this.pending]);}});
    };
    const cancelUpload=$('[data-cancel-chat-upload]',this.host);cancelUpload.onclick=()=>{this.uploadBatch?.abort();this.sendAbort?.abort(new DOMException(tText("chat:waitingIsCompleteTextSavedYouCanResend"),'AbortError'));};
    const uploadFiles=async(files,mode='preview',recording=null)=>{
      if(this.editing)return this.ctx.toast(tText("chat:firstSaveOrCancelEditingTheMessage"));
      if(this.uploading||this.sending||!files.length)return;
      if(files.length+this.pending.length>10)return this.ctx.toast(tText("chat:upTo10AttachmentsInOneMessage"));
      if(files.some(f=>f.size>maxFile))return this.ctx.toast(tText("chat:eachAttachmentShouldNotExceed50Mb"));
      const batch=new AbortController();this.uploadBatch=batch;this.uploading=true;attach.disabled=submit.disabled=true;cancelUpload.hidden=false;cancelUpload.textContent=tText("chat:cancelUpload");
      try{
        for(const [i,file] of files.entries()){
          status.textContent=tText("chat:uploadingFrom", {value0:(i+1), value1:(files.length), value2:(file.name)});
          const prepared=mode==='file'||recording?file:await previewFile(file,{signal:batch.signal});
          if(!current()||batch.signal.aborted)return;
          const stored=await this.ctx.api(`/chat/${sid}/attachments?name=${encodeURIComponent(prepared.name)}&as_file=${mode==='file'}`,'POST',prepared,true,recording?{'X-DrG-Recording':JSON.stringify(recording)}:{}, {signal:batch.signal,timeoutMs:120000});
          if(!current()||batch.signal.aborted){await cancel(stored);return;}
          this.pending.push(stored);this.retry=null;draftView();
        }
        status.textContent=tText("chat:attachmentsAreReadyToSend");return true;
      }catch(error){if(current())status.textContent=batch.signal.aborted?tText("chat:uploadCancelledYouCanSendTheText"):error.message;return false;}
      finally{if(current()&&this.uploadBatch===batch){this.uploadBatch=null;this.uploading=false;attach.disabled=submit.disabled=false;cancelUpload.hidden=true;input.value='';}}
    };
    this.recordingActions?.destroy();this.recordingActions=recordingActions(form,this.ctx,{isCurrent:current,canRecord:()=>current()&&!this.uploading&&!this.sending&&!this.editing,onOpen:()=>{this.closeMenus();this.cameraCapture?.close();this.voiceRecording?.close();},onFile:(file,recording)=>uploadFiles([file],'preview',recording)});
    let uploadMode='preview';
    const main=$('#chat-main',this.host);
    const overlay=document.createElement('div');overlay.className='chat-drop-overlay';overlay.hidden=true;overlay.innerHTML=`<div data-drop-mode="file"><span>${tHtml("chat:dragFilesHereToSend")}<strong>${tHtml("chat:withoutCompressionAsFiles")}</strong></span></div><div data-drop-mode="preview"><span>${tHtml("chat:dragPhotosAndVideosHere")}<strong>${tHtml("chat:quickWayWithPreview")}</strong></span></div>`;main.append(overlay);
    const hideDrop=()=>{overlay.hidden=true;main.classList.remove('is-file-drag');overlay.querySelectorAll('.is-active').forEach(el=>el.classList.remove('is-active'));};
    main.ondragenter=main.ondragover=e=>{if(!e.dataTransfer?.types.includes('Files'))return;e.preventDefault();if(this.editing){e.dataTransfer.dropEffect='none';return;}e.dataTransfer.dropEffect='copy';overlay.hidden=false;main.classList.add('is-file-drag');const zone=e.target.closest('[data-drop-mode]');overlay.querySelectorAll('[data-drop-mode]').forEach(el=>el.classList.toggle('is-active',el===zone));};
    main.ondragleave=e=>{if(!main.contains(e.relatedTarget))hideDrop();};
    main.ondrop=e=>{if(!e.dataTransfer?.files.length)return;e.preventDefault();const mode=e.target.closest('[data-drop-mode]')?.dataset.dropMode||'preview';hideDrop();uploadFiles([...e.dataTransfer.files],mode);};
    main.onkeydown=e=>{if(e.key==='Escape')hideDrop();};
    attach.setAttribute('aria-haspopup','menu');attach.setAttribute('aria-expanded','false');
    attach.onclick=()=>{
      if(this.attachmentContext?.element.isConnected){this.attachmentContext.close(true);this.attachmentContext=null;return;}
      this.closeMenus();
      this.attachmentContext=attachmentMenu(attach,mode=>{
        uploadMode=mode==='file'?'file':'preview';
        if(mode==='voice'||mode==='video_note'){this.recordingActions?.close();this.voiceRecording?.close();this.voiceRecording=voiceRecording(this.ctx,{kind:mode,isCurrent:current,onFile:(file,recording)=>uploadFiles([file],'preview',recording)});return;}
        if(mode==='camera'&&!matchMedia('(pointer:coarse)').matches){
          this.cameraCapture?.close();this.voiceRecording?.close();this.cameraCapture=cameraCapture(this.ctx,{isCurrent:current,onFile:file=>{if(current())uploadFiles([file]);}});return;
        }
        input.accept=mode==='file'?'':'image/*,video/*';input.multiple=mode!=='camera';
        if(mode==='camera')input.setAttribute('capture','environment');else input.removeAttribute('capture');
        input.click();
      });
    };
    input.onchange=()=>uploadFiles([...input.files],uploadMode);body.onpaste=e=>{if(e.clipboardData.files.length){e.preventDefault();uploadFiles([...e.clipboardData.files]);}};$('[data-chat-emoji]',this.host).onclick=()=>{const p=$('[data-chat-emoji-panel]',this.host),open=p.hidden;this.closeMenus();p.hidden=!open;};this.host.querySelectorAll('[data-insert-emoji]').forEach(b=>b.onclick=()=>{if(this.sending||body.readOnly||body.value.length-(body.selectionEnd-body.selectionStart)+b.dataset.insertEmoji.length>4000)return;body.setRangeText(b.dataset.insertEmoji,body.selectionStart,body.selectionEnd,'end');this.closeMenus();body.focus();resize();this.sendTyping();});
    formBind(form,async f=>{const text=f.get('body').trim(),attachments=this.pending.map(f=>f.id),replyTo=this.reply?.id??null;if(this.sending||this.uploading)return;if(this.editing){await this.submitEdit(seq);return;}if(!text&&!attachments.length)return;this.stopTyping();this.sending=true;const unlock=lockComposer(body);attach.disabled=true;this.sendAbort=new AbortController();cancelUpload.textContent=tText("chat:cancelWaiting");cancelUpload.hidden=false;
      this.sendPromise=(async()=>{if(!this.retry||this.retry.body!==text||this.retry.reply_to!==replyTo||JSON.stringify(this.retry.attachments)!==JSON.stringify(attachments))this.retry={body:text,attachments,reply_to:replyTo,client_id:crypto.randomUUID()};let saved;
        try{status.textContent=tText("chat:sending");saved=await this.ctx.api(`/chat/${sid}/messages`,'POST',this.retry,true,{}, {signal:this.sendAbort.signal});}
        catch(error){if(current()){const unavailable=error.data?.detail?.unavailable_attachments;if(Array.isArray(unavailable)){detach(this.pending.filter(f=>unavailable.includes(f.id)));status.textContent=tText("chat:unavailableAttachmentsRemovedTextSavedCheckItAnd");}else status.textContent=tText("chat:sendFailedTextAndAttachmentsSavedResend");}throw error;}
        if(!current())return;this.closeSearch();this.merge([saved],null,{follow:true});this.catchup?.().catch(()=>{});this.retry=null;this.pending=[];draftView();this.reply=null;this.drawReply();body.value='';this.drafts.delete(sid);resize();status.textContent='';this.scheduleRooms();})();
      try{await this.sendPromise;}finally{unlock();this.sendPromise=null;this.sendAbort=null;this.sending=false;if(current()){cancelUpload.hidden=true;attach.disabled=false;}}
    });body.onkeydown=e=>{if(this.sending){if(e.key==='Enter'&&!e.shiftKey)e.preventDefault();return;}if(this.mentionKey(e))return;if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();form.requestSubmit();}};
  }
}
