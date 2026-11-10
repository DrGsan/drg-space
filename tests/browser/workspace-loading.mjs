import {expect,chromium,webkit} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

const origin='http://127.0.0.1:8058',folder='artifacts/workspace-loading',baseline=process.env.DRG_LOADING_BASELINE==='1',capture=process.env.DRG_CAPTURE==='1';
const directory=await mkdtemp(tmpdir()+'/drg-workspace-loading-');
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8058','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'workspace-loading-isolated-test-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
 for(let n=0;n<100;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}
 const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
 browser=await engine.launch({headless:true,...(process.env.DRG_BROWSER_CHANNEL?{channel:process.env.DRG_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:1440,height:900},...(capture?{recordVideo:{dir:folder+'/raw',size:{width:1440,height:900}}}:{})});let csrf='';
 const api=async(path,method='GET',data)=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Workspace-loading-test-password-123';csrf=(await api('/setup','POST',{login:'loadingowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await mkdir(folder,{recursive:true});const errors=[],checks=[];
 const cases=baseline?[['classic','light',390,'ru']]:[['classic','light',1440,'ru'],['classic','light',390,'ru'],['classic','dark',320,'en'],['glass','light',390,'ru'],['glass','dark',1440,'en']];
 for(const [style,mode,width,lang] of cases){
  await api('/language','PUT',{language:lang});const me=await api('/me');await api('/appearance','PUT',{...me.settings.appearance,style,mode,seasonal:'off'});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.setViewportSize({width,height:900});
  await page.addInitScript(({lang,standalone})=>{localStorage.setItem('drg-language',lang);Object.defineProperty(navigator,'standalone',{get:()=>standalone});},{lang,standalone:width<500});
  let release;await page.route('**/api/modules',route=>new Promise(resolve=>{release=()=>route.continue().catch(()=>{}).finally(resolve);}));
  await page.goto('/');const loading=page.locator('.workspace-loading');await expect(loading).toBeVisible();
  if(!baseline){
   await expect(loading).toHaveAttribute('aria-busy','true');await expect(loading.getByRole('status')).toContainText(lang==='en'?'Loading workspace':'Загружаем пространство');
   const beam=loading.locator('.workspace-loading-beam i');const start=await beam.evaluate(el=>getComputedStyle(el).transform);await page.waitForTimeout(220);const end=await beam.evaluate(el=>getComputedStyle(el).transform);expect(start).not.toBe(end);
   expect(await loading.evaluate(el=>{const r=el.getBoundingClientRect();return r.width<=innerWidth&&document.documentElement.scrollWidth<=innerWidth;})).toBe(true);
   expect(await loading.locator('img').evaluateAll(images=>images.every(img=>img.complete&&img.naturalWidth>0))).toBe(true);
   if(style==='glass')await expect(page.locator('#app-wallpaper')).toHaveCSS('display','block');
   await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.reviewHidden});window.reviewHidden=true;document.dispatchEvent(new Event('visibilitychange'));});
   expect(await beam.evaluate(el=>getComputedStyle(el).animationPlayState)).toBe('paused');
   await page.evaluate(()=>{window.reviewHidden=false;document.dispatchEvent(new Event('visibilitychange'));});
   expect(await beam.evaluate(el=>getComputedStyle(el).animationPlayState)).toBe('running');
   await page.emulateMedia({reducedMotion:'reduce'});expect(await beam.evaluate(el=>getComputedStyle(el).animationName)).toBe('none');await expect(loading.getByRole('status')).toBeVisible();await page.emulateMedia({reducedMotion:'no-preference'});
  }
  if(!baseline)await expect(loading.locator('.workspace-loading-center')).toHaveCSS('opacity','1');
  await page.screenshot({path:`${folder}/${baseline?'before':'after'}-${engine.name()}-${style}-${mode}-${width}-${lang}.png`});checks.push({style,mode,width,lang});
  const saveVideo=capture&&style==='classic'&&mode==='light'&&width===390;
  if(saveVideo)await page.waitForTimeout(3400);
  release();await expect(page.locator('.workspace')).toBeVisible();await expect(loading).toHaveCount(0);await page.close();
  if(saveVideo)await page.video().saveAs(`${folder}/loading-${engine.name()}.webm`);
 }
 if(!baseline){
  // A real request deadline must end the animation and offer a working retry.
  await api('/language','PUT',{language:'ru'});const page=await context.newPage();await page.addInitScript(()=>localStorage.setItem('drg-language','ru'));let release;
  await page.route('**/api/modules',route=>new Promise(resolve=>{release=()=>route.abort().catch(()=>{}).finally(resolve);}));
  await page.goto('/');await expect(page.locator('.workspace-loading')).toBeVisible();await expect(page.locator('.workspace-error')).toBeVisible({timeout:20000});await expect(page.locator('.workspace-loading')).toHaveCount(0);release();await page.unroute('**/api/modules');await page.locator('[data-workspace-retry]').click();await expect(page.locator('.workspace')).toBeVisible();
  // Offline and HTTP failures leave no perpetual loading animation.
  await page.route('**/api/modules',route=>route.abort('internetdisconnected'));await page.reload();await expect(page.locator('.workspace-error')).toBeVisible();await expect(page.locator('.workspace-loading')).toHaveCount(0);await page.unroute('**/api/modules');
  await page.route('**/api/modules',route=>route.fulfill({status:503,json:{detail:'Temporarily unavailable'}}));await page.reload();await expect(page.locator('.workspace-error')).toBeVisible();await page.unroute('**/api/modules');await page.locator('[data-workspace-retry]').click();await expect(page.locator('.workspace')).toBeVisible();
  // Fast success does not wait for the decorative sweep to complete.
  await page.addInitScript(()=>{new MutationObserver(()=>{if(document.querySelector('.workspace-loading')&&!window.loadingStarted)window.loadingStarted=performance.now();if(document.querySelector('.workspace')&&!window.workspaceReady)window.workspaceReady=performance.now();}).observe(document,{subtree:true,childList:true});});
  await page.reload();await expect(page.locator('.workspace')).toBeVisible();const timing=await page.evaluate(()=>({started:window.loadingStarted,ready:window.workspaceReady}));expect(timing.started).toBeGreaterThan(0);expect(timing.ready-timing.started).toBeLessThan(2500);checks.push({fastLoadingMs:timing.ready-timing.started});await page.close();
 }
 expect(errors).toEqual([]);await writeFile(`${folder}/${baseline?'before':'after'}-${engine.name()}.json`,JSON.stringify({checks,errors},null,2));console.log('Workspace loading checked in '+engine.name());
}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});}
