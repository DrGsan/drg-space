import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac,createHash} from 'node:crypto';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium',phase=process.env.DRG_SEASONAL_FRAME_PHASE||'after';
const directory=await mkdtemp(tmpdir()+'/drg-seasonal-preview-'),origin='http://127.0.0.1:8060';
const out='artifacts/holiday-auth-frame/'+phase+(engine==='webkit'?'/webkit':'');await mkdir(out,{recursive:true});
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8060','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-seasonal-preview-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));const exited=new Promise(resolve=>server.once('exit',resolve));
const report={engine,phase,screenshots:[],checks:[]};
try{
 let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,200));}if(!ready)throw Error(output);
 browser=await ({chromium,webkit}[engine]).launch({headless:true});
 const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:950},colorScheme:'light',timezoneId:'Europe/Moscow'});
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,get:()=>true}));
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
 let csrf='';const password='Seasonal-preview-password-123';
 const send=async(path,body,method='POST')=>{const r=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 csrf=(await send('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await send('/2fa/enroll',{password}),alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 const recovery=(await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')})).recovery_codes;
 csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await send('/modules',{enabled:{tracker:true}},'PUT');
 const project=await send('/tracker/projects',{name:'DrG Space',key:'SITE',description:'Работы над приложением'});
 await send('/tracker/projects/'+project.id+'/issues',{title:'Проверить праздничное оформление',kind:'task',priority:2});
 await send('/appearance',{wallpaper:'mountains',mode:'light',style:'glass',seasonal:'off'},'PUT');
 await page.clock.setFixedTime(new Date('2026-08-20T09:00:00Z'));await page.goto('/');

 await page.setViewportSize({width:390,height:844});
 await page.locator('[data-mobile-view=settings]').click();
 await page.locator('[data-preference-key=appearance]>summary').click();
 await page.locator('[data-seasonal-preview]').selectOption('halloween');
 await page.locator('[data-mobile-view=tracker]').click();
 await page.locator('.tracker-surface').waitFor();
 await page.waitForTimeout(1200);
 await page.screenshot({path:out+'/projects-mobile.png',animations:'disabled'});
 report.mask=await page.evaluate(()=>({surface:document.querySelector('.tracker-surface').getBoundingClientRect().toJSON(),holes:[...document.querySelectorAll('.seasonal-shell-decoration mask rect')].map(e=>e.outerHTML)}));

 if(phase==='after'){
  // The gap outside a real card used to be an invisible six-pixel clipping box.
  const frame=page.locator('.seasonal-shell-decoration');
  const bounds=await page.locator('.tracker-surface').evaluate(e=>e.getBoundingClientRect().toJSON());
  const shape=await frame.locator('mask rect[fill=black]').evaluateAll((nodes,b)=>nodes.map(e=>({x:+e.getAttribute('x'),y:+e.getAttribute('y'),width:+e.getAttribute('width'),height:+e.getAttribute('height'),radius:+e.getAttribute('rx')})).find(r=>r.y===b.y),bounds);
  expect(shape).toEqual({x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height,radius:13});
  const clip={x:bounds.x-2,y:170,width:2,height:30};
  const visible=await page.screenshot({clip,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='hidden');
  const hidden=await page.screenshot({clip,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='');
  expect(createHash('sha256').update(visible).digest('hex'),'Artwork reaches the card edge').not.toBe(createHash('sha256').update(hidden).digest('hex'));
  // Inside the painted card, decor must not affect content, including glass.
  const interior={x:bounds.x+14,y:bounds.y+14,width:bounds.width-28,height:bounds.height-28};
  const a=await page.screenshot({clip:interior,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='hidden');
  const b=await page.screenshot({clip:interior,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='');expect(a.equals(b)).toBe(true);
 }
 const dates={halloween:'2026-10-31',newyear:'2026-12-25',valentine:'2026-02-14',spring:'2026-03-08',space:'2026-04-12',ordinary:'2026-08-20'};
 for(const [theme,date] of Object.entries(dates))for(const scheme of ['light','dark']){
  const publicContext=await browser.newContext({locale:scheme==='dark'?'en-US':'ru-RU',baseURL:origin,viewport:{width:390,height:844},colorScheme:scheme,timezoneId:'Europe/Moscow'});
  // An earlier account's disabled holidays and preview must have no influence.
  await publicContext.addInitScript(()=>{
   Object.defineProperty(navigator,'standalone',{configurable:true,get:()=>true});
   localStorage.setItem('drg-appearance',JSON.stringify({seasonal:'off',theme:'rose',mode:'system'}));
  });
  const login=await publicContext.newPage();login.on('pageerror',e=>errors.push(e.message));
  await login.clock.setFixedTime(new Date(date+'T09:00:00Z'));await login.goto('/');await login.locator('.auth-card').waitFor();
  if(phase==='after'){
   if(theme==='ordinary')await expect(login.locator('.seasonal-shell-decoration')).toHaveCount(0);
   else await expect(login.locator('.auth-layout')).toHaveAttribute('data-seasonal-theme',theme);
  }
  for(const width of [390,320,1440]){
   await login.setViewportSize({width,height:width>760?950:844});await login.waitForTimeout(1100);
   expect(await login.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
   for(const control of await login.locator('.auth-card input,.auth-card button').all()){
    if(!await control.isVisible())continue;await control.scrollIntoViewIfNeeded();
    expect(await control.evaluate(e=>{const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return e===hit||e.contains(hit)})).toBe(true);
   }
   await login.evaluate(()=>window.scrollTo(0,0));
   await login.screenshot({path:out+'/login-'+theme+'-'+width+(scheme==='dark'?'-dark':'')+'.png',animations:'disabled'});
  }
  if(theme==='newyear'&&phase==='after'){
   await login.emulateMedia({reducedMotion:'reduce'});
   expect(await login.locator('.seasonal-shell-decoration *,.seasonal-decoration *').evaluateAll(nodes=>nodes.every(e=>getComputedStyle(e).animationName==='none'))).toBe(true);
   await login.emulateMedia({reducedMotion:'no-preference'});
   const first=await login.locator('.seasonal-shell-decoration .seasonal-edge-motion').first().evaluate(e=>getComputedStyle(e).transform);
   await login.waitForTimeout(250);
   const second=await login.locator('.seasonal-shell-decoration .seasonal-edge-motion').first().evaluate(e=>getComputedStyle(e).transform);expect(second).not.toBe(first);
  }
  report.checks.push({theme,scheme,widths:[390,320,1440],localCalendar:true,controlsUsable:true});
  await publicContext.close();
 }
 if(phase==='after'){
  for(const theme of ['newyear','valentine','spring','space']){
   await page.locator('[data-mobile-view=settings]').click();
   if(!await page.locator('[data-seasonal-preview]').isVisible())await page.locator('[data-preference-key=appearance]>summary').click();
   await page.locator('[data-seasonal-preview]').selectOption(theme);await page.locator('[data-mobile-view=tracker]').click();
   await expect(page.locator('.workspace')).toHaveAttribute('data-seasonal-theme',theme);
   await page.screenshot({path:out+'/projects-'+theme+'-390.png',animations:'disabled'});
  }
  await send('/logout',{});await page.clock.setFixedTime(new Date('2026-12-25T09:00:00Z'));await page.goto('/');
  await expect(page.locator('.auth-layout')).toHaveAttribute('data-seasonal-theme','newyear');
  await page.locator('[name=login]').fill('alexander');await page.locator('[name=password]').fill(password);await page.locator('.auth-submit').click();
  await expect(page.locator('.auth-layout')).toHaveAttribute('data-auth-mode','2fa');await expect(page.locator('.auth-layout')).toHaveAttribute('data-seasonal-theme','newyear');
  await page.screenshot({path:out+'/login-2fa-390.png',animations:'disabled'});
  await page.locator('[name=code]').fill(recovery[0]);await page.locator('.auth-submit').click();
  await page.locator('.workspace').waitFor();await expect(page.locator('.seasonal-shell-decoration')).toHaveCount(0);await expect(page.locator('.workspace')).not.toHaveAttribute('data-seasonal-theme');
  report.accountDisabledAfterLogin=true;
 }
 expect(errors).toEqual([]);await writeFile(out+'/report.json',JSON.stringify(report,null,2));console.log('Saved',out);
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
