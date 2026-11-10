import {expect} from '@playwright/test';
import {appSection} from './notes-ui.js';
async function api(page,path,method='GET',data){
  const response=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('drg_csrf='))?.slice(9)||'');const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:r.status,data:await r.json()};},{path,method,data});
  expect(response.status,JSON.stringify(response.data)).toBe(200);return response.data;
}
export async function chatRecoveryFlow(page,sid){
  const body=page.getByLabel('Сообщение',{exact:true}),send=page.getByRole('button',{name:'Отправить',exact:true});
  const uploads='**/api/chat/'+sid+'/attachments?*';let release,held=false;
  const gate=new Promise(resolve=>release=resolve);
  const stalled=async route=>{held=true;await gate;await route.abort().catch(()=>{});};
  await page.route(uploads,stalled);
  try{
    await page.locator('[data-chat-files]').setInputFiles({name:'stalled.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});
    await expect.poll(()=>held).toBe(true);await expect(page.getByRole('button',{name:'Отменить загрузку',exact:true})).toBeVisible();
    await body.fill('Текст после отмены загрузки');await expect(send).toBeDisabled();
    await page.getByRole('button',{name:'Отменить загрузку',exact:true}).click();await expect(send).toBeEnabled();await expect(body).toBeEditable();await expect(body).toHaveValue('Текст после отмены загрузки');
    await send.click();await expect(page.locator('.chat-messages')).toContainText('Текст после отмены загрузки');
  }finally{release();await page.unroute(uploads,stalled);}
  // A browser decoder can stall before any HTTP upload exists.
  await page.evaluate(()=>{window.__normalChatDecode=HTMLImageElement.prototype.decode;HTMLImageElement.prototype.decode=()=>new Promise(()=>{});});
  try{
    await page.locator('[data-chat-files]').setInputFiles('tests/fixtures/blue.png');await expect(page.getByRole('button',{name:'Отменить загрузку',exact:true})).toBeVisible();
    await body.fill('Текст после отмены обработки фото');await page.getByRole('button',{name:'Отменить загрузку',exact:true}).click();await expect(send).toBeEnabled();await send.click();await expect(page.locator('.chat-messages')).toContainText('Текст после отмены обработки фото');
  }finally{await page.evaluate(()=>{HTMLImageElement.prototype.decode=window.__normalChatDecode;delete window.__normalChatDecode;});}
  await page.locator('[data-chat-files]').setInputFiles({name:'expired.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});
  const pending=page.locator('[data-remove-draft]');await expect(pending).toHaveCount(1);const fid=await pending.getAttribute('data-remove-draft');
  await api(page,`/chat/${sid}/attachments/${fid}`,'DELETE');
  await body.fill('Текст с исчезнувшим вложением');await send.click();
  await expect(pending).toHaveCount(0);await expect(body).toHaveValue('Текст с исчезнувшим вложением');await expect(send).toBeEnabled();await expect(page.locator('[data-chat-upload-status]')).toContainText('Текст сохранён');
  await send.click();await expect(page.locator('.chat-messages')).toContainText('Текст с исчезнувшим вложением');
  // A failed cleanup request must never trap a file chip in the composer.
  await page.locator('[data-chat-files]').setInputFiles({name:'cleanup.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')});
  await expect(pending).toHaveCount(1);const cleanupId=await pending.getAttribute('data-remove-draft'),cleanupPath='**/api/chat/'+sid+'/attachments/'+cleanupId;
  await page.route(cleanupPath,route=>route.request().method()==='DELETE'?route.fulfill({status:503,json:{detail:'Temporary cleanup failure'}}):route.continue());
  await pending.click();await expect(pending).toHaveCount(0);await body.fill('Текст после ошибки очистки');await send.click();await expect(page.locator('.chat-messages')).toContainText('Текст после ошибки очистки');
  await page.unroute(cleanupPath);await api(page,`/chat/${sid}/attachments/${cleanupId}`,'DELETE');
  await page.locator('[data-chat-files]').setInputFiles(['one','two','three'].map(name=>({name:name+'.txt',mimeType:'text/plain',buffer:Buffer.from('fixture')})));
  await expect(pending).toHaveCount(3);const clear=page.getByRole('button',{name:'Убрать все вложения',exact:true});await expect(clear).toBeVisible();expect((await clear.boundingBox()).width).toBeGreaterThan(100);
  await page.screenshot({path:'artifacts/chat-upload-recovery.png',fullPage:true,animations:'disabled'});await clear.click();await expect(pending).toHaveCount(0);
  // Transport failure preserves the body and the idempotency key for retry.
  const messagePath='**/api/chat/'+sid+'/messages';let firstId,secondId,first=true;
  const failing=async route=>{if(route.request().method()!=='POST')return route.continue();const id=route.request().postDataJSON().client_id;if(first){first=false;firstId=id;await route.abort();}else{secondId=id;await route.continue();}};
  await page.route(messagePath,failing);
  try{
    await body.fill('Безопасный повтор после ошибки сети');await send.click();await expect(send).toBeEnabled();await expect(body).toBeEditable();await expect(body).toHaveValue('Безопасный повтор после ошибки сети');await send.click();await expect(page.locator('.chat-messages')).toContainText('Безопасный повтор после ошибки сети');expect(secondId).toBe(firstId);
  }finally{await page.unroute(messagePath,failing);}
  // The server has saved the message, but its HTTP acknowledgement stalls.
  let releaseAck,ackReady=false,firstAck=true,committed,ackClient,retryClient;
  const ackGate=new Promise(resolve=>releaseAck=resolve);
  const stalledAck=async route=>{
    if(route.request().method()!=='POST')return route.continue();
    if(!firstAck){retryClient=route.request().postDataJSON().client_id;return route.continue();}
    firstAck=false;ackClient=route.request().postDataJSON().client_id;const response=await route.fetch();committed=await response.json();ackReady=true;await ackGate;await route.fulfill({response}).catch(()=>{});
  };
  await page.route(messagePath,stalledAck);
  try{
    await body.fill('Подтверждение отправки зависло');await send.click();await expect.poll(()=>ackReady).toBe(true);await expect(body).toHaveAttribute('aria-busy','true');await expect(body).toBeFocused();await expect(send).toBeDisabled();
    await body.press('x');await expect(body).toHaveValue('Подтверждение отправки зависло');
    await page.getByRole('button',{name:'Отменить ожидание',exact:true}).click();await expect(body).toBeEditable();await expect(send).toBeEnabled();await expect(body).toHaveValue('Подтверждение отправки зависло');
    await send.click();await expect(body).toHaveValue('');expect(retryClient).toBe(ackClient);await expect(page.locator(`[data-message-id="${committed.id}"]`)).toHaveCount(1);
    expect((await api(page,`/chat/${sid}/messages`)).items.filter(m=>m.client_id===ackClient)).toHaveLength(1);
  }finally{releaseAck();await page.unroute(messagePath,stalledAck);}
  // Cancel also works while shared authentication refresh is waiting on /me.
  let rejectOnce=true,releaseRefresh,refreshReady=false;
  const refreshGate=new Promise(resolve=>releaseRefresh=resolve);
  const needsRefresh=route=>{if(route.request().method()==='POST'&&rejectOnce){rejectOnce=false;return route.fulfill({status:401,json:{detail:'Session needs refresh'}});}return route.continue();};
  const stalledRefresh=async route=>{refreshReady=true;await refreshGate;await route.continue().catch(()=>{});};
  await page.route(messagePath,needsRefresh);await page.route('**/api/me',stalledRefresh);
  try{
    await body.fill('Текст при зависшем обновлении сеанса');await send.click();await expect.poll(()=>refreshReady).toBe(true);await page.getByRole('button',{name:'Отменить ожидание',exact:true}).click();await expect(send).toBeEnabled();await expect(body).toBeEditable();await expect(body).toHaveValue('Текст при зависшем обновлении сеанса');
    releaseRefresh();await page.unroute('**/api/me',stalledRefresh);await send.click();await expect(page.locator('.chat-messages')).toContainText('Текст при зависшем обновлении сеанса');
  }finally{releaseRefresh();await page.unroute('**/api/me',stalledRefresh);await page.unroute(messagePath,needsRefresh);}
  const modules=await api(page,'/modules');await api(page,'/modules','PUT',{enabled:{...modules.enabled,files:true,tasks:true}});await page.reload();
  for(const [name,selector] of [['Настройки','#profile-settings'],['Файлы','.files-panel'],['Напоминания','.tasks-shell'],['Сообщения','.chat-shell']]){
    await appSection(page,name);await expect(page.locator(selector)).toBeVisible();await page.reload();await expect(page.locator(selector)).toBeVisible();
    if(name==='Сообщения'){await expect(page.locator('.chat-empty-selection')).toBeVisible();await expect(page.locator('.chat-room.active,#message-body')).toHaveCount(0);}
  }
  await api(page,'/modules','PUT',{enabled:{chat:false}});await page.reload();await expect(page.locator('[data-view=home]')).toHaveClass(/active/);await expect(page.locator('.chat-shell')).toHaveCount(0);
  await api(page,'/modules','PUT',{enabled:{chat:true}});await page.reload();await appSection(page,'Сообщения');await page.locator(`[data-room="${sid}"]`).click();
  await api(page,'/modules','PUT',{enabled:modules.enabled});await page.reload();await expect(page.locator('.chat-shell')).toBeVisible();await page.locator(`[data-room="${sid}"]`).click();
}
