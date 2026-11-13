import {test,expect} from '@playwright/test';
import {createHmac} from 'node:crypto';
function totp(secret){const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let bits='';for(const char of secret)bits+=alphabet.indexOf(char).toString(2).padStart(5,'0');const bytes=[];for(let index=0;index+8<=bits.length;index+=8)bytes.push(parseInt(bits.slice(index,index+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
test.describe.configure({mode:'serial'});
let state,csrf,accountId,noteId,publicToken;
const title='Личная страница <img src=x onerror="window.badTranslation=true"> & English';
const body='# Не переводить содержимое\nEnglish and 日本語';
const origin='http://127.0.0.1:8002';
async function json(response){expect(response.ok(),await response.text()).toBeTruthy();return response.json();}
test.beforeAll(async({request})=>{
 const headers={Origin:origin};
 const setup=await json(await request.post('/api/setup',{headers,data:{login:'language-owner',password:'Localization-password-123',token:'browser-test-setup-key'}}));csrf=setup.csrf;
 headers['X-CSRF-Token']=csrf;
 const enrollment=await json(await request.post('/api/2fa/enroll',{headers,data:{password:'Localization-password-123'}}));
 await json(await request.post('/api/2fa/confirm',{headers,data:{code:totp(enrollment.secret)}}));
 const activated=await json(await request.post('/api/2fa/activate',{headers,data:{saved_recovery_codes:true}}));csrf=activated.csrf;headers['X-CSRF-Token']=csrf;
 const me=await json(await request.get('/api/me'));accountId=me.id;
 noteId=(await json(await request.post('/api/notes',{headers,data:{title,body}}))).id;
 publicToken=(await json(await request.post('/api/notes/'+noteId+'/shares',{headers,data:{mode:'public',permission:'view'}}))).token;
 state=await request.storageState();
});
async function signedIn(page){await page.context().addCookies(state.cookies);await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));}
async function selectAccountLanguage(page,value){
 await page.locator('.sidebar nav,#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu').first().waitFor({state:'attached'});
 const menu=page.locator('#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu');
 if(await menu.count()){if(!await menu.first().isVisible())await page.locator('#toggle-pages-sidebar,[data-toggle-tasks-sidebar],[data-chat-back]').first().click();await menu.first().click();}
 await page.locator('[data-view="settings"]').click();
 const row=page.locator('[data-preference-key="language"]');
 if(!await row.evaluate(element=>element.open))await row.locator('summary').click();
 await row.locator('select').selectOption(value);
 await row.locator('button[type="submit"]').click();
 await expect(page.locator('html')).toHaveAttribute('lang',value==='system'?'ru':value);
 await expect(page.locator('#language-settings select')).toHaveValue(value);
}
test('account language switches both ways, keeps the section and preserves stored content',async({page})=>{
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await signedIn(page);await page.goto('/');
 await expect(page.locator('html')).toHaveAttribute('lang','ru');
 await selectAccountLanguage(page,'en');
 await expect(page.locator('[data-current-view]')).toHaveAttribute('data-current-view','settings');
 await expect(page.locator('[data-view="notes"] .nav-text')).toHaveText('Notes');
 await expect(page.locator('[data-view="chat"] .nav-text')).toHaveText('Messages');
 await expect(page.locator('[data-view="files"] .nav-text')).toHaveText('Files');
 await page.goto('/page/'+noteId);
 await expect(page.locator('#page-title')).toHaveValue(title);
 await expect(page.locator('.bn-editor')).toContainText('Не переводить содержимое');
 expect(await page.evaluate(()=>window.badTranslation)).toBeUndefined();
 await page.goto('/');await selectAccountLanguage(page,'ru');
 const saved=await page.evaluate(async id=>(await(await fetch('/api/notes/'+id)).json()),noteId);
 expect(saved.title).toBe(title);expect(saved.body).toBe(body);
 expect(errors).toEqual([]);
});
test('public links select English before sign-in and keep access permissions and user content',async({browser})=>{
 const context=await browser.newContext({locale:'en-US'}),page=await context.newPage();
 await page.goto(origin+'/share/'+publicToken);
 await expect(page.locator('html')).toHaveAttribute('lang','en');
 await expect(page.locator('[data-public-language] select').first()).toHaveValue('system');
 await expect(page.locator('#shared-content')).toContainText('Не переводить содержимое');
 expect(await page.evaluate(()=>window.badTranslation)).toBeUndefined();
 await page.locator('[data-public-language] select').first().selectOption('ru');
 await expect(page.locator('html')).toHaveAttribute('lang','ru');
 await expect(page.locator('#shared-content')).toContainText('Не переводить содержимое');
 const permissions=await page.evaluate(async token=>(await(await fetch('/api/shared/'+token)).json()),publicToken);
 expect(permissions.permission).toBe('view');expect(permissions.title).toBe(title);
 await context.close();
});
test('avatar categories and all presets translate in profiles and account administration',async({page})=>{
 await signedIn(page);await page.goto('/');
 const labels={
  en:{categories:['Abstract','Landscapes','Animals','Space'],items:[
   ['Loop','Pebbles','Wave','Orbit','Petals','Arches','Crystal','Spiral','Saturn'],
   ['Forest','Coast','Dunes','Mountains','Moonlit lake','Lavender','Meadow','Birch','Island'],
   ['Cat','Fox','Owl','Panda','Koala','Rabbit','Penguin','Bear','Capybara'],
   ['Earth','Saturn','Moon','Sun','Comet','Galaxy','Nebula','Distant planets','Star cluster']]},
  ru:{categories:['Абстракция','Пейзажи','Животные','Космос'],items:[
   ['Петля','Камни','Волна','Орбита','Лепестки','Арки','Кристалл','Спираль','Сатурн'],
   ['Лес','Побережье','Дюны','Горы','Лунное озеро','Лаванда','Луг','Берёза','Остров'],
   ['Кот','Лиса','Сова','Панда','Коала','Кролик','Пингвин','Медведь','Капибара'],
   ['Земля','Сатурн','Луна','Солнце','Комета','Галактика','Туманность','Далёкие планеты','Звёздное скопление']]}
 };
 async function checkGallery(gallery,language){
  const expected=labels[language];
  await expect(gallery.locator('[data-avatar-category]')).toHaveText(expected.categories);
  for(const [index,category] of ['abstract','nature','animals','space'].entries()){
   await gallery.locator('[data-avatar-category="'+category+'"]').click();
   await expect(gallery.locator('[data-avatar-preset] span')).toHaveText(expected.items[index]);
   for(const [itemIndex,label] of expected.items[index].entries())
    await expect(gallery.locator('[data-avatar-preset="'+category+'-'+(itemIndex+1)+'"]')).toHaveAccessibleName(label);
  }
 }
 for(const language of ['en','ru']){
  await selectAccountLanguage(page,language);
  const row=page.locator('[data-preference-key="profile"]');
  if(!await row.evaluate(element=>element.open))await row.locator('summary').click();
  const gallery=row.locator('[data-avatar-presets]');
  await checkGallery(gallery,language);
  await gallery.locator('[data-avatar-category="animals"]').click();await gallery.locator('[data-avatar-preset="animals-9"]').click();
  await expect(gallery.locator('[data-avatar-preset="animals-9"]')).toHaveAttribute('aria-pressed','true');
  const me=await page.context().request.get('/api/me');
  expect((await me.json()).settings.profile.avatar_preset).toBe('animals-9');
  if(language==='en')await gallery.screenshot({path:'artifacts/localization-en-avatars.png'});
  await page.locator('[data-view="admin"]').click();
  await page.locator('[data-admin-unlock] [name=password]').fill('Localization-password-123');
  await page.locator('[data-admin-unlock] button[type=submit]').click();
  const accounts=page.locator('[data-preference-key="accounts"]');
  if(!await accounts.evaluate(element=>element.open))await accounts.locator('summary').click();
  await page.locator('[data-admin-user="'+accountId+'"] [data-open-admin-user]').click();
  await page.locator('.admin-account-details [data-admin-profile]').click();
  const dialog=page.getByRole('dialog');
  await checkGallery(dialog.locator('[data-avatar-presets]'),language);
  await dialog.locator('[data-avatar-category="animals"]').click();
  await expect(dialog.locator('[data-avatar-preset="animals-9"]')).toHaveAttribute('aria-pressed','true');
  await dialog.locator('.modal-close').click();
  await page.locator('[data-view="settings"]').click();
  await json(await page.context().request.post('/api/admin/access/lock',{headers:{Origin:origin,'X-CSRF-Token':csrf}}));
 }
});
test('English mobile settings fit the screen and server errors use the interface language',async({page})=>{
 await signedIn(page);await page.addInitScript(()=>{localStorage.setItem('drg-language','en');Object.defineProperty(navigator,'standalone',{get:()=>true,configurable:true});});
 await page.context().request.put('/api/language',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{language:'en'}});
 await page.setViewportSize({width:390,height:844});await page.goto('/');
 await expect(page.locator('html')).toHaveAttribute('lang','en');
 await page.locator('[data-mobile-view="settings"]').click();
 await expect(page.locator('[data-preference-key="language"]')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.screenshot({path:'artifacts/localization-en-mobile.png',fullPage:true});
 const response=await page.evaluate(async()=>{const response=await fetch('/api/notes/nonexistent',{headers:{'Accept-Language':'en'}});return {status:response.status,body:await response.json()};});
 expect(response.status).toBe(404);expect(response.body.code).toBeTruthy();expect(response.body.detail).not.toMatch(/[А-Яа-яЁё]/);
});
test('unsupported browser languages fall back to Russian and native passkey pages are translated',async({browser})=>{
 const context=await browser.newContext({locale:'de-DE'}),page=await context.newPage();
 await page.goto(origin+'/');await expect(page.locator('html')).toHaveAttribute('lang','ru');
 await expect(page.locator('[data-public-language] select')).toBeVisible();
 await page.locator('[data-public-language] select').selectOption('en');
 await expect(page.locator('html')).toHaveAttribute('lang','en');
 await expect(page.locator('#auth-title')).toHaveText('Sign in to your world.');
 await page.goto(origin+'/native/passkey-create');
 await expect(page.locator('h1')).toHaveText('Add a passkey');
 await expect(page.locator('html')).toHaveAttribute('lang','en');
 await context.close();
});

test('another device changing language does not reload an editor with unsaved work',async({page})=>{
 await signedIn(page);
 await page.context().request.put('/api/language',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{language:'en'}});
 await page.addInitScript(()=>{
  localStorage.setItem('drg-language','en');
  const NativeWebSocket=window.WebSocket;
  window.__localeSockets=[];
  window.WebSocket=class extends NativeWebSocket{constructor(...args){super(...args);window.__localeSockets.push(this);}};
 });
 await page.goto('/page/'+noteId);await expect(page.locator('#page-title')).toHaveValue(title);
 await page.waitForFunction(()=>window.__localeSockets.length>0);
 await page.route('**/api/notes/'+noteId,route=>route.request().method()==='PUT'?route.fulfill({status:503,json:{detail:'Temporary test failure'}}):route.continue());
 await page.locator('#page-title').fill('Несохранённый заголовок — keep this draft');
 await page.context().request.put('/api/language',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{language:'ru'}});
 let navigations=0;page.on('framenavigated',frame=>{if(frame===page.mainFrame())navigations++;});
 const checked=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/me');
 await page.evaluate(()=>window.__localeSockets.forEach(socket=>socket.close()));await checked;
 await expect(page.locator('html')).toHaveAttribute('lang','en');
 await expect(page.locator('#page-title')).toHaveValue('Несохранённый заголовок — keep this draft');
 expect(navigations).toBe(0);
});

test('English desktop opens every configured section and keeps system labels translated',async({page})=>{
 await signedIn(page);
 await page.context().request.put('/api/language',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{language:'en'}});
 await page.addInitScript(()=>localStorage.setItem('drg-language','en'));
 await page.setViewportSize({width:1440,height:1000});await page.goto('/');
 const allowed=[title,'Не переводить содержимое','Напоминания','Володя'];
 for(const section of ['home','settings','notes','tasks','chat','files','social','expenses','admin']){
  await page.locator('.sidebar nav,#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu').first().waitFor({state:'attached'});
  const menu=page.locator('#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu');
  if(await menu.count()){if(!await menu.first().isVisible())await page.locator('#toggle-pages-sidebar,[data-toggle-tasks-sidebar],[data-chat-back]').first().click();await menu.first().click();}
  await page.locator('[data-view="'+section+'"]').click();
  await expect(page.locator('[data-current-view]')).toHaveAttribute('data-current-view',section);
  if(section==='home')await expect(page.locator('#widget-grid [data-widget]')).toHaveCount(13);
  if(section==='settings'){
   const row=page.locator('[data-preference-key=language]');if(!await row.evaluate(element=>element.open))await row.locator('summary').click();
   const modules=page.locator('[data-preference-key=modules]');if(!await modules.evaluate(el=>el.open))await modules.locator('summary').click();
   await expect(modules.locator('.module-switch').filter({has:page.locator('[name=tracker]')}).locator('strong')).toHaveText('Work');
  }
  if(section==='notes'){
   await page.locator('[data-open-page="'+noteId+'"]').first().click();
   await expect(page.locator('#page-title')).toHaveValue(title);
   await expect(page.locator('.bn-editor')).toContainText('Не переводить содержимое');
  }
  if(section==='admin'){
   await page.locator('[data-admin-unlock] [name=password]').fill('Localization-password-123');
   await page.locator('[data-admin-unlock] button[type=submit]').click();
   await expect(page.locator('[data-admin-content]')).toBeVisible();
  }
  await expect.poll(async()=>page.locator('#view-content').evaluate(root=>root.textContent.trim().length)).toBeGreaterThan(20);
  const untranslated=await page.locator('#view-content').evaluate((root,allowed)=>{
   const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),found=[];let node;
   while(node=walker.nextNode()){
    const value=node.textContent.trim(),element=node.parentElement;
    if(!/[А-Яа-яЁё]/.test(value)||allowed.some(text=>value.includes(text))||!element?.getClientRects().length||getComputedStyle(element).visibility==='hidden')continue;
    found.push(value);
   }
   return [...new Set(found)];
  },allowed);
  expect(untranslated,section+' has untranslated system labels').toEqual([]);
  if(['home','settings','notes'].includes(section))await page.screenshot({path:'artifacts/localization-en-'+section+'.png',fullPage:true});
 }
});

test('first registration with an English browser creates an English reminders list',async({page,browser})=>{
 await signedIn(page);
 const manager=page.context().request,headers={Origin:origin,'X-CSRF-Token':csrf};
 await json(await manager.post('/api/admin/access/unlock',{headers,data:{password:'Localization-password-123'}}));
 const invitation=await json(await manager.post('/api/invites',{headers,data:{login:'english-newcomer'}}));
 await json(await manager.post('/api/admin/access/lock',{headers}));
 const context=await browser.newContext({locale:'en-US'}),newPage=await context.newPage();
 try{
  await newPage.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  await newPage.goto(invitation.url);
  await expect(newPage.locator('html')).toHaveAttribute('lang','en');
  await newPage.locator('#auth-form [name=login]').fill('english-newcomer');
  await newPage.locator('#auth-form [name=password]').fill('Newcomer-password-123');
  const registered=newPage.waitForResponse(response=>response.url().endsWith('/api/register')&&response.request().method()==='POST');
  await newPage.locator('#auth-form button[type=submit]').click();
  expect((await registered).ok()).toBeTruthy();
  await newPage.locator('#skip-enrollment').click();
  await newPage.locator('[data-view=tasks]').click();
  await expect(newPage.locator('.tasks-list-name')).toHaveText(['Reminders']);
  const lists=await json(await context.request.get('/api/task-lists'));
  expect(lists).toHaveLength(1);expect(lists[0].name).toBe('Reminders');
  await selectAccountLanguage(newPage,'ru');
  await newPage.locator('[data-view=tasks]').click();
  await expect(newPage.locator('.tasks-list-name')).toHaveText(['Reminders']);
 }finally{await context.close();}
});
