import {chromium,webkit} from 'playwright';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
const baseline=false,phase='after',engine=process.env.DRG_BROWSER_ENGINE||'chromium',capture=process.env.DRG_CAPTURE==='1';
const origin='http://127.0.0.1:8057',dir=await mkdtemp(tmpdir()+'/drg-motion-'),out='artifacts/auth-motion-space';
const server=spawn('.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8057','tests.browser_app:app'],{env:{...process.env,DATA_DIR:dir,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-motion-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser; server.stderr.on('data',d=>output=(output+d).slice(-4000));
try {
 for(let i=0;i<150;i++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{} if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}
 browser=await ({chromium,webkit}[engine]).launch({headless:true});
 const context=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:1100,height:850},colorScheme:'light',...(capture?{recordVideo:{dir:out+'/raw-'+phase+'-'+engine,size:{width:1100,height:850}}}:{})});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let csrf=''; const call=async(path,data,method='POST')=>{const r=await context.request.fetch(origin+'/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});if(!r.ok())throw Error(path+' '+r.status()+' '+await r.text());return r.json();};
 const password='Motion-review-password-123';csrf=(await call('/setup',{login:'demoowner',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await call('/2fa/enroll',{password}),alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 const codes=(await call('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')})).recovery_codes;csrf=(await call('/2fa/activate',{saved_recovery_codes:true})).csrf;
 for(const title of ['Идеи для дома','Планы на выходные','Новая история'])await call('/notes',{title,body:'# '+title+'\n\nВсё важное под рукой.'});
 for(const title of ['Забрать посылку','Позвонить родителям','Выбрать фильм'])await call('/tasks',{title,day:new Date().toLocaleDateString('en-CA')});
 const layout=await call('/dashboard/layout',undefined,'GET');await call('/dashboard/layout',{version:layout.version,instances:[['clock','1x1',{timezone:'Europe/Moscow'}],['tasks','2x1',{}],['notes','2x1',{}],['calendar','1x1',{}],['weather','1x1',{city:'Москва',latitude:55.75,longitude:37.62}],['favorites','1x1',{}]].map(([module_id,size,config])=>({id:'review_'+module_id,module_id,size,config}))},'PUT');
 await call('/appearance',{style:'glass',wallpaper:'warm',mode:'dark'},'PUT');
 await call('/logout');
 await page.route('**/api/login',async route=>{await new Promise(r=>setTimeout(r,450));await route.continue();});
 await page.route('**/api/2fa/verify',async route=>{await new Promise(r=>setTimeout(r,650));await route.continue();});
 await page.route('**/api/modules',async route=>{await new Promise(r=>setTimeout(r,800));await route.continue();});
 await page.route('**/api/dashboard/layout',async route=>{await new Promise(r=>setTimeout(r,850));await route.continue();});
 await page.route('**/api/task-lists',async route=>{await new Promise(r=>setTimeout(r,1600));await route.continue();});
 await page.route('**/api/widgets/review_weather/weather',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Weather test unavailable'})}));
 await page.goto('/');await page.locator('[name=login]').waitFor();await page.waitForTimeout(1200);if(capture)await page.screenshot({path:out+'/'+phase+'-login.png'});const logo=await page.locator('.brand').elementHandle(),authScheme=await page.locator('html').getAttribute('data-scheme');
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await page.locator('.auth-layout').evaluate(el=>getComputedStyle(el,'::before').animationName),'none');
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.reviewHidden});window.reviewHidden=true;document.dispatchEvent(new Event('visibilitychange'));});
 assert.equal(await page.locator('.auth-layout').evaluate(el=>getComputedStyle(el,'::before').animationPlayState),'paused');
 await page.evaluate(()=>{window.reviewHidden=false;document.dispatchEvent(new Event('visibilitychange'));});
 await page.locator('[name=login]').fill('demoowner');await page.locator('[name=password]').fill(password);await page.locator('.auth-submit').click();await page.waitForFunction(()=>document.querySelector('#auth-form')?.getAttribute('aria-busy')==='true');await page.locator('[name=code]').waitFor();assert(await logo.evaluate(el=>el.isConnected),'logo is retained across login and 2FA');await page.waitForTimeout(950);
 await page.locator('[name=code]').pressSequentially('123456',{delay:180});assert.equal(await page.locator('.auth-code-progress .is-filled').count(),6);assert.equal(await page.locator('[name=code]').getAttribute('autocomplete'),'one-time-code');if(capture)await page.screenshot({path:out+'/'+phase+'-2fa.png'});await page.locator('.auth-submit').click();await page.waitForTimeout(1000);
 if(!baseline){if(await page.locator('.auth-layout').getAttribute('data-motion-state')!=='error')throw Error('Invalid 2FA did not return an error state');if(!(await page.locator('[name=code]').isEnabled()))throw Error('Code field stayed disabled');}
 const workspaceRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/modules');
 const startupResponses=Promise.all(['/api/presence','/api/calls/config'].map(path=>page.waitForResponse(response=>new URL(response.url()).pathname===path)));
 await page.locator('[name=code]').fill(codes[0]);assert.equal(await page.locator('.auth-code-progress .is-filled').count(),0);
 assert.equal(await page.locator('.auth-card').evaluate(el=>el.getAnimations().filter(a=>a.playState==='running'&&['drg-entry-rise','drg-code-enter'].includes(a.animationName)).length),0,'editing after an error does not replay form entrance');
 const header=await page.locator('.auth-header').elementHandle();await page.locator('.auth-submit').click();await page.waitForFunction(()=>document.querySelector('.auth-layout')?.dataset.motionState==='checking');await workspaceRequest;assert.equal(await page.locator('html').getAttribute('data-scheme'),authScheme,'account theme changes inside workspace transition');assert(await header.evaluate(el=>el.isConnected),'auth shell persists while fetching workspace');await page.waitForFunction(()=>document.querySelector('#widget-grid .widget-content-placeholder'));assert.equal(await page.locator('#widget-grid>.entry-skeleton-card').count(),0);await page.waitForTimeout(30);if(capture)await page.screenshot({path:out+'/'+phase+'-loading.png'});
 await page.locator('#widget-grid [data-widget]').first().waitFor();
 const frames=await page.locator('#widget-grid').evaluate(async grid=>{
   const samples=[];
   for(let i=0;i<140;i++){
     const widget=grid.querySelector('[data-widget=clock]'),body=widget.querySelector('[data-widget-body]');
     samples.push({time:performance.now(),x:widget.offsetLeft,y:widget.offsetTop,width:widget.offsetWidth,height:widget.offsetHeight,opacity:Number(getComputedStyle(body).opacity),ready:widget.classList.contains('widget-ready')});
     await new Promise(requestAnimationFrame);
   }
   return samples;
 });
 const box=frames[0];assert(frames.every(f=>f.x===box.x&&f.y===box.y&&f.width===box.width&&f.height===box.height),'card geometry stays fixed from its first frame');
 assert(frames[0].opacity===0&&!frames[0].ready,'content stays behind its placeholder during the screen transition');
 assert(frames.some(f=>f.opacity>0.05&&f.opacity<0.95),'content has intermediate fade frames');
 await page.waitForFunction(()=>document.querySelectorAll('#widget-grid>.widget-ready').length===6);await page.waitForTimeout(600);
 assert.equal(await page.locator('[data-widget=weather] [data-widget-body]').getAttribute('aria-hidden'),null);
 assert(await page.locator('#widget-grid [data-widget-body]').evaluateAll(bodies=>bodies.every(body=>{const r=body.getBoundingClientRect(),frame=body.parentElement.getBoundingClientRect();return r.width>20&&r.height>20&&Math.abs(r.width-frame.width)<1&&Math.abs(r.height-frame.height)<1;})),'all widget content frames, including size-contained weather, keep their allocated bounds');
assert.equal(await page.locator('html').getAttribute('data-scheme'),'dark');assert.equal(await page.locator('.widget-enter').count(),0,'entrance transform released before dragging widgets');if(capture)await page.screenshot({path:out+'/'+phase+'-home.png'});
 await startupResponses;
 // Capture the very first layout in the mutation microtask, before ResizeObserver.
 for(const width of [390,760,1100,1600]){
   await page.setViewportSize({width,height:850});
   await page.locator('.sidebar [data-view=settings]').click();
   await page.evaluate(()=>{
     window.widgetBounds=[];window.widgetGridObserver?.disconnect();
     window.widgetGridObserver=new MutationObserver(()=>{
       const grid=document.querySelector('#widget-grid'),clock=grid?.querySelector('[data-widget=clock]');
       if(!clock||window.widgetBounds.length)return;
       window.widgetGridObserver.disconnect();
       const sample=()=>{
         window.widgetBounds.push({connected:clock.isConnected,width:clock.offsetWidth,height:clock.offsetHeight,x:clock.offsetLeft,y:clock.offsetTop,columns:getComputedStyle(grid).gridTemplateColumns,gridWidth:grid.getBoundingClientRect().width});
         if(window.widgetBounds.length<90)requestAnimationFrame(sample);
       };sample();
     });window.widgetGridObserver.observe(document.body,{childList:true,subtree:true});
   });
   await page.locator('.sidebar [data-view=home]').click();
   await page.waitForFunction(()=>window.widgetBounds.length===90);
   const samples=await page.evaluate(()=>window.widgetBounds),first=samples[0];
   assert(samples.every(s=>s.connected&&s.x===first.x&&s.y===first.y&&s.width===first.width&&s.height===first.height&&s.columns===first.columns),'first frame stays in place, including cached layout refresh at '+width+'px');
   const columns=first.gridWidth>=840?4:first.gridWidth>=440?2:1;
   assert(Math.abs(first.width-(first.gridWidth-(columns-1)*16)/columns)<1,'initial column geometry at '+width+'px');
 }
 await page.setViewportSize({width:1100,height:850});

 await page.locator('.sidebar [data-view=settings]').click();await page.locator('[data-preference-key=profile]>summary').click();const gallery=page.locator('[data-preference-key=profile] [data-avatar-presets]');
 if(!baseline){await gallery.locator('[data-avatar-category=space]').click();await gallery.locator('[data-avatar-preset=space-6]').click();await page.waitForFunction(()=>document.querySelector('[data-avatar-preset="space-6"]')?.getAttribute('aria-pressed')==='true'&&!document.querySelector('[data-avatar-preset="space-6"]')?.disabled);await page.waitForFunction(()=>document.querySelector('.profile-avatar img')?.naturalWidth===256);}
 if(capture)await page.screenshot({path:out+'/'+phase+'-avatars.png',fullPage:true});
 if(!baseline){const me=await call('/me',undefined,'GET');if(me.settings.profile.avatar_preset!=='space-6')throw Error('Space selection not persisted');await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>new Promise(requestAnimationFrame));if(await page.evaluate(()=>document.getAnimations().some(a=>a.playState==='running'&&a.animationName?.startsWith('drg-'))))throw Error('Reduced motion still animated');}
 const phone=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:390,height:844},isMobile:true,hasTouch:true,colorScheme:'dark',reducedMotion:'reduce'});
 await phone.addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true}));
 const mobile=await phone.newPage();await mobile.route('**/api/widgets/review_weather/weather',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Weather test unavailable'})}));await mobile.goto('/');await mobile.locator('[name=login]').waitFor();
 assert.equal(await mobile.locator('.auth-layout').evaluate(el=>getComputedStyle(el,'::before').animationName),'none');
 assert(await mobile.locator('.auth-card').evaluate(el=>el.getBoundingClientRect().right<=innerWidth));
 if(capture)await mobile.screenshot({path:out+'/after-mobile-login.png',fullPage:true});
 await mobile.locator('[name=login]').fill('demoowner');await mobile.locator('[name=password]').fill(password);await mobile.locator('.auth-submit').click();await mobile.locator('[name=code]').waitFor();
 await mobile.locator('[name=code]').fill('123');assert.equal(await mobile.locator('.auth-code-progress .is-filled').count(),3);
 if(capture)await mobile.screenshot({path:out+'/after-mobile-2fa.png',fullPage:true});
 await mobile.emulateMedia({reducedMotion:'no-preference'});
 await mobile.locator('[name=code]').fill(codes[1]);await mobile.locator('.auth-submit').click();
 await mobile.locator('.mobile-app-tabs [data-mobile-view=settings]').click();
 await mobile.locator('[data-mobile-open=home]').click();
 await mobile.waitForFunction(()=>document.querySelectorAll('#widget-grid>.widget-ready').length===6);
 const mobileClock=mobile.locator('[data-widget=clock]');assert(await mobileClock.evaluate(el=>el.getBoundingClientRect().right<=innerWidth));
 await phone.close();
 if(errors.length)throw Error(JSON.stringify(errors));
 await context.close();console.log('Motion flow '+phase+' '+engine+' passed');
} finally {await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(dir,{recursive:true,force:true});}
