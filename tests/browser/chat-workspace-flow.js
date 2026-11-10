import {expect} from '@playwright/test';
import {chatRecoveryFlow} from './chat-recovery-flow.js';
import {chatEditingFlow} from './chat-editing-flow.js';
import {readFileSync} from 'node:fs';
import {appSection} from './notes-ui.js';

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}

async function viewportFits(page){
  await expect.poll(()=>page.evaluate(()=>({horizontal:document.documentElement.scrollWidth<=innerWidth,vertical:document.documentElement.scrollHeight<=innerHeight+1}))).toEqual({horizontal:true,vertical:true});
  // visualViewport/--app-height settles asynchronously after a phone resize.
  await expect.poll(async()=>{
    const composer=await page.locator('.chat-compose').boundingBox();
    return composer ? composer.y+composer.height : Infinity;
  }).toBeLessThanOrEqual(page.viewportSize().height+1);
}

async function uploadFixture(page,path,fixture){
  const csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||''));
  const response=await page.request.post('/api'+path,{headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':csrf,Origin:'http://127.0.0.1:8001'},data:readFileSync(fixture)});
  expect(response.status(),await response.text()).toBe(200);return response.json();
}

// Background synchronization must update content without remounting immutable
// images or video. An image may come from cache and still flash, so checking
// only completed requests would miss the regression: keep actual DOM handles.
async function stableChatMediaFlow(page,peer,suffix){
  await page.bringToFront();
  const profile=await api(peer,'/me');
  await uploadFixture(peer,'/account/avatar','tests/fixtures/blue.png');
  const invitation=await api(page,'/social/friends','POST',{login:profile.login});
  const accepted=await api(peer,'/social/requests/'+invitation.id+'/accept','POST');
  const sid=accepted.space_id,room=page.locator(`[data-room="${sid}"]`);
  try{
    const image=await uploadFixture(peer,`/chat/${sid}/attachments?name=blue.png`,'tests/fixtures/blue.png');
    const video=await uploadFixture(peer,`/chat/${sid}/attachments?name=chat-video.mp4`,'tests/fixtures/chat-video.mp4');
    const sent=await api(peer,`/chat/${sid}/messages`,'POST',{body:'Медиа без мерцания '+suffix,attachments:[image.id,video.id],client_id:'workspace-stable-media-'+suffix});
    await page.locator('[data-chat-filter=all]').click();
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await expect(room).toBeVisible();await room.click();
    const message=page.locator(`[data-message-id="${sent.id}"]`),media=message.locator('video');
    await expect(page.locator('.chat-header-identity img')).toHaveCount(1);
    await expect(room.locator('img')).toHaveCount(1);
    await expect(message.locator('.chat-message-avatar img')).toHaveCount(1);
    await expect(message.locator('.chat-media-album img')).toHaveCount(1);
    await expect(media).toHaveCount(1);
    await expect.poll(()=>page.locator('.chat-header-identity img,.chat-room.active img,.chat-message-avatar img,.chat-media-album img').evaluateAll(images=>images.every(img=>img.complete&&img.naturalWidth>0))).toBe(true);
    await expect.poll(()=>media.evaluate(video=>video.readyState)).toBeGreaterThan(0);
    const position=await media.evaluate(video=>{video.pause();video.volume=.25;video.playbackRate=1.25;video.currentTime=Math.min(.2,video.duration/2);return video.currentTime;});
    expect(position).toBeGreaterThan(0);
    await expect.poll(()=>media.evaluate(video=>video.seeking)).toBe(false);
    const draft='Несохранённый черновик '+suffix;
    await page.getByLabel('Сообщение',{exact:true}).fill(draft);
    await page.evaluate(({sid,id})=>{
      const selectors={room:`[data-room="${sid}"]`,roomAvatar:`[data-room="${sid}"] img`,header:'.chat-header-identity',headerAvatar:'.chat-header-identity img',message:`[data-message-id="${id}"]`,senderAvatar:`[data-message-id="${id}"] .chat-message-avatar img`,image:`[data-message-id="${id}"] .chat-media-album img`,video:`[data-message-id="${id}"] video`,composer:'#message-body'};
      const nodes=Object.fromEntries(Object.entries(selectors).map(([key,selector])=>[key,document.querySelector(selector)]));
      const saved=window.__chatRefreshBaseline={selectors,nodes,position:nodes.video.currentTime,mediaLoads:0};
      for(const key of ['roomAvatar','headerAvatar','senderAvatar','image','video'])nodes[key].addEventListener(key==='video'?'loadedmetadata':'load',()=>saved.mediaLoads++);
    },{sid,id:sent.id});
    const stable=async()=>{
      expect(await page.evaluate(()=>{
        const saved=window.__chatRefreshBaseline,video=saved.nodes.video;
        return {nodes:Object.fromEntries(Object.entries(saved.selectors).map(([key,selector])=>[key,saved.nodes[key]===document.querySelector(selector)&&saved.nodes[key].isConnected])),mediaLoads:saved.mediaLoads,position:Math.abs(video.currentTime-saved.position)<.02,paused:video.paused,volume:video.volume,rate:video.playbackRate};
      })).toEqual({nodes:{room:true,roomAvatar:true,header:true,headerAvatar:true,message:true,senderAvatar:true,image:true,video:true,composer:true},mediaLoads:0,position:true,paused:true,volume:.25,rate:1.25});
      await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue(draft);
    };
    const synchronization=async trigger=>{
      const rooms=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/chat/rooms'&&response.request().method()==='GET',{timeout:26000});
      const catchup=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname===`/api/chat/${sid}/messages`&&url.searchParams.has('after')&&response.request().method()==='GET';},{timeout:26000});
      await trigger?.();
      const responses=await Promise.all([rooms,catchup]);for(const response of responses){expect(response.status()).toBe(200);await response.finished();}
      expect((await responses[1].json()).items).toEqual([]);
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    await synchronization(()=>page.evaluate(()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('drg-inbox-change'));}));
    await stable();
    // Exercise an actual background timer, rather than only manually emitted
    // events: empty catch-up batches used to replace the whole message log.
    await synchronization();await stable();
    const incoming='Следующее сообщение сохраняет медиа '+suffix;
    await api(peer,`/chat/${sid}/messages`,'POST',{body:incoming,client_id:'workspace-stable-new-'+suffix});
    await expect(page.locator('.chat-messages')).toContainText(incoming);
    await expect(room).toContainText(incoming);await stable();
    await room.click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await stable();
    await page.getByLabel('Сообщение',{exact:true}).fill('');
  }finally{
    await page.evaluate(()=>delete window.__chatRefreshBaseline);
    await api(page,'/social/spaces/'+sid,'DELETE');
  }
}

// Messages stay in a disposable test family and use isolated test accounts.
// This exercises real chat APIs without contacting another person.
export async function chatWorkspaceFlow(page){
  const viewport=page.viewportSize(),suffix=Date.now().toString(36),name='Беседа QA '+suffix;
  let peerContext,readRequestListener,droppedLive=0;
  const blockedLiveBodies=new Set();
  const room=await api(page,'/social/families','POST',{name});
  // Keep the real WebSocket transport, dropping only the later gap fixtures.
  await page.routeWebSocket('**/api/live/chat/'+room.id,socket=>{
    const server=socket.connectToServer();
    server.onMessage(packet=>{
      const event=JSON.parse(String(packet));
      if(['message','message_edited'].includes(event.type)&&blockedLiveBodies.has(event.message.body)){droppedLive++;return;}
      socket.send(packet);
    });
  });
  const source='Проверка поиска: поездка в субботу '+suffix;
  const original=await api(page,`/chat/${room.id}/messages`,'POST',{body:source,client_id:'workspace-source-'+suffix});
  await api(page,`/chat/${room.id}/messages`,'POST',{body:'Другая тема беседы '+suffix,client_id:'workspace-other-'+suffix});
  const button=()=>page.locator(`[data-room="${room.id}"]`);
  const history=()=>api(page,`/chat/${room.id}/messages`);
  try{
    await page.setViewportSize({width:1280,height:900});
    await appSection(page,'Сообщения');
    await button().click();
    await expect(page.locator('.chat-shell')).toHaveClass(/room-open/);
    await expect(page.locator('.sidebar,.topbar')).toHaveCount(0);
    await expect(page.locator('.chat-header')).toContainText(name);
    await expect(page.locator('.chat-messages')).toContainText(source);
    await expect(button()).toContainText('Другая тема беседы');
    const logBox=await page.locator('.chat-messages').boundingBox(),ownBox=await page.locator(`[data-message-id="${original.id}"]`).boundingBox();
    expect(ownBox.x+ownBox.width).toBeGreaterThan(logBox.x+logBox.width-45);
    await viewportFits(page);

    await page.locator('[data-chat-room-search]').fill(name);
    await expect(page.locator('[data-room]')).toHaveCount(1);
    await page.locator('[data-chat-room-search]').fill('Нет такой беседы '+suffix);
    await expect(page.locator('[data-room]')).toHaveCount(0);
    await page.locator('[data-chat-room-search]').fill('');
    await page.locator('[data-chat-filter=direct]').click();
    await expect(button()).toHaveCount(0);
    await expect.poll(()=>page.locator('[data-room]').count()).toBeGreaterThan(0);
    await page.locator('[data-chat-filter=groups]').click();
    await expect(button()).toBeVisible();
    await page.locator('[data-chat-filter=all]').click();

    // Background room summaries may arrive after the user changes a preference.
    // Hold an actual older snapshot so it cannot undo the saved star in the UI.
    let releaseSummary,summaryReady,summaryForwarded,heldSummary=false;
    const summaryGate=new Promise(resolve=>releaseSummary=resolve),summaryStarted=new Promise(resolve=>summaryReady=resolve);
    const roomsPath='**/api/chat/rooms';
    const staleSummary=async route=>{
      if(heldSummary){await route.continue();return;}
      heldSummary=true;const response=await route.fetch();summaryReady();
      summaryForwarded=summaryGate.then(()=>route.fulfill({response}));await summaryForwarded;
    };
    await page.route(roomsPath,staleSummary);
    try{
      await expect.poll(async()=>{
        await page.evaluate(()=>window.dispatchEvent(new Event('focus')));return heldSummary;
      }).toBe(true);
      await summaryStarted;
      await page.locator('[data-chat-favorite]').click();
      await expect(page.getByRole('button',{name:'Убрать беседу из избранного',exact:true})).toBeVisible();
      await expect.poll(async()=>((await api(page,'/chat/rooms')).items.find(item=>item.id===room.id)).favorite).toBe(true);
      releaseSummary();await summaryForwarded;
      await page.locator('[data-chat-filter=favorites]').click();
      await expect(button()).toBeVisible();
      await expect(page.locator('[data-room]')).toHaveCount(1);
    }finally{releaseSummary();await page.unroute(roomsPath,staleSummary);}
    // A notification's existing validated URL opens the intended conversation,
    // including after a reload, independently of the last selected room.
    await page.goto('/#chat/'+room.id);
    await expect(page.locator('.chat-header')).toContainText(name);
    await expect(page.getByRole('button',{name:'Убрать беседу из избранного',exact:true})).toBeVisible();

    await page.locator(`[data-message-id="${original.id}"]`).hover();
    await page.locator(`[data-message-id="${original.id}"]`).click({button:"right"});await page.getByRole("menuitem",{name:"Ответить",exact:true}).click();
    await expect(page.locator('[data-chat-reply]')).toContainText(source);
    const body=page.getByLabel('Сообщение',{exact:true});
    await body.fill('Ответ на поездку '+suffix);
    await body.press('Shift+Enter');await body.pressSequentially('Вторая строка');
    expect((await history()).items).toHaveLength(2);
    let lostResponse=false;
    const messagePath='**/api/chat/'+room.id+'/messages';
    const lostAcknowledgment=async route=>{
      if(route.request().method()!=='POST'||lostResponse){await route.continue();return;}
      lostResponse=true;
      const committed=await route.fetch();expect(committed.status()).toBe(200);
      await route.fulfill({status:503,json:{detail:'Тестовый обрыв после записи сообщения'}});
    };
    await page.route(messagePath,lostAcknowledgment);
    try{
      await body.press('Enter');
      await expect(page.locator('#toast')).toContainText('Тестовый обрыв после записи сообщения');
      await expect(body).toHaveValue('Ответ на поездку '+suffix+'\nВторая строка');
      await expect(page.locator('[data-chat-reply]')).toContainText(source);
      await body.press('Enter');
      await expect(body).toHaveValue('');
    }finally{await page.unroute(messagePath,lostAcknowledgment);}
    await expect.poll(async()=>{
      const items=(await history()).items,last=items.at(-1);
      return {count:items.length,body:last.body,reply:last.reply?.id};
    }).toEqual({count:3,body:'Ответ на поездку '+suffix+'\nВторая строка',reply:original.id});
    await expect(page.locator(`[data-reply-target="${original.id}"]`)).toContainText(source);
    await expect(page.locator('[data-chat-reply]')).toBeHidden();
    await expect(button()).toContainText('Ответ на поездку');

    await page.locator('[data-open-chat-search]').click();
    await page.locator('[data-chat-search]').fill('поездка в субботу');
    await expect(page.locator('[data-chat-search-results]')).toContainText(source);
    await expect(page.locator('[data-chat-search-results]')).not.toContainText('Другая тема беседы');
    await page.locator('[data-close-chat-search]').click();
    await expect(page.locator('[data-chat-search]')).toBeHidden();

    // Seeing a conversation behind an open modal must not dismiss its push
    // notification. It becomes read only when the conversation is visible again.
    const login='chatqa_'+suffix,password='Chat-QA-password-123';
    const invitation=await api(page,'/invites','POST',{login});
    peerContext=await page.context().browser().newContext({baseURL:'http://127.0.0.1:8001'});
    const peer=await peerContext.newPage();await peer.goto('/');
    const registered=await peerContext.request.post('/api/register',{headers:{Origin:'http://127.0.0.1:8001'},data:{login,password,invite:new URL(invitation.url).pathname.split('/').at(-1)}});
    expect(registered.status(),await registered.text()).toBe(200);
    const enrollment=await registered.json();
    const skipped=await peerContext.request.post('/api/2fa/skip',{headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':enrollment.csrf}});
    expect(skipped.status(),await skipped.text()).toBe(200);await peer.reload();
    await api(page,'/social/families/'+room.id+'/invites','POST',{login});
    const pending=(await api(peer,'/social')).requests.find(item=>item.space_id===room.id&&item.incoming);
    await api(peer,'/social/requests/'+pending.id+'/accept','POST');
    await stableChatMediaFlow(page,peer,suffix);
    await button().click();
    await expect(page.locator('.chat-header')).toContainText(name);
    const readRequests=[];
    readRequestListener=request=>{if(request.method()==='POST'&&new URL(request.url()).pathname===`/api/chat/${room.id}/read`)readRequests.push(request.postDataJSON().message_id);};
    page.on('request',readRequestListener);
    await page.bringToFront();
    await page.locator('[data-chat-info]').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    const incoming='Пока открыт список участников '+suffix;
    const received=await api(peer,`/chat/${room.id}/messages`,'POST',{body:incoming,client_id:'workspace-modal-'+suffix});
    await expect(page.locator('.chat-messages')).toContainText(incoming);
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    const unread=async()=>((await api(page,'/chat/rooms')).items.find(item=>item.id===room.id)).unread;
    await expect.poll(unread).toBe(1);
    expect(readRequests.filter(id=>id>=received.id)).toEqual([]);
    await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
    await expect.poll(unread).toBe(0);
    page.off('request',readRequestListener);readRequestListener=null;

    // A newer local acknowledgment cannot advance the HTTP catch-up cursor
    // past an unseen incoming message, or dismiss that message's notification.
    const missed='Сообщение, пропущенное в WebSocket '+suffix,acknowledged='Собственное сообщение после пропуска '+suffix;
    let releaseCatchup,heldCatchup=false,catchupReady=false,catchupForwarded;
    const catchupGate=new Promise(resolve=>releaseCatchup=resolve),afterPath='**/api/chat/'+room.id+'/messages?*';
    const delayedCatchup=async route=>{
      const url=new URL(route.request().url());
      if(route.request().method()!=='GET'||!url.searchParams.has('after')||heldCatchup){await route.continue();return;}
      heldCatchup=true;const response=await route.fetch();catchupReady=true;
      catchupForwarded=catchupGate.then(()=>route.fulfill({response}));await catchupForwarded;
    };
    const gapReadRequests=[];
    readRequestListener=request=>{if(request.method()==='POST'&&new URL(request.url()).pathname===`/api/chat/${room.id}/read`)gapReadRequests.push(request.postDataJSON().message_id);};
    page.on('request',readRequestListener);await page.route(afterPath,delayedCatchup);
    try{
      blockedLiveBodies.add(missed);blockedLiveBodies.add(acknowledged);
      const hidden=await api(peer,`/chat/${room.id}/messages`,'POST',{body:missed,client_id:'workspace-gap-'+suffix});
      await expect.poll(()=>droppedLive).toBeGreaterThan(0);
      await expect(page.locator('.chat-messages')).not.toContainText(missed);
      await expect.poll(unread).toBe(1);
      await body.fill(acknowledged);await body.press('Enter');
      await expect(page.locator('.chat-messages')).toContainText(acknowledged);
      await expect.poll(()=>catchupReady).toBe(true);
      await expect(page.locator('.chat-messages')).not.toContainText(missed);
      await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
      await expect.poll(unread).toBe(1);
      expect(gapReadRequests.filter(id=>id>=hidden.id)).toEqual([]);
      releaseCatchup();await catchupForwarded;
      await expect(page.locator('.chat-messages')).toContainText(missed);
      await expect(page.locator('.chat-messages')).toContainText(acknowledged);
      await expect.poll(unread).toBe(0);
    }finally{
      blockedLiveBodies.clear();releaseCatchup();await page.unroute(afterPath,delayedCatchup);
      page.off('request',readRequestListener);readRequestListener=null;
    }
    await expect(page.locator('#toast')).not.toHaveClass(/show/,{timeout:7000});
    await expect(page.locator('.notification-popup')).toHaveCount(0,{timeout:8000});
    await viewportFits(page);
    await page.screenshot({path:'artifacts/messages-workspace-desktop.png',fullPage:true,animations:'disabled'});
    const scheme=await page.evaluate(()=>document.documentElement.dataset.scheme);
    try{
      await page.evaluate(()=>document.documentElement.dataset.scheme='dark');
      await page.screenshot({path:'artifacts/messages-workspace-dark.png',fullPage:true,animations:'disabled'});
    }finally{await page.evaluate(value=>document.documentElement.dataset.scheme=value,scheme);}

    await chatRecoveryFlow(page,room.id);
    await chatEditingFlow(page,peer,room.id,suffix,blockedLiveBodies);
    await page.setViewportSize({width:390,height:844});
    await expect(page.locator('.chat-sidebar')).toBeHidden();
    await expect(page.locator('[data-chat-back]')).toBeVisible();
    await viewportFits(page);
    await page.screenshot({path:'artifacts/messages-workspace-mobile.png',fullPage:true,animations:'disabled'});
    await page.locator('[data-chat-back]').click();
    await expect(page.locator('.chat-sidebar')).toBeVisible();
    await expect(page.locator('.chat-main')).toBeHidden();
    await expect(page.locator('#chat-main-menu')).toBeVisible();
    await button().click();
    await expect(page.locator('.chat-sidebar')).toBeHidden();
    await expect(page.locator('.chat-messages')).toContainText('Ответ на поездку');
    await viewportFits(page);
    await appSection(page,'Главная');
    await expect(page.locator('.sidebar')).toBeVisible();
    await expect(page.locator('.chat-shell')).toHaveCount(0);
  }finally{
    if(readRequestListener)page.off('request',readRequestListener);
    await peerContext?.close();
    await page.setViewportSize(viewport);
    await api(page,'/social/spaces/'+room.id,'DELETE');
  }
}
