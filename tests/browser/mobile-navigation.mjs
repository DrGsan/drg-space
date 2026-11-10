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
  const password='Navigation-password-123';
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let chatActive;
  page.on('websocket',socket=>socket.on('framereceived',event=>{
    const data=JSON.parse(event.payload);if(data.type==='activity')chatActive=data.chat_active;
  }));
  await page.setViewportSize({width:390,height:844});
  await page.context().addInitScript(()=>{
    // Classic mobile browser keeps its horizontal application menu.
    window.__iconBadges=[];
    Object.defineProperty(navigator,'setAppBadge',{configurable:true,value:async n=>window.__iconBadges.push(n)});
    Object.defineProperty(navigator,'clearAppBadge',{configurable:true,value:async()=>window.__iconBadges.push(0)});
  });
  await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  async function post(context,path,data,csrf=''){
    const response=await context.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  async function activate(context,csrf){
    const enrollment=await post(context,'/2fa/enroll',{password},csrf);
    await post(context,'/2fa/confirm',{code:totp(enrollment.secret)},csrf);
    return (await post(context,'/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  }
  const context=page.context();let csrf=(await post(context,'/setup',{login:'navowner',password,token:'browser-test-setup-key'})).csrf;
  csrf=await activate(context,csrf);
  await post(context,'/admin/access/unlock',{password},csrf);
  const peer=await browser.newContext({locale:'ru-RU'});
  try{
    const invite=await post(context,'/invites',{login:'navfriend'},csrf);
    let peerCsrf=(await post(peer,'/register',{login:'navfriend',password,invite:invite.url.split('/').at(-1)})).csrf;
    peerCsrf=await activate(peer,peerCsrf);
    const request=await post(context,'/social/friends',{login:'navfriend'},csrf);
    const {space_id:sid}=await post(peer,'/social/requests/'+request.id+'/accept',{},peerCsrf);
    await context.request.put(origin+'/api/notifications/preferences',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{sound:false,popups:false}});
    // Telegram sending is isolated from the real archive and Telegram API.
    await page.route('**/api/modules',async route=>{
      const response=await route.fetch(),body=await response.json();
      body.catalog.push({id:'family_photos',kind:'module',default_enabled:false,title:'Семейные фото'});body.enabled.family_photos=true;
      await route.fulfill({response,json:body});
    });
    await page.route('**/api/family-photos/**',route=>route.fulfill({json:route.request().url().endsWith('/groups')?
      {status:{can_send:true,bot_messaging:true,worker_online:true},items:[{id:'navigation-tg',title:'Telegram QA',kind:'supergroup',can_send:true,history_complete:true,media_pending:0}]}:{items:[],more:false}}));
    await page.goto('/');
    const nav=page.locator('.sidebar nav');await expect(nav).toBeVisible();
    expect(await nav.evaluate(el=>el.scrollWidth>el.clientWidth)).toBeTruthy();
    await nav.evaluate(el=>el.scrollLeft=el.scrollWidth);
    const position=await nav.evaluate(el=>el.scrollLeft);expect(position).toBeGreaterThan(0);
    await page.locator('[data-view=settings]').click();
    await expect(page.locator('[data-view=settings]')).toHaveClass(/active/);
    await expect.poll(()=>nav.evaluate(el=>el.scrollLeft)).toBe(position);
    await page.locator('[data-view=admin]').click();
    await expect(page.locator('[data-view=admin]')).toHaveClass(/active/);
    await expect.poll(()=>nav.evaluate(el=>el.scrollLeft)).toBe(position);
    await page.screenshot({path:'artifacts/mobile-menu-position.png'});
    // A focused workspace temporarily removes the app menu; its position is
    // still restored when returning to the main screen.
    await page.locator('[data-view=chat]').click();
    await page.locator('[data-chat-section=contacts]').click();
    await page.locator('[data-contact-room="'+sid+'"]').click();
    await expect(page.locator('.chat-shell')).toHaveClass(/room-open/);
    await checkComposerFocus(page,'#message-body',`**/api/chat/${sid}/messages`);
    await page.locator('#message-body').fill('Сохранённый черновик');
    await expect(page.locator('#message-body')).toBeFocused();
    await page.locator('[data-chat-back]').click();
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await expect(page.locator('.chat-sidebar')).toBeFocused();
    await expect(page.locator('[data-chat-room-search]')).not.toBeFocused();
    await expect(page.locator('[data-chat-section=contacts]')).toHaveAttribute('aria-selected','true');
    await page.locator('[data-contact-room="'+sid+'"]').click();
    await expect(page.locator('#message-body')).toHaveValue('Сохранённый черновик');
    await page.locator('[data-chat-back]').click();
    await page.locator('[data-chat-room-search]').click();
    await expect(page.locator('[data-chat-room-search]')).toBeFocused();
    await page.locator('[data-chat-room-search]').fill('navfriend');
    await page.locator('[data-contact-room="'+sid+'"]').click();
    await page.locator('[data-chat-back]').click();
    await expect(page.locator('[data-chat-room-search]')).toHaveValue('navfriend');
    await expect(page.locator('.chat-sidebar')).toBeFocused();
    await page.locator('[data-chat-room-search]').fill('');
    await page.locator('[data-chat-section=chats]').click();
    await page.locator('[data-room="telegram:navigation-tg"]').click();
    await checkComposerFocus(page,'#fp-message','**/api/family-photos/groups/navigation-tg/messages',true);
    await page.locator('[data-fp-back]').click();
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await expect(page.locator('.chat-sidebar')).toBeFocused();
    await page.screenshot({path:'artifacts/mobile-chat-return.png'});
    await page.locator('#chat-main-menu').click();
    // Clicking the earlier Messages item may scroll it into view. Preserve
    // that actual position as well, rather than forcing the last menu item.
    expect(await nav.evaluate(el=>el.scrollLeft)).toBeGreaterThan(0);
    await page.locator('[data-view=social]').scrollIntoViewIfNeeded();
    const returned=await nav.evaluate(el=>el.scrollLeft);
    await page.locator('[data-view=social]').click();
    await expect(page.locator('[data-view=social]')).toHaveClass(/active/);
    await expect.poll(()=>nav.evaluate(el=>el.scrollLeft)).toBe(returned);
    // Foreground Messages keeps unread room counts without duplicate bell or
    // push alerts. Other sections and a hidden app resume normal delivery.
    await post(context,'/notifications/read-all',{},csrf);
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    const lastBadge=()=>page.evaluate(()=>window.__iconBadges.at(-1));
    await expect.poll(lastBadge).toBe(0);
    await page.locator('[data-view=chat]').click();
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await page.locator('[data-room="telegram:navigation-tg"]').click();
    await expect(page.locator('[data-fp-back]')).toBeVisible();
    await expect.poll(()=>chatActive).toBe(true);
    await post(peer,'/chat/'+sid+'/messages',{body:'Badge one',client_id:'badge-mobile-first'},peerCsrf);
    await post(peer,'/chat/'+sid+'/messages',{body:'Badge two',client_id:'badge-mobile-second'},peerCsrf);
    await expect(page.locator('[data-room="'+sid+'"] .chat-room-unread')).toHaveText('2');
    expect(await lastBadge()).toBe(0);
    expect((await context.request.get(origin+'/api/notifications')).ok()).toBeTruthy();
    await page.evaluate(()=>{
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect.poll(()=>chatActive).toBe(false);
    await post(peer,'/chat/'+sid+'/messages',{body:'Hidden app',client_id:'badge-mobile-hidden'},peerCsrf);
    await expect.poll(lastBadge).toBe(1);
    await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
    await expect.poll(()=>chatActive).toBe(true);
    await page.locator('[data-fp-back]').click();
    await page.locator('#chat-main-menu').click();
    await expect.poll(()=>chatActive).toBe(false);
    await post(peer,'/chat/'+sid+'/messages',{body:'Other section',client_id:'badge-mobile-other'},peerCsrf);
    await expect.poll(lastBadge).toBe(2);
    await page.locator('#open-inbox').click();
    await page.locator('[data-read-all]').click();
    await expect.poll(lastBadge).toBe(0);
    await page.locator('.inbox-dialog .modal-close').click();
    // Desktop Telegram-style folder badges count conversations; row badges
    // still count messages. Exercise real reads and both conversation kinds.
    const group=await post(context,'/social/families',{name:'Семейная группа'},csrf);
    const groupInvite=await post(context,'/social/families/'+group.id+'/invites',{login:'navfriend'},csrf);
    await post(peer,'/social/requests/'+groupInvite.id+'/accept',{},peerCsrf);
    for(let i=0;i<2;i++)await post(peer,'/chat/'+group.id+'/messages',{body:'Сообщение в группе '+(i+1),client_id:'desktop-group-message-'+i},peerCsrf);
    await page.setViewportSize({width:1440,height:900});
    await page.locator('[data-view=chat]').click();
    // A desktop entry stays empty even after asynchronous Telegram rooms arrive.
    await expect(page.locator('[data-room="telegram:navigation-tg"]')).toBeVisible();
    await expect(page.locator('.chat-empty-selection')).toHaveText('Выберите, кому хотели бы написать');
    await expect(page.locator('.chat-room.active,#message-body,[data-fp-back]')).toHaveCount(0);
    const folder=id=>page.locator('[data-chat-filter="'+id+'"] .chat-tab-unread');
    await expect(folder('all')).toHaveText('2');
    await expect(folder('unread')).toHaveText('2');
    await expect(folder('direct')).toHaveText('1');
    await expect(folder('groups')).toHaveText('1');
    await expect(folder('favorites')).toBeHidden();
    await expect(page.locator('[data-room="'+sid+'"] .chat-room-unread')).toHaveText('4');
    await expect(page.locator('[data-room="'+group.id+'"] .chat-room-unread')).toHaveText('2');
    await page.locator('[data-chat-filter=groups]').click();
    await expect(folder('groups')).toHaveText('1');
    await expect(page.locator('[data-room="'+sid+'"]')).toHaveCount(0);
    await page.screenshot({path:'artifacts/desktop-unread-folders.png',animations:'disabled'});
    await page.emulateMedia({colorScheme:'dark'});
    await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');
    await page.screenshot({path:'artifacts/desktop-unread-folders-dark.png',animations:'disabled'});
    await page.locator('[data-chat-filter=direct]').click();
    await page.locator('[data-room="'+sid+'"]').click();
    await expect(folder('direct')).toBeHidden();
    await expect(folder('all')).toHaveText('1');
    await page.locator('[data-chat-room-search]').fill('Нет такой беседы');
    await expect(folder('groups')).toHaveText('1');
    await page.locator('[data-chat-room-search]').fill('');
    await page.locator('[data-chat-filter=groups]').click();
    await page.locator('[data-room="'+group.id+'"]').click();
    await expect(folder('groups')).toBeHidden();
    await expect(folder('all')).toBeHidden();
    await page.locator('#message-body').fill('Черновик после повторного входа');
    await page.locator('#chat-main-menu').click();await page.locator('[data-view=chat]').click();
    await expect(page.locator('.chat-empty-selection')).toBeVisible();
    await expect(page.locator('.chat-room.active,#message-body')).toHaveCount(0);
    await page.locator('[data-room="'+group.id+'"]').click();
    await expect(page.locator('#message-body')).toHaveValue('Черновик после повторного входа');
    // Ignore a remembered room from older clients on a normal page reload.
    const own=await (await context.request.get(origin+'/api/me')).json();
    await page.evaluate(({uid,sid})=>sessionStorage.setItem('drg-chat-space:'+uid,sid),{uid:own.id,sid});
    await page.reload();await expect(page.locator('[data-room="telegram:navigation-tg"]')).toBeVisible();
    await expect(page.locator('.chat-empty-selection')).toBeVisible();
    await expect(page.locator('.chat-room.active,#message-body,[data-fp-back]')).toHaveCount(0);
    await page.screenshot({path:'artifacts/desktop-chat-empty-selection.png',animations:'disabled'});
    // Explicit links still open their target and do not fall back on invalid IDs.
    await page.goto('/#chat/'+sid);await page.reload();
    await expect(page.locator('#message-body')).toBeVisible();await expect(page.locator('[data-room="'+sid+'"]')).toHaveClass(/active/);
    await page.goto('/#chat/missing-room');await page.reload();
    await expect(page.locator('[data-room="telegram:navigation-tg"]')).toBeVisible();await expect(page.locator('.chat-empty-selection')).toBeVisible();
    await expect(page.locator('.chat-room.active,#message-body,[data-fp-back]')).toHaveCount(0);
    await page.locator('#chat-main-menu').click();
    await expect.poll(()=>chatActive).toBe(false);
    await post(peer,'/chat/'+sid+'/messages',{body:'Badge before logout',client_id:'badge-mobile-third'},peerCsrf);
    await expect.poll(lastBadge).toBe(1);
    await page.locator('#logout').click();
    await expect.poll(lastBadge).toBe(0);
    expect(errors).toEqual([]);
  }finally{await peer.close();}
}

async function checkComposerFocus(page,selector,path,telegram=false){
  const body=page.locator(selector),send=body.locator('xpath=ancestor::form').locator('button[type=submit]');
  let release,ready=false,mode='success',savedMessage;
  const handler=async route=>{
    if(route.request().method()!=='POST')return route.fallback();
    if(mode==='failure')return route.abort();
    const response=telegram?null:await route.fetch();
    if(response)savedMessage=await response.json();
    ready=true;await new Promise(resolve=>release=resolve);
    if(telegram)await route.fulfill({json:{id:'keyboard-outbox-'+Date.now()}});
    else await route.fulfill({response});
  };
  await page.route(path,handler);
  try{
    await body.fill('Клавиатура остаётся открытой');
    await body.evaluate(el=>{window.composerBlurs=0;el.addEventListener('blur',()=>window.composerBlurs++);});
    await send.tap();await expect.poll(()=>ready).toBe(true);
    await expect(body).toBeFocused();await expect(body).toHaveAttribute('aria-busy','true');
    expect(await body.evaluate(el=>el.readOnly||el.disabled)).toBe(false);
    await body.press('x');await expect(body).toHaveValue('Клавиатура остаётся открытой');
    // Some IME updates cannot be cancelled at beforeinput; keep the pending text.
    await body.evaluate(el=>{el.value='Изменение от IME';el.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertCompositionText',data:'IME'}));});
    await expect(body).toHaveValue('Клавиатура остаётся открытой');
    release();await expect(body).toHaveValue('');await expect(send).toBeEnabled();await expect(body).toBeFocused();
    expect(await page.evaluate(()=>window.composerBlurs)).toBe(0);
    mode='failure';await body.fill('Текст после ошибки');await send.tap();
    await expect(send).toBeEnabled();await expect(body).not.toHaveAttribute('aria-busy','true');
    await expect(body).toBeFocused();await expect(body).toHaveValue('Текст после ошибки');
    // A late acknowledgement must not reopen a manually dismissed keyboard.
    mode='success';ready=false;await send.tap();await expect.poll(()=>ready).toBe(true);
    await body.evaluate(el=>el.blur());release();await expect(body).toHaveValue('');await expect(send).toBeEnabled();await expect(body).not.toBeFocused();
    // Sending while the keyboard is already closed also leaves it closed.
    ready=false;await body.fill('Отправка с закрытой клавиатурой');await body.evaluate(el=>el.blur());
    await send.tap();await expect.poll(()=>ready).toBe(true);release();
    await expect(body).toHaveValue('');await expect(send).toBeEnabled();await expect(body).not.toBeFocused();
    if(!telegram){
      ready=false;await body.fill('Отправка клавишей Enter');await body.press('Enter');
      await expect.poll(()=>ready).toBe(true);await expect(body).toBeFocused();release();
      await expect(body).toHaveValue('');await expect(send).toBeEnabled();await expect(body).toBeFocused();
      const message=page.locator(`[data-message-id="${savedMessage.id}"]`);
      await message.locator('.chat-message-text').click({button:'right'});
      await page.getByRole('menuitem',{name:'Изменить',exact:true}).click();
      const editPath=path+'/'+savedMessage.id;
      const edit=async route=>{
        if(route.request().method()!=='PUT')return route.fallback();
        const response=await route.fetch();ready=true;await new Promise(resolve=>release=resolve);await route.fulfill({response});
      };
      await page.route(editPath,edit);
      try{
        ready=false;await body.fill('Правка без закрытия клавиатуры');await send.tap();
        await expect.poll(()=>ready).toBe(true);await expect(body).toBeFocused();await expect(body).toHaveAttribute('aria-busy','true');
        await body.press('x');await expect(body).toHaveValue('Правка без закрытия клавиатуры');release();
        await expect(body).toHaveValue('');await expect(send).toBeEnabled();await expect(body).toBeFocused();
        await expect(message).toContainText('Правка без закрытия клавиатуры');
      }finally{release?.();await page.unroute(editPath,edit);}
    }
  }finally{release?.();await page.unroute(path,handler);}
}

// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8021',directory=await mkdtemp(tmpdir()+'/drg-mobile-navigation-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8021','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page,browser);
  console.log('Mobile navigation passed: native/Telegram send focus, pending text, failures, manual dismissal, Enter, editing, back, drafts, unread counts and icon badges.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
