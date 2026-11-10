import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {createServer} from 'node:net';

const before=process.env.DRG_BASELINE==='1',phase=before?'before':'after',folder='artifacts/drg-70-71-75';
await mkdir(folder,{recursive:true});
const probe=createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
const directory=await mkdtemp(tmpdir()+'/drg-webapp-notion-'),origin='http://127.0.0.1:'+port;
const env={...process.env,DATA_DIR:directory,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'webapp-notion-isolated-check-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,NOTION_SYNC_ENABLED:'false',FAMILY_PHOTO_URL:'',CALL_STUN_URLS:'',CALL_TURN_URLS:''};
const server=spawn('.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:'+port,'tests.browser_app:app'],{env,stdio:['ignore','ignore','pipe']});
let logs='',browser,lock;server.stderr.on('data',x=>logs=(logs+x).slice(-4000));
const totp=secret=>{const bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const h=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),o=h[19]&15;return String((h.readUInt32BE(o)&0x7fffffff)%1000000).padStart(6,'0');};
async function block(){
 lock=spawn('.venv/bin/python',['-u','-c',"import os;from pathlib import Path;from app.storage import StorageGate\nwith StorageGate(Path(os.environ['DATA_DIR'])).exclusive_sync():\n print('locked',flush=True)\n input()"],{env,stdio:['pipe','pipe','pipe']});
 await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Storage lock timed out')),15000);lock.stdout.on('data',x=>{output+=x;if(output.includes('locked')){clearTimeout(timer);resolve();}});lock.once('exit',code=>{clearTimeout(timer);reject(Error('Lock process exited '+code));});});
}
async function unblock(){if(!lock)return;const p=lock;lock=null;p.stdin.end('\n');await new Promise(r=>p.exitCode!==null?r():p.once('exit',r));}
try{
 for(let n=0;n<120;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;browser=await engine.launch();
 const context=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:390,height:844},reducedMotion:'reduce'});let csrf='';
 const api=async(path,method='GET',data)=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Webapp-notion-check-password-123';csrf=(await api('/setup','POST',{login:'reviewowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 const owner=(await api('/me')).id,errors=[],checks=[],note=await api('/notes','POST',{title:'Maintenance note',body:'Preserved content'});
 for(const [width,mode,lang,scale] of [[390,'dark','ru',1],[320,'light','ru',1],[390,'dark','en',1.5],[1440,'light','en',1]]){
  await api('/language','PUT',{language:lang});await api('/appearance','PUT',{theme:'ocean',style:'glass',mode,wallpaper:'mountains',seasonal:'off'});
  const page=await context.newPage();await page.setViewportSize({width,height:844});await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:true}));page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');await page.getByRole('button',{name:lang==='ru'?'Настройки':'Settings',exact:true}).first().click();
  await page.getByText(lang==='ru'?'Личные данные':'Personal data',{exact:true}).first().click();await page.getByRole('button',{name:lang==='ru'?'Экспорт и импорт':'Export and import',exact:true}).click();
  const section=page.locator('.personal-transfer').filter({has:page.getByRole('heading',{name:lang==='ru'?'Переезд из Notion':'Migrating from Notion',exact:true})});
  if(scale!==1)await page.addStyleTag({content:`html{font-size:${scale*100}%}`});
  await section.scrollIntoViewIfNeeded();await section.screenshot({path:`${folder}/71-${phase}-${engine.name()}-${width}-${lang}.png`});
  const boxes=await section.locator('button').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}}));
  if(!before){expect(boxes).toHaveLength(2);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);if(width<=600){expect(Math.abs(boxes[0].x-boxes[1].x)).toBeLessThan(1);expect(boxes[1].y-boxes[0].y-boxes[0].height).toBeGreaterThanOrEqual(11);expect(Math.abs(boxes[0].width-boxes[1].width)).toBeLessThan(1);}for(const b of boxes){expect(b.height).toBeGreaterThanOrEqual(44);expect(b.x+b.width).toBeLessThanOrEqual(width+1);}}
  checks.push({width,mode,lang,scale,boxes});await page.close();
 }
 // Hold only this disposable store, using the same cross-process lock as restore.
 for(const [width,lang,standalone] of [[390,'ru',true],[320,'en',true],[1440,'ru',false]]){
  await api('/language','PUT',{language:lang});await context.addCookies([{name:'drg_language',value:lang,url:origin}]);await block();const page=await context.newPage();await page.setViewportSize({width,height:844});await page.addInitScript(({standalone,width})=>{Object.defineProperty(navigator,'standalone',{get:()=>standalone});localStorage.setItem('drg-appearance',JSON.stringify({mode:width===390?'dark':'light'}));},{standalone,width});
  const address=origin+(width===390?'/?source=maintenance-check#notes':'/page/'+note.id+'#retained-fragment');const r=await page.goto(address);expect(r.status()).toBe(503);expect(r.headers()['cache-control']).toBe('no-store');
  await page.screenshot({path:`${folder}/70-${phase}-${engine.name()}-${width}-${lang}.png`});
  const raw=await context.request.get('/api/me',{headers:{Accept:'text/html'}});expect(raw.status()).toBe(503);expect((await raw.json()).code).toBe('operationWithDataIsInProgressRetryLater');
  if(!before){
   await expect(page.getByRole('heading')).toContainText(lang==='ru'?'Выполняется операция с данными':'A data operation is in progress');expect(page.url()).toBe(address);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   const retry=page.getByRole('button',{name:lang==='ru'?'Повторить загрузку':'Try loading again',exact:true});await retry.click();await expect(page.getByRole('heading')).toBeVisible();expect(page.url()).toBe(address);
   await unblock();const navigation=page.waitForRequest(r=>r.isNavigationRequest());await retry.click();const original=new URL(address);original.hash='';expect((await navigation).url()).toBe(original.href);await expect(page.locator('.workspace')).toBeVisible({timeout:15000});if(width!==390)await expect.poll(()=>page.url()).toBe(address);expect((await api('/me')).id).toBe(owner);
  }else await unblock();
  await page.close();
 }
 expect(errors).toEqual([]);await writeFile(`${folder}/${phase}-${engine.name()}.json`,JSON.stringify({checks,errors},null,2));console.log(`${phase}: mobile transfer controls and real storage-lock recovery checked in ${engine.name()}`);
}finally{
 await unblock();await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});
}
