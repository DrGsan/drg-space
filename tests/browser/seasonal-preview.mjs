import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {appSection} from './notes-ui.js';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium',baseline=process.env.DRG_SEASONAL_BASELINE==='1';
const directory=await mkdtemp(tmpdir()+'/drg-seasonal-preview-'),origin='http://127.0.0.1:8062';
const out='artifacts/seasonal-preview-review'+(engine==='webkit'?'/webkit':'');await mkdir(out,{recursive:true});
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8062','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-seasonal-preview-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));const exited=new Promise(resolve=>server.once('exit',resolve));
const report={engine,baseline,screenshots:[]};
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
 const note=await send('/notes',{title:'Планы на выходные',body:'Выбрать маршрут и подготовить всё для прогулки.'});
 const project=await send('/tracker/projects',{name:'DrG Space',key:'SITE',description:'Работы над приложением'});
 await send('/tracker/projects/'+project.id+'/issues',{title:'Проверить праздничное оформление',kind:'task',priority:2});
 await send('/appearance',{wallpaper:'mountains',mode:'light',style:'glass',seasonal:'off'},'PUT');
 const stored=(await send('/appearance',undefined,'GET')).preferences;
 await page.clock.setFixedTime(new Date('2026-08-20T09:00:00Z'));await page.goto('/');
 async function settings(){
  await page.locator('.workspace').waitFor();await page.locator('[data-mobile-view=settings]').waitFor({state:'attached'});
  if(page.viewportSize().width<=760&&await page.locator('#files-main-menu').isVisible())await page.locator('#files-main-menu').click();
  else if(await page.locator('[data-mobile-view=settings]').isVisible())await page.locator('[data-mobile-view=settings]').click();else await appSection(page,'Настройки');
  await page.locator('.preference-row').first().waitFor();
  for(const row of await page.locator('.preference-row').all()){const open=await row.getAttribute('data-preference-key')==='appearance';if((await row.getAttribute('open')!==null)!==open)await row.locator(':scope>summary').click();}
  await page.locator('[data-seasonal-preview]').waitFor();
 }
 async function home(){if(await page.locator('[data-mobile-view=settings]').isVisible()){await settings();await page.locator('[data-mobile-open=home]').click();}else await appSection(page,'Главная');await page.locator('#customize-dashboard').waitFor();await expect(page.locator('.widget:not(.widget-ready)')).toHaveCount(0,{timeout:20000});}
 async function tracker(){if(await page.locator('[data-mobile-view=tracker]').isVisible())await page.locator('[data-mobile-view=tracker]').click();else await appSection(page,'Задачи');await expect.poll(async()=>await page.locator('.tracker-chrome.is-project,[data-tracker-project="'+project.id+'"]').count()).toBeGreaterThan(0);if(await page.locator('[data-tracker-project="'+project.id+'"]').count())await page.locator('[data-tracker-project="'+project.id+'"]').click();await page.locator('.tracker-chrome.is-project').waitFor();await expect(page.locator('.tracker-loading')).toHaveCount(0);}
 async function screenshot(label,phase=baseline?'before':'after'){const path=out+'/'+phase+'-'+label+'.png';await page.screenshot({path,animations:'disabled'});report.screenshots.push(path);}
 async function previewVisible(){await expect(page.locator('[data-seasonal-end-preview]')).toBeVisible();await expect(page.locator('.seasonal-preview-control')).toHaveCount(1);expect(await page.locator('.seasonal-decoration:visible').evaluateAll(nodes=>nodes.length>0&&nodes.every(n=>n.dataset.seasonalTheme==='newyear'))).toBe(true);}
 async function layout(){
  const rects=await page.evaluate(()=>{const rect=e=>e?.getBoundingClientRect().toJSON(),tabs=[...document.querySelectorAll('.mobile-app-tabs')].find(e=>e.getClientRects().length);return {bar:rect(document.querySelector('.seasonal-preview-control')),content:rect(document.querySelector('.workspace>.content')),tabs:tabs?rect(tabs):null,viewport:visualViewport.height,overflow:document.documentElement.scrollWidth-innerWidth};});
  expect(rects.overflow).toBeLessThanOrEqual(1);expect(rects.content.bottom).toBeLessThanOrEqual(rects.bar.top+1);expect(rects.bar.bottom).toBeLessThanOrEqual(rects.viewport+1);if(rects.tabs)expect(rects.bar.bottom).toBeLessThanOrEqual(rects.tabs.top);
  await expect.poll(()=>page.locator('[data-seasonal-end-preview]').evaluate(b=>{const r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===b||b.contains(hit);})).toBe(true);
 }
 for(const width of [1440,390]){
  const old=await page.locator('.workspace').elementHandle(),crossed=(page.viewportSize().width<=760)!==(width<=760);await page.setViewportSize({width,height:width===1440?950:844});if(crossed)await expect.poll(()=>old.evaluate(el=>el.isConnected)).toBe(false);
  await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');await page.locator('.appearance-seasonal').scrollIntoViewIfNeeded();if(!baseline)await previewVisible();await screenshot('settings-'+width);
  if(!baseline){await page.locator('[data-seasonal-end-preview]').click();await home();await expect(page.locator('.seasonal-decoration')).toHaveCount(0);await screenshot('home-'+width,'before');await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');}
  await home();if(baseline)await expect(page.locator('.seasonal-decoration')).toHaveCount(0);else{await previewVisible();await layout();}await screenshot('home-'+width);
  await tracker();if(baseline)await expect(page.locator('.seasonal-decoration')).toHaveCount(0);else await previewVisible();await screenshot('tracker-'+width);
  expect((await send('/appearance',undefined,'GET')).preferences).toEqual(stored);
 }
 if(!baseline){
  // The same temporary theme follows real navigation; no settings write occurs.
  for(const [section,selector] of [['notes','.notes-navigation'],['tasks','.tasks-toolbar:visible,.tasks-nav-head:visible'],['chat','.chat-nav-head'],['files','.drive-workspace-header']]){
   if(section==='files'){await settings();await page.locator('[data-mobile-open=files]').click();}else await page.locator('[data-mobile-view='+section+']').click();
   await page.locator(selector).first().waitFor();await previewVisible();await layout();
   if(section==='notes'){await page.locator('[data-open-page="'+note.id+'"]').click();await previewVisible();await layout();}
  }
  report.navigation=['settings','home','tracker','notes','tasks','chat','files'];
  await page.locator('[data-seasonal-end-preview]').click();await expect(page.locator('.seasonal-preview-control,.seasonal-decoration')).toHaveCount(0);await settings();await expect(page.locator('[data-seasonal-preview]')).toHaveValue('');
  // Save and cancel affect preferences, never the independent preview session.
  await page.locator('[data-seasonal-preview]').selectOption('newyear');await page.locator('[name=seasonal]').selectOption('auto');await page.locator('[data-appearance] [type=submit]').click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');await previewVisible();expect((await send('/appearance',undefined,'GET')).preferences.seasonal).toBe('auto');
  await page.locator('[name=seasonal]').selectOption('off');await page.locator('[data-appearance-reset]').click();await expect(page.locator('[name=seasonal]')).toHaveValue('auto');await previewVisible();
  await send('/appearance',{seasonal:'off'},'PUT');await page.reload();await settings();await expect(page.locator('.seasonal-preview-control,.seasonal-decoration')).toHaveCount(0);await expect(page.locator('[data-seasonal-preview]')).toHaveValue('');expect((await send('/appearance',undefined,'GET')).preferences).toEqual(stored);
  await page.locator('[data-seasonal-preview]').selectOption('newyear');await page.emulateMedia({reducedMotion:'reduce'});await tracker();await previewVisible();
  expect(await page.locator('.seasonal-decoration *').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationName==='none'))).toBe(true);
  for(const width of [320,390]){await page.setViewportSize({width,height:844});await previewVisible();await layout();}
  // Emulate WebKit's visual viewport when its software keyboard is open.
  await page.evaluate(()=>{Object.defineProperty(visualViewport,'height',{configurable:true,value:500});visualViewport.dispatchEvent(new Event('resize'));});await expect(page.locator('html')).toHaveClass(/mobile-keyboard/);await layout();await expect(page.locator('.mobile-app-tabs')).toBeHidden();
  await page.evaluate(()=>{delete visualViewport.height;visualViewport.dispatchEvent(new Event('resize'));});await expect(page.locator('html')).not.toHaveClass(/mobile-keyboard/);await layout();
  await page.evaluate(()=>document.documentElement.style.setProperty('--app-safe-bottom','34px'));await layout();await page.evaluate(()=>document.documentElement.style.removeProperty('--app-safe-bottom'));
  await page.emulateMedia({reducedMotion:'no-preference'});await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});await expect.poll(()=>page.locator('.seasonal-decoration').evaluateAll(nodes=>nodes.every(n=>n.classList.contains('seasonal-paused')))).toBe(true);await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  // Ordinary mobile browsers scroll workspace during preview; desktop uses
  // content. Both must preserve the user's exact place on entry and exit.
  for(const width of [390,1440]){
   const ordinary=await browser.newContext({storageState:await context.storageState(),baseURL:origin,locale:'ru-RU',viewport:{width,height:844}}),p=await ordinary.newPage();await p.clock.setFixedTime(new Date('2026-08-20T09:00:00Z'));await p.goto('/');await appSection(p,'Настройки');
   for(const row of await p.locator('.preference-row').all())if(await row.getAttribute('data-preference-key')!=='appearance'&&await row.getAttribute('open')!==null)await row.locator(':scope>summary').click();
   await p.locator('[data-seasonal-preview]').waitFor();await p.evaluate(()=>document.fonts.ready);await p.evaluate(()=>window.scrollTo(0,600));const before=await p.locator('.appearance-seasonal').evaluate(e=>({y:e.getBoundingClientRect().y,scroll:scrollY}));expect(before.scroll).toBeGreaterThan(100);
   await p.locator('[data-seasonal-preview]').evaluate(e=>{e.value='newyear';e.dispatchEvent(new Event('change',{bubbles:true}));});await expect(p.locator('.seasonal-preview-control')).toBeVisible();await expect.poll(()=>p.locator('.appearance-seasonal').evaluate(e=>e.getBoundingClientRect().y)).toBeCloseTo(before.y,0);
   expect(await p.evaluate(()=>scrollY)).toBe(0);expect(await p.locator(width<=760?'.workspace':'.workspace>.content').evaluate(e=>e.scrollTop)).toBe(before.scroll);
   await p.locator('[data-seasonal-end-preview]').click();await expect(p.locator('.seasonal-preview-control')).toHaveCount(0);expect(await p.evaluate(()=>scrollY)).toBe(before.scroll);expect(await p.locator('.appearance-seasonal').evaluate(e=>e.getBoundingClientRect().y)).toBeCloseTo(before.y,0);await ordinary.close();
  }
  report.scrollRestored=['ordinary-phone','desktop'];
  // Use actual authentication transitions, with a distinct second account.
  await send('/admin/access/unlock',{password});const invitation=await send('/invites',{}),guest=await browser.newContext({baseURL:origin});const joined=await guest.request.post('/api/register',{headers:{Origin:origin},data:{login:'preview_guest',password,invite:invitation.url.split('/').pop()}});expect(joined.ok()).toBe(true);const guestCSRF=(await joined.json()).csrf;expect((await guest.request.post('/api/2fa/skip',{headers:{Origin:origin,'X-CSRF-Token':guestCSRF}})).ok()).toBe(true);const guestID=(await (await guest.request.get('/api/me')).json()).id;await guest.close();
  await settings();await previewVisible();await page.locator('.mobile-signout').click();await page.locator('#auth-form [name=login]').waitFor();await expect(page.locator('.seasonal-preview-control,.seasonal-decoration')).toHaveCount(0);
  await page.locator('#auth-form [name=login]').fill('preview_guest');await page.locator('#auth-form [name=password]').fill(password);await page.locator('#auth-form [type=submit]').click();await page.locator('.workspace').waitFor();expect((await (await page.request.get('/api/me')).json()).id).toBe(guestID);await expect(page.locator('.seasonal-preview-control,.seasonal-decoration')).toHaveCount(0);await settings();await expect(page.locator('[data-seasonal-preview]')).toHaveValue('');
  report.lifecycle=['end-preview','save-independent','cancel-independent','reload','logout','different-account','reduced-motion','hidden-pause','keyboard-viewport','safe-area'];
 }
 expect(errors).toEqual([]);report.passed=true;await writeFile(out+'/'+(baseline?'before':'after')+'-report.json',JSON.stringify(report,null,2));console.log('Global holiday preview',baseline?'baseline':'after',engine,'passed.');
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
