import {tText,tHtml,locale} from "../../core/i18n.js";
import {messageFooter,attachmentMarkup} from '../chat/message-layout.js';
import {telegramComposer} from './composer.js';
import {personAvatar,personName} from '../../core/people.js';
import {messageDay as day,messageTime as time,chatDayLabel,sameMessageGroup} from '../chat/format.js';
import {botReactionNotice,reactionsMarkup,bindReactions,hasReactions,reactionFooter} from '../chat/reactions.js';
import {telegramPerson,telegramText,telegramAvatars} from './details.js';
import {videoControls} from './video-controls.js';
import {inlineVideos} from '../chat/video-preview.js';
import {roomMuted} from '../chat/notification-settings.js';
import {connectionMarkup,connectionLabel} from './connection-status.js';
import {syncMarkup} from './sync-status.js';
import {familyTimeline,familyMediaCount} from './timeline.js';
import {familyMonthNavigation} from './month-navigation.js';
import {stickerMarkup,stickerPlayers} from './stickers.js';
import {sectionHeader} from '../../core/section-header.js';
import {$,esc} from '../../core/ui.js';
import {chatIcon} from '../chat/icons.js';
import {chatComposerMarkup} from '../chat/composer.js';
import {bindMessagePress,messageMenu} from '../chat/message-menu.js';
import {renderMarkup} from '../../core/dom.js';
import {edgePagination} from '../../core/edge-pagination.js';
import './family-photos.css';
import '../chat/media.css';
import {animationVideo,albumMarkup,mediaFrame,telegramAlbums,fitAlbums,videoPoster,prepareVideos,visualMedia} from '../chat/media.js';

let active;
export const familyPhotosController={destroy(){active?.destroy();active=null;},async flush(){return true;}};
const base=gid=>`/family-photos/groups/${encodeURIComponent(gid)}`;
const voiceMarkup=message=>(message.media||[]).filter(file=>['voice','audio','video_note'].includes(file.kind)).map(file=>attachmentMarkup({...file,duration:file.duration??message.duration})).join('');
const thumb=file=>file.thumbnail_url||(file.kind==='photo'&&file.state==='ready'?file.url:null);
const placeholder=file=>file.state==='paused'?(file.kind==='animation'?tText("family_photos:thisGifIsNotAvailableInTg"):file.kind==='video'?tText("family_photos:thisVideoIsNotAvailableInTg"):tText("family_photos:thisPhotoIsNotAvailableInTg")):file.state==='ready'?tText("family_photos:video"):file.state==='failed'?tText("family_photos:waitingForReUpload"):tText("family_photos:uploading");
export const telegramAvatar=group=>`<span class="chat-group-avatar" aria-hidden="true">${group.avatar_url?`<img src="${esc(group.avatar_url)}" alt="">`:chatIcon('users')}</span>`;
// Archive senders have Telegram IDs, not local account IDs. Keep their fallback
// avatars separate from local profiles and private nicknames.
const senderAvatar=message=>`<div class="chat-message-avatar">${message.site_profile?personAvatar(message.site_profile):telegramPerson({name:message.sender,avatar_url:message.sender_avatar_url})}</div>`;
const blocked=e=>[401,403,404].includes(e.status);

export function openFamilyViewer(ctx,files,start=0,{more,onMessage}={}){
  if(!files.length)return {destroy(){}};
  let index=start,disposed=false,controlsCleanup;
  document.dispatchEvent(new CustomEvent('drg-media-viewer',{detail:{open:true}}));
  const d=ctx.modal('<div class="fp-viewer" data-fp-viewer></div>');
  d.classList.add('fp-viewer-dialog');
  const host=$('[data-fp-viewer]',d);
  const stop=()=>{controlsCleanup?.();controlsCleanup=null;host.querySelectorAll('video').forEach(v=>{v.pause();v.removeAttribute('src');v.load();});};
  const cleanup=()=>{disposed=true;stop();document.dispatchEvent(new CustomEvent('drg-media-viewer',{detail:{open:false}}));document.removeEventListener('keydown',keydown);};
  const destroy=()=>{if(disposed)return;cleanup();d.querySelector('.modal-close')?.click();};
  const show=()=>{
    if(disposed||!d.isConnected)return;
    const file=files[index];
    stop();
    host.innerHTML=`<div class="fp-viewer-stage">${file.state==='ready'?(file.kind==='video'||animationVideo(file)?`<video ${file.kind==='animation'?'muted loop':''} playsinline preload="metadata" src="${esc(file.url)}" poster="${esc(videoPoster(file))}" aria-label="${file.kind==='animation'?'GIF':tText("family_photos:video2")}"></video>${file.kind==='animation'?'<span class="chat-video-timer chat-gif-mark">GIF</span>':''}`:`<img src="${esc(file.url)}" alt="${esc(file.name||tText("family_photos:photo"))}">`):`<p role="status">${placeholder(file)}</p>`}</div><p class="hint" data-video-error hidden>${tHtml("family_photos:browserCouldNotPlayThisVideoItCan")}</p><div class="fp-viewer-controls"><button class="secondary" data-fp-prev aria-label="${tHtml("family_photos:previousPhoto")}" ${index===0?'disabled':''}>←</button><span>${index+1} / ${files.length} · ${esc(day(file.created))}</span><button class="secondary" data-fp-next aria-label="${tHtml("family_photos:nextPhoto")}" ${index===files.length-1&&!more?'disabled':''}>→</button></div><div class="fp-viewer-caption"><span>${esc(file.sender||'')}</span>${file.state==='ready'?`<a href="${esc(file.url)}${file.url.includes('?')?'&':'?'}download=1" download class="fp-viewer-download" aria-label="${tHtml("family_photos:downloadFile")}" title="${tHtml("family_photos:download")}">${chatIcon('download')}</a>`:''}${onMessage?`<button class="text-link" data-fp-message>${tHtml("family_photos:toMessage")}</button>`:''}</div>`;
    prepareVideos(host);const video=host.querySelector('video');if(video){if(file.kind==='animation'){video.muted=true;video.loop=true;video.play().catch(()=>{});}else controlsCleanup=videoControls($('.fp-viewer-stage',host),video);}
    host.querySelector('video')?.addEventListener('error',()=>{$('[data-video-error]',host).hidden=false;},{once:true});
    $('[data-fp-prev]',host).onclick=()=>step(-1);
    $('[data-fp-next]',host).onclick=()=>step(1);
    $('[data-fp-message]',host)?.addEventListener('click',()=>{destroy();onMessage(file);});
  };
  const step=async(delta)=>{
    if(index+delta>=files.length&&more){await more();if(disposed)return;}
    index=Math.max(0,Math.min(files.length-1,index+delta));show();
  };
  const keydown=e=>{if(!d.isConnected){destroy();return;}if(e.target.matches('input,select'))return;if(e.key==='ArrowLeft'){e.preventDefault();step(-1);}if(e.key==='ArrowRight'){e.preventDefault();step(1);}if(e.key==='Escape')destroy();};
  document.addEventListener('keydown',keydown);
  d.querySelector('.modal-close')?.addEventListener('click',cleanup,{once:true});
  d.addEventListener('click',e=>{if(e.target===d)cleanup();});
  show();return {destroy,isOpen:()=>!disposed&&d.isConnected};
}

export async function mountFamilyPhotos(host,ctx){
  familyPhotosController.destroy();
  const scope={closed:false,viewer:null,timer:null,syncTimer:null,abort:new AbortController(),destroy(){this.closed=true;this.abort.abort();document.removeEventListener('visibilitychange',visible);clearTimeout(this.timer);clearTimeout(this.syncTimer);this.viewer?.destroy();this.pagination?.destroy();this.monthNavigation?.destroy();}};
  active=scope;
  let groups=[],gid=null,files=[],more=false,loading=null,seq=0,revision=0,syncStatus=null,galleryVersions=false,galleryVersion=null,refreshing=false,syncing=null,query='',syncExpanded=false;
  let indexAvailable=false,indexItems=[],indexVersion=null,indexLoaded=false,indexError=false,indexChecked=0,indexLoading=null,windowEnd=0;
  const timezone=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
  host.innerHTML=`<div class="page-heading"><p class="eyebrow">${tHtml("family_photos:familyPhotos")}</p><h1>${tHtml("family_photos:familyPhotos2")}<span class="hello-dot">.</span></h1></div><div data-fp-main><p role="status">${tHtml("family_photos:connectingToFamilyPhotos")}</p></div><p class="hint" data-fp-version hidden></p>`;
  sectionHeader(host,ctx);
  const main=$('[data-fp-main]',host);
  main.classList.add('fp-gallery');
  scope.monthNavigation=familyMonthNavigation(main,{onMonth:key=>jumpMonth(key)});
  const request=(path,method='GET',body)=>ctx.api(path,method,body,true,{}, {signal:scope.abort.signal});
  const galleryPath=(kind,args={})=>{
    const params=new URLSearchParams();
    if(windowEnd)params.set('until',String(windowEnd));
    for(const [key,value]of Object.entries(args))if(value!=null)params.set(key,String(value));
    return base(gid)+kind+(params.size?'?'+params:'');
  };
  const loadIndex=async(current)=>{
    if(!indexAvailable||indexLoading===current)return;
    indexLoading=current;indexChecked=Date.now();
    try{
      const data=await request(base(gid)+'/media-index?timezone='+encodeURIComponent(timezone));
      if(scope.closed||scope.abort.signal.aborted||current!==seq)return;
      const changed=!indexLoaded||data.version!==indexVersion||indexError;
      indexItems=(data.items||[]).filter(m=>/^\d{4}-(0[1-9]|1[0-2])$/.test(m.key)&&Number.isSafeInteger(m.end)&&Number.isSafeInteger(m.start)&&m.end>m.start&&Number.isSafeInteger(m.count)&&m.count>0);
      indexVersion=data.version;indexLoaded=true;indexError=false;
      if(changed&&!scope.viewer?.isOpen())draw();
    }catch(error){
      if(scope.closed||scope.abort.signal.aborted||current!==seq||error.name==='AbortError')return;
      if(blocked(error)){fail(error);return;}
      indexError=true;draw();
    }finally{if(indexLoading===current)indexLoading=null;}
  };
  const jumpMonth=async(key)=>{
    const month=indexItems.find(m=>m.key===key);if(!month)return;
    const pending=select(gid,{until:month.end}),current=seq;await pending;
    if(!scope.closed&&!scope.abort.signal.aborted&&current===seq)scope.monthNavigation.jump(key);
  };
  const fail=e=>{
    if(scope.closed||e.name==='AbortError')return;
    scope.viewer?.destroy();
    galleryVersion=null;
    if(blocked(e)){files=[];scope.abort.abort();clearTimeout(scope.timer);clearTimeout(scope.syncTimer);}
    renderMarkup(main,`<section class="panel"><h2>${blocked(e)?tText("family_photos:accessIsRestricted"):tText("family_photos:serviceIsTemporarilyUnavailable")}</h2><p>${esc(e.message)}</p><button class="secondary" data-fp-retry>${tHtml("family_photos:retry")}</button></section>`);
    $('[data-fp-retry]',main).onclick=()=>mountFamilyPhotos(host,ctx);
  };
  const paintSync=(target,status,group)=>{
    renderMarkup(target,syncMarkup(status,group,{disclosure:true,open:syncExpanded}));
  };
  const openMessage=file=>{ctx.state.chatSpace='telegram:'+file.group_id;ctx.state.telegramMessage=file.message_id;ctx.navigate('chat',{chatList:false});};
  const draw=()=>{
    if(scope.closed||scope.abort.signal.aborted)return;
    const group=groups.find(g=>g.id===gid);
    const days=familyTimeline(files,query),months=indexItems;
    const tiles=items=>items.map(({file,index})=>`<button class="fp-tile ${file.state==='paused'?'is-unavailable':''}" data-fp-media-id="${esc(file.id)}" data-fp-file="${index}" aria-label="${esc((file.kind==='video'?tText("family_photos:video2"):tText("family_photos:photo"))+' · '+day(file.created)+' · '+file.sender)}">${thumb(file)?`<img src="${esc(thumb(file))}" alt="" loading="lazy">`:`<span>${placeholder(file)}</span>`}${file.kind==='video'&&file.state!=='paused'?`<span class="fp-video-mark" aria-hidden="true">${chatIcon('play')}</span>`:''}${file.album_id?`<span class="fp-album-mark" title="${tHtml("family_photos:photoFromAlbum")}" aria-hidden="true">${chatIcon('image')}</span>`:''}<span class="fp-tile-label">${esc(file.sender)} · ${esc(time(file.created))}</span></button>`).join('');
    renderMarkup(main,`<div class="fp-toolbar fp-timeline-toolbar"><label class="fp-group-picker">${chatIcon('users')}<select data-fp-group-select aria-label="${tHtml("family_photos:familyGroups")}">${groups.map(g=>`<option value="${esc(g.id)}" ${g.id===gid?'selected':''}>${esc(g.title)}</option>`).join('')}</select></label>${ctx.moduleEnabled('chat')&&gid?`<button class="secondary fp-open-chat" data-fp-chat>${chatIcon('message')}<span>${tHtml("family_photos:toConversation")}</span></button>`:''}<label class="fp-gallery-search">${chatIcon('search')}<input type="search" data-fp-search value="${esc(query)}" placeholder="${tHtml("family_photos:searchLoadedPhotos")}" aria-label="${tHtml("family_photos:searchLoadedPhotos")}"></label><div data-fp-state>${syncMarkup(syncStatus,group,{disclosure:true,open:syncExpanded})}</div></div>${group?.copy_created?`<p class="hint fp-copy-note">${tHtml("family_photos:copyFromReadOnly", {value0:(esc(new Date(group.copy_created*1000).toLocaleString(locale()))), value1:(group.copy_complete?'':(" "+tText("family_photos:incompleteHistoryOrMedia")+""))})}</p>`:''}<div class="fp-timeline-layout"><div class="fp-timeline">${days.map(d=>`<section class="fp-day-section" data-fp-day="${esc(d.key)}"><header class="fp-day-heading"><h2>${esc(d.label)}</h2><span>${esc(familyMediaCount(d.items))}</span></header><div class="fp-grid">${tiles(d.items)}</div></section>`).join('')}${!days.length?`<section class="panel fp-empty">${files.length?tHtml('family_photos:noMatchingPhotos'):tHtml("family_photos:photosAndVideosWillAppearHereAsThey")}</section>`:''}${more?(query?`<button type="button" class="secondary fp-load" data-fp-more>${tHtml('family_photos:loadMorePhotos')}</button>`:'<div class="page-edge" data-page-edge role="status"></div>'):''}</div>${indexAvailable?`<nav class="fp-date-rail" aria-label="${tHtml('family_photos:jumpToMonth')}">${indexError?`<button type="button" data-fp-index-retry>${tHtml('family_photos:retryMonthIndex')}</button>`:!indexLoaded?`<span role="status">${tHtml('family_photos:loadingMonths')}</span>`:''}${months.map(d=>`<button type="button" id="fp-month-${esc(d.key)}" data-fp-jump="${esc(d.key)}" data-fp-month="${esc(d.key)}" ${scope.monthNavigation.current()===d.key?'aria-current="location"':''}>${esc(new Date(d.key+'-15T12:00:00').toLocaleDateString(locale(),{month:'short',year:'numeric'}))}</button>`).join('')}</nav>`:''}</div>`);
    const picker=$('[data-fp-group-select]',main);if(picker)picker.onchange=()=>select(picker.value);
    main.querySelectorAll('[data-fp-file]').forEach(b=>b.onclick=()=>{scope.viewer?.destroy();scope.viewer=openFamilyViewer(ctx,files,Number(b.dataset.fpFile),{more:more?loadMore:null,onMessage:ctx.moduleEnabled('chat')?openMessage:null});});
    scope.monthNavigation.update();
    const retryIndex=$('[data-fp-index-retry]',main);if(retryIndex)retryIndex.onclick=()=>loadIndex(seq);
    const moreButton=$('[data-fp-more]',main);if(moreButton)moreButton.onclick=async()=>{moreButton.disabled=true;try{await loadMore();}catch(error){if(!scope.closed)ctx.toast(error.message);}finally{if(moreButton.isConnected)moreButton.disabled=false;}};
    scope.pagination?.update();
    const chatButton=$('[data-fp-chat]',main);
    if(chatButton)chatButton.onclick=()=>{ctx.state.chatSpace='telegram:'+gid;ctx.navigate('chat',{chatList:false});};
  };
  main.addEventListener('input',event=>{if(!event.target.matches('[data-fp-search]'))return;query=event.target.value;draw();});
  main.addEventListener('toggle',event=>{if(event.target.matches('[data-fp-sync-details]'))syncExpanded=event.target.open;},true);
  const loadMore=()=>{
    if(loading)return loading;
    if(!more||!gid||!files.length)return;
    const current=seq,cursor=files.at(-1).message_id;revision++;galleryVersion=null;
    const pending=(async()=>{
      const result=await request(galleryPath('/media',{before:cursor}));
      if(scope.closed||scope.abort.signal.aborted||document.hidden||current!==seq)return;
      const known=new Set(files.map(file=>file.id));files.push(...result.items.filter(file=>!known.has(file.id)));more=result.more&&result.items.some(file=>file.message_id<cursor);draw();
    })();
    loading=pending;
    return pending.finally(()=>{if(loading===pending)loading=null;});
  };
  scope.pagination=edgePagination(main,{canLoad:()=>!scope.closed&&!query&&!loading&&more&&files.length>0&&!scope.viewer?.isOpen(),load:loadMore,onError:e=>{if(!scope.closed)ctx.toast(e.message);}});
  const select=async(value,{until=0}={})=>{
    const groupChanged=gid!==value;windowEnd=until;
    if(groupChanged){indexItems=[];indexVersion=null;indexLoaded=false;indexError=false;indexChecked=0;indexLoading=null;}
    scope.viewer?.destroy();query='';syncExpanded=false;gid=value;ctx.state.familyPhotoGroup=value;seq++;revision++;files=[];more=false;syncStatus=null;galleryVersion=null;clearTimeout(scope.syncTimer);const current=seq;pollSync();
    draw();
    if(!gid)return;
    try{
      const [token,result]=await Promise.all([
        galleryVersions?request(galleryPath('/media-version',{through:0})):null,
        request(galleryPath('/media')),
        !indexLoaded?loadIndex(current):null
      ]);
      if(scope.closed||scope.abort.signal.aborted||current!==seq)return;
      files=result.items;more=result.more;galleryVersion=token?.version||null;draw();
    }
    catch(e){fail(e);}
  };
  main.addEventListener('click',event=>{const target=event.target.closest('[data-fp-target-group]');if(target&&groups.some(group=>group.id===target.dataset.fpTargetGroup))select(target.dataset.fpTargetGroup);});
  // A cheap scoped status request updates independently of photos/history.
  // Polling never replaces the gallery, reloads an image or moves its focus.
  const pollSync=async()=>{
    const current=seq,selected=gid;
    if(scope.closed||scope.abort.signal.aborted||document.hidden||!selected||syncing===current)return;
    syncing=current;
    try{
      const result=await request(base(selected)+'/sync');
      if(scope.closed||scope.abort.signal.aborted||current!==seq)return;
      syncStatus=result;
      const target=$('[data-fp-state]',main);
      if(target)paintSync(target,result,groups.find(g=>g.id===selected));
    }catch(e){
      if(scope.closed||scope.abort.signal.aborted||current!==seq)return;
      // An old source service can remain readable during a rolling update.
      if(e.status===403||e.status===401){scope.abort.abort();clearTimeout(scope.timer);fail(e);return;}
      syncStatus={state:'status_unavailable',batch:{},pending:0,history_complete:true};
      const target=$('[data-fp-state]',main);
      if(target)paintSync(target,syncStatus,groups.find(g=>g.id===selected));
    }finally{
      if(syncing===current)syncing=null;
      if(!scope.closed&&!scope.abort.signal.aborted&&!document.hidden&&current===seq){
        const idle=syncStatus?.state==='idle'&&!syncStatus.pending&&!syncStatus.current&&!syncStatus.pause;
        scope.syncTimer=setTimeout(pollSync,idle?10000:2000);
      }
    }
  };
  const refresh=async()=>{
    if(scope.closed||scope.abort.signal.aborted||document.hidden||refreshing)return;
    refreshing=true;
    try{
      if(loading)return;const version=revision;
      const data=await request('/family-photos/groups');if(scope.closed||scope.abort.signal.aborted||document.hidden)return;
      galleryVersions=data.status?.gallery_versions===true;
      indexAvailable=data.status?.gallery_index===true;
      const versionLabel=$('[data-fp-version]',host);
      versionLabel.textContent=data.status?.service_version?`${tText("family_photos:familyPhotos2")} ${data.status.service_version}`:'';
      versionLabel.hidden=!versionLabel.textContent;
      groups=data.items;
      if(!groups.some(g=>g.id===gid)){files=[];scope.viewer?.destroy();await select(groups.find(group=>group.id===ctx.state.familyPhotoGroup)?.id||groups[0]?.id);}
      else{
        const current=seq;
        if(indexAvailable&&Date.now()-indexChecked>60000)await loadIndex(current);
        const token=galleryVersions?await request(galleryPath('/media-version',{through:files.at(-1)?.message_id||0})):null;
        if(scope.closed||scope.abort.signal.aborted||document.hidden||current!==seq||version!==revision)return;
        if(token?.version&&token.version===galleryVersion){if(!scope.viewer?.isOpen())draw();return;}
        // Re-read loaded history to propagate edits/deletes, not only the newest page.
        let updated=[],before='',hasMore=true;
        while(hasMore&&updated.length<Math.max(files.length,60)){
          if(document.hidden)return;
          const page=await request(galleryPath('/media',before?{before}:{}));
          if(scope.closed||scope.abort.signal.aborted||current!==seq||version!==revision)return;
          updated.push(...page.items);hasMore=page.more;before=page.items.at(-1)?.message_id;
          if(!before)break;
        }
        if(scope.closed||scope.abort.signal.aborted||current!==seq||version!==revision)return;
        if(files.some(old=>!updated.some(f=>f.id===old.id)))scope.viewer?.destroy();
        files.splice(0,files.length,...updated);more=hasMore;
        galleryVersion=token?.version||null;
        if(!scope.viewer?.isOpen())draw();
      }
    }catch(e){fail(e);}
    finally{refreshing=false;if(!scope.closed&&!scope.abort.signal.aborted&&!document.hidden)scope.timer=setTimeout(refresh,10000);}
  };
  const visible=()=>{
    clearTimeout(scope.timer);clearTimeout(scope.syncTimer);indexChecked=0;
    if(!document.hidden&&!scope.closed&&!scope.abort.signal.aborted){refresh();pollSync();}
  };
  document.addEventListener('visibilitychange',visible);
  await refresh();
}


export async function watchTelegramRooms(ctx,onChange){
  if(!ctx.moduleEnabled('family_photos'))return null;
  let closed=false,timer=null,lastRooms=[];
  const read=event=>{if(closed)return;lastRooms=lastRooms.map(room=>room.group_id===event.detail?.group_id?{...room,unread:0}:room);onChange(lastRooms);};
  document.addEventListener('drg-family-read',read);
  const refresh=async()=>{
    try{
      const data=await ctx.api('/family-photos/groups');if(closed)return;
      const items=await Promise.all(data.items.map(async group=>{
        try{
          const history=await ctx.api(base(group.id)+'/messages?limit=1');
          const latest=history.items.at(-1);
          return {id:'telegram:'+group.id,group_id:group.id,name:group.title,avatar_url:group.avatar_url,kind:'family',
            members:[],unread:group.unread||0,mute_until:group.mute_until||0,pinned:false,favorite:false,
            last_message:latest?{id:latest.id,created:latest.created,body:latest.content_type==='voice'?tText("family_photos:audioMessage"):latest.text,sender:latest.sender,
              attachments:(latest.media||[]).map(file=>({kind:file.kind==='photo'?'image':file.kind,name:file.name}))}:null};
        }catch{return null;}
      }));
      if(!closed){lastRooms=items.filter(Boolean);onChange(lastRooms);}
    }catch{if(!closed)onChange([]);}
    finally{if(!closed)timer=setTimeout(refresh,15000);}
  };
  await refresh();return {destroy(){closed=true;clearTimeout(timer);document.removeEventListener('drg-family-read',read);}};
}

export function mountTelegramChat(host,ctx,gid,onBack){
  let closed=false,timer=null,viewer=null,seq=0,items=[],more=false,before=0,context=null,contextId=null,loadingOlder=null,revision=0;
  const abort=new AbortController(),request=(path,method='GET',body)=>ctx.api(path,method,body,true,{}, {signal:abort.signal});
  const pending=new Map();
  host.innerHTML=`<div class="fp-chat"><header class="chat-header"><button class="chat-back" aria-label="${tHtml("family_photos:toChatList")}" data-fp-back>${chatIcon('back')}</button><div class="chat-header-identity" data-fp-identity><h2 data-fp-title>${tHtml("family_photos:uploadingConversation")}</h2></div><div class="chat-header-actions"><button type="button" aria-label="${tHtml("family_photos:disableNotifications")}" title="${tHtml("family_photos:disableNotifications")}" data-fp-notifications aria-pressed="false" disabled>${chatIcon('bell')}</button><button aria-label="${tHtml("family_photos:photos")}" title="${tHtml("family_photos:photos")}" data-fp-gallery>${chatIcon('image')}</button></div></header><div class="chat-retention chat-retention-inline fp-chat-status" data-fp-chat-status role="status">${tHtml("family_photos:connecting")}</div><div class="chat-messages fp-chat-history" data-fp-history role="log" aria-label="${tHtml("family_photos:telegramConversation")}"></div>${chatComposerMarkup({formId:'fp-send-message',textareaId:'fp-message',telegram:true})}</div>`;
  let muteUntil=0,notificationRevision=0,savingNotifications=false;
  const notificationButton=$('[data-fp-notifications]',host);
  const drawNotifications=()=>{const muted=roomMuted({mute_until:muteUntil});notificationButton.innerHTML=chatIcon(muted?'bellOff':'bell');notificationButton.setAttribute('aria-pressed',String(muted));notificationButton.title=muted?tText("family_photos:enableNotifications"):tText("family_photos:disableNotifications");notificationButton.setAttribute('aria-label',notificationButton.title);notificationButton.disabled=savingNotifications;};
  notificationButton.onclick=async()=>{if(savingNotifications)return;savingNotifications=true;notificationRevision++;drawNotifications();try{const data=await request(`/chat/telegram:${gid}/notifications`,'PUT',{seconds:roomMuted({mute_until:muteUntil})?0:-1});if(closed)return;notificationRevision++;muteUntil=data.mute_until;document.dispatchEvent(new CustomEvent('drg-chat-notifications',{detail:{id:'telegram:'+gid,mute_until:muteUntil}}));ctx.toast(muteUntil?tText("family_photos:notificationsAreDisabledUnreadMessagesArePreserved"):tText("family_photos:notificationsEnabled"));}catch(error){if(!closed)ctx.toast(error.message);}finally{savingNotifications=false;if(!closed)drawNotifications();}};
  const history=$('[data-fp-history]',host),form=$('form',host),notice=$('[data-fp-chat-status]',host);
  const stopAudio=()=>history.querySelectorAll('audio').forEach(audio=>{audio.pause();audio.removeAttribute('src');audio.load();});
  const stickers=stickerPlayers(history,ctx,abort.signal),previews=inlineVideos(history),avatars=telegramAvatars(abort.signal);
  let reactionMirror=false;
  const reactions=bindReactions(history,ctx,{telegramMirror:()=>reactionMirror,canReact:()=>!form.hidden,getMessage:id=>telegramAlbums(items).find(m=>m.id===id),endpoint:id=>base(gid)+'/messages/'+id+'/reactions',onChange:(id,data)=>{items=items.map(m=>m.id===id?{...m,site_reactions:data.reactions,bot_reaction_sync:data.bot_reaction_sync}:m);draw(false,true);},onPeople:dialog=>avatars.hydrate(dialog)});
  let readSent=0,readPending=false;
  const markRead=async()=>{
    if(closed||readPending||document.hidden||!document.hasFocus()||ctx.state.chatSpace!=='telegram:'+gid)return;
    if(history.scrollTop+history.clientHeight<history.scrollHeight-100)return;
    const latest=items.at(-1)?.id;if(!latest||latest<=readSent)return;
    readPending=true;
    try{await request(base(gid)+'/read','POST',{message_id:latest});if(closed)return;readSent=latest;document.dispatchEvent(new CustomEvent('drg-family-read',{detail:{group_id:gid}}));document.dispatchEvent(new Event('drg-inbox-change'));}
    catch(error){if([401,403].includes(error.status))denied(error);}
    finally{readPending=false;}
  };
  history.addEventListener('scroll',markRead,{passive:true});document.addEventListener('visibilitychange',markRead);window.addEventListener('focus',markRead);
  const closeMenu=()=>{context?.destroy();context=null;contextId=null;};
  const composer=telegramComposer(host,ctx,gid,{sent(result){pending.set(result.id,{...result,label:tText("family_photos:sending")});draw(true);},changed(){refresh(false);},closeMenus:closeMenu});
  const setReply=id=>{const message=items.find(message=>message.id===id);if(message)composer.reply(message);};
  const openMedia=id=>{const files=items.flatMap(m=>m.media.filter(visualMedia).map(f=>({...f,sender:m.sender})));viewer?.destroy();viewer=openFamilyViewer(ctx,files,files.findIndex(f=>f.id===id));};
  const press=bindMessagePress(history,'[data-fp-message-id]',(article,x,y,touch)=>{
    if(context)return;
    const message=telegramAlbums(items).find(m=>m.id===Number(article.dataset.fpMessageId));if(!message)return;
    const actions=[];
    if(!form.hidden)actions.push({id:'reply',icon:'reply',label:tText("family_photos:reply2"),run:()=>setReply(message.id)});
    if(message.site_actor_id===ctx.state.me.id&&message.site_primary&&message.site_editable_until>Date.now()/1000&&['delivered','edit_failed'].includes(message.site_edit_state)&&!form.hidden)actions.push({id:'edit',icon:'compose',label:tText("family_photos:edit"),run:()=>composer.edit(message)});
    if(message.site_send_id&&['queued','failed','uncertain'].includes(message.site_edit_state)&&message.site_actor_id===ctx.state.me.id)actions.push({id:'cancel',icon:'close',label:tText("family_photos:cancelSend"),run:async()=>{await request(base(gid)+'/site-sends/'+message.site_send_id+'/cancel','POST',{});refresh(false);}});
    if(message.text)actions.push({id:'copy',icon:'copy',label:tText("family_photos:copyText"),run:async()=>{try{await navigator.clipboard.writeText(message.text);ctx.toast(tText("family_photos:copied"));}catch{ctx.toast(tText("family_photos:couldNotCopyText"));}}});
    if(message.media.some(visualMedia))actions.push({id:'media',icon:'image',label:tText("family_photos:openMedia"),run:()=>openMedia(message.media.find(visualMedia).id)});
    contextId=message.id;context=messageMenu({article,x,y,touch,actions,reactions:reactions.menu(message,article),footer:day(message.created)+' · '+time(message.created),onClose:()=>{context=null;contextId=null;}});
  });
  $('[data-fp-back]',host).onclick=()=>{closeMenu();onBack();};$('[data-fp-gallery]',host).onclick=()=>{closeMenu();ctx.navigate('family_photos');};
  const denied=error=>{if(closed)return;closeMenu();viewer?.destroy();if(blocked(error)){stopAudio();items=[];history.replaceChildren();stickers.sync();form.hidden=true;}notice.hidden=false;renderMarkup(notice,esc(error.message));};
  const draw=(follow=false,preserve=false)=>{
    const rect=history.getBoundingClientRect(),anchor=preserve?[...history.querySelectorAll('[data-fp-message-id]')].find(el=>el.getBoundingClientRect().bottom>rect.top):null;
    const anchorId=anchor?.dataset.fpMessageId,offset=anchor?anchor.getBoundingClientRect().top-rect.top:0;
    const top=history.scrollTop,height=history.scrollHeight,atEnd=!preserve&&!context&&top+history.clientHeight>=height-100;
    if(contextId&&!items.some(m=>m.id===contextId))closeMenu();
    const audioPlayers=[...history.querySelectorAll('audio')];
    let lastDay='';
    const changed=renderMarkup(history,`${more?'<div class="page-edge" data-page-edge role="status"></div>':''}`+telegramAlbums(items).map((message,index,albums)=>{
      const grouped=sameMessageGroup(albums[index-1],message),groupEnd=!sameMessageGroup(message,albums[index+1]);
      const stamp=day(message.created),separator=stamp!==lastDay?`<div class="chat-day fp-chat-day" data-fp-day="${esc(stamp)}"><time>${esc(chatDayLabel(message.created))}</time></div>`:'';lastDay=stamp;
      const source=items.find(m=>m.id===message.reply_to);
      const sticker=message.content_type==='sticker'||message.media.some(file=>file.kind==='sticker'),visual=message.media.some(visualMedia),documents=message.media.filter(file=>file.kind==='file'),mine=message.site_actor_id===ctx.state.me.id,footer=messageFooter(message,mine);
      return `${separator}<article class="chat-message fp-chat-message ${mine?'mine':''} ${message.media.length===1&&message.media[0].kind==='video_note'&&!message.text?'is-circle-only':''} ${grouped?'grouped':''} ${groupEnd?'group-end':''} ${sticker?'fp-sticker-message':''} ${message.custom_emoji?'fp-emoji-message':''} ${visual?'has-visual-media':''} ${visual&&!message.text&&!message.reply_to?'media-only':''}" data-fp-message-id="${message.id}" tabindex="0" aria-haspopup="menu">${message.sources.slice(1).map(source=>`<span class="chat-media-anchor" data-fp-source-message-id="${source.id}"></span>`).join('')}${mine?'':senderAvatar(message)+`<div class="chat-message-meta"><strong>${esc(message.site_profile?personName(message.site_profile):message.sender)}</strong></div>`}${message.reply_to?`<button class="chat-reply-quote" data-fp-jump="${message.reply_to}" aria-label="${tHtml("family_photos:toOriginalMessage")}"><strong>${esc(source?.sender||tText("family_photos:originalMessage"))}</strong><span>${esc(source?.text?.slice(0,240)|| (source?.media?.[0]?.kind==='animation'?'GIF':source?.media?.length?tText("family_photos:attachment"):tText("family_photos:goToMessage")))}</span></button>`:''}${(visual||sticker)&&!message.text?mediaFrame(sticker?stickerMarkup(message):albumMarkup(message.media.filter(visualMedia),'data-fp-chat-file'),footer):sticker?stickerMarkup(message):albumMarkup(message.media.filter(visualMedia),'data-fp-chat-file')}${voiceMarkup(message)}${message.text?`<p class="chat-message-text">${telegramText(message)}${!visual&&!documents.length&&!hasReactions(message)?footer:''}</p>`:''}${documents.length?`<div class="chat-attachments">${documents.map(attachmentMarkup).join('')}</div>`:''}${(visual||sticker)&&!message.text?reactionsMarkup(message,ctx.state.me?.id,{readOnly:form.hidden}):hasReactions(message)?reactionFooter(message,ctx.state.me?.id,footer,{readOnly:form.hidden}):visual||documents.length||!message.text?footer:''}${botReactionNotice(message)}${mine&&message.site_edit_state&&['edit_queued','editing','edit_failed','uncertain','failed'].includes(message.site_edit_state)?`<small class="fp-delivery-status" role="status">${['edit_queued','editing'].includes(message.site_edit_state)?tText("family_photos:sending2"):message.site_edit_state==='edit_failed'?tText("family_photos:couldNotChangeMessageYouCanTryAgain"):tText("family_photos:sendWasNotFullyConfirmedPleaseCheckTelegram")}</small>`:''}</article>`;
    }).join('')+[...pending].filter(([id])=>!items.some(message=>message.site_send_id===id)).map(([id,p])=>`<article class="chat-message mine group-end fp-pending-message ${(p.media||[]).some(visualMedia)?'has-visual-media':''}" data-fp-pending="${esc(id)}">${albumMarkup((p.media||[]).filter(visualMedia),'data-fp-chat-file')}${p.text?`<p class="chat-message-text">${esc(p.text)}</p>`:''}${(p.media||[]).filter(file=>!visualMedia(file)).map(attachmentMarkup).join('')}${p.created?messageFooter(p,false):''}<small class="fp-delivery-status" role="status">${esc(p.label)}</small>${['failed','uncertain','queued'].includes(p.state)?`<button type="button" class="text-link" data-fp-cancel-send="${esc(id)}">${tHtml("family_photos:cancelSend")}</button>`:''}</article>`).join(''));
    audioPlayers.filter(audio=>!audio.isConnected).forEach(audio=>{audio.pause();audio.removeAttribute('src');audio.load();});
    stickers.sync();previews.sync();avatars.hydrate(history);
    fitAlbums(history,()=>{const follow=history.scrollHeight-history.scrollTop-history.clientHeight<100;return()=>{if(follow)history.scrollTop=history.scrollHeight;};});
    if(!changed&&!follow){pagination.update();return;}
    history.querySelectorAll('[data-fp-cancel-send]').forEach(button=>button.onclick=async()=>{button.disabled=true;try{await request(base(gid)+'/site-sends/'+button.dataset.fpCancelSend+'/cancel','POST',{});pending.delete(button.dataset.fpCancelSend);draw(false,true);}catch(error){ctx.toast(error.message);button.disabled=false;}});
    history.querySelectorAll('[data-fp-reply]').forEach(b=>b.onclick=()=>setReply(Number(b.dataset.fpReply)));
    history.querySelectorAll('[data-fp-chat-file]').forEach(b=>b.onclick=()=>openMedia(b.dataset.fpChatFile));
    history.querySelectorAll('[data-fp-jump]').forEach(b=>b.onclick=()=>jump(Number(b.dataset.fpJump)));
    history.scrollTop=follow||atEnd?history.scrollHeight:top;
    if(preserve){const target=history.querySelector(`[data-fp-message-id="${anchorId}"]`);history.scrollTop+=target?target.getBoundingClientRect().top-rect.top-offset:history.scrollHeight-height;}
    pagination.update();
  };
  const loadOlder=()=>{
    if(loadingOlder)return loadingOlder;
    if(!more||!before||closed)return;
    revision++;
    loadingOlder=(async()=>{
      const cursor=before,result=await request(base(gid)+'/messages?before='+cursor);if(closed)return;
      const known=new Set(items.map(m=>m.id));items=[...result.items.filter(m=>!known.has(m.id)),...items];more=result.more&&result.items.some(m=>m.id<cursor);before=items[0]?.id||0;draw(false,true);
    })().finally(()=>{loadingOlder=null;});
    return loadingOlder;
  };
  const pagination=edgePagination(history,{root:history,canLoad:()=>!closed&&!loadingOlder&&more&&before>0&&!context,load:loadOlder,onError:denied});
  const jump=async id=>{
    let tries=0;while(!items.some(m=>m.id===id)&&more&&tries++<20&&!closed)await loadOlder();
    if(closed)return;
    const target=history.querySelector(`[data-fp-message-id="${id}"],[data-fp-source-message-id="${id}"]`)?.closest('.fp-chat-message');
    if(target){target.scrollIntoView({block:'center'});target.classList.add('fp-highlight');}
    else ctx.toast(tText("family_photos:messageIsUnavailableOrInAnOlderHistory"));
  };
  const refresh=async(initial=false)=>{
    try{
      if(loadingOlder)return;const version=revision;
      const notificationVersion=notificationRevision,current=++seq,data=await request('/family-photos/groups');if(closed||current!==seq)return;
      const group=data.items.find(g=>g.id===gid);if(!group){denied(Object.assign(new Error(tText("family_photos:groupAccessIsRestricted")),{status:403}));return;}
      if(notificationVersion===notificationRevision&&!savingNotifications){muteUntil=group.mute_until||0;drawNotifications();}
      renderMarkup($('[data-fp-identity]',host),`${telegramAvatar(group)}<span><h2 data-fp-title>${esc(group.title)}</h2></span>`);
      reactionMirror=data.status.bot_reactions===true;
      const wasReadOnly=form.hidden;composer.writable(data.status.bot_messaging===true&&group.can_send===true&&group.kind!=='export');if(wasReadOnly&&!form.hidden)closeMenu();
      if(form.hidden){context?.element.querySelector('[data-message-action=reply]')?.remove();context?.element.querySelector('[data-message-action=edit]')?.remove();context?.element.querySelector('.chat-quick-reactions')?.remove();}
      const fallback=group.copy_created?tText("family_photos:groupCopyReadOnlyStatusSavedTo", {value0:(new Date(group.copy_created*1000).toLocaleString(locale()))}):group.kind==='export'?tText("family_photos:exportSnapshotReadOnlyChangesAndDeletionsIn"):form.hidden?tText("family_photos:thisGroupIsOnlyForReading"):data.status.worker_online?(group.history_complete?'':tText("family_photos:loadingHistoryFromOldMessagesToNew")):tText("family_photos:telegramSynchronizationIsCurrentlyNotWorkingSavedArchive");
      if(group.connection){
        const exceptional=!['working','copy','export'].includes(group.connection.state);
        renderMarkup(notice,exceptional?connectionMarkup(group,{chat:true})+(form.hidden?(" "+tText("family_photos:readOnly")+""):''):form.hidden?esc(fallback):'');
        notice.hidden=!notice.innerHTML;
        let summary=$('[data-fp-source-summary]',host);
        if(!summary){summary=document.createElement('small');summary.dataset.fpSourceSummary='';$('[data-fp-title]',host).after(summary);}
        summary.textContent=connectionLabel(group.connection)+(group.connection.last_success?(" "+tText("family_photos:verified2", {value0:(new Date(group.connection.last_success*1000).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'}))})+""):'');
        summary.title=group.connection.last_success?tText("family_photos:lastSuccessfulVerification", {value0:(new Date(group.connection.last_success*1000).toLocaleString(locale()))}):'';
      }else{renderMarkup(notice,esc(fallback));notice.hidden=!fallback;}
      let rows=[],hasMore=true,cursor=0;
      do{const page=await request(base(gid)+'/messages'+(cursor?'?before='+cursor:''));if(closed||current!==seq||version!==revision)return;rows=[...page.items,...rows];hasMore=page.more;cursor=rows[0]?.id||0;}while(hasMore&&rows.length<Math.max(items.length,50));
      if(closed||current!==seq||version!==revision)return;
      if(items.some(m=>!rows.some(n=>n.id===m.id)))viewer?.destroy();
      items=rows;more=hasMore;before=rows[0]?.id||0;
      const queue=await request(base(gid)+(data.status.bot_messaging?'/site-sends':'/outbox'));if(closed)return;
      pending.clear();
      for(const delivery of queue.items){const id=delivery.id,p={...delivery};pending.set(id,p);
        if(delivery.state==='delivered'&&items.some(m=>m.id===delivery.message_id))pending.delete(id);
        else p.label=delivery.state==='uncertain'?tText("family_photos:deliveryNotConfirmedCheckTelegramBeforeResending"):delivery.state==='failed'?tText("family_photos:telegramRejectedTheMessage"):delivery.state==='delivered'?tText("family_photos:deliveredToTelegramWaitingForSynchronization"):tText("family_photos:awaitingSending");
      }
      draw(initial);
      await markRead();
      if(initial&&ctx.state.telegramMessage){const mid=ctx.state.telegramMessage;ctx.state.telegramMessage=null;await jump(mid);}
    }catch(error){denied(error);}
    finally{if(!closed)timer=setTimeout(()=>refresh(),5000);}
  };
  refresh(true);
  return {destroy(){closed=true;stopAudio();composer.destroy();pagination.destroy();stickers.destroy();previews.destroy();avatars.destroy();reactions.destroy();closeMenu();press();abort.abort();clearTimeout(timer);viewer?.destroy();history.removeEventListener('scroll',markRead);document.removeEventListener('visibilitychange',markRead);window.removeEventListener('focus',markRead);},async flush(){return composer.flush();}};
}
