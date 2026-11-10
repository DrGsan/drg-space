// Deliberately no fetch handler or cache: private pages and API responses stay
// in the application's authenticated network flow.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

function chatTarget(value) {
  try {
    const url = new URL(value || '/', self.location.origin);
    if (url.origin !== self.location.origin || url.pathname !== '/' || url.search) return {url: '/', space: null};
    const family=/^#admin\/family-photos\/(-\d{1,20}|export:[a-f0-9]{8,24})$/.exec(url.hash);
    if(family)return {url:'/#admin/family-photos/'+family[1],space:null,family_group:family[1]};
    if (url.hash === '#admin') return {url: '/#admin', space: null, security: true};
    const match = /^#chat\/((?:[A-Za-z0-9_-]{1,128}|telegram:(?:-\d{1,20}|export:[a-f0-9]{8,24})))$/.exec(url.hash);
    return match ? {url: '/#chat/' + match[1], space: match[1]} : {url: '/', space: null};
  } catch { return {url: '/', space: null}; }
}

const notificationMessages=DRG_PUSH_MESSAGES;
const readWatermarks=new Map();
function readNotice(notice){
  const data=notice.data||{},room=chatTarget(data.url).space,through=readWatermarks.get(data.account_id)?.get(room);
  return Number.isSafeInteger(data.message_id)&&data.message_id>0&&Number.isSafeInteger(through)&&data.message_id<=through;
}
self.addEventListener('message',event=>{
  const data=event.data||{};
  if(data.type!=='drg-chat-reads'||typeof data.account_id!=='string'||!/^[a-f0-9]{32}$/.test(data.account_id))return;
  try{if(new URL(event.source.url).origin!==self.location.origin)return;}catch{return;}
  const reads=readWatermarks.get(data.account_id)||new Map();
  for(const [room,through] of Object.entries(data.reads||{}))if(Number.isSafeInteger(through)&&through>=0&&chatTarget('/#chat/'+room).space===room)reads.set(room,Math.max(reads.get(room)||0,through));
  readWatermarks.set(data.account_id,reads);
  event.waitUntil((async()=>{for(const notice of await self.registration.getNotifications())if(readNotice(notice))notice.close();})());
});
self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch {}
  const target = chatTarget(payload.url);
  const messages=notificationMessages[payload.language]||notificationMessages.ru;
  // Web Push payload is encrypted in transit. Render bounded plain text;
  // never fetch private content or trust notification URLs/tags as redirects.
  const text=(value,max,fallback)=>typeof value==='string'&&value.trim()?value.slice(0,max):fallback;
  event.waitUntil((async () => {
    const data={url:target.url};
    if(target.space&&/^[a-f0-9]{32}$/.test(payload.account_id||'')&&Number.isSafeInteger(payload.message_id)&&payload.message_id>0)Object.assign(data,{account_id:payload.account_id,message_id:payload.message_id});
    const tag=target.family_group?'drg-family-export:'+target.family_group:target.security?'drg-security':payload.type==='call'?'drg-call:'+target.space:target.space?'drg-message:'+target.space:'drg-message';
    const delivered=await self.registration.getNotifications?.({tag})||[];
    // An older in-flight message must not replace the newer unread part of a
    // grouped notification. The existing newer notification stays displayed.
    if(data.message_id&&delivered.some(n=>n.data?.account_id===data.account_id&&n.data?.message_id>data.message_id))return;
    await self.registration.showNotification(payload.type==='push-test'?'DrG Space':text(payload.title,80,'DrG Space'), {
    body: payload.type==='push-test'?messages.test:payload.type==='call'?messages.call:text(payload.body,500,messages.message),
    icon: '/assets/icons/drg-d8-192.png',
    badge: '/assets/icons/drg-d8-192.png',
    tag,data,
    });
    // A read can race an in-flight push. Close only covered messages, leaving
    // a newer notification in this collapsed chat group visible.
    for(const notice of await self.registration.getNotifications?.()||[])if(readNotice(notice))notice.close();
    // Use the server's total instead of incrementing: retries and collapsed
    // notifications must not inflate the count. Test pushes leave it alone.
    if (payload.type!=='push-test' && Number.isSafeInteger(payload.badge) && payload.badge>=0) {
      try {
        if(payload.badge)await self.navigator?.setAppBadge?.(payload.badge);
        else await self.navigator?.clearAppBadge?.();
      } catch {}
    }
  })());
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = chatTarget(event.notification.data?.url);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const main = windows.find(client => {
      try { const url = new URL(client.url); return url.origin === self.location.origin && url.pathname === '/'; }
      catch { return false; }
    });
    if (main) {
      await main.focus();
      if (target.space) main.postMessage({type: 'drg-open-chat', space_id: target.space});
      if(target.family_group)main.postMessage({type:'drg-open-family-export',group_id:target.family_group});
      if (target.security) main.postMessage({type: 'drg-open-security'});
      return;
    }
    await self.clients.openWindow(target.url);
  })());
});
