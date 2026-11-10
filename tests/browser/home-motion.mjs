// Motion-only regression: real Home, real encrypted disposable API, unchanged UI.
import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {taskWidgetFlow} from './task-widget-flow.js';
const baseline=process.env.DRG_HOME_BASELINE==='1',root=process.env.DRG_HOME_ROOT||process.cwd(),phase=baseline?'before':'after';
const origin='http://127.0.0.1:8078',out=process.cwd()+'/artifacts/drg-67-animation',dir=await mkdtemp(tmpdir()+'/drg67-motion-');
const server=spawn(process.cwd()+'/.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8078','tests.browser_app:app'],{cwd:root,env:{...process.env,DATA_DIR:dir,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'drg67-isolated-animation-'.repeat(4),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FAMILY_PHOTO_URL:''},stdio:['ignore','ignore','pipe']});
const exited=new Promise(resolve=>server.once('exit',resolve));let logs='',browser;server.stderr.on('data',d=>logs=(logs+d).slice(-3000));const results=[];
try{
 for(let i=0;i<150;i++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 await mkdir(out,{recursive:true});
 for(const engine of [chromium,webkit]){
  browser=await engine.launch();const setup=await browser.newContext({baseURL:origin});let csrf='';
  const api=async(path,data,method='POST')=>{const r=await setup.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});if(!r.ok())throw Error(path+' '+await r.text());return r.json();};
  if(engine===chromium){
   const password='Drg67-local-animation-test-123';csrf=(await api('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
   const {secret}=await api('/2fa/enroll',{password}),bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
   const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
   await api('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
   globalThis.homeSession=await setup.storageState();
   await api('/appearance',{theme:'ocean',style:'glass',mode:'light',wallpaper:'mountains',seasonal:'off'},'PUT');
   for(const title of ['Забрать посылку','Позвонить родителям','Подготовить документы'])await api('/tasks',{title,day:'2026-11-01'});
   for(const title of ['Идеи для дома','Планы на выходные','Полезные ссылки'])await api('/notes',{title,body:''});
   const layout=await api('/dashboard/layout',undefined,'GET');await api('/dashboard/layout',{version:layout.version,instances:[['tasks','2x1',{}],['clock','1x1',{timezone:'Europe/Moscow'}],['calendar','1x1',{}],['notes','2x1',{}]].map(([module_id,size,config])=>({id:'drg67_'+module_id,module_id,size,config}))},'PUT');
  }
  await setup.close();
  for(const [width,lang] of [[1280,'ru'],[736,'ru'],[390,'ru'],[320,'en']]){
   const record=engine===chromium&&[1280,390].includes(width),mobile=width<500;
   const context=await browser.newContext({baseURL:origin,storageState:homeSession,viewport:{width,height:850},locale:lang==='ru'?'ru-RU':'en-US',...(record?{recordVideo:{dir:out+'/raw',size:{width,height:850}}}:{})});
   const page=await context.newPage(),errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push(new URL(r.url()).pathname);});
   await page.addInitScript(({mobile,lang})=>{
    localStorage.setItem('drg-language',lang);Object.defineProperty(navigator,'standalone',{configurable:true,value:mobile});
    window.homeFrames=[];const observer=new MutationObserver(()=>{const widget=document.querySelector('[data-widget=clock]'),body=widget?.querySelector('[data-widget-body]');if(!body||window.homeFrames.length)return;observer.disconnect();const sample=()=>{const css=getComputedStyle(body);window.homeFrames.push({x:widget.offsetLeft,y:widget.offsetTop,width:widget.offsetWidth,height:widget.offsetHeight,opacity:Number(css.opacity),transform:css.transform});if(window.homeFrames.length<80)requestAnimationFrame(sample);};sample();});observer.observe(document,{childList:true,subtree:true});
   },{mobile,lang});
   // Keep account language and client catalog aligned (same existing API).
   const languageResponse=await context.request.put('/api/language',{data:{language:lang},headers:{Origin:origin,'X-CSRF-Token':csrf||(await context.cookies()).find(c=>c.name==='drg_csrf')?.value||''}});expect(languageResponse.ok()).toBe(true);
   await page.goto('/');if(mobile){await page.locator('[data-mobile-view=settings]').click();await page.locator('[data-mobile-open=home]').click();}
   await expect(page.locator('#widget-grid>.widget-ready')).toHaveCount(4);await page.waitForFunction(()=>window.homeFrames.length===80);await page.waitForTimeout(350);
   const frames=await page.evaluate(()=>window.homeFrames),first=frames[0];expect(frames.every(f=>['x','y','width','height'].every(k=>f[k]===first[k]))).toBe(true);
   if(!baseline)expect(frames.some(f=>f.opacity>0.02&&f.opacity<0.98&&f.transform!=='none')).toBe(true);
   expect(await page.locator('[data-widget=clock] [data-widget-body]').evaluate(el=>getComputedStyle(el).transform)).toBe('none');
   expect(await page.locator('body').evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   const geometry=await page.locator('#widget-grid>[data-widget]').evaluateAll(nodes=>nodes.map(el=>({module:el.dataset.widget,x:el.offsetLeft,y:el.offsetTop,width:el.offsetWidth,height:el.offsetHeight,buttons:[...el.querySelectorAll('button')].map(b=>b.getAttribute('aria-label')||b.textContent.trim())})));
   await page.screenshot({path:`${out}/${phase}-${engine.name()}-${width}-home.png`});
   await page.waitForLoadState('networkidle');
   const dataRequests=()=>requests.filter(p=>['/api/tasks','/api/task-lists','/api/dashboard/layout','/api/notes'].includes(p));
   const requestCount=dataRequests().length;await page.locator('[data-next-month]').click();await page.waitForTimeout(450);await page.locator('[data-prev-month]').click();await page.waitForTimeout(450);expect(dataRequests().length).toBe(requestCount);
   await page.locator('#customize-dashboard').focus();
   const modalFrames=await page.evaluate(async()=>{document.querySelector('#customize-dashboard').click();const samples=[];for(let i=0;i<35;i++){const panel=document.querySelector('.modal');if(panel){const c=getComputedStyle(panel);samples.push({opacity:Number(c.opacity),transform:c.transform});}await new Promise(requestAnimationFrame);}return samples;});
   await expect(page.locator('[data-widget-type]')).toBeVisible();
   if(!baseline)expect(modalFrames.some(f=>f.opacity>0.01&&f.opacity<0.99&&f.transform!=='none')).toBe(true);
   await page.screenshot({path:`${out}/${phase}-${engine.name()}-${width}-settings.png`});
   await page.keyboard.press('Escape');await expect(page.locator('.modal-overlay')).toHaveCount(0);await expect(page.locator('#customize-dashboard')).toBeFocused();
   // Interrupt entrance, cancel a close, then open a nested existing settings form.
   await page.evaluate(()=>document.querySelector('#customize-dashboard').click());await page.waitForTimeout(40);await page.keyboard.press('Escape');await expect(page.locator('.modal-overlay')).toHaveCount(0);
   await page.locator('#customize-dashboard').click();await page.locator('[data-configure="1"]').focus();await page.locator('[data-configure="1"]').press('Enter');await expect(page.locator('.modal-overlay')).toHaveCount(2);
   await page.evaluate(()=>document.querySelectorAll('.modal-overlay')[1].addEventListener('beforeclose',e=>e.preventDefault(),{once:true}));await page.keyboard.press('Escape');await expect(page.locator('.modal-overlay')).toHaveCount(2);await page.keyboard.press('Escape');await expect(page.locator('.modal-overlay')).toHaveCount(1);await expect(page.locator('[data-configure="1"]')).toBeFocused();await page.keyboard.press('Escape');await expect(page.locator('.modal-overlay')).toHaveCount(0);
   await page.emulateMedia({reducedMotion:'reduce'});await page.locator('#customize-dashboard').click();expect(await page.locator('.modal').evaluate(el=>el.getAnimations().length)).toBe(0);await page.keyboard.press('Escape');await expect(page.locator('.modal-overlay')).toHaveCount(0);
   await page.emulateMedia({reducedMotion:'no-preference'});
   // A reminder still navigates to the original section; no new Home editor.
   await page.locator('[data-task-open]').click();await expect(page.locator('.tasks-shell')).toBeVisible();await expect(page.locator('.home-reminder-overlay')).toHaveCount(0);
   expect(errors).toEqual([]);results.push({engine:engine.name(),width,lang,geometry,intermediateContentFrames:frames.filter(f=>f.opacity>0&&f.opacity<1).length,intermediateModalFrames:modalFrames.filter(f=>f.opacity>0&&f.opacity<1).length,errors});
   await context.close();if(record)await page.video().saveAs(`${out}/${phase}-${width}.webm`);
  }
  if(!baseline&&process.env.DRG_HOME_REGRESSION==='1'){const context=await browser.newContext({baseURL:origin,storageState:homeSession,viewport:{width:1280,height:850},locale:'ru-RU'}),page=await context.newPage();await page.goto('/');await page.evaluate(async()=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('drg_csrf='))?.slice(9)||'');await fetch('/api/language',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({language:'ru'})});localStorage.setItem('drg-language','ru');});await page.reload();await taskWidgetFlow(page);await context.close();}
  await browser.close();browser=null;
 }
 await writeFile(`${out}/${phase}-report.json`,JSON.stringify(results,null,2));console.log(`${phase}: Chromium/WebKit, four widths, fixed geometry, existing forms/navigation, nested dialogs, interrupted close, reduced motion and no calendar requests passed.`);
}finally{await browser?.close();server.kill('SIGTERM');await Promise.race([exited,new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null){server.kill('SIGKILL');await exited;}await rm(dir,{recursive:true,force:true});}
