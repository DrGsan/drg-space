// Bounded observation on real local transport and private encrypted PostgreSQL.
// Report timings are local, not production latency or a 500-user load test.
import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,readFile,writeFile,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {appSection} from './notes-ui.js';
const origin='http://127.0.0.1:8079',out='artifacts/drg-69-68',directory=await mkdtemp(tmpdir()+'/drg69-browser-');
const pg=(await readFile(out+'/postgres-path.txt','utf8')).trim();
const databaseURL='postgresql:///postgres?host='+pg+'/socket&port=54869',schema='drg_audit_browser_'+Date.now();
const server=spawn('.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8079','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:databaseURL,DATABASE_SCHEMA:schema,FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-performance-observation-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FAMILY_PHOTO_URL:'',GUNICORN_WORKERS:'2',GUNICORN_THREADS:'32'},stdio:['ignore','ignore','pipe']});
const exited=new Promise(resolve=>server.once('exit',resolve));let logs='',browser,session,csrf='';server.stderr.on('data',x=>logs=(logs+x).slice(-4000));const report=process.env.DRG_AUDIT_ENGINE?JSON.parse(await readFile(out+'/browser-observation.json','utf8').catch(()=> '[]')):[];
const totp=secret=>{const bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');};
try{
 for(let n=0;n<200;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 await mkdir(out,{recursive:true});
 for(const [engine,width,mobile] of [[chromium,1280,false],[webkit,390,true]].filter(([engine])=>!process.env.DRG_AUDIT_ENGINE||engine.name()===process.env.DRG_AUDIT_ENGINE)){
  browser=await engine.launch();const context=await browser.newContext({baseURL:origin,viewport:{width,height:850},locale:'ru-RU',...(session?{storageState:session}:{})});
  const api=async(path,body,method='POST')=>{const r=await context.request.fetch('/api'+path,{method,data:body,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
  if(!session){
   const password='Local-performance-review-123';csrf=(await api('/setup',{login:'auditowner',password,token:'browser-test-setup-key'})).csrf;
   const {secret}=await api('/2fa/enroll',{password});await api('/2fa/confirm',{code:totp(secret)});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
   await api('/modules',{enabled:{tracker:true}},'PUT');
   for(let n=0;n<10;n++){await api('/notes',{title:'Audit note '+n,body:'Sample content'});await api('/tasks',{title:'Audit reminder '+n});}
   const project=await api('/tracker/projects',{name:'Audit project',key:'AUD'});
   for(let n=0;n<10;n++)await api('/tracker/projects/'+project.id+'/issues',{title:'Audit issue '+n});
   const layout=await api('/dashboard/layout',undefined,'GET');await api('/dashboard/layout',{version:layout.version,instances:[['tasks','2x1',{}],['clock','1x1',{}],['calendar','1x1',{}],['notes','2x1',{}]].map(([module_id,size,config])=>({id:'audit_'+module_id,module_id,size,config}))},'PUT');
   await api('/appearance',{theme:'ocean',style:'glass',mode:'light',wallpaper:'mountains',seasonal:'off'},'PUT');session=await context.storageState();
  }
  const page=await context.newPage(),requests=[],errors=[];let phase='startup';page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{const u=new URL(r.url());if(u.pathname.startsWith('/api/'))requests.push({phase,method:r.method(),path:u.pathname.replace(/\/[a-zA-Z0-9_-]{16,}(?=\/|$)/g,'/[id]'),started:Date.now()});});
  await page.addInitScript(mobile=>{Object.defineProperty(navigator,'standalone',{value:mobile,configurable:true});window.auditHidden=false;Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.auditHidden});window.longTasks=[];try{new PerformanceObserver(list=>window.longTasks.push(...list.getEntries().map(x=>({start:x.startTime,duration:x.duration})))).observe({type:'longtask',buffered:true});}catch{}},mobile);
  const go=async(view,label)=>{if(mobile){const back=page.locator('#files-main-menu:visible,.mobile-secondary-back:visible');if(await back.count())await back.first().click();const tab=page.locator(`[data-mobile-view=${view}]`);if(await tab.count()){await tab.click();return;}await page.locator('[data-mobile-view=settings]').click();await page.locator(`[data-mobile-open=${view}]`).click();}else await appSection(page,label);};
  const started=Date.now();await page.goto('/');await expect(page.locator('.workspace')).toBeVisible();if(await page.locator('.workspace').getAttribute('data-current-view')!=='home')await go('home','Главная');await expect(page.locator('#widget-grid>.widget-ready')).toHaveCount(4);await page.waitForTimeout(1500);const startupMs=Date.now()-started;
  phase='home-visible-45s';await page.waitForTimeout(45000);
  phase='home-hidden-22s';await page.evaluate(()=>{window.auditHidden=true;document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(22000);
  phase='resume';await page.evaluate(()=>{window.auditHidden=false;document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(1500);
  for(const [view,label] of [['notes','Заметки'],['tasks','Напоминания'],['tracker','Задачи'],['chat','Сообщения'],['files','Файлы'],['expenses','Раздельный счёт'],['settings','Настройки'],['home','Главная']]){
   console.log(engine.name(), 'opening', view);phase='open-'+view;await go(view,label);await page.waitForTimeout(1800);
  }
  phase='home-repeat-idle-12s';await page.waitForTimeout(12000);
  const grouped={};for(const r of requests){const key=r.phase+' '+r.method+' '+r.path;grouped[key]=(grouped[key]||0)+1;}
  const longTasks=await page.evaluate(()=>window.longTasks);expect(errors).toEqual([]);
  for(let i=report.length-1;i>=0;i--)if(report[i].engine===engine.name())report.splice(i,1);
  report.push({engine:engine.name(),width,webApp:mobile,method:'real 2-worker/32-thread local Gunicorn, isolated encrypted PostgreSQL; no external family service or production load',startupMs,requests:grouped,longTasks,errors});
  await writeFile(out+'/browser-observation.json',JSON.stringify(report,null,2));await context.close();await browser.close();browser=null;
 }
 console.log('Observed real initial/repeated navigation, visible/hidden idle and resume in Chromium and WebKit on private PostgreSQL.');
}finally{await browser?.close();server.kill('SIGTERM');await Promise.race([exited,new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null){server.kill('SIGKILL');await exited;}await rm(directory,{recursive:true,force:true});}
