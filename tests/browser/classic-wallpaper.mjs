// Real API in an isolated encrypted database; never touches a working account.
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const baseline=process.env.DRG_CLASSIC_BASELINE==='1',phase=baseline?'before':'after';
const out='artifacts/classic-wallpaper/'+engine;await mkdir(out,{recursive:true});
const origin='http://127.0.0.1:8058',directory=await mkdtemp(tmpdir()+'/drg-classic-wallpaper-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8058','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-classic-wallpaper-secret-'.repeat(3),SETUP_TOKEN:'classic-wallpaper-setup',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));
const exited=new Promise(resolve=>server.once('exit',resolve));
try{
 let ready=false;
 for(let attempt=0;attempt<150;attempt++){
  try{ready=(await fetch(origin+'/api/health')).ok;}catch{}
  if(ready)break;if(server.exitCode!==null)throw Error(output);
  await new Promise(resolve=>setTimeout(resolve,200));
 }
 if(!ready)throw Error(output);
 browser=await ({chromium,webkit}[engine]).launch({headless:true});
 const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1100},colorScheme:'light',timezoneId:'Europe/Moscow'});
 const page=await context.newPage();let csrf='',language='ru';const errors=[];
 page.on('pageerror',error=>{if(engine==='webkit'&&error.name==='Fetch API cannot load http'&&error.message.endsWith('due to access control checks.'))return;errors.push(error.message);});
 await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
 const send=async(path,body,method='POST')=>{
  const response=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});
  expect(response.ok(),path+' '+response.status()+' '+await response.text()).toBe(true);return response.json();
 };
 csrf=(await send('/setup',{login:'alexander',password:'Classic-wallpaper-test-123',token:'classic-wallpaper-setup'})).csrf;
 const {secret}=await send('/2fa/enroll',{password:'Classic-wallpaper-test-123'});
 const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join('');
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});
 csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await send('/appearance',{style:'classic',wallpaper:'mountains',mode:'light',seasonal:'off'},'PUT');await page.goto('/');
 async function open(){
  await page.locator('.workspace').waitFor();
  await page.locator('[data-view="settings"]:visible,[data-mobile-view="settings"]:visible').first().click();
  const row=page.locator('[data-preference-key="appearance"]');if(await row.getAttribute('open')===null)await row.locator(':scope>summary').click();
  for(const other of await page.locator('.preference-row:not([data-preference-key="appearance"])').all())if(await other.getAttribute('open')!==null)await other.locator(':scope>summary').click();
  await page.locator('[data-appearance]').waitFor();
 }
 const style=value=>page.locator('[name=style][value="'+value+'"]').check();
 const save=async()=>{await page.locator('[data-appearance] [type=submit]').click();await expect(page.locator('[data-appearance-status]')).toHaveText(language==='ru'?'Оформление сохранено':'Layout saved');};
 const stored=async()=>(await send('/appearance',undefined,'GET')).preferences;
 await open();
 if(!baseline){await expect(page.locator('[data-wallpapers]')).toBeHidden();await expect(page.locator('[data-wallpaper-hint]')).toBeVisible();}
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:1100});await page.evaluate(standalone=>document.documentElement.classList.toggle('app-standalone',standalone),width===390);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await open();
  await page.locator('[data-preference-key="appearance"]').screenshot({path:out+'/'+phase+'-'+width+'.png'});
 }
 if(!baseline){
  await page.setViewportSize({width:390,height:844});
  // Saving an opaque style must keep both presets and privately uploaded media.
  const image=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=32;c.height=32;c.getContext('2d').fillRect(0,0,32,32);return c.toDataURL('image/png').split(',')[1];});
  const upload=await page.request.post('/api/media',{headers:{Origin:origin,'X-CSRF-Token':csrf,'Content-Type':'image/png'},data:Buffer.from(image,'base64')});expect(upload.ok()).toBe(true);const custom=(await upload.json()).id;
  for(const wallpaper of ['mountains',custom]){
   await send('/appearance',{style:'glass',wallpaper},'PUT');await page.reload();await open();
   await expect(page.locator('[data-wallpapers]')).toBeVisible();await expect(page.locator('[name=wallpaper]')).toHaveValue(wallpaper);
   await style('classic');await expect(page.locator('[data-wallpapers]')).toBeHidden();
   expect(await page.locator('.appearance-preview-scene').evaluate(el=>getComputedStyle(el).backgroundImage)).toBe('none');
   await save();expect((await stored()).wallpaper).toBe(wallpaper);
   await page.reload();await open();await expect(page.locator('[data-wallpapers]')).toBeHidden();await expect(page.locator('[name=wallpaper]')).toHaveValue(wallpaper);
   await style('glass');await expect(page.locator('[data-wallpapers]')).toBeVisible();await expect(page.locator('[data-wallpaper-hint]')).toBeHidden();
   if(wallpaper===custom)await expect(page.locator('[data-custom-wallpaper]')).toBeVisible();
   await save();expect((await stored()).wallpaper).toBe(wallpaper);
   await style('classic');await page.locator('[data-appearance-reset]').click();await expect(page.locator('[data-wallpapers]')).toBeVisible();
  }
  // A prepared upload stays in the draft while the picker is hidden.
  await page.locator('[data-wallpaper-file]').setInputFiles({name:'new-wallpaper.png',mimeType:'image/png',buffer:Buffer.from(image,'base64')});
  await expect(page.locator('[data-wallpaper-status]')).toHaveText('Изображение готово. Нажмите «Сохранить оформление».');
  await style('classic');await style('glass');await expect(page.locator('[data-custom-wallpaper]')).toBeVisible();
  await page.locator('[data-appearance-reset]').click();expect((await stored()).wallpaper).toBe(custom);
  for(language of ['ru','en']){
   await send('/language',{language},'PUT');await page.reload();await open();
   for(const mode of ['light','dark']){
    await page.locator('[name=mode]').selectOption(mode);await style('classic');
    await expect(page.locator('[data-wallpaper-hint]')).toHaveText(language==='ru'?'Обои доступны в стиле «Стекло». Выбранный фон сохранён.':'Wallpapers are available in Glass style. Your selected background is kept.');
    for(const width of [1440,390,320]){
     await page.setViewportSize({width,height:844});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await open();await style('classic');await page.locator('[name=mode]').selectOption(mode);await page.locator('[data-wallpaper-hint]').scrollIntoViewIfNeeded();
     expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
     await expect(page.locator('[data-wallpapers]')).toBeHidden();
    }
   }
  }
 }
 expect(errors).toEqual([]);console.log('Classic wallpaper '+phase+' '+engine+' passed');
}catch(error){if(browser){for(const context of browser.contexts())for(const page of context.pages()){await page.screenshot({path:out+'/failure.png'});console.error((await page.locator('body').innerText()).slice(0,3000));}}throw error;}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
