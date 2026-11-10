// Real API in an isolated encrypted database; never touches a working account.
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const phase=process.env.DRG_SHARING_EXPIRY_BASELINE==='1'?'before':'after';
const out='artifacts/file-share-limits/'+engine;await mkdir(out,{recursive:true});
const origin='http://127.0.0.1:8089',directory=await mkdtemp(tmpdir()+'/drg-file-share-limits-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8089','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-file-share-limits-secret-'.repeat(3),SETUP_TOKEN:'file-share-limits-setup',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
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
 csrf=(await send('/setup',{login:'alexander',password:'File-share-limits-test-123',token:'file-share-limits-setup'})).csrf;
 const {secret}=await send('/2fa/enroll',{password:'File-share-limits-test-123'});
 const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join('');
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});
 csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;

 await send('/modules',{enabled:{files:true}},'PUT');
 const root=await send('/files/folders',{name:'Документы для передачи'});
 const upload=async(name,bytes)=>{const r=await page.request.post('/api/files/upload?parent_id='+root.id+'&name='+encodeURIComponent(name),{headers:{Origin:origin,'X-CSRF-Token':csrf},data:bytes});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 const image=await upload('Инструкция.png',await readFile('tests/fixtures/blue.png'));
 await upload('Описание.txt',Buffer.from('Shared instructions'));
 await page.goto('/');await page.locator('[data-view="files"]').click();
 if(phase==='after'){
  const catalogue=[];page.on('request',r=>{if(r.method()==='GET'&&new URL(r.url()).pathname==='/api/files')catalogue.push(r.url());});
  await expect(page.locator('[data-file-row="'+root.id+'"]')).toBeVisible();
  await page.locator('[data-select-file="'+root.id+'"]').check();catalogue.length=0;
  await page.evaluate(id=>{window.folderWorkspace=document.querySelector('.drive-panel');window.folderOriginal=document.querySelector('[data-file-row="'+id+'"]');window.folderFlicker=false;window.folderObserver=new MutationObserver(records=>{if(records.some(r=>[...r.addedNodes].some(n=>n.nodeType===1&&(n.matches('.drive-loading')||n.querySelector('.drive-loading')))))window.folderFlicker=true;});window.folderObserver.observe(document.body,{childList:true,subtree:true});},root.id);
  await page.locator('[data-new-folder]').click();await page.locator('.drive-name-input').fill('Новая папка без мерцания');await page.locator('.drive-name-input').press('Enter');
  await expect(page.locator('.file-open strong',{hasText:'Новая папка без мерцания'})).toBeVisible();expect(catalogue).toEqual([]);
  expect(await page.evaluate(()=>document.querySelector('.drive-panel')===window.folderWorkspace&&window.folderOriginal.isConnected&&!window.folderFlicker)).toBe(true);
  await expect(page.locator('[data-select-file="'+root.id+'"]').first()).toBeChecked();
  const blue=await readFile('tests/fixtures/blue.png');
  await page.locator('[data-upload-files]').setInputFiles({name:'Сохранённая миниатюра.png',mimeType:'image/png',buffer:blue});
  const uploaded=page.locator('[data-file-row]',{has:page.locator('strong',{hasText:'Сохранённая миниатюра.png'})});await expect(uploaded).toBeVisible();
  await expect(uploaded.locator('img')).toBeVisible();expect(catalogue).toHaveLength(1);await page.locator('[data-upload-close]').click();
  await uploaded.locator('img').evaluate(el=>window.uploadedThumbnail=el);catalogue.length=0;
  await page.locator('[data-upload-files]').setInputFiles([{name:'Первый.txt',mimeType:'text/plain',buffer:Buffer.from('one')},{name:'Второй.txt',mimeType:'text/plain',buffer:Buffer.from('two')}]);
  await expect(page.locator('.file-open strong',{hasText:'Второй.txt'})).toBeVisible();expect(catalogue).toHaveLength(1);
  expect(await page.evaluate(()=>document.querySelector('.drive-panel')===window.folderWorkspace&&window.folderOriginal.isConnected&&window.uploadedThumbnail.isConnected&&!window.folderFlicker)).toBe(true);
  await expect(page.locator('[data-select-file="'+root.id+'"]').first()).toBeChecked();await page.locator('[data-upload-close]').click();
  await page.evaluate(()=>window.folderObserver.disconnect());
 }
 await page.locator('[data-file-menu="'+root.id+'"]').click();await page.locator('[data-file-share]').click();
 const form=page.locator('[data-file-share-form]');await expect(form.locator('[name=auto_delete]')).not.toBeChecked();
 await form.locator('[name=duration]').fill('2');await form.locator('[name=download_limit]').fill('1');
 await form.locator('[name=auto_delete]').check();await expect(page.locator('.file-retention-confirm li')).toHaveCount(3);
 await page.locator('[data-retention-cancel]').click();await expect(form.locator('[name=auto_delete]')).not.toBeChecked();
 expect((await send('/files/'+root.id+'/retention',undefined,'GET')).files).toEqual([]);
 await form.locator('[name=auto_delete]').check();await page.locator('[data-retention-confirm]').click();
 // Mutating sharing must not remount the workspace, lose selection or refetch its catalogue.
 const catalogueRequests=[];page.on('request',r=>{if(r.method()==='GET'&&new URL(r.url()).pathname==='/api/files')catalogueRequests.push(r.url());});
 await page.evaluate(id=>{
  window.sharingWorkspace=document.querySelector('.drive-panel');window.sharingRow=document.querySelector('[data-file-row="'+id+'"]');
  window.sharingLoading=false;window.sharingObserver=new MutationObserver(records=>{if(records.some(r=>[...r.addedNodes].some(n=>n.nodeType===1&&(n.matches('.drive-loading')||n.querySelector('.drive-loading')))))window.sharingLoading=true;});
  window.sharingObserver.observe(document.body,{childList:true,subtree:true});
 },root.id);
 const expectStableWorkspace=async()=>{
  expect(await page.evaluate(()=>document.querySelector('.drive-panel')===window.sharingWorkspace&&window.sharingRow.isConnected&&!window.sharingLoading)).toBe(true);
  await expect(page.locator('[data-select-file="'+root.id+'"]').first()).toBeChecked();expect(catalogueRequests).toEqual([]);
 };
 await form.locator('[type=submit]').click();await expect(page.locator('[data-file-share-list] .share-row')).toHaveCount(1);
 await expectStableWorkspace();await expect(page.locator('[data-file-row="'+root.id+'"] .drive-shared')).toHaveCount(1);
 await expect(page.locator('[data-file-retention-list] .share-row')).toHaveCount(3);
 const shared=await page.locator('[data-file-share-result] input').inputValue(),token=new URL(shared).pathname.split('/').at(-1);
 await page.screenshot({path:out+'/owner.png'});
 const englishContext=await browser.newContext({locale:'en-GB',baseURL:origin,viewport:{width:320,height:900},colorScheme:'dark'}),english=await englishContext.newPage();
 await english.goto(shared);await expect(english.locator('[data-download-budget]')).toContainText('Downloads remaining: 1 of 1');
 expect(await english.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);await english.screenshot({path:out+'/budget-en-dark-320.png'});await englishContext.close();
 const guestContext=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1100,height:900}}),guest=await guestContext.newPage();
 await guest.goto(shared);await expect(guest.locator('[data-download-budget]')).toContainText('Осталось 1 из 1');
 for(const width of [1100,390,320]){
  await guest.setViewportSize({width,height:900});expect(await guest.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await expect(guest.locator('[data-download-budget]')).toBeVisible();await guest.screenshot({path:out+'/budget-'+width+'.png'});
 }
 await guest.setViewportSize({width:1100,height:900});
 await guest.evaluate(()=>{window.budgetPanel=document.querySelector('.drive-panel');window.budgetRow=document.querySelector('[data-file-row]');});
 await guest.locator('[data-shared-open="'+image.id+'"]').click();await expect(guest.locator('.drive-preview-content img')).toBeVisible();
 expect((await send('/files/'+root.id+'/shares',undefined,'GET'))[0].used).toBe(0);
 await guest.locator('[data-preview-close]').click();await guest.locator('[data-file-menu="'+image.id+'"]').click();
 const pendingDownload=guest.waitForEvent('download');await guest.locator('[data-shared-download]').click();const downloaded=await pendingDownload;
 expect(await downloaded.failure()).toBeNull();
 await expect.poll(async()=>(await send('/files/'+root.id+'/shares',undefined,'GET'))[0].used).toBe(1);
 await expect(guest.locator('[data-download-budget]')).toContainText('исчерпан');
 await expect(guest.locator('.shared-drive-download')).toHaveAttribute('aria-disabled','true');
 expect(await guest.evaluate(()=>window.budgetPanel===document.querySelector('.drive-panel')&&window.budgetRow.isConnected)).toBe(true);

 await guest.locator('[data-shared-open="'+image.id+'"]').click();await expect(guest.locator('.drive-preview-dialog')).toHaveCount(0);
 await guest.screenshot({path:out+'/exhausted.png'});
 // Raising the cap retains the used count and enables a new whole-folder ZIP.
 await page.locator('[data-limit]').click();await page.locator('[data-limit-form] [name=limit]').fill('2');await page.locator('[data-limit-form] button').click();
 await expect(page.locator('[data-file-share-list]')).toContainText('Использовано: 1');
 await expectStableWorkspace();
 await guest.reload();const zipPromise=guest.waitForEvent('download');await guest.locator('.shared-drive-download').click();expect(await (await zipPromise).failure()).toBeNull();
 await expect.poll(async()=>(await send('/files/'+root.id+'/shares',undefined,'GET'))[0].used).toBe(2);
 await guestContext.close();
 page.once('dialog',dialog=>dialog.accept());await page.locator('[data-retention-off="'+image.id+'"]').click();await expect(page.locator('[data-file-retention-list] .share-row')).toHaveCount(2);
 page.once('dialog',dialog=>dialog.accept());await page.locator('[data-retention-off="'+root.id+'"]').click();await expect(page.locator('[data-file-retention-list] .share-row')).toHaveCount(1);
 await expect(page.locator('[data-file-row="'+root.id+'"] .drive-temporary')).toHaveCount(0);
 expect((await send('/files/'+root.id+'/shares',undefined,'GET'))[0].token).toBe(token);
 await expectStableWorkspace();await page.evaluate(()=>window.sharingObserver.disconnect());
 // Removing a file's timer updates its badge without replacing its row or thumbnail.
 const scheduled=(await send('/files/'+root.id+'/retention',undefined,'GET')).files[0];
 await page.locator('.modal-overlay .modal-close').click();await page.locator('[data-open-file="'+root.id+'"]').click();
 const scheduledRow=page.locator('[data-file-row="'+scheduled.id+'"]');await expect(scheduledRow.locator('.drive-temporary')).toHaveCount(1);
 await scheduledRow.locator('[data-file-menu]').click();await page.locator('[data-file-share]').click();
 await page.evaluate(id=>{window.sharingRow=document.querySelector('[data-file-row="'+id+'"]');window.sharingThumbnail=window.sharingRow.querySelector('img');window.sharingWorkspace=document.querySelector('.drive-panel');},scheduled.id);
 catalogueRequests.length=0;page.once('dialog',dialog=>dialog.accept());await page.locator('[data-retention-off]').click();
 await expect(scheduledRow.locator('.drive-temporary')).toHaveCount(0);
 expect(await page.evaluate(()=>window.sharingRow.isConnected&&document.querySelector('.drive-panel')===window.sharingWorkspace&&window.sharingRow.querySelector('img')===window.sharingThumbnail)).toBe(true);expect(catalogueRequests).toEqual([]);
 await page.locator('.modal-overlay .modal-close').click();await page.locator('[data-files-view="shared"]').click();
 const sharedRow=page.locator('[data-file-row="'+root.id+'"]');
 if(phase==='after'){await expect(sharedRow.locator('.drive-expiry')).toBeVisible();await expect(sharedRow.locator('.drive-expiry')).not.toContainText('Invalid');}
 for(const width of [1440,1100,800,390,320]){
  await page.setViewportSize({width,height:900});if(phase==='after')await expect(sharedRow.locator('.drive-expiry')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  if(phase==='after'){const rowBox=await sharedRow.boundingBox(),expiryBox=await sharedRow.locator('.drive-expiry').boundingBox();expect(expiryBox.x+expiryBox.width).toBeLessThanOrEqual(rowBox.x+rowBox.width+1);}
  await page.screenshot({path:out+'/shared-expiry-'+phase+'-'+width+'.png'});
 }
 await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option="grid"]').click();
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:900});if(phase==='after')await expect(sharedRow.locator('.drive-expiry')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:out+'/shared-expiry-grid-'+phase+'-'+width+'.png'});
 }
 await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option="list"]').click();
 await page.setViewportSize({width:1440,height:1100});
 await page.locator('[data-file-menu="'+root.id+'"]').click();await page.locator('[data-file-share]').click();
 await expect(page.locator('[data-revoke-file-share]')).toHaveCount(1);
 await page.evaluate(()=>window.sharingWorkspace=document.querySelector('.drive-panel'));catalogueRequests.length=0;
 // A new unlimited link changes the aggregate date; revoking it restores the finite date.
 await page.locator('[data-file-share-form] [type=submit]').click();await expect(page.locator('[data-file-share-list] .share-row')).toHaveCount(2);
 if(phase==='after')await expect(sharedRow.locator('.drive-expiry')).toContainText('Без срока');
 const unlimited=new URL(await page.locator('[data-file-share-result] input').inputValue()).pathname.split('/').at(-1);
 const unlimitedContext=await browser.newContext({locale:'ru-RU',baseURL:origin}),unlimitedGuest=await unlimitedContext.newPage();
 await unlimitedGuest.goto('/f/'+unlimited);await expect(unlimitedGuest.locator('.shared-drive-download')).toBeVisible();
 await expect(unlimitedGuest.locator('[data-download-budget]')).toBeHidden();await expect(unlimitedGuest.locator('[data-download-hint]')).toBeHidden();await unlimitedContext.close();

 await page.locator('[data-revoke-file-share="'+unlimited+'"]').click();await expect(page.locator('[data-file-share-list] .share-row')).toHaveCount(1);
 if(phase==='after')await expect(sharedRow.locator('.drive-expiry')).not.toContainText('Без срока');
 expect(catalogueRequests).toEqual([]);
 await page.locator('[data-revoke-file-share]').click();await expect(page.locator('[data-file-share-list] .share-row')).toHaveCount(0);
 await expect(page.locator('[data-file-row="'+root.id+'"]')).toHaveCount(0);await expect(page.locator('.drive-empty')).toBeVisible();
 expect(await page.evaluate(()=>document.querySelector('.drive-panel')===window.sharingWorkspace)).toBe(true);expect(catalogueRequests).toEqual([]);
 expect(errors).toEqual([]);console.log('File shares real API '+engine+' passed');
}catch(error){if(browser)for(const c of browser.contexts())for(const p of c.pages()){await p.screenshot({path:out+'/failure-'+c.pages().indexOf(p)+'.png'});console.error((await p.locator('body').innerText()).slice(-2200));}throw error;}
finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
