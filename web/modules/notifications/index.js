import {formatNumber} from '../../core/i18n.js';
import {tText,tHtml,locale} from "../../core/i18n.js";
import './inbox.css';
import {telegramPerson,telegramAvatars} from '../family-photos/details.js';
import {sidebarIcon} from '../../core/sidebar-icons.js';
import {$,esc,date} from '../../core/ui.js';
import {connectRoom} from '../../core/realtime.js';
import {renderMarkup} from '../../core/dom.js';
import {personAvatar,personName} from '../../core/people.js';
import {startPresence} from '../../core/presence.js';
import {pushSupportMessage} from '../../core/push.js';
import {appBadge} from '../../core/app-badge.js';
import {applyMessageReads,syncDeliveredReads} from '../../core/notification-reads.js';
export const inboxIcon = `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M10 21h4"/></svg>`;

function systemIcon(kind){
 const paths={session:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4m-4-11 3 3 5-5"/>',security:'<path d="M12 2 4 5v6c0 5 4 8 8 11 4-3 8-6 8-11V5Z"/><path d="m8 12 3 3 5-6"/>',quota:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',call:'<path d="m4 3 4 1 1 5-3 2c2 4 3 5 7 7l2-3 5 1 1 4c-1 4-7 2-12-2S1 4 4 3Z"/>',mention:'<circle cx="11" cy="11" r="4"/><path d="M15 7v7c0 4 8 2 6-5a9 9 0 1 0-5 12"/>'};
 if(['invitation','task_invitation'].includes(kind))return sidebarIcon('users-round');
 return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${paths[kind]||'<path d="M21 11a9 9 0 0 1-9 9H3l2-5a9 9 0 1 1 16-4Z"/><path d="M8 8h8M8 12h5"/>'}</svg>`;
}
let active;
const titles={tracker:tText("tracker:notification"),tracker_invitation:tText("tracker:projectInvitation"),task_invitation:tText("notifications:reminderInvitation"),family_export_ready:tText("notifications:familyPhotoArchiveIsReady"),security:tText("notifications:portalSecurity"),quota:tText("notifications:spaceQuotaChanged"),invitation:tText("notifications:invitation"),call:tText("notifications:voiceCall"),message:tText("notifications:newMessage"),family_message:tText("notifications:newMessage"),session:tText("notifications:newAccountLogin"),mention:tText("notifications:youHaveBeenMentioned")};
let audio;
function unlockAudio(){try{audio??=new (window.AudioContext||window.webkitAudioContext)();if(audio.state==='suspended')audio.resume().catch(()=>{});}catch{}}
document.addEventListener('pointerdown',unlockAudio,{once:true});document.addEventListener('keydown',unlockAudio,{once:true});
function ping(){if(audio?.state!=='running')return;const oscillator=audio.createOscillator(),gain=audio.createGain();oscillator.type='sine';oscillator.frequency.setValueAtTime(880,audio.currentTime);oscillator.frequency.exponentialRampToValueAtTime(660,audio.currentTime+.12);gain.gain.setValueAtTime(.06,audio.currentTime);gain.gain.exponentialRampToValueAtTime(.001,audio.currentTime+.17);oscillator.connect(gain);gain.connect(audio.destination);oscillator.start();oscillator.stop(audio.currentTime+.18);}
export const inboxController={
  destroy(){active?.destroy();active=null;},
  attach(ctx){if(!active||active.uid!==ctx.state.me.id){this.destroy();active=createInbox(ctx);}active.attach(ctx);},
  open(){active?.open();},
};
function createInbox(initial){
  let ctx=initial,disposed=false,items=new Map(),initialized=false,unread=0,dialog,timer,cursor=0,filter='unread';
  const avatars=telegramAvatars();
  const seen=new Set(),cleared=new Map(),reads={};const uid=ctx.state.me.id;
  const prefs=()=>({sound:true,popups:true,...ctx.state.me.settings.notifications});
  const badge=()=>{if(disposed)return;appBadge.set(unread);const button=document.querySelector('#open-inbox');if(!button)return;const label=tText("notifications:notifications", {value0:(unread?(" "+tText("notifications:unread", {value0:(unread)})+""):'')});if(button.getAttribute('aria-label')!==label)button.setAttribute('aria-label',label);renderMarkup(button,`${inboxIcon}${unread?`<b class=notification-count>${unread>99?'99+':unread}</b>`:''}`);};
  function description(item){const p=item.payload;if(item.kind==='tracker')return tText('tracker:notificationDescription',{title:p.title});if(item.kind==='tracker_invitation')return tText('tracker:invitationDescription',{name:p.project_name});const actor=personName(p.actor_profile||{id:p.actor_id,login:p.actor||''});return item.kind==='task_invitation'?tText('notifications:invitesToReminderList',{actor,list:p.list_name}):item.kind==='family_export_ready'?tText("notifications:zipIsReadyToDownload", {value0:(p.group_title||tText("notifications:familyGroup"))}):item.kind==='security'?tText("notifications:securityEventDetectedDetailsAreAvailableInAdministration"):item.kind==='quota'?(p.limit_bytes===null?tText("notifications:nowUnlimitedWorkspace"):tText("notifications:newLimitGb", {value0:(formatNumber((p.limit_bytes/1024**3),{minimumFractionDigits:2,maximumFractionDigits:2}))})):item.kind==='session'?sessionDescription(p):item.kind==='invitation'?tText("notifications:invites", {value0:(actor), value1:(p.kind==='family'?tText("notifications:toTheWorkspace", {value0:(p.space_name)}):tText("notifications:addHimAsAFriend"))}):`${p.actor_id?actor:p.actor||''}${p.preview?' · '+p.preview:''}`;}
  function sessionDescription(payload){
    const agent=payload.agent||'',browser=/Edg\//.test(agent)?'Edge':/Firefox\//.test(agent)?'Firefox':/Chrome\//.test(agent)?'Chrome':/Safari\//.test(agent)?'Safari':tText("notifications:browser"),device=/iPad/.test(agent)?'iPad':/iPhone/.test(agent)?'iPhone':/Android/.test(agent)?'Android':/Macintosh|Mac OS/.test(agent)?'Mac':/Windows/.test(agent)?'Windows':/Linux/.test(agent)?'Linux':'';
    return [browser,device,payload.ip].filter(Boolean).join(' · ');
  }
  const groupDay=stamp=>{const date=new Date(stamp*1000),today=new Date();today.setHours(0,0,0,0);const yesterday=new Date(today);yesterday.setDate(today.getDate()-1);return date>=today?tText("notifications:today"):date>=yesterday?tText("notifications:yesterday"):date.toLocaleDateString(locale(),{day:'numeric',month:'long'});};
  function visual(item){
    const person=item.payload.actor_profile;
    if(person)return personAvatar(person);
    const p=item.payload;if(item.kind==='family_message'&&/^-?\d{1,20}$/.test(p.telegram_sender_id||'')&&/^(?:-\d{1,20}|export:[a-f0-9]{8,24})$/.test(p.family_group_id||''))return telegramPerson({name:p.actor,avatar_url:'/api/family-photos/groups/'+p.family_group_id+'/people/'+p.telegram_sender_id+'/avatar'});
    return `<span class="inbox-event-icon" aria-hidden="true">${systemIcon(item.kind)}</span>`;
  }
  async function act(item,action){
    try{
      if(['tracker','tracker_invitation'].includes(item.kind)){if(!ctx.moduleEnabled('tracker')){ctx.toast(tText('tracker:enableModule'));return ctx.navigate('settings');}await mark(item);dialog?.querySelector('.modal-close')?.click();ctx.state.trackerProject=item.payload.project_id;ctx.state.trackerIssue=item.payload.issue_id;return ctx.navigate('tracker');}
      if(item.kind==='task_invitation'&&action){await ctx.api('/task-lists/'+encodeURIComponent(item.payload.list_id)+'/invitations/'+encodeURIComponent(item.payload.invitation_id)+(action==='accept'?'/accept':''),action==='accept'?'POST':'DELETE');item.payload.pending=false;await mark(item);draw();document.dispatchEvent(new Event('drg-inbox-change'));document.dispatchEvent(new Event('drg-task-lists-change'));return;}
      if(item.kind==='task_invitation'&&item.payload.pending){return;}
      if(item.kind==='task_invitation'){await mark(item);dialog?.querySelector('.modal-close')?.click();ctx.state.tasksFocusList=item.payload.list_id;return ctx.navigate('tasks');}
      if(item.kind==='invitation'&&action){await ctx.api('/social/requests/'+item.payload.request_id+(action==='accept'?'/accept':''),action==='accept'?'POST':'DELETE');item.payload.pending=false;ctx.state.spaces=(await ctx.api('/social')).spaces;await mark(item);draw();return;}
      await mark(item);dialog?.querySelector('.modal-close')?.click();
      if(item.kind==='family_export_ready'&&ctx.state.me.role==='owner'){ctx.state.adminFamilyExportRequested=item.payload.family_group_id;return ctx.navigate('admin');}
      if(item.kind==='security'&&ctx.state.me.role==='owner'){ctx.state.adminSecurityRequested=true;return ctx.navigate('admin');}
      if(item.kind==='session')return ctx.navigate('security');
      if(['message','family_message','call'].includes(item.kind)){ctx.state.chatSpace=item.payload.space_id;if(ctx.moduleEnabled('chat'))return ctx.navigate('chat',{chatList:false});ctx.toast(tText("notifications:enableTheChatModuleInSettingsToOpen"));return ctx.navigate('settings');}
      if(item.kind==='mention')return ctx.openPage(item.payload.note_id);
      return ctx.navigate('social');
    }catch(e){ctx.toast(e.message);}
  }
  async function mark(item){if(item.read)return;await ctx.api('/notifications/'+item.id,'PATCH');item.read=true;unread=Math.max(0,unread-1);badge();}
  function announce(item){
    if(item.silent||item.payload?.silent)return;
    if(prefs().sound){try{const key='drg-inbox-sound:'+uid;if(localStorage.getItem(key)!==String(item.id)){localStorage.setItem(key,String(item.id));ping();}}catch{ping();}}
    if(!prefs().popups)return;
    let stack=document.querySelector('#notification-toasts');if(!stack){stack=document.createElement('div');stack.id='notification-toasts';stack.setAttribute('aria-live','polite');document.body.append(stack);}
    const button=document.createElement('button');button.className='notification-popup';button.innerHTML=`<strong>${esc(titles[item.kind]||tText("notifications:notification"))}</strong><span>${esc(description(item))}</span>`;button.onclick=()=>{button.remove();act(item);};stack.append(button);while(stack.children.length>3)stack.firstElementChild.remove();setTimeout(()=>button.remove(),7000);
  }
  function ingest(data,announceNew){
    if(disposed)return;
    unread=data.unread;cursor=Math.max(cursor,data.cursor||0);
    for(const [sid,mid] of Object.entries(data.message_reads||{}))if(Number.isSafeInteger(mid)&&mid>=0)reads[sid]=Math.max(reads[sid]||0,mid);
    for(const item of data.items||[]){applyMessageReads([item],reads);items.set(item.id,item);if(announceNew&&!item.read&&!seen.has(item.id))announce(item);seen.add(item.id);}
    applyMessageReads(items.values(),reads);syncDeliveredReads(uid,reads);
    for(const [sid,id] of Object.entries(data.chat_cleared||{}))cleared.set(sid,Math.max(id,cleared.get(sid)||0));
    for(const [id,item] of items)if(item.kind==='message'&&item.payload.message_id<=(cleared.get(item.payload.space_id)||0))items.delete(id);
    badge();draw();document.dispatchEvent(new Event('drg-inbox-change'));
  }
  const activityState=()=>({chat_active:ctx.state.view==='chat'&&!document.hidden&&document.hasFocus()});
  const activity=event=>socket.send({type:'activity',...activityState(),...(event?.type==='pagehide'?{chat_active:false}:{})});
  function draw(){
    if(!dialog?.isConnected)return;
    const host=dialog.querySelector('[data-inbox-items]');let previousDay='';
    renderMarkup(host,[...items.values()].sort((a,b)=>b.id-a.id).filter(item=>filter!=='unread'||!item.read).map(item=>{
      const day=groupDay(item.created),heading=day===previousDay?'':`<h3 class="inbox-day">${esc(day)}</h3>`;previousDay=day;
      const who=item.payload.actor_profile?personName(item.payload.actor_profile):item.payload.actor;
      const title=['message','family_message','call','mention'].includes(item.kind)&&who?who:titles[item.kind]||tText("notifications:notification");
      return `${heading}<article class="inbox-item ${item.read?'':'unread'}" data-inbox-id="${item.id}">${visual(item)}<div class="inbox-event-body"><button data-event-open="${item.id}" class="inbox-open"><span class="inbox-event-heading"><strong>${esc(title)}</strong><time datetime="${new Date(item.created*1000).toISOString()}" title="${esc(date(item.created))}">${new Date(item.created*1000).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'})}</time></span>${item.payload.space_name?`<small class="inbox-source">${esc(item.payload.space_name)}</small>`:''}<span>${esc(['message','family_message'].includes(item.kind)?item.payload.preview||tText("notifications:openConversation"):description(item))}</span></button>${['invitation','task_invitation'].includes(item.kind)&&item.payload.pending?`<div class="inbox-actions"><button class="primary compact" data-inbox-accept="${item.id}">${tHtml("notifications:accept")}</button><button class="secondary compact" data-inbox-decline="${item.id}">${tHtml("notifications:decline")}</button></div>`:''}</div><button class="icon-btn inbox-remove" data-event-remove="${item.id}" aria-label="${tHtml("notifications:deleteNotification")}">×</button>${item.read?'':`<span class="inbox-unread-dot" aria-label="${tHtml("notifications:unread2")}"></span>`}</article>`;
    }).join('')||`<div class="inbox-empty">${inboxIcon}<strong>${filter==='unread'?tText("notifications:allRead"):tText("notifications:noNotificationsYet")}</strong><span>${tHtml("notifications:messagesInvitationsAndAccountEventsWillAppearHere")}</span></div>`);
    avatars.hydrate(host);
    dialog.querySelectorAll('[data-inbox-filter]').forEach(button=>button.setAttribute('aria-selected',String(button.dataset.inboxFilter===filter)));
    $('[data-inbox-unread]',dialog).textContent=unread?String(unread):'';
    host.querySelectorAll('[data-event-open]').forEach(b=>b.onclick=()=>act(items.get(Number(b.dataset.eventOpen))));
    for(const [key,action] of [['inboxAccept','accept'],['inboxDecline','decline']])host.querySelectorAll('[data-'+key.replace(/[A-Z]/g,x=>'-'+x.toLowerCase())+']').forEach(b=>b.onclick=()=>act(items.get(Number(b.dataset[key])),action));
    host.querySelectorAll('[data-event-remove]').forEach(b=>b.onclick=async()=>{try{const id=Number(b.dataset.eventRemove);await ctx.api('/notifications/'+id,'DELETE');if(!items.get(id).read)unread=Math.max(0,unread-1);items.delete(id);draw();badge();}catch(e){ctx.toast(e.message);}});
  }
  async function open(anchor=document.querySelector('#open-inbox')){
    const left=(anchor?.getBoundingClientRect().left??innerWidth)<innerWidth/2;
    const data=await ctx.api('/notifications');items=new Map(data.items.map(x=>[x.id,x]));ingest(data,false);
    if(dialog?.isConnected){dialog.querySelector('.modal-close')?.click();return;}
    filter='unread';
    dialog=ctx.modal(`<header class="inbox-header"><h2>${tHtml("notifications:notifications2")} <span data-inbox-unread></span></h2><button class="text-link" data-read-all>${tHtml("notifications:readAll")}</button></header><div class="inbox-tabs" role="tablist" aria-label="${tHtml("notifications:notificationFilter")}"><button type="button" role="tab" aria-selected="true" data-inbox-filter="all">${tHtml("notifications:all")}</button><button type="button" role="tab" aria-selected="false" data-inbox-filter="unread">${tHtml("notifications:unread3")}</button></div><div class="inbox-scroll"><div data-inbox-items></div><button class="text-link" data-inbox-more>${tHtml("notifications:moreNotifications")}</button></div><footer class="inbox-footer">${tHtml("notifications:notificationsAreStoredFor7Days")}</footer>`);dialog.classList.add('inbox-dialog');dialog.classList.toggle('inbox-left',left);
    dialog.querySelectorAll('[data-inbox-filter]').forEach(button=>button.onclick=()=>{filter=button.dataset.inboxFilter;draw();});
    dialog.querySelector('[data-read-all]').onclick=async()=>{try{await ctx.api('/notifications/read-all','POST');for(const item of items.values())item.read=true;unread=0;badge();draw();}catch(e){ctx.toast(e.message);}};
    const more=dialog.querySelector('[data-inbox-more]');more.hidden=!data.more;more.onclick=async()=>{try{const next=await ctx.api('/notifications?before='+Math.min(...items.keys()));ingest(next,false);more.hidden=!next.more;}catch(e){ctx.toast(e.message);}};
    draw();
  }
  const socket=connectRoom('inbox',ctx,{state:activityState,pingInterval:10000,onMessage:data=>{
    if(data.type==='ready'){ingest(data,initialized);initialized=true;activity();}
    if(data.type==='notifications')ingest(data,true);
    if(data.type==='chat_changed')document.dispatchEvent(new Event('drg-inbox-change'));
  }});
  const namesChanged=()=>{if(dialog?.isConnected)draw();};document.addEventListener('drg-people-names-change',namesChanged);
  const presence=startPresence(()=>ctx);
  const resume=async()=>{if(disposed||document.visibilityState==='hidden')return;try{ingest(await ctx.api('/notifications?after='+cursor),false);if(!disposed)appBadge.set(unread,{force:true});}catch{}};
  document.addEventListener('drg-message-read',resume);
  document.addEventListener('visibilitychange',resume);
  document.addEventListener('visibilitychange',activity);
  for(const type of ['focus','blur','pageshow','pagehide'])window.addEventListener(type,activity);
  timer=setInterval(async()=>{if(disposed)return;for(const [id,item] of items)if(item.created<Date.now()/1000-7*86400||item.expires&&item.expires<=Date.now()/1000)items.delete(id);try{if(!socket.online){const data=await ctx.api('/notifications?after='+cursor);ingest(data,initialized);initialized=true;}}catch{}badge();draw();},5000);
  return {uid,open,attach(next){ctx=next;activity();const b=document.querySelector('#open-inbox');if(b)b.onclick=()=>open(b).catch(e=>ctx.toast(e.message));badge();presence.paint();},destroy(){disposed=true;avatars.destroy();dialog?.querySelector('.modal-close')?.click();document.removeEventListener('drg-people-names-change',namesChanged);socket.destroy();presence.destroy();clearInterval(timer);document.removeEventListener('drg-message-read',resume);document.removeEventListener('visibilitychange',resume);document.removeEventListener('visibilitychange',activity);for(const type of ['focus','blur','pageshow','pagehide'])window.removeEventListener(type,activity);appBadge.set(0,{force:true});document.querySelector('#notification-toasts')?.remove();}};
}
export function mountNotificationPreferences(host,ctx){
  const p={sound:true,popups:true,...ctx.state.me.settings.notifications};
  host.innerHTML=`<h2>${tHtml("notifications:notifications2")}</h2><form data-notification-settings><label class=check-label><input name=sound type=checkbox ${p.sound?'checked':''}>${tHtml("notifications:soundForNewMessagesAndNotifications")}</label><label class=check-label><input name=popups type=checkbox ${p.popups?'checked':''}>${tHtml("notifications:popUpNotifications")}</label><button class=secondary type=submit>${tHtml("notifications:saveNotificationSettings")}</button></form><div class=push-preferences><h3>${tHtml("notifications:pushNotificationsOnThisDevice")}</h3><p class=hint>${tHtml("notifications:receiveNotificationsWhileTheAppIsClosedNotifications")}</p><p class=hint role=status data-push-status>${tHtml("notifications:checkingNotificationAvailability")}</p><button type=button class=secondary data-push-enable hidden>${tHtml("notifications:enablePushNotifications")}</button><button type=button class=secondary data-push-test hidden>${tHtml("notifications:testNotificationDelivery")}</button><button type=button class=text-link data-push-disable hidden>${tHtml("notifications:disableOnThisDevice")}</button></div>`;
  $('[data-notification-settings]',host).onsubmit=async e=>{e.preventDefault();try{ctx.state.me.settings.notifications=await ctx.api('/notifications/preferences','PUT',{sound:host.querySelector('[name=sound]').checked,popups:host.querySelector('[name=popups]').checked});unlockAudio();ctx.toast(tText("notifications:notificationSettingsSaved"));}catch(error){ctx.toast(error.message);}};
  const status=$('[data-push-status]',host),enable=$('[data-push-enable]',host),disable=$('[data-push-disable]',host),test=$('[data-push-test]',host);
  async function refresh(){
    try{
      const state=await ctx.push.state();if(!host.isConnected)return;
      test.hidden=!state.enabled;enable.hidden=state.enabled;disable.hidden=!state.enabled&&!state.hasSubscription;
      enable.disabled=!state.configured||!state.supported||state.permission==='denied';
      disable.disabled=false;
      status.textContent=state.enabled?tText("notifications:pushNotificationsAreEnabledOnThisDevice"):!state.supported?pushSupportMessage(state.reason):!state.configured?tText("notifications:pushNotificationsHaveNotBeenConfiguredOnThe"):state.permission==='denied'?tText("notifications:notificationPermissionIsDeniedAllowDrgSiteNotifications"):tText("notifications:pushNotificationsAreDisabledOnThisDevice");
    }catch(error){if(host.isConnected){status.textContent=error.message;enable.hidden=false;enable.disabled=true;}}
  }
  enable.onclick=async()=>{
    enable.disabled=disable.disabled=true;
    try{await ctx.push.enable();ctx.toast(tText("notifications:messageNotificationsAreEnabled"));}
    catch(error){ctx.toast(error.message);}
    finally{await refresh();}
  };
  disable.onclick=async()=>{
    enable.disabled=disable.disabled=true;
    try{await ctx.push.disable();ctx.toast(tText("notifications:notificationsOnThisDeviceAreDisabled"));}
    catch(error){ctx.toast(error.message);}
    finally{await refresh();}
  };
  test.onclick=async()=>{
    test.disabled=true;
    try{await ctx.push.test();status.textContent=tText("notifications:thePushServiceHasBeenVerifiedASystem");}
    catch(error){status.textContent=error.message;}
    finally{test.disabled=false;}
  };
  refresh();
}
