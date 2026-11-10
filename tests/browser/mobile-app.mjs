import {touchSequence} from './touch-gesture.mjs';
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page,browser){
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.emulateMedia({colorScheme:process.env.DRG_COLOR_SCHEME||'light'});
  await page.setViewportSize({width:390,height:844});
  await page.context().addInitScript(()=>{Object.defineProperty(navigator,'standalone',{configurable:true,value:true});document.addEventListener('DOMContentLoaded',()=>{if(innerWidth<=760){document.documentElement.style.setProperty('--app-safe-top','59px');document.documentElement.style.setProperty('--app-safe-bottom','34px');}},{once:true});});
  await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  let telegramReady=false;
  await page.route('**/api/modules',async route=>{
    if(route.request().method()!=='GET')return route.continue();
    const response=await route.fetch(),body=await response.json();
    body.catalog.push({id:'family_photos',kind:'module',default_enabled:false,title:'Семейные фото'});body.enabled.family_photos=true;
    await route.fulfill({response,json:body});
  });
  await page.route('**/api/family-photos/**',route=>route.fulfill({json:route.request().url().endsWith('/groups')?
    {status:{can_send:false,worker_online:false},items:telegramReady?[{id:'tab-entry-tg',title:'Архив Telegram',kind:'export',history_complete:true,media_pending:0}]:[]}:{items:[],more:false}}));
  const password='Mobile-interface-password-123',context=page.context();
  async function post(ctx,path,data,csrf=''){
    const response=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  async function activate(ctx,csrf){
    const result=await post(ctx,'/2fa/enroll',{password},csrf);
    const confirmed=await post(ctx,'/2fa/confirm',{code:totp(result.secret)},csrf);
    return {recovery:confirmed.recovery_codes[0],csrf:(await post(ctx,'/2fa/activate',{saved_recovery_codes:true},csrf)).csrf};
  }
  let csrf=(await post(context,'/setup',{login:'mobileowner',password,token:'browser-test-setup-key'})).csrf;
  const auth=await activate(context,csrf);csrf=auth.csrf;
  await post(context,'/notes',{title:'Поездка на выходные',body:'Собрать вещи и выбрать маршрут'},csrf);
  await post(context,'/notes',{title:'Идеи для дома',body:'Свет в гостиной'},csrf);
  await post(context,'/tasks',{title:'Забрать посылку',day:new Date().toLocaleDateString('en-CA'),time:''},csrf);
  const peer=await browser.newContext({locale:'ru-RU'});
  try{
    await post(context,'/admin/access/unlock',{password},csrf);
    const invite=await post(context,'/invites',{login:'mobilefriend'},csrf);
    let peerCsrf=(await post(peer,'/register',{login:'mobilefriend',password,invite:invite.url.split('/').at(-1)})).csrf;
    peerCsrf=(await activate(peer,peerCsrf)).csrf;
    const request=await post(context,'/social/friends',{login:'mobilefriend'},csrf);
    const {space_id:sid}=await post(peer,'/social/requests/'+request.id+'/accept',{},peerCsrf);
    await post(peer,'/chat/'+sid+'/messages',{body:'Привет! Как насчёт встретиться в выходные?',client_id:'mobile-hello'},peerCsrf);
    await post(context,'/logout',{},csrf);
    await page.goto('/');
    await expect(page.locator('#auth-form')).toBeVisible();
    await expect(page.locator('[name=login]')).not.toBeFocused();
    await expect(page.locator('.auth-intro h1')).toHaveText('Войти в свой мир.');
    await page.screenshot({path:'artifacts/mobile-app-login.png',animations:'disabled'});
    await page.locator('[name=login]').fill('mobileowner');await page.locator('[name=password]').fill(password);
    await page.locator('#auth-form button[type=submit]').click();
    await expect(page.locator('[name=code]')).toBeVisible();
    await page.locator('[name=code]').fill(auth.recovery);await page.locator('#auth-form button[type=submit]').click();
    const tabs=page.locator('.mobile-app-tabs');
    await expect(tabs).toBeVisible();await expect(tabs.locator('button')).toHaveCount(4);
    const footerIsland=async()=>{
      expect(await tabs.evaluate(el=>{
        const r=el.getBoundingClientRect(),style=getComputedStyle(el),buttons=[...el.querySelectorAll('button')];
        const safeBottom=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-safe-bottom'))||0;
        return Math.abs(r.bottom-(innerHeight-Math.max(12,safeBottom-10)))<1&&r.height===64&&r.left>=12&&r.right<=innerWidth-12&&buttons.every(b=>{
          const box=b.getBoundingClientRect(),icon=b.querySelector('svg').getBoundingClientRect(),label=b.querySelector('span').getBoundingClientRect();
          return box.top>=r.top&&box.bottom<=r.bottom&&icon.left>=r.left&&icon.right<=r.right&&label.bottom<r.bottom;
        })&&style.borderRadius==='999px'&&(style.backdropFilter||style.webkitBackdropFilter).includes('blur')&&!el.contains(document.elementFromPoint(innerWidth/2,innerHeight-2));
      })).toBe(true);
    };
    await footerIsland();
    await expect(tabs.locator('[aria-current=page]')).toHaveText('Сообщения');
    await expect(page.locator('.sidebar')).toHaveCount(0);
    await expect(page.locator('[data-room="'+sid+'"]')).toBeVisible();
    const bar=async selector=>{const r=await page.locator(selector).boundingBox();expect(r.height).toBe(44);expect(Math.abs(r.y-59)).toBeLessThan(1);};
    await bar('.chat-sidebar-heading');
    expect((await page.locator('[data-chat-rooms]').boundingBox()).y).toBeLessThanOrEqual(59+88);
    await expect(page.locator('[data-chat-room-search]')).toBeHidden();
    await page.locator('[data-open-room-search]').click();await expect(page.locator('[data-chat-room-search]')).toBeFocused();
    await page.locator('[data-chat-room-search]').fill('Несуществующий контакт');await expect(page.locator('[data-room]')).toHaveCount(0);
    await page.locator('[data-close-room-search]').click();await expect(page.locator('[data-chat-room-search]')).toBeHidden();await expect(page.locator('[data-room="'+sid+'"]')).toBeVisible();
    await page.locator('[data-open-room-search]').click();await touchSequence(page,[[8,320],[45,322],[115,324]]);await expect(page.locator('[data-chat-room-search]')).toBeHidden();await expect(page.locator('[data-chat-room-search]')).not.toBeFocused();
    await page.locator('[data-mobile-chat-section]').selectOption('contacts');await expect(page.locator('[data-contact-room="'+sid+'"]')).toBeVisible();
    await expect(page.locator('[data-mobile-chat-section] option[value=calls]')).toBeEnabled();await page.locator('[data-mobile-chat-section]').selectOption('calls');await expect(page.locator('[data-new-call]').first()).toBeVisible();
    await page.locator('[data-new-chat]').click();await expect(page.locator('[data-new-guest-call]')).toBeVisible();await page.locator('.modal-close').click();await page.locator('[data-mobile-chat-section]').selectOption('chats');
    await page.screenshot({path:'artifacts/mobile-app-messages.png',animations:'disabled'});
    const noOverflow=async()=>expect(await page.evaluate(()=>document.querySelector('#app').scrollWidth<=document.querySelector('#app').clientWidth)).toBeTruthy();
    await noOverflow();
    await page.locator('[data-room="'+sid+'"]').click();await expect(tabs).toBeHidden();
    await bar('.chat-header');
    await page.locator('#message-body').fill('Мобильный черновик');
    await touchSequence(page,[[90,320],[200,320]]);await expect(tabs).toBeHidden();
    await touchSequence(page,[[8,320],[12,370],[115,372]]);await expect(tabs).toBeHidden();
    await touchSequence(page,[[8,320],[45,322],[115,324]]);await expect(tabs).toBeVisible();
    await expect(page.locator('[data-chat-room-search]')).not.toBeFocused();
    await tabs.locator('[data-mobile-view=notes]').click();
    await expect(page.locator('#pages-sidebar')).toBeVisible();
    await expect(page.locator('#notion-page')).toBeHidden();
    await bar('.notes-navigation');await bar('.pages-sidebar-head');
    await footerIsland();
    await page.screenshot({path:'artifacts/mobile-app-notes.png',animations:'disabled'});
    await page.locator('#add-root-page').click();await expect(page.locator('#page-title')).toBeVisible();
    await bar('.notes-navigation');expect((await page.locator('.page-top').boundingBox()).height).toBe(0);
    for(const selector of ['#notes-main-menu','#favorite-page','#share-page','#page-actions','.notes-navigation .inbox-button']){
      const r=await page.locator(selector).boundingBox();expect(Math.abs(r.y-59)).toBeLessThan(1);expect(r.height).toBe(44);
      expect(await page.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})).toBe(true);
    }
    await page.screenshot({path:'artifacts/mobile-app-note-toolbar.png',animations:'disabled'});
    let releaseSave;
    const holdSave=async route=>{
      if(route.request().method()==='POST'&&route.request().postDataJSON()?.title==='Сохранить при переходе')await new Promise(resolve=>releaseSave=resolve);
      await route.continue();
    };
    await page.route('**/api/notes',holdSave);
    await page.locator('#page-title').fill('Сохранить при переходе');
    await touchSequence(page,[[8,340],[45,342],[115,344]]);
    await expect.poll(()=>typeof releaseSave).toBe('function');
    await expect(page.locator('#page-title')).toBeVisible(); // Saving still pending.
    releaseSave();await expect(page.locator('#pages-sidebar')).toBeVisible();
    await page.unroute('**/api/notes',holdSave);
    await tabs.locator('[data-mobile-view=tasks]').click();
    await expect(page.locator('[data-task-view-title]')).toHaveText('Сегодня');
    await bar('.tasks-toolbar');await bar('.tasks-center-heading');expect((await page.locator('[data-task-rows]').boundingBox()).y).toBeLessThanOrEqual(59+88);
    await footerIsland();
    await expect(page.locator('.tasks-row-title').filter({hasText:'Забрать посылку'})).toBeVisible();
    const notes=await (await context.request.get(origin+'/api/notes')).json();expect(notes.some(n=>n.title==='Сохранить при переходе')).toBeTruthy();
    await page.screenshot({path:'artifacts/mobile-app-tasks.png',animations:'disabled'});await noOverflow();
    await page.locator('.tasks-row-title').filter({hasText:'Забрать посылку'}).click();
    await page.locator('[aria-label="Название выбранного напоминания"]').fill('Забрать посылку и документы');
    await touchSequence(page,[[8,340],[45,342],[115,344]]);
    await expect(page.locator('.tasks-properties')).toBeHidden();
    const savedTasks=await (await context.request.get(origin+'/api/tasks')).json();expect(savedTasks.some(t=>t.title==='Забрать посылку и документы')).toBeTruthy();
    await page.locator('[data-mobile-task-view=scheduled]').click();
    await expect(page.locator('[data-mobile-task-view=scheduled]')).toHaveAttribute('aria-pressed','true');
    await page.locator('[data-toggle-tasks-sidebar]').click();await expect(page.locator('.tasks-sidebar')).toBeVisible();
    await page.locator('[data-tasks-main-menu]').click();await expect(page.locator('.tasks-sidebar')).toBeHidden();
    await tabs.locator('[data-mobile-view=settings]').click();
    await expect(page.locator('.mobile-account')).toBeVisible();
    await expect(page.locator('.mobile-setting-row')).toHaveCount(7);
    await bar('.topbar');
    await footerIsland();await page.locator('.content').evaluate(el=>el.scrollTop=200);await footerIsland();
    await page.screenshot({path:'artifacts/mobile-app-settings.png',animations:'disabled'});await noOverflow();
    // All ordinary sections share Settings' 44px phone bar; the focused
    // notes/tasks/chat headers are exercised separately above; Files owns its navigation too.
    for(const view of ['admin','expenses','files','family_photos','home']){
      await page.locator('.mobile-section-links [data-mobile-open='+view+']').click();
      if(view==='admin'){
        const unlock=page.locator('[data-admin-unlock]');await expect(unlock).toBeVisible();
        await unlock.locator('[name=password]').fill(password);await unlock.locator('[type=submit]').click();
        await expect(page.locator('.admin-unlock-dialog')).toHaveCount(0);
      }
      if(view==='files'){
        await expect(page.locator('.workspace')).toHaveClass(/files-open/);
        await expect(page.locator('.workspace>.sidebar,.content>.topbar')).toHaveCount(0);
        await expect(page.locator('#files-main-menu')).toBeVisible();await expect(page.locator('.drive-workspace-header #open-inbox')).toBeVisible();await noOverflow();
        await page.locator('#files-main-menu').click();await expect(page.locator('.mobile-screen-title')).toHaveText('Настройки');continue;
      }
      await bar('.topbar');await expect(page.locator('.topbar #open-inbox')).toBeVisible();
      expect(await page.locator('.mobile-screen-title').evaluate(el=>getComputedStyle(el).fontSize)).toBe('17px');
      await expect(page.locator('#view-content>.section-heading h1')).toBeHidden();await noOverflow();
      if(view==='expenses'){await page.locator('[data-start-night]').click();await expect(page.locator('[data-start-event]')).toBeVisible();await page.locator('.modal-close').click();}
      await page.locator('.mobile-secondary-back').click();await expect(page.locator('.mobile-screen-title')).toHaveText('Настройки');
    }
    await page.locator('.mobile-setting-row summary').filter({hasText:'Оформление'}).click();
    await expect(page.locator('#appearance-settings')).toBeVisible();
    await page.locator('.mobile-section-links [data-mobile-open=social]').click();
    await expect(page.locator('.mobile-screen-title')).toHaveText('Люди и группы');
    await expect(page.locator('.topbar #refresh-social')).toBeVisible();await bar('.topbar');
    const previousRefresh=await page.locator('#refresh-social').elementHandle();
    await page.locator('#refresh-social').click();await expect.poll(()=>previousRefresh.evaluate(el=>el.isConnected)).toBe(false);
    await expect(page.locator('.social-sections')).toBeVisible();await expect(page.locator('#refresh-social')).toHaveCount(1);await expect(page.locator('#open-inbox')).toHaveCount(1);
    await touchSequence(page,[[8,340],[45,342],[115,344]]);
    await expect(page.locator('.mobile-screen-title')).toHaveText('Настройки');
    await tabs.locator('[data-mobile-view=chat]').click();
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await expect(tabs).toBeVisible();
    await expect(page.locator('[data-room="'+sid+'"]')).toBeVisible();
    await expect(page.locator('[data-chat-room-search]')).not.toBeFocused();
    await page.locator('[data-room="'+sid+'"]').click();
    await expect(page.locator('#message-body')).toHaveValue('Мобильный черновик');
    await expect(page.locator('.chat-message-text').filter({hasText:'Привет! Как насчёт встретиться в выходные?'})).toBeVisible();
    await page.screenshot({path:'artifacts/mobile-app-chat.png',animations:'disabled'});
    await page.locator('[data-chat-back]').click();
    // Tapping the selected tab also opens the chat list, even if the Messages
    // workspace was last showing Contacts rather than Chats.
    await page.locator('[data-mobile-chat-section]').selectOption('contacts');
    await tabs.locator('[data-mobile-view=chat]').click();
    await expect(page.locator('[data-mobile-chat-section]')).toHaveValue('chats');
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await expect(page.locator('[data-room="'+sid+'"]')).toBeVisible();
    // The tab must also ignore the last opened external Telegram group.
    telegramReady=true;await tabs.locator('[data-mobile-view=settings]').click();await tabs.locator('[data-mobile-view=chat]').click();
    await page.locator('[data-room="telegram:tab-entry-tg"]').click();
    await page.locator('[data-fp-back]').click();await tabs.locator('[data-mobile-view=settings]').click();
    await tabs.locator('[data-mobile-view=chat]').click();
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await expect(page.locator('[data-room="telegram:tab-entry-tg"]')).toBeVisible();
    // An explicit notification still opens its conversation after a list entry.
    await tabs.locator('[data-mobile-view=settings]').click();
    await post(peer,'/chat/'+sid+'/messages',{body:'Вход из уведомления',client_id:'mobile-tab-notification'},peerCsrf);
    const notified=()=>page.locator('.inbox-open').filter({hasText:'Вход из уведомления'});
    await page.locator('#open-inbox').click();await expect(notified()).toBeVisible();await notified().click();
    await expect(page.locator('.chat-shell')).toHaveClass(/room-open/);
    await expect(page.locator('#message-body')).toHaveValue('Мобильный черновик');
    await page.locator('[data-chat-back]').click();
    await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');
    await page.screenshot({path:'artifacts/mobile-app-messages-dark.png',animations:'disabled'});
    // Separate phone section memory does not replace the desktop homepage.
    await page.setViewportSize({width:1440,height:900});await page.goto('/');
    await expect(tabs).toBeHidden();await expect(page.locator('.sidebar')).toBeVisible();
    await expect(page.locator('.sidebar [data-view=home]')).toHaveClass(/active/);
    await page.screenshot({path:'artifacts/mobile-app-desktop-regression.png',animations:'disabled'});
    // Compact width alone never switches the ordinary browser to the app UI.
    const ordinary=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:390,height:844},storageState:await context.storageState()});
    const normal=await ordinary.newPage();await normal.goto('/');await expect(normal.locator('.mobile-app-tabs')).toBeHidden();await expect(normal.locator('.sidebar')).toBeVisible();await ordinary.close();
    // Disabled modules disappear from tabs and cannot be restored from history.
    await page.unroute('**/api/modules');
    await page.setViewportSize({width:390,height:844});await page.goto('/');
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await expect(page.locator('#message-body,[data-fp-back]')).toHaveCount(0);
    await tabs.locator('[data-mobile-view=settings]').click();
    await page.locator('.mobile-setting-row summary').filter({hasText:'Модули'}).click();
    await page.locator('#modules-form [name=chat]').uncheck();await page.locator('#modules-form button[type=submit]').click();
    await expect(tabs.locator('[data-mobile-view=chat]')).toHaveCount(0);
    const me=await (await context.request.get(origin+'/api/me')).json();
    await page.evaluate(uid=>sessionStorage.setItem('drg-mobile-view:'+uid,'chat'),me.id);await page.goto('/');
    await expect(tabs.locator('[aria-current=page]')).toHaveText('Заметки');
    await noOverflow();
    await page.setViewportSize({width:320,height:568});await noOverflow();
    await tabs.locator('[data-mobile-view=notes]').click();
    await page.locator('[data-open-page]').filter({hasText:'Сохранить при переходе'}).click();
    await expect(page.locator('#page-title')).toHaveValue('Сохранить при переходе');
    for(const selector of ['#notes-main-menu','#favorite-page','#share-page','#page-actions','.notes-navigation .inbox-button']){
      const r=await page.locator(selector).boundingBox();expect(r.x).toBeGreaterThanOrEqual(0);expect(r.x+r.width).toBeLessThanOrEqual(320);
      expect(await page.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));})).toBe(true);
    }
    await page.locator('#notes-main-menu').click();await expect(page.locator('#pages-sidebar')).toBeVisible();await noOverflow();
    await tabs.locator('[data-mobile-view=settings]').click();
    await page.setViewportSize({width:1440,height:900});
    await expect(page.locator('.mobile-more-sections')).toHaveCount(0);await expect(page.locator('.settings-grid .preference-row')).toHaveCount(7);
    await page.setViewportSize({width:390,height:844});await expect(page.locator('.mobile-setting-row')).toHaveCount(7);
    expect(errors).toEqual([]);
  }finally{await peer.close();}
}

// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8022',directory=await mkdtemp(tmpdir()+'/drg-mobile-app-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8022','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,isMobile:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page,browser);
  console.log('Installed mobile app passed: login/2FA, tabs, chat draft/back, notes save, tasks, secondary sections, desktop and disabled modules.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
