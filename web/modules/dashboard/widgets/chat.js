import {tText,tHtml,locale} from "../../../core/i18n.js";
import {esc} from '../../../core/ui.js';
import {spaceAvatar} from '../../../core/space-avatar.js';
import {personName} from '../../../core/people.js';
import {renderMarkup} from '../../../core/dom.js';
import {filterRooms,messagePreview,messageTime} from '../../chat/format.js';
import {watchTelegramRooms,telegramAvatar} from '../../family-photos/index.js';
import {widgetSize} from '../sizes.js';
import {chatWidgetGeometry} from './chat-layout.js';
import './chat.css';

export function mountChatWidget(host,ctx){
  let disposed=false,loading=false,loaded=false,error='',rooms=[],telegram=[],watcher,frame,timer;
  const size=widgetSize(ctx.instance.size),compact=size==='1x1',abort=new AbortController();
  const section=host.closest('[data-widget]'),toolbar=section?.querySelector('.widget-toolbar');
  const all=document.createElement('button');all.type='button';all.className='text-link widget-chat-all';all.textContent=tText("dashboard:all");
  all.setAttribute('aria-label',tText("dashboard:openAllConversations"));toolbar?.insertBefore(all,toolbar.querySelector('[data-configure]'));
  host.innerHTML=`<div class="widget-chats"><div class="widget-chat-list" role="list" aria-label="${tHtml("dashboard:myConversations")}"></div><footer class="widget-chat-footer" role="status"></footer></div>`;
  const list=host.querySelector('.widget-chat-list'),footer=host.querySelector('.widget-chat-footer');
  const open=(id)=>{if(id){ctx.state.chatSpace=id;ctx.navigate('chat',{chatList:false});}else ctx.navigate('chat',{chatList:true});};
  all.onclick=()=>open();
  const render=()=>{
    if(disposed||!host.isConnected)return;
    const selected=ctx.instance.config?.space_id;
    const items=filterRooms([...rooms,...telegram].filter(room=>!selected||room.id===selected),'all').map(room=>room.kind==='friends'?{...room,name:personName(room.members.find(p=>p.id!==ctx.state.me.id)||room.members[0])}:room);
    all.textContent=tText("dashboard:all2", {value0:(rooms.length+telegram.length)});
    const geometry=chatWidgetGeometry(size,list.clientWidth,list.clientHeight,Math.max(1,parseFloat(getComputedStyle(host).fontSize)/14));
    list.dataset.layout=compact?'avatars':'rows';list.style.setProperty('--chat-widget-columns',geometry.columns);list.style.setProperty('--chat-widget-row',geometry.rowHeight+'px');
    if(!loaded&&!items.length){list.innerHTML='<p class="widget-chat-empty">'+(error?`${esc(error)} <button type="button" class="text-link" data-chat-retry>${tHtml("dashboard:retry")}</button>`:tText("dashboard:loadingConversations"))+'</p>';footer.textContent='';return;}
    const visible=items.slice(0,geometry.capacity),focused=list.contains(document.activeElement)?document.activeElement.closest('[data-widget-chat]')?.dataset.widgetChat:null;
    renderMarkup(list,visible.map(room=>{
      const message=room.last_message,preview=room.deletion?tText("dashboard:waitingToDelete"):messagePreview(message),sender=room.kind==='family'&&message?(message.sender_profile?personName(message.sender_profile):message.sender||''):'';
      const text=(sender?sender+': ':'')+preview,unread=Number.isSafeInteger(room.unread)&&room.unread>0?room.unread:0;
      const badge=unread?`<span class="widget-chat-unread" aria-label="${tHtml("dashboard:unread", {value0:(unread)})}">${unread>99?'99+':unread}</span>`:'';
      const stamp=message?.created,valid=Number.isFinite(stamp)&&stamp>0,date=valid?new Date(stamp*1000):null;
      const time=valid?`<time datetime="${date.toISOString()}">${date.toDateString()===new Date().toDateString()?messageTime(stamp):date.toLocaleDateString(locale(),{day:'numeric',month:'short'})}</time>`:'';
      const avatar=room.id.startsWith('telegram:')?telegramAvatar(room):spaceAvatar(room,ctx.state.me);
      return `<div role="listitem"><button type="button" class="widget-chat-item" data-widget-chat="${esc(room.id)}" title="${esc(room.name+' · '+text)}" aria-label="${esc(room.name+(unread?tText("dashboard:unread2", {value0:(unread)}):''))}"><span class="widget-chat-avatar">${avatar}${compact?badge:''}</span><span class="widget-chat-copy"><strong>${esc(room.name)}</strong>${compact?'':`<span class="widget-chat-preview">${esc(text)}</span>`}</span>${compact?'':`<span class="widget-chat-meta">${time}${badge}</span>`}</button></div>`;
    }).join('')||'<p class="widget-chat-empty">'+(items.length?tText("dashboard:expandTheWidgetToShowConversations"):`${tHtml("dashboard:noChatsYet")} <button type="button" class="text-link" data-widget-social>${tHtml("dashboard:peopleAndGroups")}</button>`)+'</p>');
    if(focused)list.querySelector(`[data-widget-chat="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
    footer.textContent=error?tText("dashboard:failedToUpdateConversations"):items.length?tText("dashboard:outOfConversations", {value0:(visible.length), value1:(items.length), value2:(visible.length<items.length?(" "+tText("dashboard:othersInAll")+""):'')}):'';
  };
  const schedule=()=>{if(frame===undefined)frame=requestAnimationFrame(()=>{frame=undefined;render();});};
  const load=async()=>{
    if(disposed||loading||document.hidden)return;
    loading=true;
    try{const data=await ctx.api('/chat/rooms','GET',undefined,true,{}, {signal:abort.signal});if(disposed)return;rooms=data.items;loaded=true;error='';render();}
    catch(e){if(!disposed){error=e.message;render();}}
    finally{loading=false;}
  };
  host.onclick=e=>{const button=e.target.closest('button');if(!button)return;if(button.dataset.widgetChat)open(button.dataset.widgetChat);else if(button.hasAttribute('data-widget-social'))ctx.navigate('social');else if(button.hasAttribute('data-chat-retry'))load();};
  const refresh=()=>{clearTimeout(timer);timer=setTimeout(load,180);};
  const events=[[document,'drg-inbox-change',refresh],[document,'visibilitychange',refresh],[window,'focus',refresh],[window,'online',refresh],[document,'drg-people-names-change',schedule]];
  for(const [target,type,handler] of events)target.addEventListener(type,handler);
  const observer=new ResizeObserver(schedule);observer.observe(list);
  const poll=setInterval(load,20000);render();load();
  watchTelegramRooms(ctx,items=>{if(!disposed){telegram=items;render();}}).then(next=>{if(disposed)next?.destroy();else watcher=next;});
  return ()=>{disposed=true;abort.abort();watcher?.destroy();observer.disconnect();clearInterval(poll);clearTimeout(timer);if(frame!==undefined)cancelAnimationFrame(frame);for(const [target,type,handler] of events)target.removeEventListener(type,handler);all.remove();host.onclick=null;};
}
