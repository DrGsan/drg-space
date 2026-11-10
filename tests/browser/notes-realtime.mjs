import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page){
 const errors=[];page.on('pageerror',e=>{errors.push(e.message);console.log(e.message);});const password='Realtime-test-password-123';
 function apiFor(context){return async(path,method='GET',data,csrf='')=>{const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(response.ok(),path+' '+await response.text()).toBe(true);return response.json();};}
 const api=apiFor(page.context());
 async function activate(api,csrf){const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);return (await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;}
 let csrf=(await api('/setup','POST',{login:'realtimeowner',password,token:'browser-test-setup-key'})).csrf;csrf=await activate(api,csrf);await api('/admin/access/unlock','POST',{password},csrf);
 const invitation=await api('/invites','POST',{login:'realtimefriend'},csrf),peerContext=await browser.newContext({locale:'ru-RU',baseURL:origin}),peerApi=apiFor(peerContext),peer=await peerContext.newPage();peer.on('pageerror',e=>errors.push(e.message));
 const registered=await peerApi('/register','POST',{login:'realtimefriend',password,invite:invitation.url.split('/').at(-1)}),peerCsrf=await activate(peerApi,registered.csrf);
 const request=await api('/social/friends','POST',{login:'realtimefriend'},csrf),space=await peerApi('/social/requests/'+request.id+'/accept','POST',undefined,peerCsrf);
 const blocks=[{id:'live_text',type:'paragraph',content:'Начало: '},{id:'empty_toggle',type:'heading',props:{level:2,isToggleable:true},content:'Пустой раздел',children:[]},{id:'move_text',type:'paragraph',content:'Переносимый блок'},...Array.from({length:80},(_,i)=>({id:'large_'+i,type:'paragraph',content:'Длинная заметка '+i+' '+('совместный текст '.repeat(30))}))];
 const note=await api('/notes','POST',{title:'Совместный набор',space_id:space.space_id,blocks},csrf);
 const deltas=[];page.on('websocket',socket=>socket.on('framesent',frame=>{try{const payload=JSON.parse(frame.payload);if(payload.type==='update')deltas.push(payload.update.length);}catch{}}));
 await page.goto('/page/'+note.id);await peer.goto('/page/'+note.id);
 for(const p of [page,peer])await expect(p.locator('#page-save-status')).toContainText('Сохранено');
 const target=p=>p.locator('[data-id=live_text]>.bn-block>.bn-block-content .bn-inline-content');
 const phrase='три четыре быстрых слова';await target(page).click();await page.keyboard.press('End');const started=Date.now();await page.keyboard.type(phrase,{delay:10});await expect(target(peer)).toContainText(phrase,{timeout:2500});const elapsed=Date.now()-started;expect(elapsed).toBeLessThan(2500);
 await target(peer).click();await peer.keyboard.press('End');await peer.keyboard.type(' ответ собеседника',{delay:5});await expect(target(page)).toContainText('ответ собеседника',{timeout:2500});
 // Drag an actual block handle onto the native empty-toggle placeholder.
 const toggle=page.locator('[data-id=empty_toggle]');await toggle.locator('.bn-toggle-button').click();
 const placeholder=toggle.locator('.bn-toggle-add-block-button');await expect(placeholder).toBeVisible();
 await page.locator('[data-id=move_text] .bn-inline-content').hover();const handle=page.locator('.bn-side-menu [draggable=true]');await expect(handle).toBeVisible();
 const transfer=await page.evaluateHandle(()=>new DataTransfer());await handle.dispatchEvent('dragstart',{dataTransfer:transfer});await placeholder.dispatchEvent('dragover',{dataTransfer:transfer});await expect(page.locator('.drg-toggle-drop-target')).toBeVisible();await placeholder.dispatchEvent('drop',{dataTransfer:transfer});await handle.dispatchEvent('dragend',{dataTransfer:transfer});
 await expect.poll(async()=>{const saved=await api('/notes/'+note.id);return saved.blocks.find(b=>b.id==='empty_toggle').children.map(b=>b.id);}).toEqual(['move_text']);
 expect((await api('/notes/'+note.id)).blocks.some(b=>b.id==='move_text')).toBe(false);
 await expect(peer.locator('.bn-block-outer[data-id=empty_toggle] .bn-block-outer[data-id=move_text]')).toHaveCount(1);
 await page.keyboard.press('ControlOrMeta+z');
 await expect.poll(async()=>{const saved=await api('/notes/'+note.id);return saved.blocks.find(b=>b.id==='empty_toggle').children.length;}).toBe(0);
 await expect(page.locator('.bn-block-group>.bn-block-outer[data-id=move_text]')).toBeVisible();
 // Hidden style is shared, while disclosure remains private to each viewer.
 await target(page).evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));});
 await page.locator('[data-format-spoiler]').click();
 const ownSpoiler=target(page).locator('.drg-spoiler'),peerSpoiler=target(peer).locator('.drg-spoiler');
 await expect(peerSpoiler).toHaveAttribute('role','button');await page.evaluate(()=>getSelection().removeAllRanges());await ownSpoiler.click();await expect(ownSpoiler).toHaveClass(/is-revealed/);await expect(peerSpoiler).not.toHaveClass(/is-revealed/);
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks.find(b=>b.id==='live_text').content)).toContain('"spoiler":true');
 await peer.reload();await expect(peerSpoiler).toHaveAttribute('role','button');
 // Formula atoms survive the collaborative server projection and reopening.
 await target(page).evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));});
 await page.locator('[data-format-equation]').click();await page.getByLabel('Запись LaTeX').fill(String.raw`\frac{a}{b}`);await page.getByRole('button',{name:'Готово',exact:true}).click();await expect(peer.locator('.drg-inline-equation .katex')).toBeVisible();
 await expect.poll(async()=>JSON.stringify((await api('/notes/'+note.id)).blocks)).toContain('"type":"equation"');
 await page.reload();await expect(page.locator('.drg-inline-equation .katex')).toBeVisible();
 expect(deltas.length).toBeGreaterThan(0);expect(Math.max(...deltas)).toBeLessThan(10000);expect(errors).toEqual([]);
 console.log(JSON.stringify({threeWorkers:true,fourWordsMilliseconds:elapsed,deltas:deltas.length,largestDeltaBase64Bytes:Math.max(...deltas)}));await peerContext.close();
}
const origin='http://127.0.0.1:8038',directory=await mkdtemp(tmpdir()+'/drg-notes-realtime-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8038','tests.browser_app:app'],{env:{...process.env,GUNICORN_WORKERS:'3',GUNICORN_THREADS:'16',DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Notes realtime and empty toggle passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
