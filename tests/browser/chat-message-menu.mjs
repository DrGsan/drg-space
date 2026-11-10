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
  await page.setViewportSize({width:390,height:844});
  await page.context().addInitScript(()=>{
    Object.defineProperty(navigator,'standalone',{value:true,configurable:true});
    Object.defineProperty(navigator,'clipboard',{value:{async writeText(text){window.copiedText=text;}},configurable:true});
  });
  await page.emulateMedia({colorScheme:'dark'});
  const owner=page.context(),password='Message-menu-password-123';
  async function api(context,path,method='POST',data,csrf=''){
    const r=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  }
  let csrf=(await api(owner,'/setup','POST',{login:'menuowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await api(owner,'/2fa/enroll','POST',{password},csrf);
  await api(owner,'/2fa/confirm','POST',{code:totp(enrolled.secret)},csrf);
  csrf=(await api(owner,'/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
  await api(owner,'/admin/access/unlock','POST',{password},csrf);
  const invitation=await api(owner,'/invites','POST',{login:'menupeer'},csrf);
  const peer=await browser.newContext({locale:'ru-RU',baseURL:origin});
  try{
    const registered=await api(peer,'/register','POST',{login:'menupeer',password,invite:new URL(invitation.url).pathname.split('/').at(-1)});
    const peerCsrf=(await api(peer,'/2fa/skip','POST',undefined,registered.csrf)).csrf;
    const friend=await api(owner,'/social/friends','POST',{login:'menupeer'},csrf);
    const {space_id:sid}=await api(peer,'/social/requests/'+friend.id+'/accept','POST',undefined,peerCsrf);
    const send=(ctx,token,text)=>api(ctx,'/chat/'+sid+'/messages','POST',{body:text,client_id:crypto.randomUUID()},token);
    const incoming=await send(peer,peerCsrf,'Привет!');
    const outgoing=await send(owner,csrf,'Хорошо');
    const multiline=await send(owner,csrf,'Проверяем перенос длинного сообщения: текст должен занимать только нужное место, а время оставаться рядом.');
    await page.goto('/');await page.locator('[data-room="'+sid+'"]').click();
    const own=page.locator('[data-message-id="'+outgoing.id+'"]'),other=page.locator('[data-message-id="'+incoming.id+'"]');
    await expect(own).toBeVisible();await expect(other).toBeVisible();
    const attachmentMenu=page.getByRole('menu',{name:'Прикрепить',exact:true}),attach=page.locator('[data-attach-chat]');
    for(const width of [320,390]){
      await page.setViewportSize({width,height:844});await attach.click();
      await expect(attachmentMenu.getByRole('menuitem')).toHaveText(['Фото или видео','Файл','Камера']);
      await expect(attachmentMenu.locator('svg')).toHaveCount(3);
      const rect=await attachmentMenu.boundingBox();expect(rect.x).toBeGreaterThanOrEqual(8);expect(rect.x+rect.width).toBeLessThanOrEqual(width-8);
      expect(await attachmentMenu.getByRole('menuitem').first().evaluate(el=>el.offsetHeight)).toBe(44);
      await page.keyboard.press('Escape');await expect(attachmentMenu).toHaveCount(0);await expect(attach).toBeFocused();
    }
    await attach.click();const cameraChooser=page.waitForEvent('filechooser');await attachmentMenu.getByRole('menuitem',{name:'Камера',exact:true}).click();
    const nativeCamera=await cameraChooser;expect(await nativeCamera.element().getAttribute('capture')).toBe('environment');expect(nativeCamera.isMultiple()).toBe(false);
    await nativeCamera.setFiles('tests/fixtures/blue.png');await expect(page.locator('[data-chat-drafts] img')).toBeVisible();await expect(page.locator('[data-chat-drafts] b')).toHaveText(/^blue\.(png|webp)$/);
    await page.locator('[data-remove-draft]').click();
    for(const [name,accept] of [['Файл',''],['Фото или видео','image/*,video/*']]){
      await attach.click();const chooser=page.waitForEvent('filechooser');await attachmentMenu.getByRole('menuitem',{name,exact:true}).click();const selected=await chooser;
      expect(await selected.element().getAttribute('capture')).toBeNull();expect(await selected.element().getAttribute('accept')).toBe(accept);expect(selected.isMultiple()).toBe(true);await selected.setFiles([]);
    }
    for(const node of [own,other]){
      expect((await node.boundingBox()).height).toBeLessThanOrEqual(34);
      await expect(node.locator('.chat-message-actions')).toBeHidden();
    }
    await page.screenshot({path:'artifacts/messages-compact-mobile.png',animations:'disabled'});
    await expect(other.locator('.chat-message-meta')).toBeHidden();await expect(other.locator('.chat-message-avatar')).toBeHidden();
    expect((await page.locator('[data-message-id="'+multiline.id+'"]').boundingBox()).height).toBeLessThan(110);
    const menu=page.getByRole('menu',{name:'Действия сообщения'}),overlay=page.locator('.chat-context-layer.is-open');
    if(browser.browserType().name()==='chromium'){
      const device=await owner.newCDPSession(page),rect=await own.boundingBox();
      await device.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:rect.x+10,y:rect.y+10}]});
      await expect(menu).toBeVisible();await device.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      await expect(overlay).toBeVisible();await page.touchscreen.tap(4,4);await expect(menu).toHaveCount(0);await device.detach();
    }
    async function hold(node){
      await node.scrollIntoViewIfNeeded();const box=await node.boundingBox(),event={pointerType:'touch',isPrimary:true,pointerId:1,clientX:box.x+8,clientY:box.y+8};
      await node.dispatchEvent('pointerdown',event);await expect(menu).toBeVisible();await node.dispatchEvent('pointerup',event);
      await overlay.dispatchEvent('click');await expect(overlay).toBeVisible();
      await expect(overlay).toBeVisible();await expect.poll(()=>overlay.evaluate(el=>getComputedStyle(el).backdropFilter||getComputedStyle(el).webkitBackdropFilter)).toBe('blur(18px)');
    }
    const box=await own.boundingBox(),event={pointerType:'touch',isPrimary:true,pointerId:1,clientX:box.x+8,clientY:box.y+8};
    await own.dispatchEvent('pointerdown',event);await own.dispatchEvent('pointermove',{...event,clientY:event.clientY+35});await page.waitForTimeout(550);await expect(menu).toHaveCount(0);
    await own.dispatchEvent('pointerdown',event);await own.dispatchEvent('pointercancel',event);await page.waitForTimeout(550);await expect(menu).toHaveCount(0);
    await page.locator('#message-body').fill('Несохранённый черновик');
    await hold(own);await expect(menu.getByRole('menuitem',{name:'Изменить',exact:true})).toBeVisible();
    expect(await page.locator('#app').evaluate(app=>app.inert)).toBe(true);
    expect(await page.evaluate(()=>document.activeElement.tagName)).toBe('BUTTON');
    await page.screenshot({path:'artifacts/message-context-dark.png',animations:'disabled'});
    const reads=[];const readListener=request=>{if(request.method()==='POST'&&request.url().endsWith('/chat/'+sid+'/read'))reads.push(request);};page.on('request',readListener);
    const unseen=await send(peer,peerCsrf,'Сообщение под размытым фоном');
    await expect(page.locator('[data-message-id="'+unseen.id+'"]')).toHaveCount(1);await page.waitForTimeout(300);
    expect(reads).toHaveLength(0);await expect(overlay).toBeVisible();page.off('request',readListener);
    await menu.getByRole('menuitem',{name:'Копировать текст',exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>window.copiedText)).toBe('Хорошо');await expect(overlay).toHaveCount(0);
    expect(await page.locator('#app').evaluate(app=>app.inert)).toBe(false);await expect(page.locator('#message-body')).toHaveValue('Несохранённый черновик');
    await hold(other);await expect(menu.getByRole('menuitem',{name:'Изменить',exact:true})).toHaveCount(0);await expect(menu.getByRole('menuitem',{name:'Удалить',exact:true})).toHaveCount(0);
    await menu.getByRole('menuitem',{name:'Ответить',exact:true}).click();await expect(page.locator('[data-chat-reply]')).toContainText('Привет!');
    await page.locator('[data-cancel-reply]').click();
    await hold(own);await menu.getByRole('menuitem',{name:'Изменить',exact:true}).click();
    await page.locator('#message-body').fill('Исправлено');await page.getByRole('button',{name:'Сохранить изменения',exact:true}).click();
    await expect(own).toContainText('Исправлено');await expect(page.locator('#message-body')).toHaveValue('Несохранённый черновик');
    for(const width of [320,390]){
      await page.setViewportSize({width,height:844});await hold(own);
      for(const height of [430,844]){
        await page.setViewportSize({width,height});await expect(overlay).toBeVisible();
        const rect=await menu.boundingBox();expect(rect.x).toBeGreaterThanOrEqual(0);expect(rect.x+rect.width).toBeLessThanOrEqual(width);expect(rect.y).toBeGreaterThanOrEqual(0);expect(rect.y+rect.height).toBeLessThanOrEqual(height);
      }
      await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);
    }
    // Deletion is an explicit, author-only action and reconciles a second open client.
    const temporary=await send(owner,csrf,'Сообщение для удаления');
    const quoted=await api(peer,'/chat/'+sid+'/messages','POST',{body:'Ответ сохранится',reply_to:temporary.id,client_id:crypto.randomUUID()},peerCsrf);
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    const temporaryNode=page.locator('[data-message-id="'+temporary.id+'"]');
    await expect(temporaryNode).toBeVisible();
    const peerPage=await peer.newPage();
    await peerPage.goto('/');await peerPage.locator('[data-view=chat]').click();await peerPage.locator('[data-room="'+sid+'"]').click();
    await expect(peerPage.locator('[data-message-id="'+temporary.id+'"]')).toBeVisible();
    await hold(temporaryNode);await page.waitForTimeout(250);
    await expect(menu.getByRole('menuitem',{name:'Удалить',exact:true})).toHaveCSS('color','rgb(229, 72, 77)');
    await page.screenshot({path:'artifacts/ios-03-web-delete-menu.png',animations:'disabled'});
    await menu.getByRole('menuitem',{name:'Удалить',exact:true}).click();
    await page.locator('[data-cancel-delete]').click();await expect(temporaryNode).toBeVisible();
    await hold(temporaryNode);await page.waitForTimeout(250);
    await menu.getByRole('menuitem',{name:'Удалить',exact:true}).click();
    await page.locator('[data-confirm-delete]').click();
    await expect(temporaryNode).toHaveCount(0);
    await expect(peerPage.locator('[data-message-id="'+temporary.id+'"]')).toHaveCount(0);
    await expect(page.locator('[data-message-id="'+quoted.id+'"] .chat-reply-quote')).toContainText('недоступно');
    await expect(peerPage.locator('[data-message-id="'+quoted.id+'"] .chat-reply-quote')).toContainText('недоступно');
    await peerPage.reload();if(await peerPage.locator('[data-view=chat]').isVisible())await peerPage.locator('[data-view=chat]').click();
    await peerPage.locator('[data-room="'+sid+'"]').click();
    await expect(peerPage.locator('[data-message-id="'+temporary.id+'"]')).toHaveCount(0);
    await peerPage.close();
    // A held video opens actions, a normal tap still opens the player.
    const attachment=await api(owner,'/chat/'+sid+'/attachments?name=test.mp4','POST',await readFile('tests/fixtures/chat-video.mp4'),csrf);
    const videoMessage=await api(owner,'/chat/'+sid+'/messages','POST',{body:'',attachments:[attachment.id],client_id:crypto.randomUUID()},csrf);
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    const tile=page.locator('[data-chat-media-id="'+attachment.id+'"]');await tile.scrollIntoViewIfNeeded();
    await expect.poll(()=>tile.locator('video').evaluate(video=>video.poster.startsWith('data:image/'))).toBe(true);
    await hold(tile);await expect(page.locator('.chat-context-preview video')).toHaveCount(0);await expect(page.locator('.fp-viewer')).toHaveCount(0);
    await tile.dispatchEvent('click');await expect(page.locator('.fp-viewer')).toHaveCount(0);
    await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await page.waitForTimeout(950);await tile.click();await expect(page.locator('.fp-viewer')).toBeVisible();await page.locator('.modal-close').click();
    // A group still identifies the sender and shares the same menu.
    const family=await api(owner,'/social/families','POST',{name:'Групповой чат'},csrf);
    await api(owner,'/social/families/'+family.id+'/invites','POST',{login:'menupeer'},csrf);
    const request=(await api(peer,'/social','GET')).requests.find(r=>r.space_id===family.id&&r.incoming);
    await api(peer,'/social/requests/'+request.id+'/accept','POST',undefined,peerCsrf);
    const groupMessage=await api(peer,'/chat/'+family.id+'/messages','POST',{body:'Сообщение в группе',client_id:crypto.randomUUID()},peerCsrf);
    await page.locator('[data-chat-back]').click();await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await page.locator('[data-room="'+family.id+'"]').click();
    const groupNode=page.locator('[data-message-id="'+groupMessage.id+'"]');await expect(groupNode.locator('.chat-message-meta')).toBeVisible();
    await hold(groupNode);await expect(menu.getByRole('menuitem',{name:'Изменить',exact:true})).toHaveCount(0);
    await page.locator('.chat-context-layer').click({position:{x:4,y:4}});await expect(menu).toHaveCount(0);
    await page.emulateMedia({colorScheme:'light'});await hold(groupNode);await page.screenshot({path:'artifacts/message-context-light.png',animations:'disabled'});
    // Route destruction tears down the overlay, gesture timers and inert state.
    await page.locator('[data-mobile-view=settings]').dispatchEvent('click');await expect(menu).toHaveCount(0);
    expect(await page.locator('#app').evaluate(app=>app.inert)).toBe(false);
    // Desktop retains hover buttons and its regular context menu.
    const desktop=await browser.newContext({locale:'ru-RU',baseURL:origin,...(browser.browserType().name()==='chromium'?{permissions:['camera']}:{})});await desktop.addCookies(await owner.cookies());const pc=await desktop.newPage();
    try{
      await pc.setViewportSize({width:1280,height:900});await pc.goto('/');await pc.locator('[data-view=chat]').click();await pc.locator('[data-room="'+sid+'"]').click();
      const node=pc.locator('[data-message-id="'+outgoing.id+'"]');await node.hover();await node.click({button:'right'});
      await expect(pc.getByRole('menu',{name:'Действия сообщения'})).toBeVisible();await expect(pc.locator('.chat-context-layer')).toHaveCount(0);
      await pc.keyboard.press('Escape');await expect(node).toBeFocused();
      await node.focus();await pc.keyboard.press('Shift+F10');await expect(pc.getByRole('menu')).toBeVisible();await pc.keyboard.press('Escape');
      const pcAttach=pc.locator('[data-attach-chat]'),pcMenu=pc.getByRole('menu',{name:'Прикрепить',exact:true});
      for(const scheme of ['light','dark']){
        await pc.emulateMedia({colorScheme:scheme});await pcAttach.click();
        await expect(pcMenu.getByRole('menuitem')).toHaveText(['Фото или видео','Файл','Камера']);
        expect(await pcMenu.getByRole('menuitem').first().evaluate(el=>el.offsetHeight)).toBe(30);
        expect(await pcMenu.evaluate(el=>getComputedStyle(el).backdropFilter||getComputedStyle(el).webkitBackdropFilter)).toContain('blur(24px)');
        await pc.screenshot({path:'artifacts/chat-attachment-menu-'+scheme+'.png',animations:'disabled'});
        await pc.keyboard.press('End');await expect(pcMenu.getByRole('menuitem',{name:'Камера',exact:true})).toBeFocused();await pc.keyboard.press('Escape');
        await node.click({button:'right'});await expect(pc.getByRole('menuitem',{name:/Переслать|Выбрать/})).toHaveCount(0);await expect(pc.locator('.chat-message-menu-date')).toHaveCount(0);
        await pc.screenshot({path:'artifacts/chat-message-menu-desktop-'+scheme+'.png',animations:'disabled'});await pc.keyboard.press('Escape');
      }
      if(browser.browserType().name()==='chromium'){
        await pc.evaluate(()=>{
          const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);window.cameraTracks=[];
          navigator.mediaDevices.getUserMedia=async options=>{const stream=await original(options);window.cameraTracks.push(...stream.getTracks());return stream;};
        });
        await pcAttach.click();await pcMenu.getByRole('menuitem',{name:'Камера',exact:true}).click();
        await expect(pc.getByRole('button',{name:'Сделать фото',exact:true})).toBeEnabled();
        await pc.getByRole('button',{name:'Сделать фото',exact:true}).click();await expect(pc.locator('[data-chat-drafts] img')).toBeVisible();
        await expect.poll(()=>pc.evaluate(()=>window.cameraTracks.length>0&&window.cameraTracks.every(track=>track.readyState==='ended'))).toBe(true);
        await pc.locator('[data-remove-draft]').click();
        // Closing before permission resolves must also stop the late stream.
        await pc.evaluate(()=>{
          const original=navigator.mediaDevices.getUserMedia;
          navigator.mediaDevices.getUserMedia=async options=>{await new Promise(resolve=>setTimeout(resolve,500));return original(options);};
        });
        await pcAttach.click();await pcMenu.getByRole('menuitem',{name:'Камера',exact:true}).click();await pc.keyboard.press('Escape');
        await expect.poll(()=>pc.evaluate(()=>window.cameraTracks.length>=2&&window.cameraTracks.every(track=>track.readyState==='ended'))).toBe(true);
      }
      await pc.evaluate(()=>{navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('Denied','NotAllowedError'));});
      await pcAttach.click();await pcMenu.getByRole('menuitem',{name:'Камера',exact:true}).click();
      await expect(pc.locator('[data-camera-status]')).toContainText('Разрешите доступ');await expect(pc.getByRole('button',{name:'Повторить',exact:true})).toBeVisible();
      await pc.keyboard.press('Escape');await expect(pc.locator('.chat-camera-preview')).toHaveCount(0);
      await pcAttach.click();await pc.locator('[data-chat-back]').dispatchEvent('click');await expect(pcMenu).toHaveCount(0);
    }finally{await desktop.close();}
    expect(errors).toEqual([]);
  }finally{await peer.close();}
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8028',directory=await mkdtemp(tmpdir()+'/drg-message-menu-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8028','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
  browser=await engine.launch({headless:true,args:engine===chromium?['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']:[]});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,isMobile:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Message menu passed: compact direct/group bubbles, touch cancellation, reply/copy/edit permissions, focused blur, viewport bounds, keyboard changes, media holds and desktop fallback.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
