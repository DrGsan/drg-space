// A disposable real HTTP server and three real WebRTC clients. No signaling mocks.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {installCallOutputFixture} from './call-output-fixture.js';
import {silenceTestAudio} from './silent-audio.js';
const reviewOut=process.env.DRG_CALL_REVIEW_OUT||'artifacts';
const isWebKit=process.env.DRG_BROWSER_ENGINE==='webkit';
const isLiveKit=process.env.DRG_TEST_LIVEKIT==='1';
const origin='http://127.0.0.1:8019',directory=await mkdtemp(tmpdir()+'/drg-guest-browser-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8019','tests.browser_app:app'],{env:{...process.env,CALL_LIVEKIT_ENABLED:isLiveKit?'true':'false',DATA_DIR:directory,DATABASE_URL:'',DATABASE_SCHEMA:'guest_browser',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-browser-guest-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:process.env.CALL_STUN_URLS||'',CALL_TURN_URLS:process.env.CALL_TURN_URLS||''},stdio:['ignore','ignore','pipe']});
let serverError='';server.stderr.on('data',data=>{serverError=(serverError+data).slice(-4000);});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
let browser;const inspectedPages=[];
try{
 let ready=false;for(let n=0;n<100;n++){try{const r=await fetch(origin+'/api/health');if(r.ok){ready=true;break;}}catch{}if(server.exitCode!==null)throw Error(serverError);await delay(100);}assert(ready,serverError);
 browser=await (isWebKit?webkit:chromium).launch({headless:true,...(isWebKit?{}:{args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']})});
 const contexts=[];const errors=[];
 async function context(viewport={width:1280,height:900}){
  const c=await browser.newContext({locale:'ru-RU',viewport,hasTouch:viewport.width<760,permissions:isWebKit?['microphone']:['microphone','clipboard-read','clipboard-write']});contexts.push(c);await silenceTestAudio(c);await c.addInitScript(installCallOutputFixture);
  await c.addInitScript(({relay,webkit,sfu})=>{
   const Peer=window.RTCPeerConnection;window.__guestPCs=[];window.RTCPeerConnection=class extends Peer{constructor(config,...args){super({...config,...(relay?{iceTransportPolicy:'relay'}:{})},...args);__guestPCs.push(this);}setConfiguration(config){return super.setConfiguration({...config,...(relay?{iceTransportPolicy:'relay'}:{})});}};
   if(sfu){const Socket=window.WebSocket;window.__sfuSockets=[];window.WebSocket=class extends Socket{constructor(...args){super(...args);if(String(args[0]).startsWith('wss://'))__sfuSockets.push(this);}};}
   const nativeCapture=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
   // WebKit shares its mock hardware microphone across contexts. A permitted
   // native capture unlocks ICE addresses; independent tones supply each peer.
   const capture=webkit?async constraints=>{
    const native=await nativeCapture(constraints),audio=new AudioContext(),oscillator=audio.createOscillator(),output=audio.createMediaStreamDestination();oscillator.connect(output);oscillator.start();await audio.resume();
    const track=output.stream.getAudioTracks()[0],stop=track.stop.bind(track);track.stop=()=>{stop();native.getTracks().forEach(t=>t.stop());oscillator.stop();audio.close().catch(()=>{});};return output.stream;
   }:nativeCapture;window.__guestStreams=[];
   const devices=navigator.mediaDevices;Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:devices});Object.defineProperty(devices,'getUserMedia',{configurable:true,writable:true,value:async constraints=>{const stream=await capture(constraints);__guestStreams.push(stream);return stream;}});
  },{relay:process.env.DRG_TEST_RELAY==='1',webkit:isWebKit,sfu:isLiveKit});
  await c.addInitScript(({sfu,relay})=>{window.__expectSFU=sfu;window.__expectRelay=relay;},{sfu:isLiveKit,relay:process.env.DRG_TEST_RELAY==='1'});const p=await c.newPage();inspectedPages.push(p);if(isLiveKit)p.on('console',message=>{if(message.type()==='error')console.error('SDK error:',message.text().replace(/([?&](?:access_token|join_request)=)[^&\s'"]+/g,'$1[redacted]').replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g,'[redacted]'))});p.on('pageerror',error=>{errors.push(error.message);console.error('Browser error:',error.message,error.stack);});await p.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));return [c,p];
 }
 const [ownerContext,owner]=await context();
 async function api(path,data,csrf=''){
  const response=await ownerContext.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});assert(response.ok(),path+' '+await response.text());return response.json();
 }
 let csrf=(await api('/setup',{login:'guestowner',password:'Owner-password-123',token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll',{password:'Owner-password-123'},csrf);await api('/2fa/confirm',{code:totp(enrollment.secret)},csrf);csrf=(await api('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
 let badWorkspace=2;
 await owner.route('**/api/modules',route=>badWorkspace?route.fulfill({status:badWorkspace--===2?503:200,contentType:'text/html',body:'<h1>Unavailable</h1>'}):route.continue());
 await owner.goto(origin);await owner.getByRole('heading',{name:'Не удалось загрузить пространство',exact:true}).waitFor();
 await owner.getByText('Сервер временно недоступен. Повторите попытку.',{exact:true}).first().waitFor();
 await owner.locator('[data-workspace-retry]').click();await eventuallyWorkspaceFailure();
 async function eventuallyWorkspaceFailure(){while(badWorkspace)await delay(20);await owner.locator('[data-workspace-retry]:not([disabled])').waitFor();}
 await owner.locator('[data-workspace-retry]').click();await owner.locator('[data-view=chat]').waitFor();
 await (await owner.waitForResponse(response=>new URL(response.url()).pathname==='/api/presence'&&response.request().method()==='GET')).finished();
 // Another startup can still open Messages even when an unrelated section fails.
 await owner.route('**/api/notes',route=>route.fulfill({status:503,contentType:'text/html',body:'Unavailable'}));
 await owner.reload();await owner.locator('[data-workspace-chat]').waitFor();await owner.locator('[data-workspace-chat]').click();
 await owner.unroute('**/api/notes');
 if(!isLiveKit){
  // Rendering matrix only: real SFU media is exercised by the opt-in branch.
  let personal=true,group=false;
  await owner.route('**/api/calls/config',route=>route.fulfill({json:{available:personal,iceServers:[],guest_available:group,livekit_available:group,guest_transports:['livekit']}}));
  await mkdir(reviewOut,{recursive:true});
  for(const [turn,sfu] of [[true,false],[false,true],[true,true],[false,false]]){
   personal=turn;group=sfu;const configured=owner.waitForResponse(r=>new URL(r.url()).pathname==='/api/calls/config');await owner.reload();await owner.locator('[data-chat-section=chats]:visible, [data-view=chat]:visible').first().waitFor();if(await owner.locator('[data-view=chat]').isVisible())await owner.locator('[data-view=chat]').click();await owner.locator('[data-chat-section=chats]').waitFor();await configured;
   const tab=owner.locator('[data-chat-section=calls]');
   if(!turn&&!sfu){await expect(tab).toBeHidden();continue;}
   await expect(tab).toBeVisible();await tab.click();await owner.locator('[data-new-call]').click();
   if(sfu)await expect(owner.locator('[data-new-guest-call]')).toBeVisible();
   else {await expect(owner.locator('[data-new-guest-call]')).toBeHidden();await expect(owner.getByText('Пока нет контактов для звонка.',{exact:true})).toBeVisible();}
   await owner.screenshot({path:reviewOut+'/service-matrix-'+Number(turn)+'-'+Number(sfu)+'.png'});
  }
  const blocked=await ownerContext.request.post(origin+'/api/calls/guest-rooms',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{id:crypto.randomUUID().replaceAll('-',''),transport:'livekit'}});
  assert.equal(blocked.status(),503);assert.equal((await (await ownerContext.request.get(origin+'/api/calls/guest-rooms')).json()).items.length,0);
  console.log('Service availability passed: four UI combinations; no group creation with coturn alone; direct server bypass rejected.');
 }else{
 await owner.locator('[data-chat-section=calls]').waitFor();
 await owner.locator('[data-chat-section=calls]').click();await owner.locator('[data-new-call]').click();await owner.locator('[data-new-guest-call]').waitFor();
 await owner.evaluate(()=>document.documentElement.dataset.scheme='dark');await mkdir(reviewOut,{recursive:true});await owner.screenshot({path:reviewOut+'/guest-call-picker.png'});
 const creationIds=[];let loseCreateResponse=true;
 await owner.route('**/api/calls/guest-rooms',async route=>{
  if(route.request().method()!=='POST')return route.continue();creationIds.push(route.request().postDataJSON().id);
  if(loseCreateResponse){loseCreateResponse=false;await route.fetch();return route.fulfill({status:503,contentType:'text/html',body:'Unavailable after commit'});}
  return route.continue();
 });
 await owner.locator('[data-new-guest-call]').click();await owner.locator('[data-guest-create-retry]').waitFor();await owner.locator('[data-guest-create-retry]').click();
 await owner.locator('[data-guest-url]').waitFor();const url=await owner.locator('[data-guest-url]').inputValue();assert(url.startsWith(origin+'/call/'));
 assert.equal(creationIds.length,2);assert.equal(creationIds[0],creationIds[1],'creation retry keeps its idempotency key');
 assert.equal((await (await ownerContext.request.get(origin+'/api/calls/guest-rooms')).json()).items.length,1,'lost response must not duplicate the invitation');
 const roomId=(await (await ownerContext.request.get(origin+'/api/calls/guest-rooms')).json()).items[0].id;
 await owner.locator('[data-guest-title]').fill('Семейный вечер 🐈');
 await owner.locator('[data-guest-avatar-file]').setInputFiles('tests/fixtures/chat-portrait.png');
 await expect(owner.locator('[data-guest-save]')).toBeEnabled();
 let failAppearance=true;
 await owner.route('**/api/calls/guest-rooms/'+roomId,route=>failAppearance?(failAppearance=false,route.fulfill({status:503,json:{detail:'Попробуйте сохранить ещё раз'}})):route.continue());
 await owner.locator('[data-guest-save]').click();await expect(owner.locator('[data-guest-settings-status]')).toHaveText('Попробуйте сохранить ещё раз');
 await expect(owner.locator('[data-guest-title]')).toHaveValue('Семейный вечер 🐈');
 await owner.locator('[data-guest-save]').click();await expect(owner.locator('[data-guest-settings-status]')).toHaveText('Сохранено');
 await expect(owner.locator('[data-guest-invite-title]')).toHaveText('Семейный вечер 🐈');
 const initialAppearance=(await (await ownerContext.request.get(origin+'/api/calls/guest-rooms')).json()).items[0];
 assert.equal(initialAppearance.transport,isLiveKit?'livekit':'webrtc');assert.equal(initialAppearance.url,url);assert(initialAppearance.avatar_url);
 await owner.locator('.modal-close').click();await owner.locator('[data-guest-room="'+roomId+'"]').click();
 await expect(owner.locator('[data-guest-title]')).toHaveValue('Семейный вечер 🐈');
 await expect(owner.locator('.guest-avatar-picker img')).toBeVisible();
 for(const width of [390,320]){await owner.setViewportSize({width,height:844});assert(await owner.locator('.modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1));}
 await owner.screenshot({path:reviewOut+'/guest-call-invitation-mobile.png'});await owner.setViewportSize({width:1440,height:1000});
 await owner.locator('[data-guest-copy]').click();if(!isWebKit)await owner.getByText('Ссылка скопирована',{exact:true}).waitFor();await owner.screenshot({path:reviewOut+'/guest-call-invitation.png'});
 let joinFailures=3,stalledJoin;
 await owner.route('**/api/calls/guest-rooms/*/join',route=>{
  if(joinFailures===3){joinFailures--;stalledJoin=route;return;}
  if(joinFailures===2){joinFailures--;return route.fulfill({status:503,contentType:'text/html',body:'Unavailable'});}
  if(joinFailures===1){joinFailures--;return route.abort('connectionfailed');}
  return route.continue();
 });
 await owner.locator('[data-guest-enter]').click();await owner.locator('[data-guest-status]').waitFor({timeout:18000});
 assert((await owner.locator('[data-guest-status]').innerText()).includes('Сервер не ответил вовремя'));
 assert(await owner.evaluate(()=>__guestStreams.every(stream=>stream.getTracks().every(track=>track.readyState==='ended'))),'failed join releases the microphone');
 await stalledJoin.abort().catch(()=>{});
 await owner.locator('[data-guest-enter]').click();await owner.getByText('Сервер временно недоступен. Повторите попытку.',{exact:true}).first().waitFor();
 await owner.locator('[data-guest-enter]').waitFor();assert.equal(await owner.locator('.guest-call-panel').count(),0);
 await owner.locator('[data-guest-enter]').click();await owner.getByText('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.',{exact:true}).first().waitFor();
 await owner.screenshot({path:reviewOut+'/guest-call-join-recovery.png'});
 await owner.locator('[data-guest-enter]').click();await owner.locator('.guest-call-person').waitFor();
 if(isLiveKit){
  const [brokenContext,broken]=await context();
  await broken.route('**/chunk-livekit-room-*.js',r=>r.abort());await broken.goto(url);
  await broken.getByLabel('Ваше имя',{exact:true}).fill('Проверка загрузки');await broken.getByRole('button',{name:'Присоединиться',exact:true}).click();
  await expect(broken.locator('[data-guest-lobby-status]')).toHaveText('Не удалось загрузить модуль звонка. Обновите страницу и повторите вход.');
  assert(await broken.evaluate(()=>__guestStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))),'missing SDK releases the microphone');
  await broken.unroute('**/chunk-livekit-room-*.js');await broken.reload();await broken.getByLabel('Ваше имя',{exact:true}).fill('Проверка загрузки');await broken.getByRole('button',{name:'Присоединиться',exact:true}).click();
  await broken.locator('.guest-call-person').first().waitFor();await broken.locator('[data-guest-leave]').click();await expect(broken.locator('.guest-call-panel')).toHaveCount(0);
 }
 const [guest1Context,guest1]=await context();await guest1.route('**/api/guest-calls/*',route=>route.fulfill({status:503,contentType:'text/html',body:'Unavailable'}));
 await guest1.goto(url);await guest1.getByRole('heading',{name:'Приглашение недоступно',exact:true}).waitFor();
 assert((await guest1.locator('.auth-card').innerText()).includes('Сервис звонков временно недоступен'));
 await guest1.unroute('**/api/guest-calls/*');await guest1.reload();await expect(guest1.locator('[data-guest-lobby-title]')).toHaveText('Семейный вечер 🐈');await expect(guest1.locator('[data-guest-lobby-avatar] img')).toBeVisible();await guest1.getByLabel('Ваше имя',{exact:true}).fill('Гость Один');await guest1.screenshot({path:reviewOut+'/guest-call-lobby.png'});
 await guest1.getByRole('button',{name:'Присоединиться',exact:true}).click();await guest1.locator('.guest-call-person').first().waitFor();
 await owner.locator('[data-guest-output]').click();await owner.getByRole('combobox',{name:'Устройство вывода звука'}).selectOption('test-headphones');await expect(owner.locator('[data-guest-output-menu]')).toBeHidden();
 const [guest2Context,guest2]=await context({width:390,height:844});await guest2.goto(url);await guest2.getByLabel('Ваше имя',{exact:true}).fill('Гость Два');await guest2.getByRole('button',{name:'Присоединиться',exact:true}).click();
 const pages=[owner,guest1,guest2];
 async function eventually(check,label,timeout=20000){const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await check())return;await delay(100);}throw Error(label);}
 for(const p of pages){
  await eventually(async()=>await p.locator('.guest-call-person').count()===3,'Three participants visible');
  await eventually(()=>p.evaluate(async()=>{
   const active=__guestPCs.filter(pc=>pc.connectionState!=='closed');if(active.length!==(window.__expectSFU?1:2)||active.some(pc=>pc.connectionState!=='connected'||window.__expectRelay&&pc.getConfiguration().iceTransportPolicy!=='relay'))return false;
   const reports=await Promise.all(active.map(pc=>pc.getStats()));return reports.every(report=>{if(window.__expectRelay){const entries=[...report.values()],transport=entries.find(r=>r.type==='transport'&&r.selectedCandidatePairId),pair=transport?report.get(transport.selectedCandidatePairId):entries.find(r=>r.type==='candidate-pair'&&r.nominated&&r.state==='succeeded'),local=pair&&report.get(pair.localCandidateId);const matchingRelay=entries.some(r=>r.type==='local-candidate'&&r.candidateType==='relay'&&r.port===local?.port&&r.protocol===local?.protocol&&r.transportId===local?.transportId&&r.usernameFragment===local?.usernameFragment&&r.url===local?.url);if(!(local?.candidateType==='relay'||/^turns?:/.test(local?.url||'')&&(['udp','tcp','tls'].includes(local?.relayProtocol)||matchingRelay)))return false;}return [...report.values()].filter(r=>r.type==='inbound-rtp'&&(r.kind||r.mediaType)==='audio'&&r.packetsReceived>0).length>=(window.__expectSFU?2:1)});
  }),'All three clients receive real audio from both peers');
 }
 if(isLiveKit){
  const count=await owner.evaluate(()=>__sfuSockets.length);
  await owner.evaluate(()=>{const socket=__sfuSockets.find(s=>s.readyState===WebSocket.OPEN);if(!socket)throw Error('No live SFU signaling socket');socket.close(4000,'isolated application recovery test')});
  await eventually(()=>owner.evaluate(previous=>__sfuSockets.length>previous&&__sfuSockets.some(s=>s.readyState===WebSocket.OPEN),count),'LiveKit signaling reconnects');
  await expect(owner.locator('[data-guest-call-status]')).not.toContainText('Восстанавливаем');
  await expect.poll(()=>owner.locator('audio').evaluateAll(nodes=>nodes.filter(e=>e.srcObject).map(e=>e.sinkId))).toEqual(['test-headphones','test-headphones']);
  console.log('LiveKit application signaling recovered and selected outputs retained.');
 }
 // Phone rooms start at the receiver and switch all peers with one tap.
 await expect.poll(()=>guest2.locator('audio').evaluateAll(nodes=>nodes.filter(el=>el.srcObject).map(el=>el.sinkId))).toEqual(['test-receiver','test-receiver']);
 await guest2.locator('[data-guest-output]').click();await expect(guest2.locator('[data-guest-output-menu]')).toBeHidden();await expect(guest2.locator('[data-guest-output]')).toHaveAttribute('aria-pressed','true');
 await expect.poll(()=>guest2.locator('audio').evaluateAll(nodes=>nodes.filter(el=>el.srcObject).map(el=>el.sinkId))).toEqual(['test-speaker','test-speaker']);
 await guest2.screenshot({path:reviewOut+'/guest-phone-speakerphone.png'});
 await guest2.locator('[data-guest-output]').click();await expect.poll(()=>guest2.locator('audio').evaluateAll(nodes=>nodes.filter(el=>el.srcObject).map(el=>el.sinkId))).toEqual(['test-receiver','test-receiver']);
 await guest2.locator('[data-guest-devices]').click();await expect(guest2.locator('[data-guest-output-menu] select')).toHaveCount(0);await guest2.getByRole('button',{name:'Test headphones',exact:true}).click();
 await expect.poll(()=>guest2.locator('audio').evaluateAll(nodes=>nodes.filter(el=>el.srcObject).map(el=>el.sinkId))).toEqual(['test-headphones','test-headphones']);
 await guest2.locator('[data-guest-output]').click();await guest2.locator('[data-guest-output]').click();
 // A selected output applies to every actual player, including a late join.
 await expect.poll(()=>owner.locator('audio').evaluateAll(nodes=>nodes.filter(el=>el.srcObject).map(el=>el.sinkId))).toEqual(['test-headphones','test-headphones']);
 await owner.locator('[data-guest-output]').click();
 await owner.evaluate(()=>window.__callOutputReject=[...document.querySelectorAll('audio')].find(el=>el.srcObject));
 await owner.getByRole('combobox',{name:'Устройство вывода звука'}).selectOption('test-speaker');
 await expect(owner.locator('[data-guest-output-status]')).toContainText(/браузер/i);
 await expect(owner.getByRole('combobox',{name:'Устройство вывода звука'})).toHaveValue('test-headphones');
 await owner.screenshot({path:reviewOut+'/guest-output-error.png'});
 await owner.getByRole('combobox',{name:'Устройство вывода звука'}).selectOption('test-speaker');
 await expect.poll(()=>owner.locator('audio').evaluateAll(nodes=>nodes.filter(el=>el.srcObject).map(el=>el.sinkId))).toEqual(['test-speaker','test-speaker']);
 await owner.locator('[data-guest-output]').click();
 await owner.evaluate(()=>{window.__copiedReports=[];Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async text=>window.__copiedReports.push(text)});});
 await owner.locator('[data-guest-copy-output]').click();await owner.locator('[data-guest-copy-output]').click();
 assert.equal(await owner.evaluate(()=>window.__copiedReports.length),2);
 const outputReport=await owner.evaluate(()=>JSON.parse(window.__copiedReports.at(-1)));
 assert.equal(outputReport.audioOutput.players,2);assert.equal(outputReport.audioOutput.selection,'device');
 assert(!/test-speaker|test-headphones|Test speaker|Test headphones|candidate:|guestowner|Гость/.test(JSON.stringify(outputReport)));
 await owner.locator('[data-guest-output]').click();
 for(const p of pages)assert.equal(await p.evaluate(()=>navigator.audioSession.type),'play-and-record');
 // Existing participants receive appearance changes through their ordinary sync.
 await owner.locator('[data-guest-minimize]').click();await owner.locator('[data-guest-room="'+roomId+'"]').click();
 await owner.locator('[data-guest-title]').fill('Планы на выходные');await owner.locator('[data-guest-remove-avatar]').click();
 await owner.locator('[data-guest-save]').click();await expect(owner.locator('[data-guest-settings-status]')).toHaveText('Сохранено');
 for(const p of pages){await expect(p.locator('[data-guest-call-title]')).toHaveText('Планы на выходные');await expect(p.locator('[data-guest-call-avatar] img')).toHaveCount(0);}
 await owner.locator('[data-guest-avatar-file]').setInputFiles('tests/fixtures/blue.png');await expect(owner.locator('[data-guest-save]')).toBeEnabled();await owner.locator('[data-guest-save]').click();await expect(owner.locator('[data-guest-settings-status]')).toHaveText('Сохранено');
 for(const p of pages){await expect(p.locator('[data-guest-call-avatar] img')).toBeVisible();await expect(p.locator('[data-guest-call-avatar] img')).not.toHaveAttribute('src',initialAppearance.avatar_url);}
 await owner.locator('.modal-close').click();await owner.locator('[data-guest-minimize]').click();
 await owner.screenshot({path:reviewOut+'/guest-call-group-dark.png'});await guest2.screenshot({path:reviewOut+'/guest-call-group-mobile.png'});
 assert(await guest2.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 async function fullPhoneCall(){
  await eventually(()=>guest2.locator('.guest-call-panel:not(.is-minimized)').evaluate(el=>{const r=el.getBoundingClientRect();return Math.abs(r.x)<1&&Math.abs(r.y)<1&&Math.abs(r.width-innerWidth)<1&&Math.abs(r.height-innerHeight)<1;}),'Guest call fills phone viewport');
  assert(await guest2.locator('.guest-call-controls').evaluate(el=>[...el.querySelectorAll('button')].every(b=>{const r=b.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;})));
 }
 await fullPhoneCall();await guest2.setViewportSize({width:844,height:390});await fullPhoneCall();await guest2.screenshot({path:reviewOut+'/guest-call-fullscreen-landscape.png'});await guest2.setViewportSize({width:320,height:568});await fullPhoneCall();await guest2.setViewportSize({width:390,height:844});
 await guest2.locator('[data-guest-minimize]').click();assert(await guest2.locator('.guest-call-panel').evaluate(el=>el.classList.contains('is-minimized')));await guest2.locator('[data-guest-minimize]').click();await fullPhoneCall();
 await guest1.locator('[data-guest-mute]').click();assert(await guest1.evaluate(()=>__guestStreams.every(s=>s.getAudioTracks().every(t=>!t.enabled))));
 await eventually(async()=>await owner.locator('.guest-call-person').filter({hasText:'Гость Один'}).innerText().then(text=>text.includes('Микрофон выключен')),'Mute state visible to others');
 await guest2.locator('[data-guest-leave]').click();await eventually(async()=>await owner.locator('.guest-call-person').count()===2,'A guest leaves without ending room');
 assert(await guest2.evaluate(()=>__guestStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))&&__guestPCs.every(pc=>pc.connectionState==='closed')));
 await guest2.reload();await guest2.getByLabel('Ваше имя',{exact:true}).waitFor();
 await guest2.getByLabel('Ваше имя',{exact:true}).fill('<img src=x onerror=alert(1)>');await guest2.getByRole('button',{name:'Присоединиться',exact:true}).click();
 await eventually(async()=>await owner.locator('.guest-call-person').count()===3,'A guest can rejoin');assert.equal(await owner.locator('.guest-call-person img').count(),0);
 await owner.locator('.guest-call-person').filter({hasText:'<img src=x onerror=alert(1)>'}).waitFor();
 await owner.locator('[data-guest-end-all]').click();await eventually(async()=>await guest1.locator('.guest-call-panel').count()===0,'Host ends everyone');
 assert(await guest1.evaluate(()=>__guestStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))&&__guestPCs.every(pc=>pc.connectionState==='closed')));
 assert.equal(await guest1.evaluate(()=>navigator.audioSession.type),'auto');assert.equal(await owner.evaluate(()=>navigator.audioSession.type),'auto');
 await guest2.reload();await guest2.getByRole('heading',{name:'Приглашение недоступно',exact:true}).waitFor();
 assert.equal((await guest1Context.request.get(origin+'/api/me')).status(),401);
 // Cancelled permission prompts must stop a microphone granted after hanging up.
 const another=await api('/calls/guest-rooms',{id:crypto.randomUUID().replaceAll('-',''),transport:'livekit'},csrf);
 const [lateContext,late]=await context();await late.goto(another.url);await late.evaluate(()=>{window.releaseCapture=null;navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>releaseCapture=resolve);});
 await late.getByLabel('Ваше имя',{exact:true}).fill('Поздний микрофон');await late.getByRole('button',{name:'Присоединиться',exact:true}).click();await late.locator('[data-guest-leave]').click();
 assert(await late.evaluate(async()=>{const audio=new AudioContext(),stream=audio.createMediaStreamDestination().stream;releaseCapture(stream);await new Promise(resolve=>setTimeout(resolve,50));const stopped=stream.getTracks().every(t=>t.readyState==='ended');await audio.close();return stopped;}));
 await api('/calls/guest-rooms/'+another.id+'/end',{},csrf);
 assert.deepEqual(errors,[]);
 for(const c of contexts)await c.close();console.log('Guest call browser passed: appearance save/retry/reopen, name/avatar live updates and removal, create/copy, name-only entry, three-way real audio, mute, mobile, leave, host end, microphone/peer cleanup, link revocation.');
 }
 }catch(error){
 for(const p of inspectedPages)if(!p.isClosed()){
  const diagnostics=await p.evaluate(async()=>{
   const result=[];
   for(const pc of window.__guestPCs||[])result.push({state:pc.connectionState,ice:pc.iceConnectionState,gather:pc.iceGatheringState,signal:pc.signalingState,stats:[...(await pc.getStats()).values()].filter(s=>['candidate-pair','local-candidate','remote-candidate','inbound-rtp'].includes(s.type))});
   return result;
  });
  console.error('Peer diagnostics',JSON.stringify(diagnostics));
  console.error((await p.locator('body').innerText()).slice(-3500));
  await p.screenshot({path:reviewOut+'/guest-call-failure-'+inspectedPages.indexOf(p)+'.png'});
 }
 throw error;
}finally{
 let cleanupError;try{await browser?.close();if(isLiveKit)await cleanupSFU();}catch(error){cleanupError=error;}finally{server.kill('SIGTERM');await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);});await rm(directory,{recursive:true,force:true});}if(cleanupError)throw cleanupError;
}

async function cleanupSFU(){
 const url=process.env.CALL_LIVEKIT_API_URL||process.env.CALL_LIVEKIT_URL?.replace(/^wss:/,'https:');
 const key=process.env.CALL_LIVEKIT_API_KEY,secret=process.env.CALL_LIVEKIT_API_SECRET;
 if(!url||!key||!secret)throw Error('Missing SFU cleanup configuration');
 const now=Math.floor(Date.now()/1000),b=v=>Buffer.from(JSON.stringify(v)).toString('base64url'),prefix=b({alg:'HS256',typ:'JWT'})+'.'+b({iss:key,sub:'isolated-test-cleanup',nbf:now-5,exp:now+60,video:{roomList:true,roomCreate:true}}),token=prefix+'.'+createHmac('sha256',secret).update(prefix).digest('base64url');
 const {createHash}=await import('node:crypto'),namespace='drg-'+createHash('sha256').update(origin).digest('hex').slice(0,16)+'-';
 const rpc=async(method,body)=>{const r=await fetch(url+'/twirp/livekit.RoomService/'+method,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('SFU cleanup HTTP '+r.status);return r.json();};
 const rooms=(await rpc('ListRooms',{})).rooms||[];let removed=0;
 for(const room of rooms)if(room.name.startsWith(namespace)){await rpc('DeleteRoom',{room:room.name});removed++;}
 const remaining=((await rpc('ListRooms',{})).rooms||[]).filter(room=>room.name.startsWith(namespace)).length;
 if(remaining)throw Error('Isolated SFU rooms remain');console.log('Isolated SFU cleanup verified; remaining rooms: 0.');
}
