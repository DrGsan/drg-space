// Runs against the disposable two-server Docker harness. No production sessions.
import {expect} from '@playwright/test';
import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHmac} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {appSection} from './notes-ui.js';
import {federationDatabaseScenario} from './federation-databases.mjs';
import {federationDatabaseSecurityScenario} from './federation-database-security.mjs';

const names=['alice','bob'],password='Federation-browser-test-password-123',suffix=Date.now().toString(36),login=name=>'web'+name+suffix;
const state=JSON.parse(await readFile(process.env.FEDERATION_BROWSER_STATE,'utf8'));
const cookieCachePath=process.env.FEDERATION_BROWSER_STATE+'.browser-cookies.json',debugCookies=process.env.FEDERATION_BROWSER_LOCAL_ASSETS==='1'||process.env.FEDERATION_DATABASES_ONLY==='1';let ownerCookies={};if(debugCookies)try{ownerCookies=JSON.parse(await readFile(cookieCachePath,'utf8'));}catch{}
const origins=Object.fromEntries(names.map(name=>[name,`https://${name}.federation.test:8443`]));
const mapping=names.map(name=>`MAP ${name}.federation.test:8443 127.0.0.1:${process.env['FEDERATION_'+name.toUpperCase()+'_PORT']}`).join(', ');
for(const name of names)if(!/^\d+$/.test(process.env['FEDERATION_'+name.toUpperCase()+'_PORT']||''))throw Error('Missing disposable browser port: '+name);
const browser=await chromium.launch({headless:true,args:['--no-proxy-server','--host-resolver-rules='+mapping,'--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required']});
const contexts=[],errors=[];
function totp(secret){let bits='';for(const letter of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(letter).toString(2).padStart(5,'0');const key=[];for(let i=0;i+8<=bits.length;i+=8)key.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(key)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
async function rawApi(page,path,method='GET',body){return page.evaluate(async({path,method,body})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},...(body===undefined?{}:{body:JSON.stringify(body)})});return {status:response.status,body:await response.json()};},{path,method,body});}
async function api(page,path,method='GET',body){
  const result=await rawApi(page,path,method,body);
  expect(result.status,path+' '+JSON.stringify(result.body)).toBeLessThan(300);return result.body;
}
async function newPage(name,saved){
  const context=await browser.newContext({locale:'ru-RU',baseURL:origins[name],ignoreHTTPSErrors:true,viewport:{width:1280,height:1000},permissions:['microphone','camera','clipboard-read','clipboard-write']});contexts.push(context);
  if(process.env.FEDERATION_BROWSER_LOCAL_ASSETS==='1')await context.route('**/assets/**',async route=>{const url=new URL(route.request().url()),name=url.pathname.slice('/assets/'.length);if(!Object.values(origins).includes(url.origin)||!url.pathname.startsWith('/assets/')||name.includes('..'))return route.continue();await route.fulfill({path:fileURLToPath(new URL('../../app/static/assets/'+name,import.meta.url))});});
  if(saved)await context.addCookies(saved.cookies.map(cookie=>({...cookie,secure:true,httpOnly:cookie.name!=='drg_csrf',sameSite:'Lax'})));
  if(saved&&ownerCookies[name])await context.addCookies(ownerCookies[name]);
  if(!saved)await context.addInitScript(({port})=>{
    const Native=window.RTCPeerConnection;window.__federationPeers=[];
    window.RTCPeerConnection=class extends Native{constructor(config={}){const iceServers=(config.iceServers||[]).map(server=>({...server,urls:(Array.isArray(server.urls)?server.urls:[server.urls]).map(url=>url.startsWith('turn:')?`turn:127.0.0.1:${port}?transport=udp`:url)}));super({...config,iceServers,iceTransportPolicy:'relay'});window.__federationPeers.push(this);}};
  },{port:process.env.FEDERATION_TURN_PORT});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(name+': '+error.message));page.on('response',async response=>{if(!response.ok()&&new URL(response.url()).pathname.startsWith('/api/federation/')){const data=response.request().postDataJSON();console.log('Federation browser rejection '+response.status()+' '+new URL(response.url()).pathname+' '+(data?.intent?.operation||data?.operation||'')+' '+(await response.text()).slice(0,400));}});await page.goto('/');return page;
}
async function register(name){
  const owner=await newPage(name,state[name]);
  await owner.locator('.workspace').waitFor({state:'visible'});
  let result=await rawApi(owner,'/invites','POST',{login:login(name)});if(result.status===423){await api(owner,'/admin/access/unlock','POST',{password:'Disposable-prototype-password-123'});result={status:200,body:await api(owner,'/invites','POST',{login:login(name)})};}expect(result.status,'Invite creation '+JSON.stringify(result.body)).toBeLessThan(300);const invitation=result.body;
  if(debugCookies){ownerCookies[name]=await owner.context().cookies();await writeFile(cookieCachePath,JSON.stringify(ownerCookies),{mode:0o600});}
  const page=await newPage(name);await api(page,'/register','POST',{login:login(name),password,invite:invitation.url.split('/').at(-1)});
  const enrollment=await api(page,'/2fa/enroll','POST',{password});await api(page,'/2fa/confirm','POST',{code:totp(enrollment.secret)});await api(page,'/2fa/activate','POST',{saved_recovery_codes:true});
  await page.reload();await appSection(page,'Люди и группы');await expect(page.locator('[data-own-contact]')).toBeVisible();return page;
}
async function card(page){await page.locator('[data-own-contact]').click();const textarea=page.locator('[data-contact-card]');await expect(textarea).toBeVisible();const result=await textarea.inputValue();await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();return result;}
async function importCard(page,value){await page.locator('[data-add-contact]').click();await page.locator('[data-contact-import] textarea').fill(value);await page.locator('[data-check-contact]').click();await expect(page.locator('[data-contact-preview]')).toBeVisible();await page.locator('[name=verified]').check();await page.locator('[data-confirm-contact]').click();await expect(page.locator('[data-contact-import]')).toHaveCount(0);await expect(page.locator('[data-federation-person]')).toHaveCount(1);}
async function refreshPeople(page){await page.locator('#refresh-social').click();await expect(page.locator('[data-federation-person]')).toHaveCount(1);}
const block=page=>page.locator('[data-id=federation_text] .bn-inline-content').first();
async function share(page,role){await expect(page.locator('#page-title')).toBeEnabled();await page.locator('#share-page').click();await page.locator('[data-federation-share]').click();await expect(page.locator('[data-grant-person]')).toHaveCount(1);await page.locator('[data-grant-person]').selectOption(role);await expect(page.locator('[data-grant-person]')).toBeEnabled();await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();}
async function rtcStats(page){return page.evaluate(async()=>{const pc=window.__federationPeers.at(-1);if(!pc)return null;const report=await pc.getStats(),all=[...report.values()],pair=all.find(item=>item.type==='candidate-pair'&&item.state==='succeeded'&&item.nominated),local=pair&&report.get(pair.localCandidateId),remote=pair&&report.get(pair.remoteCandidateId);return {connection:pc.connectionState,local:local?.candidateType,remote:remote?.candidateType,received:all.filter(item=>item.type==='inbound-rtp'&&item.kind==='audio').reduce((sum,item)=>sum+(item.bytesReceived||0),0),sent:all.filter(item=>item.type==='outbound-rtp'&&item.kind==='audio').reduce((sum,item)=>sum+(item.bytesSent||0),0)};});}

try{
  await mkdir('artifacts',{recursive:true});
  const alice=await register('alice'),bob=await register('bob');
  const aliceCard=await card(alice),bobCard=await card(bob);
  await importCard(alice,bobCard);await importCard(bob,aliceCard);
  // Browser-generated signing and encryption keys must be nonextractable.
  for(const page of [alice,bob])expect(await page.evaluate(()=>new Promise((resolve,reject)=>{const request=indexedDB.open('drg-federation-devices');request.onsuccess=()=>{const get=request.result.transaction('devices').objectStore('devices').getAll();get.onsuccess=()=>resolve(get.result.every(row=>row.signing.extractable===false&&(row.wrappedEncryption?row.wrappingKey.extractable===false&&!('encryption' in row):row.encryption.extractable===false)));get.onerror=reject;};request.onerror=reject;}))).toBe(true);
  await alice.locator('[data-federation-invite]').click();await expect(alice.locator('[data-federation-person]')).toContainText('Приглашение отправлено');
  await refreshPeople(bob);await bob.locator('[data-federation-accept]').click();await expect(bob.locator('[data-federation-person]')).toContainText('В друзьях');await refreshPeople(alice);await expect(alice.locator('[data-federation-person]')).toContainText('В друзьях');
  console.log('PASS: actual browser contact exchange, independent confirmation, invitation, acceptance, nonextractable device keys.');
  if(process.env.FEDERATION_DATABASES_ONLY==='1'){
    const fixture=await federationDatabaseScenario({alice,bob,api,share});await federationDatabaseSecurityScenario({alice,bob,api,share,fixture});expect(errors).toEqual([]);
  }else{
  const note=await api(alice,'/notes','POST',{title:'Общая страница двух серверов',blocks:[{id:'federation_text',type:'paragraph',content:'Начальная запись'}]});
  await alice.goto('/page/'+note.id);await expect(block(alice)).toContainText('Начальная запись');await share(alice,'editor');
  await expect.poll(async()=>(await api(alice,'/notes/'+note.id)).federation_shared).toBe(true);
  await bob.locator('[data-federation-notes]').click();await expect(bob.locator(`[data-remote-note="${note.id}"]`)).toBeVisible();await bob.locator(`[data-remote-note="${note.id}"]`).click();await expect(block(bob)).toContainText('Начальная запись');
  await block(bob).click();await bob.keyboard.press('End');await bob.keyboard.type(' REMOTE_BROWSER_EDIT',{delay:5});await expect(bob.locator('#page-save-status')).toContainText('Сохранено');await expect(block(alice)).toContainText('REMOTE_BROWSER_EDIT',{timeout:15000});
  await block(alice).click();await alice.keyboard.press('End');await alice.keyboard.type(' LOCAL_BROWSER_EDIT',{delay:5});await expect(block(bob)).toContainText('LOCAL_BROWSER_EDIT',{timeout:15000});
  await expect.poll(async()=>(await api(alice,'/notes/'+note.id)).body).toContain('REMOTE_BROWSER_EDIT');
  // Native file drop follows the production chunk upload and sealed read path.
  await block(bob).evaluate(element=>{const pixel=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZr0AAAAASUVORK5CYII='),value=>value.charCodeAt(0)),bytes=new Uint8Array(600000);bytes.set(pixel);const transfer=new DataTransfer();transfer.items.add(new File([bytes],'federation-picture.png',{type:'image/png'}));element.dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,bubbles:true,cancelable:true}));});
  await expect.poll(async()=>JSON.stringify((await api(alice,'/notes/'+note.id)).blocks),{timeout:25000}).toContain(origins.alice+'/api/notes/'+note.id+'/files/');
  await expect.poll(async()=>bob.locator('#block-editor img[src^="blob:"]').evaluateAll(images=>images.some(image=>image.complete&&image.naturalWidth===1)),{timeout:20000}).toBe(true);
  await bob.locator('#page-actions').click();await bob.locator('[data-remote-child]').click();await expect(bob.locator('#page-title')).toHaveValue('Без названия');await bob.locator('#page-title').fill('Подстраница с сервера друга');await expect(bob.locator('#page-save-status')).toContainText('Сохранено');
  await expect.poll(async()=>(await api(alice,'/notes')).some(item=>item.title==='Подстраница с сервера друга'&&item.parent_id===note.id)).toBe(true);
  await alice.screenshot({path:'artifacts/federation-owner-note.png',fullPage:true});await bob.screenshot({path:'artifacts/federation-remote-note.png',fullPage:true});
  console.log('PASS: native shared note editor, owner/remote CRDT edits, chunked attachment upload and sealed image rendering, remote child creation, retained private ownership.');

  await appSection(alice,'Люди и группы');await appSection(bob,'Люди и группы');await alice.locator('[data-federation-call]').click();await expect(bob.locator('[data-call-answer]')).toBeVisible({timeout:20000});await bob.locator('[data-call-answer]').click();
  for(const page of [alice,bob])await expect.poll(async()=>rtcStats(page),{timeout:45000}).toMatchObject({connection:'connected',local:'relay',remote:'relay'});
  for(const page of [alice,bob])await expect.poll(async()=>{const value=await rtcStats(page);return value?.received>1000&&value?.sent>1000;},{timeout:20000}).toBe(true);
  await alice.locator('[data-call-mute]').click();await expect(bob.locator('[data-peer-muted]')).toBeVisible({timeout:15000});await alice.locator('[data-call-end]').click();await expect(bob.locator('[data-call-status]')).toContainText('заверш',{ignoreCase:true,timeout:15000});
  console.log('PASS: real native incoming/answer/end UI, signed SDP and media state, bidirectional RTP using forced TURN relay.');

  await alice.goto('/page/'+note.id);await share(alice,'viewer');await bob.locator('[data-federation-notes]').click();await bob.locator(`[data-remote-note="${note.id}"]`).click();await expect(bob.locator('#page-title')).toBeDisabled();await expect(bob.locator('.tiptap')).toHaveAttribute('contenteditable','false');
  await share(alice,'none');await expect(bob.locator('[data-remote-access-lost]')).toBeVisible({timeout:15000});await expect(bob.locator('.tiptap')).toHaveAttribute('contenteditable','false');
  const legacy=await api(alice,'/notes','POST',{title:'Прежняя личная заметка',body:'LEGACY_PRIVATE_BODY\n\nСодержимое до межсерверного обновления.'});
  await alice.goto('/page/'+legacy.id);await expect(alice.getByRole('textbox',{name:'Содержимое страницы'})).toContainText('LEGACY_PRIVATE_BODY');await share(alice,'editor');
  await expect.poll(async()=>{const saved=await api(alice,'/notes/'+legacy.id);return saved.federation_shared&&saved.body.includes('LEGACY_PRIVATE_BODY')&&saved.body.includes('Содержимое до межсерверного обновления.');}).toBe(true);
  const fixture=await federationDatabaseScenario({alice,bob,api,share});await federationDatabaseSecurityScenario({alice,bob,api,share,fixture});
  expect(errors).toEqual([]);
  console.log('PASS: editor→viewer permission change, owner revocation freezes remote editor, legacy body-only private note preserved and shared; no browser exceptions.');
  }
}catch(error){for(let i=0;i<contexts.length;i++){const page=contexts[i].pages()[0];if(page){console.log('Browser failure context '+i+': '+await page.locator('.modal .error,#toast').allTextContents().catch(()=>[]));await page.screenshot({path:`artifacts/federation-failure-${i}.png`,fullPage:true}).catch(()=>{});}}throw error;
}finally{await browser.close();}
