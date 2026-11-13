import {readAppStyles} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';

const engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const bundle=await build({stdin:{contents:`import {inboxController} from './web/modules/notifications/index.js';inboxController.attach=()=>{};export {mountChat,chatController} from './web/modules/chat/index.js';export {mountFamilyPhotoAccess} from './web/modules/admin/family-photos.js';`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'BotUI',write:false,loader:{'.css':'empty'}});
const browser=await(engine==='webkit'?webkit:chromium).launch({headless:true,...(engine==='webkit'||!process.env.DRG_BROWSER_CHANNEL?{}:{channel:process.env.DRG_BROWSER_CHANNEL})});
await mkdir('artifacts',{recursive:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844},isMobile:true,hasTouch:true}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true}));
 const png=await readFile('tests/fixtures/blue.png');
 await page.route('https://bot-ui.test/**',route=>route.request().url().includes('/uploads/')?route.fulfill({body:png,contentType:'image/png'}):route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta name="viewport" content="width=device-width,initial-scale=1"><html class="app-standalone" data-scheme="dark"><main id="app"><div class="workspace chat-open"><main class="content"><section id="view-content"></section></main></div></main></html>'}));
 await page.goto('https://bot-ui.test/');
 await page.clock.install();
 await page.addStyleTag({content:(await readAppStyles()).replace(/@font-face\s*\{[^}]*\}/g,'')});
 await page.addStyleTag({content:'.workspace{display:block!important;width:100%;height:100dvh!important}.content{margin:0!important;padding:0!important;height:100%}#view-content{height:100%}#app{position:fixed;inset:0}'});
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(async()=>{
   const now=Math.floor(Date.now()/1000),me={id:'a'.repeat(32),role:'owner',login:'Anna',display_name:'Анна',settings:{}},peer={id:'b'.repeat(32),login:'Boris',display_name:'Борис'};
   const state=window.botFixture={allowed:false,files:[],pending:[],messages:[{id:1,created:now-60,sender:'Борис',sender_id:'42',text:'Привет из Telegram',media:[]}],calls:[],next:10,holdUpload:false,ambiguous:false};
   const ctx=window.botCtx={state:{me},moduleEnabled:()=>true,navigate(){},toast(text){state.toast=text;},async api(path,method='GET',body,retry,headers,options={}){
     state.calls.push({path,method,body:body instanceof Blob?'blob':body});
     if(path==='/chat/rooms')return {items:[{id:'direct',name:'Борис',kind:'friends',members:[me,peer],favorite:false,pinned:false,unread:0}]};
     if(path==='/chat/typing')return {rooms:{},server_time:now};
     if(path.startsWith('/chat/direct/messages'))return {items:[],more:false,reads:{},server_time:now,edit_cursor:0};
     if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Семейная группа',kind:'supergroup',can_send:state.allowed,history_complete:true,unread:0}],status:{can_send:true,bot_messaging:true,bot_reactions:true,worker_online:true}};
     if(path.endsWith('/read'))return {ok:true};
     if(path.includes('/site-uploads/')){
       if(method==='DELETE')return {ok:true};
       if(state.holdUpload)return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
       if(!(body instanceof Blob))throw Error('Upload must be a Blob');
       const id=path.split('/site-uploads/')[1].split('?')[0],params=new URLSearchParams(path.split('?')[1]);
       const file={id,name:params.get('name'),kind:params.get('mode')==='file'?'file':'photo',mime:params.get('mode')==='file'?'application/octet-stream':'image/png',size:body.size,state:'ready',site_upload:true,url:'/uploads/'+id};state.files.push(file);return structuredClone(file);
     }
     if(path.endsWith('/site-sends'))return {items:structuredClone(state.pending)};
     if(path.endsWith('/cancel')){state.pending=[];return {ok:true};}
     if(path.endsWith('/reactions')){
       const message=state.messages.find(item=>item.id===Number(path.split('/').at(-2)));
       message.site_reactions=message.site_reactions?.length?[]:[{emoji:body.emoji,count:1,people:[me],source:'site'}];
       message.bot_reaction_sync={state:'queued'};
       return {reactions:structuredClone(message.site_reactions),bot_reaction_sync:message.bot_reaction_sync};
     }
     if(path.includes('/family-photos/')&&path.includes('/messages')){
       if(method==='POST'){
         let job=state.pending.find(job=>job.client_id===body.client_id);
         if(!job){job={id:('c'+state.next++).padEnd(32,'c'),actor_id:me.id,site_actor_id:me.id,created:now,text:body.text,state:'queued',client_id:body.client_id,media:state.files.filter(file=>body.attachments.includes(file.id)),message_ids:[]};state.pending.push(job);}
         if(state.ambiguous){state.ambiguous=false;throw Error('Ответ потерян. Можно повторить.');}
         return structuredClone(job);
       }
       if(method==='PUT'){const message=state.messages.find(message=>message.id===Number(path.split('/').at(-1)));message.site_edit_state='edit_queued';state.edit={message,text:body.text};return {state:'edit_queued'};}
       return {items:structuredClone(state.messages),more:false};
     }
     if(path.endsWith('/outbox'))return {items:[]};
     throw Error('Unexpected '+method+' '+path);
   }};
   window.deliver=()=>{const job=state.pending[0];state.messages.push({id:state.next++,created:now,sender:'Анна',sender_id:'site:'+me.id,site_actor_id:me.id,site_send_id:job.id,site_primary:true,site_revision:0,site_edit_state:'delivered',site_editable_until:now+86400,site_profile:me,text:job.text,media:structuredClone(job.media)});state.pending=[];};
   await BotUI.mountChat(document.querySelector('#view-content'),ctx);
 });
 await page.locator('[data-room="telegram:-1001"]').click();
 await expect(page.locator('[data-fp-chat-status]')).toHaveText('Эта группа доступна только для чтения.');
 await expect(page.locator('[data-fp-compose]')).toBeHidden();
 await page.evaluate(()=>botFixture.allowed=true);await page.clock.runFor(6000);
 await expect(page.locator('[data-fp-compose]')).toBeVisible();await expect(page.locator('[data-fp-chat-status]')).toBeHidden();
 const input=page.locator('#fp-message');await input.fill('Семейная фотография');
 await page.locator('[data-attach-chat]').click();
 await expect(page.getByRole('menu',{name:'Прикрепить'}).getByRole('menuitem')).toHaveText(['Фото или видео','Файл','Камера','Голосовое сообщение','Видеокружок']);
 const chooser=page.waitForEvent('filechooser');await page.getByRole('menuitem',{name:'Фото или видео',exact:true}).click();await(await chooser).setFiles('tests/fixtures/blue.png');
 await expect(page.locator('[data-chat-drafts] img')).toBeVisible();
 await page.locator('[data-fp-compose] [type=submit]').click();
 await expect(page.locator('[data-fp-pending].mine')).toContainText('Семейная фотография');await expect(input).toHaveValue('');
 await page.evaluate(()=>deliver());await page.clock.runFor(6000);
 await expect(page.locator('[data-fp-pending]')).toHaveCount(0);
 const mine=page.getByRole('log').locator('.fp-chat-message.mine');await expect(mine).toHaveCount(1);await expect(mine).toContainText('Семейная фотография');
 await expect(mine.locator('.chat-message-meta,.chat-message-avatar')).toHaveCount(0);
 await mine.dispatchEvent('contextmenu',{clientX:220,clientY:300});await page.locator('[data-message-action=edit]').click();
 await expect(input).toHaveValue('Семейная фотография');await input.fill('Исправленная подпись');await page.locator('[data-fp-compose] [type=submit]').click();
 await expect(mine.locator('.chat-message-text')).toHaveText('Семейная фотография');
 await page.evaluate(()=>{botFixture.edit.message.text=botFixture.edit.text;botFixture.edit.message.site_revision=1;botFixture.edit.message.edited=Math.floor(Date.now()/1000);botFixture.edit.message.site_edit_state='delivered';});await page.clock.runFor(6000);
 await expect(mine).toContainText('Исправленная подпись');await expect(mine).toContainText('изменено');
 await page.locator('[data-fp-message-id="1"]').dispatchEvent('contextmenu',{clientX:120,clientY:100});await expect(page.locator('[data-message-action=edit]')).toHaveCount(0);await page.keyboard.press('Escape');
 await mine.dispatchEvent('contextmenu',{clientX:220,clientY:300});await page.locator('[data-more-reactions]').last().click();
 await expect(page.locator('.chat-reaction-picker')).toContainText('самую популярную доступную реакцию');
 await page.locator('[data-choose-reaction="👍"]').click();await expect(mine.locator('.fp-reaction-status')).toContainText('Ожидает отправки');
 await page.evaluate(()=>{const message=botFixture.messages.find(m=>m.site_actor_id);message.bot_reaction_sync={state:'delivered'};});await page.clock.runFor(6000);
 await expect(mine.locator('.fp-reaction-status')).toHaveCount(0);await expect(mine.locator('.chat-reactions')).toContainText('👍');
 // Ambiguous requests keep both the draft and their idempotency ID.
 await input.fill('Не продублировать');await page.evaluate(()=>botFixture.ambiguous=true);await page.locator('[data-fp-compose] [type=submit]').click();await expect(input).toHaveValue('Не продублировать');
 await page.locator('[data-fp-compose] [type=submit]').click();await expect(input).toHaveValue('');
 assert.equal(await page.evaluate(()=>botFixture.pending.length),1);
 const ids=await page.evaluate(()=>botFixture.calls.filter(call=>call.method==='POST'&&call.path.endsWith('/messages')).slice(-2).map(call=>call.body.client_id));assert.equal(ids[0],ids[1]);
 await page.locator('[data-fp-cancel-send]').click();await expect(page.locator('[data-fp-pending]')).toHaveCount(0);
 // Cancellation leaves text intact and releases the unknown upload ID.
 await input.fill('Черновик');await page.evaluate(()=>botFixture.holdUpload=true);await page.locator('[data-attach-chat]').click();
 const fileChooser=page.waitForEvent('filechooser');await page.getByRole('menuitem',{name:'Файл',exact:true}).click();await(await fileChooser).setFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('текст')});
 await expect(page.locator('[data-cancel-chat-upload]')).toBeVisible();await page.locator('[data-cancel-chat-upload]').click();await expect(page.locator('[data-cancel-chat-upload]')).toBeHidden();await expect(input).toHaveValue('Черновик');
 await page.evaluate(()=>botFixture.holdUpload=false);
 for(const width of [390,320,1440])for(const scheme of ['dark','light']){
   await page.setViewportSize({width,height:844});await page.locator('html').evaluate((el,scheme)=>el.dataset.scheme=scheme,scheme);
   const tg=await page.locator('[data-fp-compose]').evaluate(form=>({padding:getComputedStyle(form).padding,gap:getComputedStyle(form).gap,button:[...form.querySelectorAll('[type=submit],[data-recording-hold]')].find(button=>button.getClientRects().length).getBoundingClientRect().height,font:getComputedStyle(form.querySelector('textarea')).fontSize}));
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   if(width===390&&scheme==='dark')await page.screenshot({path:`artifacts/telegram-composer-${engine}.png`});
   if(width<=760)await page.locator('[data-fp-back]').click();
   await page.locator('[data-room=direct]').click();await page.locator('#send-message textarea').fill('Черновик');
   const native=await page.locator('#send-message').evaluate(form=>({padding:getComputedStyle(form).padding,gap:getComputedStyle(form).gap,button:[...form.querySelectorAll('[type=submit],[data-recording-hold]')].find(button=>button.getClientRects().length).getBoundingClientRect().height,font:getComputedStyle(form.querySelector('textarea')).fontSize}));
   assert.deepEqual(tg,native,'Telegram and direct composers use identical geometry');
   if(width<=760)await page.locator('[data-chat-back]').click();
   await page.locator('[data-room="telegram:-1001"]').click();
 }
 await page.evaluate(()=>botFixture.allowed=false);await page.clock.runFor(6000);
 await expect(page.locator('[data-fp-compose]')).toBeHidden();await expect(page.locator('[data-fp-chat-status]')).toHaveText('Эта группа доступна только для чтения.');
 assert.deepEqual(errors,[]);await page.evaluate(()=>BotUI.chatController.destroy());
 await page.evaluate(()=>{
   document.querySelector('#view-content').innerHTML='<details open class="preference-row"><summary>Семейные фото<span data-preference-status></span></summary><section id="access" class="admin-family-photos preference-body"></section></details>';
   const groups=[{id:'-1001',title:'Семейная группа',kind:'supergroup',enabled:true,connection:{state:'migrated',checked_at:1792600000,last_success:1792590000},bot_reason:'not_member'},{id:'-42',title:'Обычная группа',kind:'group',enabled:true},{id:'export:abc',title:'Копия',kind:'export',copy_created:1,enabled:true}];
   const data={users:[{id:'b'.repeat(32),login:'Boris',role:'member',enabled:true,groups:['-1001','-42','export:abc'],write_groups:[]}],groups,status:{available:true},server_time:Math.floor(Date.now()/1000)};
   BotUI.mountFamilyPhotoAccess(document.querySelector('#access'),{state:botCtx.state,async api(path,method,body){if(method==='PUT'){window.savedGrant=body;return {ok:true,...body};}return structuredClone(data);}});
 });
 await expect(page.locator('[data-fp-managed-group="-1001"]')).toContainText('Группа преобразована');
 await expect(page.locator('[data-fp-managed-group="-1001"]')).toContainText('Отправка: старый архив');
 await page.locator('[data-fp-user] summary').click();
 await expect(page.locator('.fp-access-permissions th')).toHaveText(['Группа','Просмотр','Отправка']);
 await expect(page.getByRole('checkbox',{name:'Отправка: Обычная группа',exact:true})).toBeDisabled();
 await expect(page.getByRole('checkbox',{name:'Отправка: Копия',exact:true})).toBeDisabled();
 const view=page.getByRole('checkbox',{name:'Просмотр: Семейная группа',exact:true}),write=page.getByRole('checkbox',{name:'Отправка: Семейная группа',exact:true});
 await write.check();await page.getByRole('button',{name:'Сохранить доступ'}).click();
 await expect(page.locator('[data-fp-grant-status]')).toContainText('Доступ сохранён');
 assert.deepEqual(await page.evaluate(()=>savedGrant.write_groups),['-1001']);
 await view.uncheck();await expect(write).toBeDisabled();await expect(write).not.toBeChecked();
 await page.getByRole('button',{name:'Сохранить доступ'}).click();
 await expect(page.locator('[data-fp-grant-status]')).toContainText('Доступ сохранён');
 assert.deepEqual(await page.evaluate(()=>savedGrant.write_groups),[]);
 for(const width of [390,1440]){
   await page.setViewportSize({width,height:1000});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'management table stays within the viewport');
   await page.screenshot({path:`artifacts/family-connection-admin-${width}-${engine}.png`});
 }
 assert.deepEqual(errors,[]);
 console.log('Telegram bot UI passed: permissions/read-only, identical native composer, image upload, own delivered bubble, edit confirmation, idempotent retry, cancellation, themes and 320–1440px');
}finally{await browser.close();}
