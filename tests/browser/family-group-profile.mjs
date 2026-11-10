// Real gallery + message sidebar + open Telegram conversation on the same page.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({stdin:{contents:`
  import {inboxController} from './web/modules/notifications/index.js';
  // This test changes metadata only; avoid unrelated notification transports.
  inboxController.attach=()=>{};
  export {mountChat,chatController} from './web/modules/chat/index.js';
  export {mountFamilyPhotos,familyPhotosController} from './web/modules/family-photos/index.js';`,resolveDir:process.cwd()},
  bundle:true,format:'iife',globalName:'FamilyProfile',write:false,loader:{'.css':'empty'}});
const blue=await readFile('tests/fixtures/blue.png'),sand=await readFile('tests/fixtures/sand.png');
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1100,height:900}}),errors=[];let avatarReads=0,photoReads=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://family-profile.test/**',route=>{
    if(route.request().url().includes('/avatar')){avatarReads++;return route.fulfill({body:route.request().url().includes('v=2')?sand:blue,contentType:'image/png',headers:{'Cache-Control':'no-store'}});}
    if(route.request().url().includes('/photo/')){photoReads++;return route.fulfill({body:blue,contentType:'image/png',headers:{'Cache-Control':'no-store'}});}
    return route.fulfill({body:'<main id="gallery"></main><main id="messages"></main>',contentType:'text/html'});
  });
  await page.goto('http://family-profile.test/');await page.clock.install();await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{
    const gid='export:abcdef0123456789';
    window.profileState={gid,title:'Лёвик',version:'1',connection:{state:'working',checked_at:Math.floor(Date.now()/1000),last_success:Math.floor(Date.now()/1000)}};
    const file={id:'a',group_id:gid,message_id:1,kind:'photo',state:'ready',created:1700000000,sender:'Анна',name:'Фото',url:'/photo/a.png',thumbnail_url:'/photo/a.png'};
    const ctx={state:{me:{id:'owner',settings:{}}},moduleEnabled:()=>true,navigate(){},toast(message){throw Error(message);},
      async api(path){
        const s=profileState;
        if(path==='/chat/rooms')return {items:[]};
        if(path==='/chat/typing')return {rooms:{},server_time:Date.now()/1000};
        if(path==='/family-photos/groups')return {items:[{id:gid,title:s.title,history_complete:true,kind:'supergroup',connection:structuredClone(s.connection),
          ...(s.version?{avatar_version:s.version,avatar_url:'/avatar?v='+s.version}:{}),can_send:s.connection.state==='working'}],status:{can_send:true,bot_messaging:true,worker_online:true}};
        if(path.endsWith('/sync'))return {state:'idle',server_time:Date.now()/1000,pending:0,batch:{},history_complete:true,connection:structuredClone(s.connection)};
        if(path.endsWith('/media'))return {items:[structuredClone(file)],more:false};
        if(path.includes('/messages'))return {items:[{id:1,created:1700000000,sender:'Анна',text:'Семейное сообщение',media:[]}],more:false};
        if(path.endsWith('/outbox')||path.endsWith('/site-sends'))return {items:[]};
        if(path.endsWith('/read'))return {ok:true};
        throw Error('Unexpected API '+path);
      }};
    await FamilyProfile.mountFamilyPhotos(document.querySelector('#gallery'),ctx);
    await FamilyProfile.mountChat(document.querySelector('#messages'),ctx);
  });
  const room=page.locator('[data-room="telegram:export:abcdef0123456789"]');await room.waitFor({timeout:5000}).catch(async error=>{throw Error((await page.locator('body').innerText())+' '+JSON.stringify(errors),{cause:error});});await room.click();
  await page.locator('[data-fp-title]').waitFor();
  await page.waitForFunction(()=>[...document.querySelectorAll('img')].every(img=>img.complete&&img.naturalWidth));
  assert.equal(await room.locator('strong').innerText(),'Лёвик');
  assert.equal(await page.locator('[data-fp-title]').innerText(),'Лёвик');
  const photosBefore=photoReads;
  await page.locator('#fp-message').fill('Мой черновик');
  await page.evaluate(()=>{
    const input=document.querySelector('#fp-message');input.focus();input.setSelectionRange(1,4);
    window.keptProfile={room:document.querySelector('[data-room]'),message:document.querySelector('[data-fp-message-id]'),input,photo:document.querySelector('.fp-tile img')};
    profileState.title='Лёвик · новое название';profileState.version='2';
  });
  await page.clock.runFor(16000);
  assert.equal(await room.locator('strong').innerText(),'Лёвик · новое название');
  assert.equal(await page.locator('[data-fp-title]').innerText(),'Лёвик · новое название');
  assert.equal(await page.locator('#gallery [data-fp-group]').innerText(),'Лёвик · новое название');
  assert.match(await room.locator('img').getAttribute('src'),/v=2$/);
  assert.match(await page.locator('[data-fp-identity] img').getAttribute('src'),/v=2$/);
  await page.waitForFunction(()=>[...document.querySelectorAll('[src="/avatar?v=2"]')].every(img=>img.complete&&img.naturalWidth));
  assert(await page.evaluate(()=>keptProfile.room===document.querySelector('[data-room]')&&keptProfile.message===document.querySelector('[data-fp-message-id]')&&keptProfile.photo===document.querySelector('.fp-tile img')),'metadata update preserves group identity and existing media/messages');
  assert(await page.evaluate(()=>keptProfile.input===document.querySelector('#fp-message')&&keptProfile.input.value==='Мой черновик'&&keptProfile.input.selectionStart===1&&keptProfile.input.selectionEnd===4&&document.activeElement===keptProfile.input),'metadata update preserves composer, draft, caret and focus');
  assert.equal(photoReads,photosBefore,'renaming and changing avatar do not reload photo collection');
  const changedAvatarReads=avatarReads;
  await page.clock.runFor(16000);assert.equal(avatarReads,changedAvatarReads,'unchanged avatar versions do not refetch no-store images');
  await page.evaluate(()=>{profileState.version=null;profileState.title='Лёвик без аватара';});
  await page.clock.runFor(16000);
  assert.equal(await room.locator('img').count(),0,'removed avatar disappears from sidebar');
  assert.equal(await page.locator('[data-fp-identity] img').count(),0,'removed avatar disappears from open conversation');
  assert.equal(await room.locator('strong').innerText(),'Лёвик без аватара');
  assert.equal(await page.locator('[data-fp-title]').innerText(),'Лёвик без аватара');
  assert.equal(await page.locator('#gallery [data-fp-group]').innerText(),'Лёвик без аватара');
  await page.evaluate(()=>profileState.connection.state='migrated');await page.clock.runFor(16000);
  assert((await page.locator('[data-fp-chat-status]').innerText()).includes('Группа преобразована'),JSON.stringify({text:await page.locator('[data-fp-chat-status]').innerText(),errors}));
  assert((await page.locator('#gallery [data-fp-connection]').getAttribute('title')).includes('новые сообщения здесь не появляются'));
  assert.equal(await page.locator('#fp-send-message').isVisible(),false);
  assert(await page.evaluate(()=>keptProfile.message===document.querySelector('[data-fp-message-id]')&&keptProfile.photo===document.querySelector('.fp-tile img')),'source status changes preserve message and photo DOM');
  await page.evaluate(()=>profileState.connection.state='access_lost');await page.clock.runFor(16000);
  assert((await page.locator('[data-fp-source-summary]').innerText()).includes('Нет доступа'));
  await page.evaluate(()=>{FamilyProfile.chatController.destroy();FamilyProfile.familyPhotosController.destroy();});
  assert.deepEqual(errors,[]);
  console.log('Family group profile passed: real gallery/sidebar/open-chat rename + avatar replacement/removal, stable IDs/media/draft/focus, versioned images without repeat fetches.');
}finally{await browser.close();}
