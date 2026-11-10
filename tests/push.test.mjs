import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {applicationServerKey,chatFromPushUrl,familyFromPushUrl,createPushClient,pushSupport} from '../web/core/push.js';

const publicKey=Buffer.from([4,...Array(64).fill(7)]).toString('base64url');
function browser({permission='default',subscription=null,subscribed=false,failPost=false,failDelete=false}={}){
  const calls=[];
  const local={endpoint:'https://push.example/device',toJSON(){return {endpoint:this.endpoint,keys:{p256dh:'public',auth:'secret'},expirationTime:null};},async unsubscribe(){calls.push('unsubscribe');subscription=null;return true;}};
  if(subscription===true)subscription=local;
  const registration={active:{scriptURL:'https://note.example/push-sw.js'},pushManager:{
    async getSubscription(){return subscription;},
    async subscribe(options){calls.push('subscribe');assert.equal(options.userVisibleOnly,true);assert.equal(options.applicationServerKey.length,65);subscription=local;return local;},
  },async getNotifications(){return [{close(){calls.push('close notification');}}];}};
  const env={isSecureContext:true,atob:value=>Buffer.from(value,'base64').toString('binary'),location:{origin:'https://note.example'},PushManager:function(){},Notification:{permission,requestPermission(){calls.push('request permission');this.permission='granted';return Promise.resolve('granted');}},navigator:{platform:'MacIntel',maxTouchPoints:0,userAgent:'Safari',serviceWorker:{async getRegistration(){return registration;},async register(path,options){calls.push('register');assert.equal(path,'/push-sw.js');assert.deepEqual(options,{scope:'/',updateViaCache:'none'});return registration;},ready:Promise.resolve(registration)}}};
  const api=async(path,method='GET',body)=>{
    calls.push([path,method,body]);
    if(path==='/push')return {configured:true,public_key:publicKey};
    if(path==='/push/status'&&method==='POST')return {subscribed};
    if(path==='/push/subscriptions'&&method==='POST'){if(failPost)throw new Error('network');subscribed=true;return {subscribed:true};}
    if(path==='/push/subscriptions'&&method==='DELETE'){if(failDelete)throw new Error('network');subscribed=false;return {ok:true};}
    throw new Error('Unexpected API path');
  };
  return {env,api,calls,client:createPushClient({api,env}),registration};
}

test('iPhone requires installed app; a secure installed app can opt in',()=>{
  const {env}=browser();env.navigator.userAgent='iPhone';
  assert.deepEqual(pushSupport(env),{supported:false,reason:'install'});
  env.navigator.standalone=true;
  assert.deepEqual(pushSupport(env),{supported:true,reason:'ready'});
  env.isSecureContext=false;assert.equal(pushSupport(env).reason,'insecure');
  env.isSecureContext=true;delete env.PushManager;assert.equal(pushSupport(env).reason,'unsupported');
});

test('VAPID decoding accepts unpadded URL-safe keys and rejects malformed server keys',()=>{
  assert.equal(applicationServerKey(publicKey).length,65);
  assert.throws(()=>applicationServerKey('AAAA'),/некорректный ключ/);
});

test('chat links only accept this origin and a single safe chat identifier',()=>{
  const origin='https://note.example';
  assert.equal(chatFromPushUrl('/#chat/space-12_A',origin),'space-12_A');
  assert.equal(chatFromPushUrl('/#chat/telegram:-100123',origin),'telegram:-100123');
  assert.equal(chatFromPushUrl('/#chat/telegram:export:abcdef0123456789',origin),'telegram:export:abcdef0123456789');
  for(const value of ['/#chat/telegram:owner','/#chat/telegram:export:xyz','/#chat/telegram:export:abcdef0123456789/extra'])assert.equal(chatFromPushUrl(value,origin),null);
  for(const value of ['https://evil.example/#chat/abc','/share/token#chat/abc','/?redirect=x#chat/abc','/#chat/%3Cscript%3E','/#chat/abc/extra','javascript:alert(1)'])assert.equal(chatFromPushUrl(value,origin),null);
});

test('reading preferences never asks permission and does not bind a different account',async()=>{
  const {client,calls}=browser({permission:'granted',subscription:true,subscribed:false});
  const state=await client.state();
  assert.equal(state.enabled,false);assert.equal(state.hasSubscription,true);
  assert.equal(calls.includes('request permission'),false);
  assert.equal(calls.some(call=>Array.isArray(call)&&call[0]==='/push/subscriptions'&&call[1]==='POST'),false);
});

test('startup updates only our existing worker without permission or account binding',async()=>{
  const {client,calls,registration}=browser();
  registration.update=async()=>calls.push('update');
  await client.refreshWorker();assert.deepEqual(calls,['update']);
  calls.length=0;registration.active.scriptURL='https://note.example/other-sw.js';
  await client.refreshWorker();assert.deepEqual(calls,[]);
  registration.active.scriptURL='https://note.example/push-sw.js';
  registration.update=async()=>{throw Error('offline');};
  await client.refreshWorker();assert.deepEqual(calls,[]);
});

test('explicit enable requests permission before any asynchronous registration and saves one device',async()=>{
  const {client,calls}=browser();await client.state();calls.length=0;
  const pending=client.enable();assert.equal(calls[0],'request permission');await pending;
  assert.equal(calls.filter(call=>call==='subscribe').length,1);
  assert.equal(calls.includes('register'),false);
  const saved=calls.find(call=>Array.isArray(call)&&call[1]==='POST');
  assert.equal(saved[2].endpoint,'https://push.example/device');assert.equal(saved[2].device_name,'Mac');
  assert.equal((await client.state()).enabled,true);
});

test('permission denial creates no endpoint and no browser subscription',async()=>{
  const {client,calls}=browser({permission:'denied'});await client.state();calls.length=0;
  await assert.rejects(client.enable(),/Разрешите уведомления/);
  assert.equal(calls.length,0);
});

test('failed server registration discards a newly created local subscription',async()=>{
  const {client,calls}=browser({permission:'granted',failPost:true});await client.state();
  await assert.rejects(client.enable(),/network/);assert.equal(calls.at(-1),'unsubscribe');
});

test('logout revokes server endpoint before dropping the local subscription and displayed notifications',async()=>{
  const {client,calls}=browser({permission:'granted',subscription:true,subscribed:true});await client.state();calls.length=0;
  await client.logout();
  const revoke=calls.findIndex(call=>Array.isArray(call)&&call[1]==='DELETE');
  assert.ok(revoke>=0&&revoke<calls.indexOf('unsubscribe'));assert.ok(calls.includes('close notification'));
  await assert.rejects(client.enable(),/не настроены/);
});

test('a disconnected server cannot prevent local unsubscribe or logout',async()=>{
  const {client,calls}=browser({permission:'granted',subscription:true,failDelete:true});
  await client.logout();assert.ok(calls.includes('unsubscribe'));assert.ok(calls.includes('close notification'));
});

const workerMessages={ru:JSON.parse(await readFile(new URL('../locales/ru/push.json',import.meta.url),'utf8')),en:JSON.parse(await readFile(new URL('../locales/en/push.json',import.meta.url),'utf8'))};
const workerSource=await readFile(new URL('../web/public/push-sw.js',import.meta.url),'utf8');
function worker(clients=[],{unsupported=false,rejected=false}={}){
  const listeners={},shown=[],delivered=[],opened=[],messages=[],focused=[],badges=[];
  const self={navigator:unsupported?{}:{async setAppBadge(n){if(rejected)throw new Error('denied');badges.push(n);},async clearAppBadge(){badges.push(0);}},location:{origin:'https://note.example'},addEventListener(name,handler){listeners[name]=handler;},skipWaiting:async()=>{},registration:{async getNotifications({tag}={}){return delivered.filter(n=>!n.closed&&(!tag||n.tag===tag));},async showNotification(title,options){shown.push({title,options});for(const n of delivered)if(n.tag===options.tag)n.closed=true;const notice={data:options.data,tag:options.tag,closed:false,close(){this.closed=true;}};delivered.push(notice);}},clients:{claim:async()=>{},async matchAll(){return clients.map(url=>({url,async focus(){focused.push(url);},postMessage(data){messages.push({...data});}}));},async openWindow(url){opened.push(url);}}};
  vm.runInNewContext(workerSource,{self,URL,DRG_PUSH_MESSAGES:workerMessages});
  async function event(name,properties){let completion;listeners[name]({...properties,waitUntil(promise){completion=promise;}});await completion;}
  return {listeners,shown,delivered,opened,messages,focused,badges,event};
}

test('read reconciliation is bounded by account, chat and latest grouped message',async()=>{
 const sw=worker(),account='a'.repeat(32),other='b'.repeat(32);
 const push=(mid,room='room',uid=account)=>sw.event('push',{data:{json:()=>({url:'/#chat/'+room,account_id:uid,message_id:mid})}});
 const read=(through,uid=account)=>sw.event('message',{source:{url:'https://note.example/'},data:{type:'drg-chat-reads',account_id:uid,reads:{room:through}}});
 await push(20);await push(10);
 assert.equal(sw.shown.length,1,'An older in-flight alert cannot replace the newer unread part');
 await push(15,'another');await read(15);assert.equal(sw.delivered.filter(n=>!n.closed).length,2);
 await read(99,other);assert.equal(sw.delivered.filter(n=>!n.closed).length,2);
 await read(20);assert.deepEqual(sw.delivered.filter(n=>!n.closed).map(n=>n.data.url),['/#chat/another']);
 await push(19);assert.equal(sw.delivered.filter(n=>!n.closed).length,1,'Late covered push is removed after display');
 await push(21);assert.equal(sw.delivered.filter(n=>!n.closed).length,2,'New message beyond watermark stays visible');
});

test('background pushes use absolute unread totals and never badge test or malformed pushes',async()=>{
  const sw=worker();
  for(const payload of [{badge:4},{badge:4},{badge:2},{badge:0},{type:'push-test',badge:99},{},{badge:-1},{badge:'5'},{badge:1.5}])
    await sw.event('push',{data:{json:()=>payload}});
  assert.deepEqual(sw.badges,[4,4,2,0]);assert.equal(sw.shown.length,9);
  for(const options of [{unsupported:true},{rejected:true}]){
    const other=worker([],options);await other.event('push',{data:{json:()=>({badge:3})}});
    assert.equal(other.shown.length,1);
  }
});

test('worker shows bounded plain-text previews, ignores malicious redirects, and never caches private requests',async()=>{
  const sw=worker();assert.equal(sw.listeners.fetch,undefined);
  await sw.event('push',{data:{json:()=>({title:'Secret sender',body:'Sensitive message',url:'https://evil.example'})}});
  assert.equal(sw.shown[0].title,'Secret sender');assert.equal(sw.shown[0].options.body,'Sensitive message');assert.equal(sw.shown[0].options.data.url,'/');
  await sw.event('push',{data:{json:()=>{throw new Error('invalid JSON');}}});assert.equal(sw.shown.length,2);
  await sw.event('push',{data:{json:()=>({url:'/#chat/friends-12'})}});assert.equal(sw.shown[2].options.tag,'drg-message:friends-12');
});

test('notification click focuses existing main app and sends only a validated chat target',async()=>{
  const sw=worker(['https://evil.example/','https://note.example/share/secret','https://note.example/']);let closed=false;
  await sw.event('notificationclick',{notification:{data:{url:'/#chat/room-7'},close(){closed=true;}}});
  assert.equal(closed,true);assert.deepEqual(sw.focused,['https://note.example/']);assert.deepEqual(sw.messages,[{type:'drg-open-chat',space_id:'room-7'}]);assert.equal(sw.opened.length,0);
});

test('notification click opens the app when no main window exists and cannot open an external URL',async()=>{
  const sw=worker(['https://evil.example/','https://note.example/share/secret']);
  await sw.event('notificationclick',{notification:{data:{url:'/#chat/room-7'},close(){}}});assert.deepEqual(sw.opened,['/#chat/room-7']);
  await sw.event('notificationclick',{notification:{data:{url:'//evil.example/phish'},close(){}}});assert.deepEqual(sw.opened,['/#chat/room-7','/']);
});

test('security push canonicalizes the owner route and opens it without exposing an incident payload',async()=>{
  const sw=worker(['https://note.example/share/public-note']);
  await sw.event('push',{data:{json:()=>({type:'security',title:'Безопасность портала',body:'Требуется проверка.',url:'https://note.example/#admin',incident_id:'private-incident',ip:'203.0.113.9'})}});
  const notification=sw.shown[0];
  assert.equal(notification.title,'Безопасность портала');
  assert.equal(notification.options.tag,'drg-security');
  assert.equal(notification.options.data.url,'/#admin');
  assert.deepEqual(Object.keys(notification.options.data),['url'],'worker keeps no account, incident or IP data in the click target');
  await sw.event('notificationclick',{notification:{data:notification.options.data,close(){}}});
  assert.deepEqual(sw.opened,['/#admin']);
  assert.deepEqual(sw.messages,[]);
});

test('security push click focuses the existing main app and sends only drg-open-security',async()=>{
  const sw=worker(['https://evil.example/','https://note.example/share/public-note','https://note.example/#chat/room-7']);let closed=false;
  await sw.event('notificationclick',{notification:{data:{url:'/#admin',incident_id:'private-incident'},close(){closed=true;}}});
  assert.equal(closed,true);
  assert.deepEqual(sw.focused,['https://note.example/#chat/room-7']);
  assert.deepEqual(sw.messages,[{type:'drg-open-security'}]);
  assert.deepEqual(sw.opened,[]);
});

test('security push rejects malicious or noncanonical URLs at delivery and click without security navigation',async()=>{
  for(const url of ['https://evil.example/#admin','//evil.example/#admin','/share/secret#admin','/?redirect=evil#admin','/#admin/extra','/#%61dmin','javascript:alert(1)']){
    const closed=worker();
    await closed.event('push',{data:{json:()=>({type:'security',url})}});
    assert.equal(closed.shown[0].options.data.url,'/',url);
    assert.notEqual(closed.shown[0].options.tag,'drg-security',url);
    // Click parsing also validates data independently of the delivery handler.
    await closed.event('notificationclick',{notification:{data:{url},close(){}}});
    assert.deepEqual(closed.opened,['/'],url);
    const active=worker(['https://note.example/']);
    await active.event('notificationclick',{notification:{data:{url},close(){}}});
    assert.deepEqual(active.focused,['https://note.example/'],url);
    assert.deepEqual(active.messages,[],url);
    assert.deepEqual(active.opened,[],url);
  }
});


test('worker preserves message previews and call names with safe bounds and fallbacks',async()=>{
 const sw=worker();await sw.event('push',{data:{json:()=>({title:'Александр',body:'Привет 👋',url:'/#chat/room-7'})}});
 assert.equal(sw.shown[0].title,'Александр');assert.equal(sw.shown[0].options.body,'Привет 👋');
 await sw.event('push',{data:{json:()=>({type:'call',title:'batman',body:'other',url:'/#chat/room-7'})}});
 assert.equal(sw.shown[1].title,'batman');assert.equal(sw.shown[1].options.body,'Входящий голосовой звонок');
 await sw.event('push',{data:{json:()=>({title:'x'.repeat(200),body:'y'.repeat(3000)})}});assert.equal(sw.shown[2].title.length,80);assert.equal(sw.shown[2].options.body.length,500);
 await sw.event('push',{data:{json:()=>({title:{bad:1},body:null})}});assert.equal(sw.shown[3].title,'DrG Space');assert.equal(sw.shown[3].options.body,'Новое сообщение');
});

 test('family push opens its validated Telegram archive room',async()=>{
  const sw=worker();
  await sw.event('push',{data:{json:()=>({title:'Семья',body:'Фото',url:'/#chat/telegram:export:abcdef0123456789'})}});
  assert.equal(sw.shown[0].options.tag,'drg-message:telegram:export:abcdef0123456789');
  await sw.event('notificationclick',{notification:{data:{url:'/#chat/telegram:export:abcdef0123456789'},close(){}}});
  assert.deepEqual(sw.opened,['/#chat/telegram:export:abcdef0123456789']);
});


test('archive push opens only a validated owner family route',async()=>{
  const origin='https://note.example';
  assert.equal(familyFromPushUrl('/#admin/family-photos/-1001',origin),'-1001');
  assert.equal(familyFromPushUrl('/#admin/family-photos/export:abcdef0123456789',origin),'export:abcdef0123456789');
  for(const value of ['https://evil.example/#admin/family-photos/-1001','/?x=1#admin/family-photos/-1001','/#admin/family-photos/../x','/#admin/family-photos/-1001/extra'])assert.equal(familyFromPushUrl(value,origin),null);
  const sw=worker(['https://note.example/']);
  await sw.event('push',{data:{json:()=>({type:'family-export',title:'Архив готов',url:'/#admin/family-photos/-1001'})}});
  assert.equal(sw.shown[0].options.data.url,'/#admin/family-photos/-1001');
  await sw.event('notificationclick',{notification:{data:sw.shown[0].options.data,close(){}}});
  assert.deepEqual(sw.messages,[{type:'drg-open-family-export',group_id:'-1001'}]);
  const closed=worker();await closed.event('notificationclick',{notification:{data:{url:'/#admin/family-photos/-1001'},close(){}}});
  assert.deepEqual(closed.opened,['/#admin/family-photos/-1001']);
});
