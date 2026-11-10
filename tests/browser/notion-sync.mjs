import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {touchSequence} from './touch-gesture.mjs';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {createServer} from 'node:net';
const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
const dir=await mkdtemp(tmpdir()+'/drg62-browser-'),origin='http://127.0.0.1:'+port;
const server=spawn('.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:'+port,'tests.notion_sync_browser_app:app'],{env:{...process.env,DRG_NOTION_BROWSER_TEST:'1',DATA_DIR:dir,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'drg62-only-isolated-capture-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,FAMILY_PHOTO_URL:'',CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',x=>logs=(logs+x).slice(-3000));const exited=new Promise(r=>server.once('exit',r));
try{
 for(let n=0;n<150;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();const context=await browser.newContext({baseURL:origin,viewport:{width:1280,height:900},locale:'ru-RU'});let csrf='';
 // Calls are outside this fixture. Avoid unrelated background call polling
 // racing synthetic full-page reloads in WebKit.
 await context.route('**/api/calls/current?*',route=>route.fulfill({json:{call:null}}));
 await context.route('**/api/chat/unread',route=>route.fulfill({json:{unread:0}}));
 const api=async(path,data,method='POST')=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Local-notion-review-123';csrf=(await api('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await api('/2fa/enroll',{password}),bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 await api('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await api('/appearance',{theme:'ocean',style:'glass',mode:process.env.DRG_NOTION_THEME||'light',wallpaper:'mountains',seasonal:'off'},'PUT');
 const source=(await api('/browser/notion-fixture',{})).source;
 const page=await context.newPage(),errors=[],requests=[];await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:true}));page.on('pageerror',e=>{errors.push(e.message);console.error('Browser error',e.stack);});page.on('request',r=>{if(r.url().includes('/api/account/notion-sync'))requests.push(r.url());});
 const visit=async url=>{await page.waitForLoadState('networkidle');await page.goto(url);};
 const openBadge=async selector=>{const badge=page.locator(selector);if(!await badge.evaluate(el=>el.open))await badge.locator('summary').click();};
 await visit('/');await page.getByRole('button',{name:'Настройки',exact:true}).first().click();
 await page.getByText('Личные данные',{exact:true}).first().click();
 await page.getByRole('button',{name:'Экспорт и импорт',exact:true}).click();
 await page.getByRole('button',{name:'Синхронизация с Notion',exact:true}).click();
 const dialog=page.getByRole('dialog').last();await expect(dialog.locator('[data-sync-preview]')).toBeVisible();
 await dialog.screenshot({path:'artifacts/drg-62/after-web-connect.png'});
 await dialog.locator('[data-source-picker]').click();await expect(dialog.getByRole('option',{name:/Семейная библиотека/})).toBeVisible();
 await dialog.screenshot({path:'artifacts/drg-62/after-web-page-picker.png'});
 await page.setViewportSize({width:390,height:844});await dialog.screenshot({path:'artifacts/drg-62/after-webapp-page-picker.png'});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.setViewportSize({width:1280,height:900});await dialog.locator('[data-source-search]').fill('библиотека');await expect(dialog.locator('[data-source-list]').getByRole('option')).toHaveCount(1);await dialog.getByRole('option',{name:/Семейная библиотека/}).click();await dialog.getByRole('button',{name:'Проверить состав переноса',exact:true}).click();
 await expect(dialog.locator('[data-sync-progress]')).toBeVisible({timeout:10000});
 await expect(dialog.locator('[data-sync-progress]')).toContainText(/Проверено страниц: [1-9]/,{timeout:10000});
 await dialog.screenshot({path:'artifacts/drg-62/after-web-progress.png'});
 await dialog.getByRole('button',{name:'Проверить состав переноса',exact:true}).click();
 expect(await dialog.locator('[data-sync-error]').textContent()).toBe('');
 const checking=await api('/account/notion-sync',undefined,'GET');expect(checking.roots).toHaveLength(1);
 await expect(dialog.locator('[data-sync-confirm]')).toBeVisible({timeout:30000});
 await dialog.screenshot({path:'artifacts/drg-62/after-web-preview.png'});
 const reviewPattern='**/api/account/notion-sync';let reviewPolls=0,reviewPhase='importing',reviewExtra=false,reviewScreenshot=false;
 await page.route(reviewPattern,async route=>{
  const response=await route.fetch(),body=await response.json();reviewPolls++;
  body.roots=body.roots.map(root=>({...root,status:reviewPhase,copy_available:reviewPhase==='active',summary:{pages:183,files:270,databases:6,issue_count:reviewExtra?18:17,issues:Array.from({length:reviewExtra?18:17},(_,i)=>({page:'Проверочная страница '+(i+1)+' — длинное название раздела библиотеки',reason:'notionUnsupportedBlock'}))},progress:reviewPhase==='importing'?{copied_pages:reviewPolls,total_pages:183}:null,error:reviewPhase==='importing'&&reviewPolls%2?'notionAssetUnavailable':null}));
  for(const root of body.roots){
    const content=reviewScreenshot?root.summary.issues.slice(0,3):root.summary.issues;
    if(reviewScreenshot)Object.assign(root,{title:'IT',summary:{...root.summary,pages:57,files:64,databases:0}});
    root.summary.issue_groups=[{kind:'content',count:content.length,issues:content},{kind:'appearance',count:1,issues:[{page:'Мой сервер',reason:'notionIconUnavailable'}]},{kind:'write',count:2,issues:[{page:'Mac',reason:'notionSyncedBlockReadonly'},{page:'SSH Phrase',reason:'notionSyncedBlockReadonly'}]}];
    root.summary.issue_count=content.length+3;
  }
  await route.fulfill({response,json:body});
 });
 await dialog.locator('[data-sync-confirm]').click();await expect(dialog.locator('[data-link-visibility]')).toBeVisible();
 await expect(dialog.locator('[data-sync-open]')).toHaveCount(0);await expect(dialog.locator('[data-sync-action=disconnect]')).toHaveCount(1);
 await expect(dialog.locator('[data-issue-group]')).toHaveCount(3);
 let retainedDetails;
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});const details=dialog.locator('[data-link-issues] details').first();
  if(!await details.evaluate(el=>el.open))await details.locator('summary').click();
  retainedDetails=await details.elementHandle();const item=details.locator('li').nth(9),retainedItem=await item.elementHandle();
  await item.scrollIntoViewIfNeeded();const top=await dialog.evaluate(el=>el.scrollTop);expect(top).toBeGreaterThan(0);
  const selected=await item.evaluate(el=>{const selection=getSelection(),range=document.createRange();range.selectNodeContents(el);selection.removeAllRanges();selection.addRange(range);return selection.toString();});
  if(width===390)reviewExtra=true;
  const initial=reviewPolls;await expect.poll(()=>reviewPolls,{timeout:12000}).toBeGreaterThan(initial+2);
  expect(await retainedDetails.evaluate(el=>el.isConnected&&el.open)).toBe(true);expect(await retainedItem.evaluate(el=>el.isConnected)).toBe(true);
  expect(Math.abs(await dialog.evaluate(el=>el.scrollTop)-top)).toBeLessThan(2);expect(await page.evaluate(()=>getSelection().toString())).toBe(selected);
  await dialog.screenshot({path:`artifacts/drg-62/after-web-issues-${width}.png`});
 }
 reviewPhase='active';await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await expect(dialog.locator('[data-sync-action=pause]')).toBeVisible({timeout:10000});
 expect(await retainedDetails.evaluate(el=>el.isConnected&&el.open)).toBe(true);
 reviewScreenshot=true;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
 await expect(dialog.locator('[data-issue-group=content] li')).toHaveCount(3);
 await page.setViewportSize({width:1280,height:900});await dialog.evaluate(el=>el.scrollTop=el.scrollHeight);
 await dialog.locator('[data-issue-group=content] summary').click();
 await dialog.screenshot({path:'artifacts/drg-62/after-web-transfer-report.png'});
 await page.setViewportSize({width:390,height:844});await dialog.evaluate(el=>el.scrollTop=el.scrollHeight);
 await dialog.screenshot({path:'artifacts/drg-62/after-webapp-transfer-report.png'});
 await page.setViewportSize({width:1280,height:900});
 await page.unroute(reviewPattern);await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));

 await dialog.locator('[data-sync-open]').click();await expect(page.locator('.notion-source-banner')).toBeVisible({timeout:15000});
 const layoutNotePath=new URL(page.url()).pathname.replace('/page/','/api/notes/');let plainLayout=true;
 const layoutPattern='**'+layoutNotePath;
 await page.route(layoutPattern,async route=>{
  const response=await route.fetch(),body=await response.json();
  try{await route.fulfill({response,json:{...body,cover:'night',cover_url:null,icon:'🧨',icon_url:null,...(plainLayout?{notion_sync:null}:{})}});}
  catch(error){
   // Synthetic full-page reloads can cancel an in-flight polling response in
   // WebKit after its route was already disposed. Keep all other failures.
   if(!String(error.message).includes('Route is already handled!'))throw error;
  }
 });
 await page.waitForLoadState('networkidle');await page.reload();await page.waitForLoadState('networkidle');const normalTitle=await page.locator('#page-title').boundingBox(),normalIcon=await page.locator('.page-decoration').boundingBox();
 await page.screenshot({path:'artifacts/drg-62/before-web-note-status-layout.png'});plainLayout=false;await page.reload();await page.waitForLoadState('networkidle');
 expect(Math.abs((await page.locator('#page-title').boundingBox()).y-normalTitle.y)).toBeLessThan(1);expect(Math.abs((await page.locator('.page-decoration').boundingBox()).y-normalIcon.y)).toBeLessThan(1);
 await expect(page.locator('.page-cover-wrap .notion-source-banner>summary')).toBeVisible();await page.screenshot({path:'artifacts/drg-62/after-web-note-status-layout.png'});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/drg-62/after-webapp-note-status-layout.png'});await page.setViewportSize({width:1280,height:900});
 await page.waitForLoadState('networkidle');await page.unroute(layoutPattern);await page.reload();await openBadge('.notion-source-banner');
 await expect(page.locator('.notion-cover-warning')).toBeVisible();await expect(page.locator('.notion-icon-warning')).toBeVisible();
 await expect(page.locator('#page-title')).toBeDisabled();await page.screenshot({path:'artifacts/drg-62/after-web-linked.png'});
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});await page.screenshot({path:`artifacts/drg-62/after-webapp-linked-${width}.png`});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);if(width===390)await page.screenshot({path:'artifacts/drg-62/after-webapp-icon-warning.png'});
  await openBadge('.notion-source-banner');await page.locator('[data-sync-manage]').click();const d=page.getByRole('dialog').last();await expect(d.locator('[data-sync-action=pause]')).toBeVisible();
  await d.screenshot({path:`artifacts/drg-62/after-webapp-connect-${width}.png`});
  await d.getByRole('button',{name:'Закрыть',exact:true}).click();const before=requests.length;await page.waitForTimeout(2200);expect(requests.length).toBe(before);
 }
 const linkedRoot=(await api('/account/notion-sync',undefined,'GET')).roots.find(root=>root.status==='active');
 await page.setViewportSize({width:1280,height:900});
 // Recovery always requires a fresh, page-scoped review. Opening it is read-only.
 await page.route('**/api/account/notion-sync',async route=>{
  const response=await route.fetch(),body=await response.json();body.roots=body.roots.map(root=>root.id===linkedRoot.id?{...root,status:'paused',mode:'duplex'}:root);await route.fulfill({response,json:body});
 });
 let failFresh=true,recoveryReads=0;const decisions=[];
 await page.route('**/recovery*',async route=>{
  if(route.request().method()==='POST'){
   const body=route.request().postDataJSON();decisions.push(body);expect(body.note_version).toBe(10);expect(body.local_hash).toBe('a'.repeat(64));
   await route.fulfill({json:{note_id:linkedRoot.note_id,independent_copy:body.keep_copy?linkedRoot.note_id:null,direction:body.direction}});return;
  }
  recoveryReads++;await new Promise(r=>setTimeout(r,300));
  if(failFresh){await route.fulfill({status:503,json:{detail:'Notion временно недоступен'}});return;}
  const blocked=new URL(route.request().url()).searchParams.get('note_id')==='blocked-note-001';
  await route.fulfill({json:{pages:[{note_id:linkedRoot.note_id,title:'Test'},{note_id:'blocked-note-001',title:'Mac — неподдерживаемая структура'}],note_id:blocked?'blocked-note-001':linkedRoot.note_id,title:'Test',local:'Оставим только это\nНовая запись\nПроверка связей',remote:'Оставим только это\nТекст из Notion',can_push:!blocked,push_reason:blocked?'notionUnsupportedWrite':'',hash:'b'.repeat(64),local_hash:'a'.repeat(64),note_version:10,version:linkedRoot.version}});
 });
 await openBadge('.notion-source-banner');await page.locator('[data-sync-manage]').click();await page.getByRole('dialog').last().locator('[data-sync-conflict]').click();
 const comparison=page.getByRole('dialog').last();
 await expect(comparison.locator('[data-conflict-error]')).toContainText('Notion временно недоступен');expect(decisions).toHaveLength(0);
 failFresh=false;await comparison.locator('[data-review-retry]').click();await expect(comparison.locator('pre').last()).toContainText('Текст из Notion');await expect(comparison.locator('[data-accept-source]')).toBeEnabled();await expect(comparison.locator('[data-send-local]')).toBeEnabled();
 await expect(comparison.locator('[data-keep-copy]')).toBeChecked();
 await comparison.screenshot({path:'artifacts/drg-62/after-web-recovery.png'});
 await page.setViewportSize({width:390,height:1000});await comparison.screenshot({path:'artifacts/drg-62/after-webapp-recovery.png'});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await comparison.locator('[data-recovery-page]').selectOption('blocked-note-001');await expect(comparison.locator('[data-send-local]')).toBeDisabled();await expect(comparison.locator('[data-accept-source]')).toBeEnabled();
 await comparison.screenshot({path:'artifacts/drg-62/after-webapp-recovery-blocked.png'});
 await comparison.locator('[data-recovery-page]').selectOption(linkedRoot.note_id);await expect(comparison.locator('[data-send-local]')).toBeEnabled();
 await comparison.locator('[data-keep-copy]').uncheck();await comparison.locator('[data-reload-review]').click();await expect(comparison.locator('[data-keep-copy]')).not.toBeChecked();
 const reads=recoveryReads;await page.waitForTimeout(2200);expect(recoveryReads).toBe(reads);expect(decisions).toHaveLength(0);
 await comparison.locator('[data-accept-source]').click();await expect(comparison.getByRole('status')).toContainText('Выбранная страница восстановлена');expect(decisions[0].direction).toBe('pull');expect(decisions[0].keep_copy).toBe(false);expect(await comparison.locator('a').count()).toBe(0);
 await comparison.getByRole('button',{name:'Закрыть',exact:true}).click();await page.getByRole('dialog').last().locator('[data-sync-conflict]').click();
 const send=page.getByRole('dialog').last();await expect(send.locator('[data-send-local]')).toBeEnabled();await expect(send.locator('[data-keep-copy]')).toBeChecked();await send.locator('[data-send-local]').click();await expect(send.locator('a')).toBeVisible();expect(decisions[1].direction).toBe('push');expect(decisions[1].keep_copy).toBe(true);
 await send.getByRole('button',{name:'Закрыть',exact:true}).click();await page.getByRole('dialog').last().getByRole('button',{name:'Закрыть',exact:true}).click();await page.setViewportSize({width:1280,height:900});
 await page.unrouteAll({behavior:'wait'});
 const anchor=await api('/notes',{title:'Обычная папка',body:'Сохранённый текст'});
 await page.reload();await expect(page.locator('.notion-source-banner')).toBeVisible();
 await page.locator('#page-actions').click();await expect(page.locator('[data-page-action=move-down]')).toBeVisible();
 await page.getByRole('dialog').last().screenshot({path:'artifacts/drg-62/after-webapp-reorder-menu.png'});
 await page.locator('[data-page-action=move-down]').click();
 await expect.poll(async()=>{const items=await api('/notes?catalog=true',undefined,'GET');return items.find(n=>n.id===linkedRoot.note_id).position>items.find(n=>n.id===anchor.id).position;}).toBe(true);
 await expect(page.locator('#page-title')).toBeDisabled();
 // A background pause must update the banner without replacing a dirty editor.
 // The failed save is retained in the existing encrypted per-account draft.
 let stoppedWrite=false;const writePattern='**/api/notes**',notePath='/api/notes/'+linkedRoot.note_id;
 await page.route(writePattern,async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  if(path===notePath&&request.method()==='PUT'){stoppedWrite=true;await route.fulfill({status:423,json:{detail:'Синхронизация приостановлена.'}});return;}
  if(request.method()!=='GET'||![notePath,'/api/notes'].includes(path)){await route.continue();return;}
  const response=await route.fetch(),body=await response.json();
  const rewrite=note=>note.id===linkedRoot.note_id?{...note,cover:'night',cover_url:null,role:stoppedWrite?'viewer':'editor',manage:true,notion_sync:{...note.notion_sync,mode:'duplex',status:stoppedWrite?'paused':'active',read_only:stoppedWrite,error:stoppedWrite?'notionUnsupportedWrite':null}}:note;
  try{await route.fulfill({response,json:Array.isArray(body)?body.map(rewrite):rewrite(body)});}catch(error){if(!request.failure()&&!page.isClosed())throw error;}
 });
 await page.reload();await expect(page.locator('#page-title')).toBeEnabled();
 const editable=page.locator('#block-editor [contenteditable="true"]').first();await editable.click();await editable.press('End');await page.keyboard.insertText(' PRIVATE_NOTION_DRAFT_62');
 await expect(page.locator('[data-note-recovery]')).toBeVisible({timeout:15000});
 console.log('Notion UI: paused write retained an encrypted draft.');
 await expect(page.locator('.notion-source-banner')).toContainText('Приостановлено');
 await expect(page.locator('.notion-source-banner')).not.toContainText('Обновляется автоматически');
 await openBadge('[data-note-recovery]');await page.locator('[data-recovery-view]').click();await expect(page.getByRole('dialog').last().locator('textarea')).toHaveValue(/PRIVATE_NOTION_DRAFT_62/);
 await page.getByRole('dialog').last().getByRole('button',{name:'Закрыть',exact:true}).click();await page.locator('[data-note-recovery]>summary').click();
 await page.setViewportSize({width:1280,height:900});await expect(page.locator('.page-cover-wrap [data-note-recovery]>summary')).toBeVisible();await page.screenshot({path:'artifacts/drg-62/after-web-status-and-draft.png'});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/drg-62/after-webapp-paused-draft.png'});await openBadge('[data-note-recovery]');
 await page.locator('[data-recovery-open]').click();await expect(page.locator('#page-title')).toBeDisabled();await page.unrouteAll({behavior:'wait'});await page.reload();
 await openBadge('[data-note-recovery]');await page.locator('[data-recovery-view]').click();await expect(page.getByRole('dialog').last().locator('textarea')).toHaveValue(/PRIVATE_NOTION_DRAFT_62/);await page.getByRole('dialog').last().getByRole('button',{name:'Закрыть',exact:true}).click();
 console.log('Notion UI: encrypted draft survived reload.');
 await openBadge('.notion-source-banner');await page.locator('[data-sync-copy]').click();await expect(page.locator('#page-title')).toBeEnabled({timeout:15000});await expect(page.locator('.notion-source-banner')).toHaveCount(0);
 const chain=[];
 for(let level=1;level<=4;level++)chain.push(await api('/notes',{title:'Уровень '+level,body:'Текст уровня '+level,parent_id:chain.at(-1)?.id||null}));
 await page.setViewportSize({width:390,height:844});await visit('/page/'+chain[0].id);
 for(const note of chain.slice(1)){await page.locator('.inline-page-link').filter({hasText:note.title}).click();await expect(page.locator('#page-title')).toHaveValue(note.title);}
 await page.screenshot({path:'artifacts/drg-62/before-webapp-swipe-level4.png'});
 await page.locator('#page-title').fill('Уровень 4 — правка');
 const swipe=()=>touchSequence(page,[[8,300],[45,302],[110,304]],{selector:'.notes-workspace'});
 for(const note of chain.slice(0,3).reverse()){await swipe();await expect(page.locator('#page-title')).toHaveValue(note.title);await expect(page.locator('.notes-shell')).not.toHaveClass(/mobile-notes-list/);if(note.id===chain[2].id)await page.screenshot({path:'artifacts/drg-62/after-webapp-swipe-level3.png'});}
 expect((await api('/notes/'+chain[3].id,undefined,'GET')).title).toBe('Уровень 4 — правка');
 await page.screenshot({path:'artifacts/drg-62/after-webapp-swipe-level1.png'});
 await swipe();await expect(page.locator('.notes-shell')).toHaveClass(/mobile-notes-list/);
 await visit('/page/'+chain[3].id);await expect(page.locator('#page-title')).toHaveValue('Уровень 4 — правка');
 await swipe();await expect(page.locator('#page-title')).toHaveValue(chain[2].title);
 const refreshingRoot=(await api('/account/notion-sync',undefined,'GET')).roots[0];await visit('/page/'+refreshingRoot.note_id);
 await expect(page.locator('#block-editor')).toContainText('Здесь собраны');await openBadge('.notion-source-banner');const updated=await api('/browser/notion-update',{});
 await expect(page.locator('#block-editor')).toContainText(updated.text,{timeout:20000});
 expect(await page.locator('.notion-source-banner').evaluate(el=>el.open)).toBe(true);
 const databasePreview=await api('/account/notion-sync/preview',{connection_id:'fixture-connection',source:'88888888-8888-4888-8888-888888888888'});
 const databaseStatus=async()=>(await api('/account/notion-sync',undefined,'GET')).roots.find(root=>root.id===databasePreview.id);
 await expect.poll(async()=>(await databaseStatus()).status,{timeout:30000}).toBe('ready');
 const readyDatabase=await databaseStatus();await api('/account/notion-sync/roots/'+databasePreview.id+'/confirm',{preview_hash:readyDatabase.preview_hash});
 await expect.poll(async()=>(await databaseStatus()).progress?.copied_pages,{timeout:30000}).toBe(4);
 expect((await databaseStatus()).status).toBe('importing');
 const copiedDatabase=await databaseStatus();const databaseContainer=await api('/notes/'+copiedDatabase.note_id,undefined,'GET');expect(databaseContainer.blocks[0].type).toBe('pageLink');await visit('/page/'+copiedDatabase.note_id);
 await expect(page.locator('#page-title')).toHaveValue('Заметки');
 await expect(page.locator('.inline-page-link').filter({hasText:'Заметки'})).toBeVisible();
 await page.screenshot({path:'artifacts/drg-62/before-webapp-database-navigation.png'});
 await api('/browser/notion-presentation',{});
 const db=page.locator('#database-container');await expect(db.locator('.note-database')).toHaveAttribute('data-layout','list',{timeout:20000});
 expect((await api('/notes/'+copiedDatabase.note_id,undefined,'GET')).kind).toBe('database');
 const collapsed=await api('/notes?catalog=true',undefined,'GET');expect(collapsed.some(note=>note.id===copiedDatabase.note_id)).toBe(false);
 expect(collapsed.find(note=>note.navigation_aliases?.includes(copiedDatabase.note_id)).parent_id).toBe(null);
 await page.screenshot({path:'artifacts/drg-62/after-webapp-database-navigation.png'});
 await expect(db.getByRole('tab',{name:'Заметки',exact:true})).toHaveAttribute('aria-selected','true');
 await expect(db.locator('.db-list-row')).toHaveCount(1);await expect(db.locator('.db-list-row')).toContainText('Текущая запись');
 await db.getByRole('tab',{name:'Архив',exact:true}).click();await expect(db.locator('.db-list-row')).toHaveCount(1);await expect(db.locator('.db-list-row')).toContainText('Архивная запись');
 await page.screenshot({path:'artifacts/drg-62/after-webapp-database-views.png'});
 await expect(page.locator('#page-title')).toHaveValue('Заметки');await page.screenshot({path:'artifacts/drg-62/before-webapp-partial-title.png'});
 const liveDatabase=await api('/browser/notion-database-update',{});await expect(page.locator('#page-title')).toHaveValue(liveDatabase.title,{timeout:20000});
 expect((await databaseStatus()).status).toBe('importing');await expect(db.locator('.db-list-row')).toHaveCount(1);
 await page.screenshot({path:'artifacts/drg-62/after-webapp-partial-title.png'});
 await expect(page.locator('#page-title')).toBeDisabled();await expect(db.locator('#db-add')).toHaveCount(0);
 // Leave the database through the app before the separate full-page login
 // reload checks, so WebKit does not report unloading fetches as CORS errors.
 await page.locator('#notes-main-menu').click();await expect(db.locator('.note-database')).toHaveCount(0);
 await page.setViewportSize({width:1280,height:900});
 const currentMe=await api('/me',undefined,'GET');let shownRole='admin';
 await page.route('**/api/me',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...currentMe,role:shownRole})}));
 for(const role of ['admin','member']){
  shownRole=role;await visit('/');await page.locator('.workspace').waitFor();if(await page.locator('#notes-main-menu').count())await page.locator('#notes-main-menu').click();await page.getByRole('button',{name:'Настройки',exact:true}).first().click();await page.locator('#open-personal-data').waitFor({state:'attached'});if(!await page.locator('#open-personal-data').isVisible())await page.getByText('Личные данные',{exact:true}).first().click();await page.locator('#open-personal-data').click();await expect(page.getByRole('button',{name:'Синхронизация с Notion',exact:true})).toHaveCount(0);
 }
 expect(errors).toEqual([]);console.log('Notion sync browser flow passed: preview/import, read-only and owner-only UI, independent copy, 390/320 layouts, stable issue list, local root order, four-level swipe with saved edits, live source text, database views and filters, no closed-dialog polling or JavaScript errors.');

}finally{await browser?.close();server.kill('SIGTERM');await Promise.race([exited,new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null){server.kill('SIGKILL');await exited;}await rm(dir,{recursive:true,force:true});}
