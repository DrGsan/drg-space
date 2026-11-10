import {appSection,newNote} from './notes-ui.js';
import {expect} from '@playwright/test';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
async function api(page,path,method='GET',data){
 const r=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');const res=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:res.status,data:await res.json()};},{path,method,data});expect(r.status,JSON.stringify(r.data)).toBe(200);return r.data;
}
export async function communityFlow(page,browser){
 await appSection(page, 'Настройки');
 await page.getByRole('checkbox',{name:'Модуль: Файлы',exact:true}).check();
 await page.getByRole('button',{name:'Сохранить модули',exact:true}).click();
 await appSection(page, 'Файлы');
 await page.locator('[data-new-folder]').click();await page.getByLabel('Название папки',{exact:true}).fill('Browser documents');await page.getByLabel('Название папки',{exact:true}).press('Enter');
 await page.locator('.file-open').filter({hasText:'Browser documents'}).click();
 await page.locator('[data-upload-files]').setInputFiles({name:'browser.md',mimeType:'text/markdown',buffer:Buffer.from('Original browser text')});
 await expect(page.locator('.file-list .file-open').filter({hasText:'browser.md'})).toBeVisible();
 // Browser directory input sends real local files and preserves relative paths.
 const staging=await mkdtemp(join(tmpdir(),'drg-directory-browser-')),local=join(staging,'Local project');
 try{
  await mkdir(join(local,'nested','deep'),{recursive:true});await writeFile(join(local,'readme.txt'),'Local root content');await writeFile(join(local,'nested','deep','readme.txt'),'Nested content');
  await page.locator('[data-upload-directory]').setInputFiles(local);
  await expect(page.locator('[data-folder-upload-status]')).toContainText('Готово. Загружено объектов: 2 из 2');
  await page.locator('[data-upload-close]').click();await page.locator('.file-open').filter({hasText:'Local project'}).click();
  await expect(page.locator('.file-list')).toContainText('nested');await expect(page.locator('.file-list')).toContainText('readme.txt');
  await page.locator('.file-open').filter({hasText:'nested'}).click();await page.locator('.file-open').filter({hasText:'deep'}).click();
  await page.locator('.file-open').filter({hasText:'readme.txt'}).click();await expect(page.getByLabel('Содержимое файла')).toHaveValue('Nested content');await page.getByRole('dialog').locator('.modal-close').click();
  await page.locator('.file-crumbs [data-folder]').filter({hasText:'Browser documents'}).click();
 }finally{await rm(staging,{recursive:true,force:true});}
 // The File System Access picker can also preserve empty directories.
 await page.evaluate(()=>{
  window.__originalDirectoryPicker=window.showDirectoryPicker;
  const empty={kind:'directory',async *entries(){}};
  window.showDirectoryPicker=async()=>({name:'Native folder',kind:'directory',async *entries(){yield ['empty',empty];yield ['native.txt',{kind:'file',getFile:async()=>new File(['Native picker content'],'native.txt',{type:'text/plain'})}];}});
 });
 await page.locator('[data-upload-folder]').click();await expect(page.locator('[data-folder-upload-status]')).toContainText('Готово. Загружено объектов: 1 из 1');await page.locator('[data-upload-close]').click();await page.locator('.file-open').filter({hasText:'Native folder'}).click();
 await page.locator('.file-open').filter({hasText:'empty'}).click();await expect(page.locator('.file-empty strong')).toHaveText('Папка пока пуста.');
 await page.locator('.file-crumbs [data-folder]').filter({hasText:'Browser documents'}).click();await page.evaluate(()=>window.showDirectoryPicker=window.__originalDirectoryPicker);
 await appSection(page, 'Управление');await expect(page.locator('[data-view=admin]')).toHaveClass(/active/);await expect(page.locator('[data-view=settings]')).not.toHaveClass(/active/);
 expect(await page.locator('[data-view=admin]').evaluate(el=>el.previousElementSibling.dataset.view)).toBe('settings');
 await appSection(page, 'Настройки');await expect(page.locator('#view-content #open-admin, #view-content #invite-form')).toHaveCount(0);await appSection(page, 'Файлы');
 await page.locator('.file-open').filter({hasText:'browser.md'}).click();
 await page.getByRole('dialog').getByLabel('Содержимое файла').fill('Changed in browser');await page.getByRole('dialog').getByRole('button',{name:'Сохранить файл'}).click();
 await expect(page.locator('[data-file-save-status]')).toHaveText('Файл сохранён');await page.getByRole('dialog').locator('.modal-close').click();
 await page.locator('.file-row').filter({hasText:'browser.md'}).locator('[data-file-menu]').click();await page.locator('[data-file-share]').click();
 await page.getByRole('dialog').getByLabel('Права доступа').selectOption('edit');await page.getByRole('dialog').getByLabel('Срок',{exact:true}).fill('13');await page.getByRole('dialog').getByLabel('Единица').selectOption('minutes');await page.getByRole('dialog').getByRole('button',{name:'Создать ссылку'}).click();
 const url=await page.getByLabel('Ссылка на файл').inputValue();await page.getByRole('dialog').locator('.modal-close').click();
 const context=await browser.newContext(),publicPage=await context.newPage();const errors=[];publicPage.on('pageerror',e=>errors.push(e.message));
 // Slow grants must not expose a clickable file before its handler is ready.
 let releaseGrant,grantStarted,grantForwarded;
 const heldGrant=new Promise(resolve=>releaseGrant=resolve),startedGrant=new Promise(resolve=>grantStarted=resolve);
 const grantPath='**/api/shared-files/*/edit-session';
 await publicPage.route(grantPath,async route=>{grantStarted();grantForwarded=heldGrant.then(()=>route.continue());await grantForwarded;},{times:1});
 try{await publicPage.goto(url);await startedGrant;await expect(publicPage.locator('[data-shared-open]')).toHaveCount(0);}
 finally{releaseGrant();await grantForwarded;await publicPage.unroute(grantPath);}
 await publicPage.locator('[data-shared-open]').click();await publicPage.getByRole('dialog').getByLabel('Содержимое файла').fill('Shared editor works');await publicPage.getByRole('dialog').getByRole('button',{name:'Сохранить файл'}).click();await expect(publicPage.locator('[data-file-save-status]')).toHaveText('Файл сохранён');
 await publicPage.getByRole('dialog').locator('.modal-close').click();await publicPage.screenshot({path:'artifacts/shared-files-desktop.png',fullPage:true});expect(errors).toEqual([]);await context.close();
 await newNote(page);await page.getByLabel('Название заметки').fill('Decorated page');await expect(page.locator('#page-save-status')).toHaveText('Сохранено');
 await page.getByRole('button',{name:'Изменить иконку'}).click();await expect.poll(()=>page.locator('.emoji-option').count()).toBeGreaterThan(100); // Full first category, not a small hand-written list.
 await page.getByLabel('Поиск эмодзи').fill('кот');await expect.poll(()=>page.locator('.emoji-option').count()).toBeGreaterThan(0);await page.locator('.emoji-option').first().click();
 await page.getByRole('button',{name:'Изменить иконку'}).click();await page.getByRole('button',{name:'Загрузить иконку'}).click();await page.locator('[data-icon-file]').setInputFiles('tests/fixtures/blue.png');await expect(page.locator('#change-icon img')).toBeVisible();
 await page.route('**/api/note-cover-search?**',r=>r.fulfill({json:{items:[],provider:'commons'}}));
 await page.getByRole('button',{name:'Добавить обложку'}).click();await expect(page.getByLabel('Каталог')).toHaveValue('commons');await page.getByRole('dialog').getByRole('button',{name:'Загрузить',exact:true}).click();await page.locator('[data-cover-file]').setInputFiles('tests/fixtures/blue.png');await expect(page.locator('.page-cover img')).toBeVisible();await page.unroute('**/api/note-cover-search?**');
 await page.screenshot({path:'artifacts/page-assets-desktop.png',fullPage:true});await page.reload();await expect(page.locator('.page-cover img')).toBeVisible();
 // A notification invite appears on the dashboard even when social isn't opened.
 const invite=await api(page,'/invites','POST',{login:'inboxfriend'}),guestContext=await browser.newContext({baseURL:'http://127.0.0.1:8001'}),guest=await guestContext.newPage();
 await guest.goto('/');const register=await guestContext.request.post('/api/register',{headers:{Origin:'http://127.0.0.1:8001'},data:{login:'inboxfriend',password:'Inbox-friend-password-123',invite:invite.url.split('/').at(-1)}});expect(register.status()).toBe(200);const enrollment=await register.json();
 const skip=await guestContext.request.post('/api/2fa/skip',{headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':enrollment.csrf}});expect(skip.status()).toBe(200);await guest.reload();
 await expect(guest.getByRole('heading',{name:'Привет, inboxfriend.'})).toBeVisible();await api(page,'/social/friends','POST',{login:'inboxfriend'});
 await expect(guest.locator('#open-inbox .notification-count')).toBeVisible();await guest.locator('#open-inbox').click();expect(await guest.locator('.modal-overlay').evaluate(el=>Math.round(el.getBoundingClientRect().width))).toBe(guest.viewportSize().width);await expect(guest.getByRole('dialog')).toContainText('demoowner приглашает');await guest.getByRole('dialog').getByRole('button',{name:'Принять',exact:true}).click();await expect(guest.locator('[data-inbox-accept]')).toHaveCount(0);await guest.screenshot({path:'artifacts/notifications-desktop.png',fullPage:true});await guestContext.close();
 // A computed property can be configured and previewed through the property editor.
 const db=await api(page,'/databases','POST',{title:'Browser formulas'});const current=await api(page,'/databases/'+db.id);
 await api(page,'/databases/'+db.id,'PUT',{version:current.version,properties:[{id:'number',name:'Цена',type:'number'},{id:'total',name:'Итого',type:'formula',expression:'prop("Цена") * 2'}],views:[{id:'table',name:'Таблица',type:'table'}]});await api(page,'/databases/'+db.id+'/records','POST',{title:'Product',properties:{number:11}});
 await page.goto('/page/'+db.id);await expect(page.locator('.db-table')).toContainText('22');await page.getByRole('button',{name:'Настройки базы',exact:true}).click();await expect(page.locator('[data-formula]')).toHaveValue('prop("Цена") * 2');await page.locator('[data-preview-formula]').click();await expect(page.locator('[data-formula-result]')).toContainText('22');await page.getByRole('dialog').locator('.modal-close').click();
}
