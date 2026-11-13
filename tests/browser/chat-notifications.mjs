import {readAppStyles} from './fixtures.mjs';
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {build} from 'esbuild';
import {mkdir} from 'node:fs/promises';
const bundle=await build({stdin:{contents:`import {inboxController} from './web/modules/notifications/index.js';inboxController.attach=()=>{};export {mountChat,chatController} from './web/modules/chat/index.js';`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'MuteTest',write:false,loader:{'.css':'empty'}});
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1100,height:850}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://mute.test/**',r=>r.fulfill({body:'<html data-theme="ocean" data-scheme="dark"><body><main id="app"></main></body></html>',contentType:'text/html'}));
  await page.route('http://mute.test/api/users/peer/avatar*',r=>r.fulfill({path:'tests/fixtures/blue.png',contentType:'image/png'}));
  await page.goto('http://mute.test/');await page.addStyleTag({content:await readAppStyles()});
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{
    const stamp=Math.floor(Date.now()/1000),gid='-1001234567890';
    const members=[{id:'me',login:'Я'},{id:'peer',login:'Николай',avatar_url:'/api/users/peer/avatar?v=1'}];
    window.muteFixture={rooms:[{id:'direct',name:'Николай',kind:'friends',members,created:stamp,pinned:false,favorite:false,unread:2},{id:'family',name:'Семья',kind:'family',members,created:stamp,pinned:false,favorite:false,unread:3}],groupMute:0,calls:[],toasts:[]};
    const ctx={state:{me:{id:'me',login:'Я',settings:{}}},moduleEnabled:()=>true,navigate(){},toast(text){muteFixture.toasts.push(text);},
      async api(path,method='GET',body){
        const f=muteFixture;
        if(path.endsWith('/notifications')&&method==='PUT'){f.calls.push({path,...body});const until=body.seconds<0?-1:body.seconds?stamp+body.seconds:0;const room=f.rooms.find(r=>path===`/chat/${r.id}/notifications`);if(room)room.mute_until=until;else f.groupMute=until;return {mute_until:until};}
        if(path==='/chat/rooms')return {items:structuredClone(f.rooms)};
        if(path==='/chat/typing')return {rooms:{},server_time:stamp};
        if(path==='/family-photos/groups')return {items:[{id:gid,title:'Лёвик',unread:4,mute_until:f.groupMute,can_send:false}],status:{can_send:false,worker_online:true}};
        if(path.includes('/messages'))return {items:method==='GET'&&!path.includes('after=')?[path.startsWith('/family-photos/')
          ?{id:1,created:stamp,sender:'@Николай Тест',sender_id:'42',text:'Архивное сообщение',media:[]}
          :{id:1,created:stamp,sender:'Николай',sender_id:'peer',sender_profile:members[1],body:'Входящее сообщение',attachments:[]}]:[],more:false,reads:{},server_time:stamp,edit_cursor:0};
        if(path.endsWith('/outbox'))return {items:[]};
        if(path==='/me')return ctx.state.me;
        throw Error('Unexpected API '+path);
      }};
    await MuteTest.mountChat(document.querySelector('#app'),ctx);
  });
  for(const sid of ['direct','family']){
    const room=page.locator(`[data-room="${sid}"]`);await room.click();
    await page.locator('[data-chat-menu]').click();
    const menu=page.locator('[data-chat-menu-panel]');
    const labels=await menu.getByRole('menuitem').allTextContents();expect(labels.indexOf('Выключить на время…')).toBe(labels.indexOf('Люди и группы')+1);
    await menu.getByRole('menuitem',{name:'Выключить на время…'}).click();
    await expect(menu.getByRole('menuitem')).toHaveText(['Назад','Не беспокоить 1 час','Не беспокоить 8 часов','Не беспокоить 1 день','Не беспокоить 7 дней']);
    if(sid==='direct'){
      await page.setViewportSize({width:390,height:844});const box=await menu.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(390);
      await mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/chat-mute-menu-mobile.png'});await page.setViewportSize({width:1100,height:850});
    }
    await menu.getByRole('menuitem',{name:'Не беспокоить 8 часов',exact:true}).click();await expect(menu).toBeHidden();
    await expect(room.locator('.chat-room-muted')).toHaveCount(1);await expect(room.locator('.chat-room-unread')).toHaveText(sid==='direct'?'2':'3');
    await page.locator('[data-chat-menu]').click();await menu.getByRole('menuitem',{name:'Включить уведомления',exact:true}).click();await expect(room.locator('.chat-room-muted')).toHaveCount(0);
  }
  const photoRoom=page.locator('[data-room="telegram:-1001234567890"]');await photoRoom.click();
  const bell=page.locator('[data-fp-notifications]');await expect(bell).toBeEnabled();await expect(page.locator('[data-fp-gallery]')).toBeVisible();
  await bell.click();await expect(bell).toHaveAttribute('aria-pressed','true');await expect(bell).toHaveAttribute('aria-label','Включить уведомления');
  await expect(photoRoom.locator('.chat-room-muted')).toHaveCount(1);await expect(photoRoom.locator('.chat-room-unread')).toHaveText('4');
  await page.screenshot({path:'artifacts/chat-family-muted.png'});
  await bell.click();await expect(bell).toHaveAttribute('aria-pressed','false');await expect(photoRoom.locator('.chat-room-muted')).toHaveCount(0);
  for(const width of [1440,390,320])for(const scheme of ['light','dark']){
    await page.setViewportSize({width,height:900});
    if(width<=760){const back=page.locator('[data-fp-back],[data-chat-back]').filter({visible:true});if(await back.count())await back.click();}
    await page.locator('html').evaluate((el,scheme)=>el.dataset.scheme=scheme,scheme);
    for(const sid of ['direct','family','telegram:-1001234567890']){
      await page.locator(`[data-room="${sid}"]`).click();
      const message=page.locator(sid.startsWith('telegram:')?'[data-fp-message-id="1"]':'[data-message-id="1"]');
      await expect(message).toBeVisible();
      const avatar=message.locator('.chat-message-avatar'),name=message.locator('.chat-message-meta');
      if(sid==='direct'){
        await expect(avatar).toBeHidden();await expect(name).toBeHidden();
        expect(await message.evaluate(el=>getComputedStyle(el).marginLeft)).toBe('0px');
      }else{
        await expect(avatar).toBeVisible();await expect(name).toBeVisible();
        if(sid==='family')await expect.poll(()=>avatar.locator('img').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
        else await expect(avatar).toHaveText('НТ');
        const a=await avatar.boundingBox(),m=await message.boundingBox();
        expect(a.width).toBe(32);expect(a.x).toBeGreaterThanOrEqual(0);expect(a.x+a.width).toBeLessThanOrEqual(m.x);
      }
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      if(scheme==='dark')await page.screenshot({path:`artifacts/chat-sender-${sid.split(':')[0]}-${width}.png`});
      if(width<=760)await page.locator(sid.startsWith('telegram:')?'[data-fp-back]':'[data-chat-back]').click();
    }
  }
  expect(await page.evaluate(()=>muteFixture.calls.map(c=>c.seconds))).toEqual([28800,0,28800,0,-1,0]);expect(errors).toEqual([]);
  await page.evaluate(()=>MuteTest.chatController.destroy());
  console.log('Chat notification menus, mobile bounds, per-room mute indicators, family bell, unread counts and sender avatars (direct/group/archive, light/dark, 320–1440px) passed.');
}finally{await browser.close();}
