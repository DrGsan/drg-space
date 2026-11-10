// Exercise the actual module and shared DOM reconciler in Chromium, without a live account.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,
  format:'iife',globalName:'FamilyPhotoRefresh',write:false,loader:{'.css':'empty'}});
const png=await readFile('tests/fixtures/blue.png');
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true});

async function readyImage(page,selector){
  const started=Date.now();
  while(Date.now()-started<5000){
    if(await page.locator(selector).first().evaluate(img=>img.complete&&img.naturalWidth>0))return;
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  throw Error('Fixture image did not load');
}

async function fixture(mode,versions=false){
  const page=await browser.newPage({locale:process.env.DRG_BROWSER_LOCALE||'ru-RU'});let mediaRequests=0;
  await page.route('http://family-refresh.test/**',route=>{
    if(route.request().url().includes('/media/')){
      mediaRequests++;return route.fulfill({body:png,contentType:'image/png',headers:{'Cache-Control':'no-store'}});
    }
    return route.fulfill({contentType:'text/html',body:'<main id="fixture"></main>'});
  });
  await page.goto('http://family-refresh.test/');
  await page.clock.install();
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async ({mode,versions})=>{
    const file=id=>({id,group_id:'-1001',message_id:id==='a'?1:2,created:1700000000,
      kind:'photo',state:'ready',sender:'Анна',name:id+'.png',url:'/media/'+id+'.png',thumbnail_url:'/media/'+id+'.png'});
    const state=window.fixtureState={calls:0,paths:[],version:1,reads:[],groups:[{id:'-1001',title:'Семейная группа',history_complete:true,
      media_pending:0,state:'online',can_send:true}],files:[file('a'),file('b')],
      messages:[{id:1,created:1700000000,sender:'Анна',text:'Первое сообщение',media:[file('a')]},
        {id:2,created:1700000001,sender:'Анна',text:'Второе сообщение',media:[file('b')]}]};
    const ctx={state:{me:{id:'owner',role:'owner'},chatSpace:'telegram:-1001'},moduleEnabled:()=>true,navigate(){},toast(){},
      modal(markup){const d=document.createElement('div');d.innerHTML='<button class="modal-close">Закрыть</button>'+markup;
        d.querySelector('button').onclick=()=>d.remove();document.body.append(d);return d;},
      async api(path,method,body){
        state.calls++;
        state.paths.push(path);
        if(path.endsWith('/read')){state.reads.push(body.message_id);return {ok:true};}
        if(path==='/family-photos/groups')return structuredClone({items:state.groups,status:{can_send:true,bot_messaging:true,worker_online:true,gallery_versions:versions}});
        if(path.endsWith('/sync'))return {state:'idle',history_complete:true,pending:0};
        if(path.includes('/media-version'))return {version:String(state.version)};
        if(path.includes('/media'))return structuredClone({items:state.files,more:false});
        if(path.includes('/messages'))return structuredClone({items:state.messages,more:false});
        if(path.includes('/outbox')||path.includes('/site-sends'))return {items:[]};
        throw Error('Unexpected test API path');
      }};
    const host=document.querySelector('#fixture');
    if(mode==='gallery')await FamilyPhotoRefresh.mountFamilyPhotos(host,ctx);
    else if(mode==='chat')window.mounted=FamilyPhotoRefresh.mountTelegramChat(host,ctx,'-1001',()=>{});
    else window.mounted=await FamilyPhotoRefresh.watchTelegramRooms(ctx,items=>{window.receivedRooms=items;});
  },{mode,versions});
  return {page,requests:()=>mediaRequests};
}

try{
  const {page,requests}=await fixture('gallery');
  await readyImage(page,'.fp-tile img');
  assert.equal(await page.locator('[data-fp-admin],[data-fp-admin-form]').count(),0,'gallery contains no access management');
  await page.evaluate(()=>{
    window.kept={image:document.querySelector('.fp-tile img'),tile:document.querySelector('.fp-tile'),grid:document.querySelector('.fp-grid')};
    kept.tile.focus();
  });
  const before=requests();
  await page.clock.runFor(21000);
  assert(await page.evaluate(()=>fixtureState.calls>=6),'gallery completed multiple background polls');
  assert(await page.evaluate(()=>kept.image===document.querySelector('.fp-tile img')&&kept.tile.isConnected&&kept.grid===document.querySelector('.fp-grid')),
    'unchanged gallery images and grid survive background polling');
  assert.equal(requests(),before,'background polling must not refetch unchanged no-store images');
  assert(await page.evaluate(()=>document.activeElement===kept.tile),'gallery focus survives polling');
  await page.evaluate(()=>{fixtureState.groups[0].media_pending=1;fixtureState.files.unshift({...fixtureState.files[0],id:'new',message_id:3,url:'/media/new.png',thumbnail_url:'/media/new.png'});});
  await page.clock.runFor(11000);
  assert.equal(await page.locator('.fp-tile').count(),3,'new photos still appear');
  assert(await page.evaluate(()=>kept.image===document.querySelectorAll('.fp-tile img')[1]&&kept.image.isConnected&&document.activeElement===kept.tile),
    'an inserted photo and status change preserve existing images and focus');
  await page.close();

  const versioned=await fixture('gallery',true),vp=versioned.page;
  await readyImage(vp,'.fp-tile img');
  await vp.clock.runFor(21000);
  assert.equal(await vp.evaluate(()=>fixtureState.paths.filter(p=>p.endsWith('/media')).length),1,'unchanged version skips gallery pages');
  assert(await vp.evaluate(()=>fixtureState.paths.filter(p=>p.endsWith('/sync')).length<=3),'idle sync status polls once per ten seconds');
  await vp.evaluate(()=>{fixtureState.files.pop();fixtureState.version++;});
  await vp.clock.runFor(11000);
  assert.equal(await vp.locator('.fp-tile').count(),1,'deletion without a newer message still refreshes visible photos');
  await vp.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  const hiddenCalls=await vp.evaluate(()=>fixtureState.calls);
  await vp.clock.runFor(31000);
  assert.equal(await vp.evaluate(()=>fixtureState.calls),hiddenCalls,'hidden gallery stops all polling');
  await vp.evaluate(()=>{fixtureState.files[0].sender='Новое имя';fixtureState.version++;Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
  await vp.clock.runFor(100);
  assert.match(await vp.locator('.fp-tile').getAttribute('aria-label'),/Новое имя/,'returning to the gallery checks for edits immediately');
  await vp.evaluate(()=>FamilyPhotoRefresh.familyPhotosController.destroy());
  const destroyedCalls=await vp.evaluate(()=>fixtureState.calls);
  await vp.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await vp.clock.runFor(31000);
  assert.equal(await vp.evaluate(()=>fixtureState.calls),destroyedCalls,'destroy removes visibility listener and timers');
  await vp.close();

  const chat=await fixture('chat');
  await readyImage(chat.page,'.fp-chat-message img');
  await chat.page.locator('#fp-message').fill('Черновик ответа');
  await chat.page.evaluate(()=>{
    const input=document.querySelector('#fp-message');input.focus();input.setSelectionRange(1,4);
    window.kept={message:document.querySelector('.fp-chat-message'),image:document.querySelector('.fp-chat-message img'),input};
  });
  const chatBefore=chat.requests();
  await chat.page.clock.runFor(21000);
  assert(await chat.page.evaluate(()=>fixtureState.calls>=12),'chat completed multiple background polls');
  assert(await chat.page.evaluate(()=>kept.message===document.querySelector('.fp-chat-message')&&kept.image===document.querySelector('.fp-chat-message img')),
    'chat articles and media survive polling');
  assert.equal(chat.requests(),chatBefore,'chat polling must not refetch unchanged media');
  assert(await chat.page.evaluate(()=>kept.input.value==='Черновик ответа'&&kept.input.selectionStart===1&&kept.input.selectionEnd===4),
    'draft and caret survive polling');
  await chat.page.evaluate(()=>{
    kept.input.blur();const range=document.createRange();range.selectNodeContents(document.querySelector('.fp-chat-message p'));
    getSelection().removeAllRanges();getSelection().addRange(range);
  });
  await chat.page.clock.runFor(6000);
  assert.equal(await chat.page.evaluate(()=>getSelection().toString()),'Первое сообщение','text selection survives polling');
  await chat.page.evaluate(()=>fixtureState.messages.forEach(m=>m.album_id='77'));
  await chat.page.clock.runFor(6000);
  assert.equal(await chat.page.locator('.fp-chat-message').count(),1,'source album renders as one bubble');
  assert.equal(await chat.page.locator('.chat-media-tile').count(),2,'all album members remain visible');
  assert.equal(await chat.page.locator('[data-fp-source-message-id="2"]').count(),1,'secondary message retains a reply jump anchor');
  await readyImage(chat.page,'.chat-media-tile img');
  await chat.page.evaluate(()=>window.albumImage=document.querySelector('.chat-media-tile img'));
  const albumRequests=chat.requests();await chat.page.clock.runFor(6000);
  assert(await chat.page.evaluate(()=>albumImage===document.querySelector('.chat-media-tile img')&&albumImage.isConnected),'album polling preserves its image node');
  assert.equal(chat.requests(),albumRequests,'album polling does not refetch media');
  assert.deepEqual(await chat.page.evaluate(()=>fixtureState.reads),[2],'visible initial chat sends one read receipt');
  await chat.page.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}));
  await chat.page.evaluate(()=>fixtureState.messages.push({id:3,created:1700000002,sender:'Иван',text:'Новое сообщение',media:[]}));
  await chat.page.clock.runFor(6000);
  assert.equal(await chat.page.locator('.fp-chat-message').count(),2,'new messages still appear');
  assert.deepEqual(await chat.page.evaluate(()=>fixtureState.reads),[2],'hidden chat does not read arriving messages');
  await chat.page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
  await chat.page.clock.runFor(100);
  assert.deepEqual(await chat.page.evaluate(()=>fixtureState.reads),[2,3],'returning to the visible chat advances the read cursor');
  assert(await chat.page.evaluate(()=>albumImage.isConnected&&kept.message===document.querySelector('.fp-chat-message')),
    'appending messages preserves older articles');
  // The same hold menu works for imported albums without exposing write
  // actions in a read-only export, or retaining an overlay after revocation.
  await chat.page.setViewportSize({width:390,height:844});
  await chat.page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{async writeText(text){window.copiedText=text;}}}));
  await chat.page.locator('[data-fp-message-id="3"]').dispatchEvent('contextmenu',{clientX:50,clientY:80});
  assert.equal(await chat.page.locator('[data-message-action=reply]').count(),1,'live writable group offers reply');
  await chat.page.evaluate(()=>fixtureState.groups[0].can_send=false);await chat.page.clock.runFor(6000);
  assert.equal(await chat.page.locator('[data-message-action=reply]').count(),0,'read-only transition removes reply from an open menu');
  await chat.page.locator('[data-message-action=copy]').click();
  assert.equal(await chat.page.evaluate(()=>window.copiedText),'Новое сообщение','archive copy uses message text');
  await chat.page.clock.runFor(200);
  await chat.page.locator('[data-fp-message-id="1"]').dispatchEvent('contextmenu',{clientX:50,clientY:80});
  assert.equal(await chat.page.locator('[data-message-action=reply],[data-message-action=edit],[data-message-action=delete]').count(),0,'read-only exports expose no write actions');
  assert.equal(await chat.page.locator('[data-message-action=media]').count(),1,'albums retain their viewer action');
  await chat.page.evaluate(()=>mounted.destroy());
  assert.equal(await chat.page.locator('.chat-context-layer,.chat-message-menu').count(),0,'leaving the archive removes its action overlay');
  await chat.page.close();

  const sidebar=await fixture('sidebar');
  await sidebar.page.clock.runFor(31000);
  assert(await sidebar.page.evaluate(()=>fixtureState.calls>=6),'room watcher completed multiple background polls');
  assert.deepEqual(await sidebar.page.evaluate(()=>receivedRooms.map(room=>({id:room.id,kind:room.kind,name:room.name,body:room.last_message.body,attachment:room.last_message.attachments[0].kind}))),
    [{id:'telegram:-1001',kind:'family',name:'Семейная группа',body:'Второе сообщение',attachment:'image'}],
    'external groups use the ordinary family room model and latest message preview');
  await sidebar.page.close();
  console.log('Family photo refresh passed: stable gallery/chat nodes and shared family room model, no media refetch, preserved focus/draft/selection, live inserts.');
}finally{await browser.close();}
