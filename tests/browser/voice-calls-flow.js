import {expect} from '@playwright/test';
import {installCallOutputFixture} from './call-output-fixture.js';
import {appSection} from './notes-ui.js';
import {installSilentAudio,silenceTestAudio} from './silent-audio.js';
async function api(page,path,method='GET',data){const result=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('drg_csrf='))?.slice(9)||'');const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:response.status,data:await response.json()};},{path,method,data});expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;}
async function enroll(page,password,totp){const data=await api(page,'/2fa/enroll','POST',{password});await api(page,'/2fa/confirm','POST',{code:totp(data.secret)});await api(page,'/2fa/activate','POST',{saved_recovery_codes:true});await page.reload();await expect(page.locator('.workspace')).toBeVisible();}
async function chat(page){await appSection(page,'Сообщения');await expect(page.locator('.chat-section-tabs')).toBeVisible();}
async function phoneCallBounds(page){
  await expect.poll(()=>page.locator('.voice-call:not(.is-minimized)').evaluate(el=>{const r=el.getBoundingClientRect();return Math.abs(r.x)<1&&Math.abs(r.y)<1&&Math.abs(r.width-innerWidth)<1&&Math.abs(r.height-innerHeight)<1;})).toBe(true);
  expect(await page.locator('.voice-call').evaluate(el=>[...el.querySelectorAll('button')].filter(b=>b.getClientRects().length).every(b=>{const r=b.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;}))).toBe(true);
}
export async function voiceCallsFlow(page,browser,totp,reviewOut='artifacts'){
  await silenceTestAudio(page.context());await page.evaluate(installSilentAudio);
  await page.context().addInitScript(installCallOutputFixture);await page.evaluate(installCallOutputFixture);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewportSize({width:1280,height:900});
  const password='Voice-call-test-password-123',saved=(await api(page,'/modules')).enabled;
  await api(page,'/modules','PUT',{enabled:{...saved,chat:true}});
  const other=await browser.newContext({baseURL:'http://127.0.0.1:8001',locale:'ru-RU',hasTouch:true,permissions:['microphone']});await silenceTestAudio(other);await other.addInitScript(installCallOutputFixture);const peer=await other.newPage();peer.on('pageerror',e=>errors.push(e.message));await peer.goto('/');
  let mockMicrophone;
  if(browser.browserType().name()==='webkit'){
    await page.context().grantPermissions(['microphone']);
    // WebKit's test runner cannot grant a hardware microphone. Supply a real
    // Web Audio track, while exercising native SDP, ICE, RTP and playback.
    const microphone=mockMicrophone=()=>{const install=()=>{const devices=navigator.mediaDevices;window.__voiceNativeCapture??=devices.getUserMedia.bind(devices);Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:devices});Object.defineProperty(devices,'getUserMedia',{configurable:true,value:async()=>{const native=await window.__voiceNativeCapture({audio:true}),audio=new AudioContext(),oscillator=audio.createOscillator(),output=audio.createMediaStreamDestination();oscillator.connect(output);oscillator.start();try{await audio.resume();}catch(error){native.getTracks().forEach(t=>t.stop());throw new Error('WebKit test audio: '+error.name+' '+error.message);}const track=output.stream.getAudioTracks()[0],stop=track.stop.bind(track);track.stop=()=>{stop();native.getTracks().forEach(t=>t.stop());oscillator.stop();audio.close().catch(()=>{});};return output.stream;}});};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();};
    await page.context().addInitScript(microphone);await other.addInitScript(microphone);await peer.evaluate(microphone);
  }
  try{
    const invitation=await api(page,'/invites','POST',{login:'voicefriend'});
    await api(peer,'/register','POST',{login:'voicefriend',password,invite:new URL(invitation.url).pathname.split('/').at(-1)});await enroll(peer,password,totp);
    const request=await api(page,'/social/friends','POST',{login:'voicefriend'}),accepted=await api(peer,'/social/requests/'+request.id+'/accept','POST'),sid=accepted.space_id;
    await page.reload();for(const view of [page,peer]){const observe=forceRelay=>{const RealPeer=window.RTCPeerConnection;window.__voicePeers=[];window.RTCPeerConnection=class extends RealPeer{constructor(config,...rest){super({...config,...(forceRelay?{iceTransportPolicy:'relay'}:{})},...rest);window.__voicePeers.push(this);}};};await view.addInitScript(observe,process.env.DRG_TEST_RELAY==='1');await view.evaluate(observe,process.env.DRG_TEST_RELAY==='1');}await chat(page);await page.locator(`[data-room="${sid}"]`).click();
    await page.locator('#message-body').fill('Проверка прочтения');await page.locator('#message-body').press('Enter');
    const sent=page.locator('.chat-message.mine').filter({hasText:'Проверка прочтения'});await expect(sent.locator('[aria-label="Отправлено"]')).toBeVisible();
    await api(peer,'/notifications/read-all','POST');await expect(sent.locator('[aria-label="Прочитано"]')).toHaveCount(0);
    await peer.bringToFront();await chat(peer);await peer.locator(`[data-room="${sid}"]`).click();await expect(sent.locator('[aria-label="Прочитано"]')).toBeVisible({timeout:15000});
    await expect(page.locator('.chat-header .person-presence')).toHaveText('В сети',{timeout:10000});await expect(page.locator('.chat-header [data-person-online]')).toBeVisible();
    await peer.locator('#message-body').fill('Черновик не передаётся');await expect(page.locator('#chat-status')).toContainText('печатает',{timeout:10000});await expect(page.locator(`[data-room="${sid}"] .chat-room-preview`)).toContainText('печатает');
    await page.screenshot({path:reviewOut+'/chat-read-typing.png',fullPage:true});await peer.locator('#message-body').fill('');await expect(page.locator('#chat-status')).not.toContainText('печатает',{timeout:10000});
    await page.getByRole('tab',{name:'Контакты',exact:true}).click();await expect(page.locator('[data-contact-room]').filter({hasText:'voicefriend'})).toBeVisible();await expect(page.locator('#message-body')).toBeVisible();
    await page.getByRole('tab',{name:'Звонки',exact:true}).click();await expect(page.locator('[data-new-call]')).toBeVisible();await page.reload();await expect(page.getByRole('tab',{name:'Звонки',exact:true})).toHaveAttribute('aria-selected','true');
    await page.getByRole('tab',{name:'Чаты',exact:true}).click();await expect(page.locator('.chat-empty-selection')).toBeVisible();await page.locator(`[data-room="${sid}"]`).click();await expect(page.locator('#message-body')).toBeVisible();if(mockMicrophone)for(const view of [page,peer]){await view.evaluate(mockMicrophone);expect(await view.evaluate(()=>navigator.mediaDevices.getUserMedia.toString())).toContain('createMediaStreamDestination');}await page.bringToFront();
    // Cancel a permission prompt that never settles, then grant it late. The
    // abandoned track must stop and a new call must start without waiting 25s.
    await page.evaluate(()=>{window.__realVoiceMic=navigator.mediaDevices.getUserMedia;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,writable:true,value:()=>new Promise(resolve=>{window.__lateVoiceGrant=()=>{const audio=new AudioContext(),stream=audio.createMediaStreamDestination().stream;window.__lateVoiceTrack=stream.getAudioTracks()[0];resolve(stream);setTimeout(()=>audio.close(),1000);};})});});
    await page.getByRole('button',{name:'Позвонить',exact:true}).click();await expect(page.locator('.voice-call [data-call-status]')).toHaveText('Подключаем микрофон…');await page.locator('[data-call-end]').click();await page.locator('[data-call-close]').evaluateAll(nodes=>nodes.forEach(node=>node.click()));
    await page.evaluate(()=>{Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,writable:true,value:window.__realVoiceMic});window.__lateVoiceGrant();});await expect.poll(()=>page.evaluate(()=>window.__lateVoiceTrack.readyState)).toBe('ended');
    await page.getByRole('button',{name:'Позвонить',exact:true}).click();await expect(page.locator('.voice-call [data-call-status]')).toHaveText('Исходящий вызов');await peer.bringToFront();
    await expect(peer.getByRole('dialog',{name:'Звонок'})).toBeVisible({timeout:15000});if(mockMicrophone)await peer.evaluate(mockMicrophone);await peer.getByRole('button',{name:'Принять',exact:true}).click();
    await expect.poll(async()=>(await api(page,'/calls/history')).items[0]?.status,{timeout:20000}).toBe('active');
    await expect(page.locator('.voice-call [data-call-status]')).toHaveText(/\d+:\d{2}/);await expect(peer.locator('.voice-call [data-call-status]')).toHaveText(/\d+:\d{2}/);
    await expect.poll(()=>peer.locator('audio').evaluate(el=>el.srcObject?.getAudioTracks().filter(t=>t.readyState==='live').length||0)).toBe(1);
    // The phone's main button never opens a device menu. Headphones are a
    // separate explicit choice; no microphone/connection is replaced.
    await peer.setViewportSize({width:430,height:932});
    await peer.locator('[data-call-devices]').click();await peer.getByRole('menuitemradio',{name:'Телефон — у уха',exact:true}).click();
    await peer.locator('[data-call-output]').click();await expect(peer.locator('[data-call-output]')).toHaveAttribute('aria-pressed','true');await expect(peer.locator('[data-call-outputs]')).toBeHidden();
    expect(await peer.locator('audio').evaluate(el=>el.sinkId)).toBe('test-speaker');
    await peer.locator('[data-call-output]').click();expect(await peer.locator('audio').evaluate(el=>el.sinkId)).toBe('test-receiver');
    await peer.locator('[data-call-devices]').click();await peer.screenshot({path:reviewOut+'/phone-headphones.png'});await peer.getByRole('menuitemradio',{name:'Test headphones',exact:true}).click();
    expect(await peer.locator('audio').evaluate(el=>el.sinkId)).toBe('test-headphones');
    await peer.locator('[data-call-output]').click();await peer.locator('[data-call-output]').click();
    await peer.evaluate(()=>window.__callOutputReject=document.querySelector('audio'));await peer.locator('[data-call-output]').click();
    await expect(peer.locator('[data-call-route-status]')).toContainText(/браузер/i);await expect(peer.locator('[data-call-output]')).toHaveAttribute('aria-pressed','false');await expect(peer.locator('[data-call-outputs]')).toBeHidden();
    await peer.locator('[data-call-output]').click();await peer.screenshot({path:reviewOut+'/phone-speakerphone.png'});await phoneCallBounds(peer);
    await peer.locator('[data-call-output]').click();await peer.screenshot({path:reviewOut+'/phone-earpiece.png'});
    await peer.setViewportSize({width:1280,height:900});
    // Window restore and native fullscreen have distinct buttons and icons.
    await page.locator('[data-call-minimize]').click();await expect(page.locator('.voice-call')).toHaveClass(/is-minimized/);
    expect(await page.locator('[data-call-minimize] svg').innerHTML()).not.toBe(await page.locator('[data-call-expand] svg').innerHTML());await expect(page.locator('[data-call-minimize]')).toHaveAttribute('title','Развернуть звонок');
    if(await page.evaluate(()=>document.fullscreenEnabled&&typeof document.querySelector('.voice-call').requestFullscreen==='function')){
      await page.locator('[data-call-expand]').click();await expect(page.locator('.voice-call')).not.toHaveClass(/is-minimized/);await expect(page.locator('[data-call-expand]')).toHaveAttribute('aria-label','Выйти из полного экрана');await expect.poll(()=>page.evaluate(()=>document.fullscreenElement?.classList.contains('voice-call'))).toBe(true);
      await page.locator('[data-call-minimize]').click();await expect(page.locator('.voice-call')).toHaveClass(/is-minimized/);expect(await page.evaluate(()=>document.fullscreenElement===null)).toBe(true);
      await page.locator('[data-call-expand]').click();await expect(page.locator('[data-call-expand]')).toHaveAttribute('aria-label','Выйти из полного экрана');await page.evaluate(()=>document.exitFullscreen());await expect(page.locator('[data-call-expand]')).toHaveAttribute('aria-label','На весь экран');
    }else await page.locator('[data-call-minimize]').click();
    for(const view of [page,peer])expect(await view.evaluate(()=>window.__drgSilentTestAudio&&[...document.querySelectorAll('audio,video')].every(el=>el.muted)&&window.__drgSilentTestGains.every(node=>node.gain.value===0))).toBe(true);
    for(const viewport of [{width:430,height:932},{width:390,height:844},{width:320,height:568},{width:740,height:390}]){await page.setViewportSize(viewport);await phoneCallBounds(page);}
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:reviewOut+'/call-fullscreen-mobile.png'});
    await page.locator('[data-call-minimize]').click();await expect(page.locator('.voice-call')).toHaveClass(/is-minimized/);await expect(page.locator('[data-call-expand]')).toBeHidden();await page.getByRole('button',{name:'Развернуть звонок',exact:true}).click();await phoneCallBounds(page);
    await page.setViewportSize({width:1280,height:900});
    for(const view of [page,peer])await expect.poll(()=>view.evaluate(async()=>{let packets=0;for(const pc of window.__voicePeers){for(const value of (await pc.getStats()).values())if(value.type==='inbound-rtp'&&value.kind==='audio')packets+=value.packetsReceived||0;}return packets;})).toBeGreaterThan(0);
    // Route selection uses browser-provided IDs and keeps RTP/microphone live.
    // Fake outputs are necessary because headless CI has no iPhone hardware.
    await page.locator('[data-call-output]').click();
    await page.evaluate(()=>window.__callOutputDelay=true);
    await page.getByRole('menuitemradio',{name:'Speaker',exact:true}).click();
    await expect(page.locator('[data-output-status]')).toHaveText('Переключаем аудиовыход…');
    await expect(page.getByRole('menuitemradio',{name:'Test headphones',exact:true})).toBeDisabled();
    await page.evaluate(()=>window.__releaseCallOutput());
    await expect(page.locator('[data-call-outputs]')).toBeHidden();
    await page.locator('[data-call-output]').click();
    await expect(page.getByRole('menuitemradio',{name:'Speaker',exact:true})).toHaveAttribute('aria-checked','true');
    await page.evaluate(()=>window.__callOutputReject=document.querySelector('audio'));
    await page.getByRole('menuitemradio',{name:'Test headphones',exact:true}).click();
    await expect(page.locator('[data-output-status]')).toContainText(/браузер/i);
    await expect(page.getByRole('menuitemradio',{name:'Speaker',exact:true})).toHaveAttribute('aria-checked','true');
    await page.screenshot({path:reviewOut+'/call-output-error.png'});
    await page.setViewportSize({width:430,height:932});
    expect(await page.locator('[data-call-outputs]').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;})).toBe(true);
    await page.screenshot({path:reviewOut+'/call-output-error-phone.png'});await page.setViewportSize({width:1280,height:900});await expect(page.locator('[data-call-output]')).toHaveAttribute('aria-haspopup','menu');
    await page.getByRole('menuitemradio',{name:'Test headphones',exact:true}).click();
    expect(await page.locator('audio').evaluate(el=>el.sinkId)).toBe('test-headphones');
    await page.evaluate(()=>{window.__callOutputs=[];navigator.mediaDevices.dispatchEvent(new Event('devicechange'));});
    await expect.poll(()=>page.locator('audio').evaluate(el=>el.sinkId)).toBe('');
    for(const view of [page,peer])expect(await view.evaluate(()=>navigator.audioSession.type)).toBe('play-and-record');
    const micId=await page.evaluate(()=>window.__voicePeers.find(pc=>pc.connectionState==='connected').getSenders().find(s=>s.track?.kind==='audio').track.id);
    await page.evaluate(()=>{Object.defineProperty(navigator.audioSession,'state',{configurable:true,writable:true,value:'interrupted'});navigator.audioSession.dispatchEvent(new Event('statechange'));});
    await expect(page.locator('[data-call-audio]')).toBeVisible();
    await page.evaluate(()=>{navigator.audioSession.state='active';navigator.audioSession.dispatchEvent(new Event('statechange'));});
    await expect(page.locator('[data-call-audio]')).toBeHidden();
    expect(await page.evaluate(()=>window.__voicePeers.find(pc=>pc.connectionState==='connected').getSenders().find(s=>s.track?.kind==='audio').track.id)).toBe(micId);
    await page.locator('[data-call-output]').click();await page.locator('[data-call-quality]').click();await expect(page.locator('[data-call-quality-report]')).toContainText('Потери пакетов');await expect(page.locator('[data-call-quality-report]')).toContainText('Максимум восстановления');
    const gain=page.locator('[data-call-auto-gain]');
    if(await gain.isEnabled()){
      const before=await page.evaluate(()=>{const t=window.__voicePeers[0].getSenders().find(s=>s.track?.kind==='audio').track;return {id:t.id,enabled:t.enabled,gain:t.getSettings().autoGainControl};});
      await gain.click();await expect(page.locator('[data-output-status]')).not.toContainText('Применяем');
      const after=await page.evaluate(()=>{const t=window.__voicePeers[0].getSenders().find(s=>s.track?.kind==='audio').track;return {id:t.id,enabled:t.enabled,gain:t.getSettings().autoGainControl,readyState:t.readyState};});
      expect(after.id).toBe(before.id);expect(after.enabled).toBe(before.enabled);expect(after.readyState).toBe('live');
      if(after.gain===before.gain){await expect(page.locator('[data-output-status]')).toContainText('Браузер');await expect(gain).toBeChecked({checked:before.gain});}else{expect(after.gain).toBe(!before.gain);await expect(page.locator('[data-output-status]')).toContainText('Автоматическая громкость '+(!before.gain?'включена':'выключена'));}
    }
    await page.evaluate(()=>{window.__copiedReports=[];Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:async text=>window.__copiedReports.push(text)});});
    await page.locator('[data-call-copy-quality]').click();await page.locator('[data-call-copy-quality]').click();
    expect(await page.evaluate(()=>window.__copiedReports.length)).toBe(2);
    const report=await page.evaluate(()=>JSON.parse(window.__copiedReports.at(-1)));
    expect(report.audioOutput.players).toBe(1);expect(report.audioOutput.sessionType).toBe('play-and-record');
    expect(JSON.stringify(report)).not.toMatch(/test-speaker|test-headphones|Test speaker|Test headphones|candidate:/);
    await page.screenshot({path:reviewOut+'/call-output-menu.png',fullPage:true});await page.locator('[data-output-close]').click();
    await page.getByRole('button',{name:'Выключить микрофон',exact:true}).click();await expect(peer.locator('[data-peer-muted]')).toBeVisible({timeout:15000});await expect(page.getByRole('button',{name:'Включить микрофон',exact:true})).toHaveAttribute('aria-pressed','true');await page.getByRole('button',{name:'Включить микрофон',exact:true}).click();
    if(process.env.DRG_TEST_RELAY==='1')for(const view of [page,peer])expect(await view.evaluate(async()=>{const stats=await window.__voicePeers[0].getStats();for(const record of stats.values())if(record.type==='transport'&&record.selectedCandidatePairId){const pair=stats.get(record.selectedCandidatePairId);return [stats.get(pair.localCandidateId)?.candidateType,stats.get(pair.remoteCandidateId)?.candidateType];}})).toEqual(['relay','relay']);
    // Real canvas video tracks exercise native RTP in browsers without webcams.
    for(const view of [page,peer])await view.evaluate(()=>{
      const devices=navigator.mediaDevices;Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:devices});const real=devices.getUserMedia.bind(devices);
      const visual=()=>{const canvas=document.createElement('canvas');canvas.width=640;canvas.height=360;const ctx=canvas.getContext('2d');let frame=0;const timer=setInterval(()=>{ctx.fillStyle=frame++%2?'#6687d8':'#73af89';ctx.fillRect(0,0,640,360);ctx.fillStyle='white';ctx.fillText('WebRTC '+frame,20,20);},60);const stream=canvas.captureStream(15);const track=stream.getVideoTracks()[0],stop=track.stop.bind(track);track.stop=()=>{clearInterval(timer);stop();};(window.__visualTracks??=[]).push(track);return stream;};
      window.__captureVisual=visual;Object.defineProperty(devices,'getUserMedia',{configurable:true,value:constraints=>constraints.video?Promise.resolve(visual()):real(constraints)});
      Object.defineProperty(devices,'getDisplayMedia',{configurable:true,value:()=>Promise.resolve(visual())});
    });
    for(const view of [page,peer]){await view.locator('[data-call-camera]').click();await expect(view.locator('[data-call-local]')).toBeVisible();}
    for(const view of [page,peer])await expect.poll(()=>view.evaluate(async()=>{let packets=0;for(const pc of window.__voicePeers)for(const item of (await pc.getStats()).values())if(item.type==='inbound-rtp'&&item.kind==='video')packets+=item.framesDecoded||0;return packets;}),{timeout:15000}).toBeGreaterThan(0);
    for(const view of [page,peer]){await expect(view.locator('.voice-call')).toHaveClass(/has-video/,{timeout:15000});await expect.poll(()=>view.locator('[data-call-remote]').evaluate(el=>el.videoWidth)).toBeGreaterThan(0);}await expect(peer.locator('[data-call-remote]')).toBeVisible();await page.screenshot({path:reviewOut+'/video-call-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});await phoneCallBounds(page);await page.screenshot({path:reviewOut+'/video-call-fullscreen-mobile.png'});await page.setViewportSize({width:1280,height:900});
    await page.locator('[data-call-screen]').click();await expect(peer.locator('[data-call-remote]')).toHaveClass(/is-screen/,{timeout:15000});await expect(page.locator('[data-self-screen]')).toBeVisible();
    await expect.poll(()=>peer.evaluate(async()=>{const pc=window.__voicePeers.find(pc=>pc.connectionState==='connected'),track=pc.getTransceivers().filter(t=>t.receiver.track.kind==='video')[1].receiver.track;for(const item of (await pc.getStats(track)).values())if(item.type==='inbound-rtp')return item.packetsReceived||0;return 0;})).toBeGreaterThan(0);
    await page.screenshot({path:reviewOut+'/screen-call-desktop.png',fullPage:true});
    await page.locator('[data-call-screen]').click();await expect(page.locator('[data-self-screen]')).toBeHidden();await expect(peer.locator('[data-call-remote]')).not.toHaveClass(/is-screen/,{timeout:15000});
    await page.locator('[data-call-camera]').click();await expect(peer.locator('[data-call-remote]')).toBeHidden({timeout:15000});await page.locator('[data-call-minimize]').click();
    await page.screenshot({path:reviewOut+'/voice-call-desktop.png',fullPage:true});
    // Changing the workspace must not end or remount the active media session.
    await page.getByRole('tab',{name:'Настройки',exact:true}).click();await expect(page.locator('.voice-call')).toBeVisible();await expect.poll(async()=>(await api(page,'/calls/history')).items[0]?.status).toBe('active');
    await page.evaluate(()=>{Object.defineProperty(navigator.mediaDevices,'getDisplayMedia',{configurable:true,value:()=>new Promise(resolve=>{window.__lateScreenGrant=()=>resolve(window.__captureVisual());})});});await page.locator('[data-call-screen]').click();
    await page.evaluate(()=>{window.__callOutputs=[{kind:'audiooutput',deviceId:'test-headphones',label:'Test headphones'}];window.__callOutputDelay=true;window.__retiredCallAudio=document.querySelector('audio');});
    await page.locator('[data-call-output]').click();await page.getByRole('menuitemradio',{name:'Test headphones',exact:true}).click();
    await expect(page.locator('[data-output-status]')).toHaveText('Переключаем аудиовыход…');
    await page.getByRole('button',{name:'Завершить',exact:true}).click();await page.evaluate(()=>window.__lateScreenGrant());await expect.poll(()=>page.evaluate(()=>window.__visualTracks.every(t=>t.readyState==='ended'))).toBe(true);await expect.poll(async()=>(await api(peer,'/calls/history')).items[0]?.status).toBe('ended');await expect(peer.locator('audio')).toHaveJSProperty('srcObject',null);for(const view of [page,peer])expect(await view.evaluate(()=>navigator.audioSession.type)).toBe('auto');for(const view of [page,peer])expect(await view.evaluate(()=>window.__visualTracks.every(t=>t.readyState==='ended'))).toBe(true);
    if(await page.locator('[data-call-close]').count())await page.locator('[data-call-close]').click();if(await peer.locator('[data-call-close]').count())await peer.locator('[data-call-close]').click();
    await chat(page);await page.getByRole('tab',{name:'Звонки',exact:true}).click();await expect(page.locator('.chat-call-row')).toHaveCount(1);await expect(page.locator('.chat-call-row')).toContainText('Исходящий');
    await page.setViewportSize({width:390,height:844});await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);await expect(page.locator('.chat-section-tabs')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:reviewOut+'/voice-calls-mobile.png',fullPage:true});
    await page.bringToFront();if(mockMicrophone)await page.evaluate(mockMicrophone);await page.evaluate(()=>window.__callOutputs=[{kind:'audiooutput',deviceId:'test-speaker',label:'Speaker'},{kind:'audiooutput',deviceId:'test-receiver',label:'Receiver'},{kind:'audiooutput',deviceId:'test-headphones',label:'Test headphones'}]);await page.locator('.chat-call-row').click();await expect(page.locator('.voice-call [data-call-status]')).toHaveText('Исходящий вызов');
    expect(await page.evaluate(()=>document.querySelector('audio')!==window.__retiredCallAudio)).toBe(true);
    await page.evaluate(()=>{window.__outputBeforeLate=window.__callOutputChanges.length;window.__releaseCallOutput();});await expect.poll(()=>page.evaluate(()=>window.__callOutputChanges.length-window.__outputBeforeLate)).toBeGreaterThanOrEqual(2);await expect.poll(()=>page.evaluate(()=>window.__retiredCallAudio.sinkId)).toBe('');
    // A new desktop call starts on the system output; the retired request must not select headphones.
    expect(await page.locator('audio').evaluate(el=>el.sinkId)).toBe('');expect(await page.evaluate(()=>navigator.audioSession.type)).toBe('play-and-record');
    await phoneCallBounds(page);await peer.setViewportSize({width:390,height:844});await peer.bringToFront();await expect(peer.locator('[data-call-answer]')).toBeVisible({timeout:15000});await phoneCallBounds(peer);await peer.screenshot({path:reviewOut+'/incoming-call-fullscreen-mobile.png'});await peer.locator('[data-call-end]').click();await expect.poll(async()=>(await api(page,'/calls/history')).items[0]?.status).toBe('declined');
    await expect.poll(async()=>(await api(page,'/calls/history')).items[0]?.status).toBe('declined');
    // Finish this call's terminal UI before the next scenario opens dialogs.
    await page.locator('[data-call-close]').click();await expect(page.locator('.voice-call')).toHaveCount(0);
    await page.setViewportSize({width:1280,height:900});await page.getByRole('tab',{name:'Звонки',exact:true}).click();
    await page.route('**/api/calls/config',async route=>{const response=await route.fetch();await route.fulfill({response,json:{available:false,relay_configured:true,iceServers:[]}});});await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await expect(page.getByRole('tab',{name:'Звонки',exact:true})).toHaveCount(0);await expect(page.locator('[data-chat-call]')).toBeHidden();await expect(page.getByRole('tab',{name:'Чаты',exact:true})).toHaveAttribute('aria-selected','true');
    await page.unroute('**/api/calls/config');await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.getByRole('tab',{name:'Звонки',exact:true})).toBeVisible();
    expect(errors).toEqual([]);
  }finally{await other.close();await api(page,'/modules','PUT',{enabled:saved});await page.setViewportSize({width:1280,height:900});}
}
