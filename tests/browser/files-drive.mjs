import {driveUploadStability} from './drive-upload-stability.js';
import {driveUploadRetry} from './drive-upload-retry.js';
import {driveSharingFlow} from './drive-sharing-flow.js';
import {filesTrashFlow} from './files-trash-flow.js';
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page){
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const password='Files-drive-password-123';
  async function post(path,data,csrf=''){
    const r=await page.context().request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  }
  let csrf=(await post('/setup',{login:'driveowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await post('/2fa/enroll',{password},csrf);await post('/2fa/confirm',{code:totp(enrolled.secret)},csrf);csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  const folder=await post('/files/folders',{name:'Документы'},csrf),target=await post('/files/folders',{name:'Вложенная',parent_id:folder.id},csrf);
  await page.goto('/');await page.locator('.sidebar [data-view=files]').click();await expect(page.locator('.drive-panel')).toBeVisible();
  await expect(page.locator('.workspace')).toHaveClass(/files-open/);
  await expect(page.locator('.workspace>.sidebar,.content>.topbar')).toHaveCount(0);
  await page.locator('#files-main-menu').click();await expect(page.locator('.sidebar')).toBeVisible();
  await page.locator('.sidebar [data-view=files]').click();await expect(page.locator('.drive-panel')).toBeVisible();
  async function drop(selector,folder=false){
    await page.locator(selector).evaluate((el,folder)=>{
      const dt=new DataTransfer();dt.items.add(new File(['drop contents'],'drop.txt',{type:'text/plain'}));
      if(folder){
        const entryFile=name=>({name,isFile:true,isDirectory:false,file:ok=>ok(new File(['nested'],name,{type:'text/plain'}))});
        const dir=(name,batches)=>({name,isFile:false,isDirectory:true,createReader(){let i=0;return {readEntries:ok=>ok(batches[i++]||[])};}});
        // Multiple readEntries batches, an empty folder and a nested file.
        const root=dir('С устройства',[[entryFile('one.txt')],[dir('Пустая',[[]]),dir('Вложенная',[[entryFile('two.txt')]])]]);
        Object.defineProperty(dt,'items',{value:[{kind:'file',webkitGetAsEntry:()=>root},{kind:'file',webkitGetAsEntry:()=>dir('Ещё папка',[[]])},{kind:'file',webkitGetAsEntry:()=>entryFile('loose.txt'),getAsFile:()=>new File(['loose'],'loose.txt',{type:'text/plain'})}]});
      }
      for(const type of ['dragenter','dragover','drop'])el.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:dt}));
    },folder);
  }
  await drop('.drive-main');await expect(page.locator('.file-list')).toContainText('drop.txt').catch(async e=>{throw Error((await page.locator('body').innerText())+' '+JSON.stringify(errors),{cause:e});});
  await page.locator('[data-upload-close]').click();
  const bell=await page.locator('#open-inbox').elementHandle();
  await page.locator('[data-open-file="'+folder.id+'"]').click();
  expect(await bell.evaluate(el=>el.isConnected)).toBe(true);
  await drop('[data-file-row="'+target.id+'"]');
  await page.locator('[data-open-file="'+target.id+'"]').click();await expect(page.locator('.file-list')).toContainText('drop.txt');
  await expect(page.locator('[data-upload-close]')).toBeEnabled();await page.locator('[data-upload-close]').click();
  const uploaded=await (await page.request.get('/api/files?parent_id='+target.id)).json();const file=uploaded.items[0];
  // Native HTML drag start captures the current selection, then a breadcrumb receives it.
  await page.locator('[data-file-row="'+file.id+'"]').evaluate(el=>{const dt=new DataTransfer();window.testDrag=dt;el.dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt}));});
  await page.locator('.file-crumbs [data-folder=""]').evaluate(el=>{for(const type of ['dragover','drop'])el.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:testDrag}));});
  await expect(page.locator('.file-list')).not.toContainText('drop.txt');
  await page.locator('.file-crumbs [data-folder=""]').click();await expect(page.locator('.file-list')).toContainText('drop.txt');
  await expect(page.locator('.drive-wordmark')).toHaveText('DrG Drive');
  await expect(page.locator('[data-files-search]')).toHaveCount(0);
  await page.locator('[data-files-sort]').click();await page.locator('[data-sort-option=updated]').click();await expect(page.locator('[data-files-sort]')).toHaveText('по дате');
  await page.locator('[data-new-folder]').click();await page.getByLabel('Название папки',{exact:true}).press('Escape');
  await expect(page.locator('[data-file-row="new-folder"]')).toHaveCount(0);
  await page.locator('[data-new-folder]').click();await page.getByLabel('Название папки',{exact:true}).fill('Недопустимое/имя');await page.getByLabel('Название папки',{exact:true}).press('Enter');await expect(page.getByLabel('Название папки',{exact:true})).toBeEnabled();await page.getByLabel('Название папки',{exact:true}).fill('Временная');await page.getByLabel('Название папки',{exact:true}).press('Enter');
  await expect(page.locator('.file-list')).toContainText('Временная');
  const created=(await (await page.request.get('/api/files')).json()).items.find(x=>x.name==='Временная');
  await expect(page.locator('[data-file-row="'+created.id+'"]')).toBeVisible();
  await page.locator('[data-file-menu="'+created.id+'"]').click();await page.locator('[data-file-rename]').click();
  await page.getByLabel('Новое имя',{exact:true}).fill('Переименована');await page.getByLabel('Новое имя',{exact:true}).press('Enter');
  await expect(page.locator('[data-file-row="'+created.id+'"]')).toContainText('Переименована');
  await page.locator('[data-file-menu="'+created.id+'"]').click();await page.locator('[data-file-properties]').click();
  await expect(page.getByRole('dialog')).toContainText('Переименована');await page.locator('.modal-close').click();
  await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option=grid]').click();await expect(page.locator('.drive-panel')).toHaveAttribute('data-layout','grid');
  expect(await page.locator('.file-open strong').first().evaluate(el=>el.getBoundingClientRect().height)).toBeGreaterThan(10);
  await page.screenshot({path:'artifacts/files-drive-grid.png',fullPage:true});
  await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option=list]').click();
  await page.locator('[data-upload-files]').setInputFiles({name:'preview.png',mimeType:'image/png',buffer:await readFile('web/public/assets/icons/drg-d8-512.png')});
  await expect(page.locator('[data-folder-upload-status]')).toContainText('Готово.');await page.locator('[data-upload-close]').click();
  await page.locator('.file-open').filter({hasText:'preview.png'}).click();
  await expect(page.locator('.drive-preview-content img')).toBeVisible();expect(await page.locator('.drive-preview-content img').evaluate(img=>img.naturalWidth)).toBe(512);
  await page.locator('[data-preview-menu]').click();await page.keyboard.press('Escape');await expect(page.locator('.drive-preview-dialog')).toBeVisible();
  await page.locator('[data-preview-menu]').click();await expect(page.locator('[data-file-share]')).toBeVisible();
  await page.screenshot({path:'artifacts/files-drive-preview.png',fullPage:true});
  await page.keyboard.press('Escape');await page.locator('[data-preview-close]').click();
  await expect(page.locator('.drive-preview-dialog')).toHaveCount(0);
  await page.locator('[data-file-menu="'+created.id+'"]').click();
  await page.keyboard.press('Escape');
  const canceledDelete=page.waitForEvent('dialog'),deleteClick=page.locator('[data-delete-selected]').click();
  await (await canceledDelete).dismiss();await deleteClick;
  await expect(page.locator('[data-file-row="'+created.id+'"]')).toBeVisible();
  const image=(await (await page.request.get('/api/files')).json()).items.find(x=>x.name==='preview.png');
  await page.locator('[data-file-menu="'+image.id+'"]').click();await page.locator('[data-file-duplicate]').click();
  await expect(page.locator('.file-list')).toContainText('preview копия.png');
  const copy=(await (await page.request.get('/api/files')).json()).items.find(x=>x.name==='preview копия.png');
  expect(await (await page.request.get('/api/files/'+copy.id+'/content')).body()).toEqual(await (await page.request.get('/api/files/'+image.id+'/content')).body());
  if(await page.locator('[data-upload-close]').count())await page.locator('[data-upload-close]').click();
  await drop('.drive-main',true);await expect(page.locator('[data-folder-upload-status]')).toContainText('Готово.');
  await page.locator('[data-upload-close]').click();
  await expect(page.locator('.modal-overlay')).toHaveCount(0);
  await expect(page.locator('.file-list')).toContainText('Ещё папка');await expect(page.locator('.file-list')).toContainText('loose.txt');
  await page.locator('.file-open').filter({hasText:'С устройства'}).click();
  await expect(page.locator('.file-list')).toContainText('Пустая');await expect(page.locator('.file-list')).toContainText('Вложенная');await expect(page.locator('.file-list')).toContainText('one.txt');
  await page.locator('.file-open').filter({hasText:'Вложенная'}).click();await expect(page.locator('.file-list')).toContainText('two.txt');
  // Hold the first POST while the panel shows progress; Stop prevents the next POST.
  let releaseUpload,uploadStarted,uploadReached=false;
  const uploadHeld=new Promise(resolve=>releaseUpload=resolve),uploadReady=new Promise(resolve=>uploadStarted=resolve);
  await page.route('**/api/files/upload?name=stop-first.txt*',async route=>{uploadReached=true;uploadStarted();await uploadHeld;await route.continue();},{times:1});
  await page.locator('[data-upload-files]').setInputFiles([{name:'stop-first.txt',mimeType:'text/plain',buffer:Buffer.from('first')},{name:'stop-second.txt',mimeType:'text/plain',buffer:Buffer.from('second')}]);
  await expect.poll(()=>uploadReached).toBe(true);await uploadReady;await expect(page.locator('[data-upload-stop]')).toBeVisible();await expect(page.locator('[data-upload-close]')).toBeHidden();
  await expect(page.locator('.drive-upload-ring')).toHaveAttribute('aria-valuenow','0');
  await page.screenshot({path:'artifacts/files-drive-uploading.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'artifacts/files-drive-uploading-mobile.png',fullPage:true});await page.setViewportSize({width:1280,height:900});
  await page.locator('[data-upload-stop]').click();releaseUpload();
  await expect(page.locator('[data-folder-upload-status]')).toContainText('остановлена');
  await expect(page.locator('.file-list')).toContainText('stop-first.txt');await expect(page.locator('.file-list')).not.toContainText('stop-second.txt');
  await page.locator('[data-upload-close]').click();
  await page.route('**/api/files/upload?name=denied.txt*',route=>route.fulfill({status:413,contentType:'application/json',body:JSON.stringify({detail:'Проверка ошибки загрузки'})}),{times:1});
  await page.locator('[data-upload-files]').setInputFiles({name:'denied.txt',mimeType:'text/plain',buffer:Buffer.from('denied')});
  await expect(page.locator('[data-folder-upload-status]')).toHaveText('Проверка ошибки загрузки');await page.locator('[data-upload-close]').click();await expect(page.locator('[data-new-folder]')).toBeEnabled();
  await driveUploadStability(page);
  await driveUploadRetry(page);
  await page.locator('[data-files-view=recent]').click();await expect(page.locator('.file-list')).toContainText('two.txt');
  await page.locator('[data-files-view=shared]').click();await expect(page.locator('.file-list')).toContainText('Нет общих файлов');
  await post('/files/'+folder.id+'/shares',{mode:'public'},csrf);await page.locator('[data-files-view=shared]').click();await expect(page.locator('.file-list')).toContainText('Документы');
  await page.locator('[data-files-view=active]').click();
  await driveSharingFlow(page,csrf,origin);
  // Touch-accessible move dialog.
  const rootListing=await (await page.request.get('/api/files')).json();const loose=rootListing.items.find(x=>x.name==='drop.txt');
  await page.locator('[data-file-menu="'+loose.id+'"]').click();await page.locator('[data-file-move]').click();await page.locator('[data-move-to="'+folder.id+'"]').click();await page.locator('[data-confirm-move]').click();await expect(page.locator('[data-file-row="'+loose.id+'"]')).toHaveCount(0);
  expect(await page.locator('.file-open').first().isEnabled()).toBe(true);
  for(const [name,body] of [['Инструкция.pdf','%PDF-1.4\nFixture'],['Таблица.csv','name,value\nExample,12'],['Пример.py','print("hello")']]){
    const response=await page.request.post('/api/files/upload?name='+encodeURIComponent(name),{headers:{Origin:origin,'X-CSRF-Token':csrf,'Content-Type':'application/octet-stream'},data:Buffer.from(body)});expect(response.ok()).toBe(true);
  }
  await page.locator('[data-files-view=active]').click();
  await expect(page.locator('.drive-thumbnail').first()).toBeVisible();
  await expect.poll(()=>page.locator('.drive-thumbnail').first().evaluate(img=>img.naturalWidth)).toBeGreaterThan(0);
  await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option=grid]').click();
  await page.screenshot({path:'artifacts/files-drive-grid.png',fullPage:true});
  await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option=list]').click();
  for(const width of [1440,1024,700,390,320]){
    await page.setViewportSize({width,height:900});
    if(width===320){await page.locator('[data-file-menu]').first().click();expect(await page.locator('.drive-popover').evaluate(el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;})).toBe(true);await page.keyboard.press('Escape');}
    for(const dark of [false,true]){
      await page.evaluate(dark=>document.documentElement.dataset.scheme=dark?'dark':'light',dark);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`artifacts/files-drive-${width}-${dark?'dark':'light'}.png`,fullPage:true});
    }
  }
  await page.setViewportSize({width:1280,height:900});
  await filesTrashFlow(page);
  await page.locator('#open-inbox').click();await expect(page.getByRole('dialog')).toContainText('Уведомления');await page.locator('.modal-close').click();
  // Installed phone app has no ordinary rail or topbar either.
  await page.setViewportSize({width:390,height:844});
  await page.context().addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:true}));
  await page.reload();await page.locator('[data-mobile-view=settings]').click();
  await page.locator('.mobile-section-links [data-mobile-open=files]').click();
  await expect(page.locator('.drive-panel')).toBeVisible();
  await expect(page.locator('.workspace>.sidebar,.content>.topbar')).toHaveCount(0);
  await expect(page.locator('.drive-workspace-header #open-inbox')).toBeVisible();
  await expect(page.locator('.drive-sidebar')).toBeHidden();
  await expect(page.locator('.workspace>.mobile-app-tabs:not(.drive-mobile-tabs)')).toBeHidden();
  await expect(page.locator('.drive-mobile-tabs')).toBeVisible();
  await expect(page.locator('#files-main-menu')).toHaveText('Назад');
  for(const view of ['recent','shared','trash','active']){
    await page.locator('[data-mobile-files-view='+view+']').click();
    await expect(page.locator('[data-mobile-files-view='+view+']')).toHaveAttribute('aria-current','page');
  }
  for(const width of [320,390,760]){
    await page.setViewportSize({width,height:844});
    const bar=await page.locator('.drive-mobile-tabs').boundingBox();
    expect(bar.y).toBeGreaterThan(650);expect(bar.height).toBeLessThanOrEqual(66);expect(bar.x+bar.width).toBeLessThanOrEqual(width);
    expect(await page.locator('.drive-mobile-tabs').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
    await page.locator('.workspace>.content').evaluate(el=>el.scrollTop=el.scrollHeight);
    const last=await page.locator('.drive-panel .drive-row').last().boundingBox();expect(last.y+last.height).toBeLessThanOrEqual(bar.y);
  }
  await page.evaluate(()=>document.documentElement.classList.add('mobile-keyboard'));await expect(page.locator('.drive-mobile-tabs')).toBeHidden();
  await page.evaluate(()=>document.documentElement.classList.remove('mobile-keyboard'));
  await page.setViewportSize({width:390,height:844});
  await page.locator('.workspace>.content').evaluate(el=>el.scrollTop=0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'artifacts/files-focused-installed-phone.png',fullPage:true});
  await page.locator('#files-main-menu').click();await expect(page.locator('.mobile-screen-title')).toHaveText('Настройки');
  await expect(page.locator('.drive-mobile-tabs')).toHaveCount(0);
  await expect(page.locator('.workspace>.mobile-app-tabs')).toBeVisible();
  expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8035',directory=await mkdtemp(tmpdir()+'/drg-files-drive-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8035','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:false,isMobile:false}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Files drive passed: device drops, recursive directories, moves, inline names, grid/list, image preview, menus, sorting, sharing, trash, desktop/mobile and themes.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
