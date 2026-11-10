import {appSection} from './notes-ui.js';
import { expect } from '@playwright/test';
async function request(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}
async function familyRow(page,name){
  const row=page.locator('[data-social-name="'+name+'"]');
  if(await row.getAttribute('open')===null)await row.locator(':scope > summary').click();
  return row;
}
async function refreshPeople(page){
  // Refresh first loads notes, then replaces the people form. Do not type
  // into the old form while that explicit refresh is still in flight.
  const previous=await page.locator('#refresh-social').elementHandle();
  await page.getByRole('button',{name:'Обновить приглашения',exact:true}).click();
  await previous.waitForElementState('hidden');await previous.dispose();
  await expect(page.locator('#refresh-social')).toBeVisible();
}
export async function socialFlow(page,browser,totp){
  const errors=[];const extra=await browser.newContext({baseURL:'http://127.0.0.1:8001'}),friend=await extra.newPage();friend.on('pageerror',e=>errors.push(e.message));
  const invite=await request(page,'/invites','POST',{login:'realtimefriend'});
  await friend.goto('/');
  // Register and enroll via the real authenticated endpoints; the UI flows are
  // covered elsewhere, keeping this test focused on independent live editors.
  const registered=await extra.request.post('/api/register',{data:{login:'realtimefriend',password:'Realtime-friend-password-123',invite:new URL(invite.url).pathname.split('/').at(-1)}});
  expect(registered.status(),await registered.text()).toBe(200);await friend.reload();
  const enrollment=await request(friend,'/2fa/enroll','POST',{password:'Realtime-friend-password-123'});
  await request(friend,'/2fa/confirm','POST',{code:totp(enrollment.secret)});await request(friend,'/2fa/activate','POST',{saved_recovery_codes:true});await friend.reload();
  await request(page,'/appearance','PUT',{theme:'ocean',mode:'light'});await page.reload();
  await appSection(page, 'Люди и группы');
  await page.locator('[data-preference-key=create-space]>summary').click();await page.getByLabel('Тип пространства',{exact:true}).selectOption('family');await page.getByLabel('Название пространства',{exact:true}).fill('Наш уютный дом');await page.getByRole('button',{name:'Создать пространство',exact:true}).click();
  await (await familyRow(page,'Наш уютный дом')).getByRole('button',{name:'＋ Участник',exact:true}).click();await page.getByLabel('Логин участника').fill('realtimefriend');await page.getByRole('button',{name:'Отправить приглашение',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  await appSection(friend, 'Люди и группы');await friend.getByRole('button',{name:'Принять',exact:true}).click();await expect(friend.locator('.social-card')).toHaveCount(1);
  await refreshPeople(page);
  await page.locator('[data-preference-key=add-friend]>summary').click();await page.getByLabel('Логин друга').fill('realtimefriend');await page.getByRole('button',{name:'Пригласить друга',exact:true}).click();await expect(page.locator('.social-request')).toContainText('Приглашение отправлено');
  await refreshPeople(friend);await friend.getByRole('button',{name:'Принять',exact:true}).click();await expect(friend.locator('.social-card')).toHaveCount(2);await refreshPeople(page);
  await expect(page.locator('.social-card')).toHaveCount(3);
  await expect(page.locator('#toast')).not.toHaveClass(/show/,{timeout:7000});
  await page.screenshot({path:'artifacts/family-friends-desktop.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'artifacts/family-friends-mobile.png',fullPage:true});await page.setViewportSize({width:1280,height:900});
  const family=(await request(page,'/social')).spaces.find(s=>s.name==='Наш уютный дом');
  await (await familyRow(page,'Наш уютный дом')).getByRole('button',{name:'Общие заметки',exact:true}).click();
  await page.getByRole('button',{name:'Создать страницу',exact:true}).click();
  await expect(page.getByLabel('Название заметки')).toBeEnabled({timeout:20000});
  await page.getByLabel('Название заметки').fill('Наши планы на выходные');
  const content=page.getByRole('textbox',{name:'Содержимое страницы'});await content.click();await page.keyboard.type('Общие планы: ');
  await expect(page.locator('#page-save-status')).toContainText('Сохранено',{timeout:15000});
  let friendSocket,friendServer;await extra.routeWebSocket('**/api/live/notes/**',socket=>{friendSocket=socket;friendServer=socket.connectToServer();});
  const url=page.url();await friend.goto(url);await expect(friend.getByLabel('Название заметки')).toHaveValue('Наши планы на выходные');
  const other=friend.getByRole('textbox',{name:'Содержимое страницы'});await expect(other).toContainText('Общие планы:');
  await content.click();await page.keyboard.press('Control+End');await other.click();await friend.keyboard.press('Control+End');
  // Insert two phrases concurrently into the same paragraph. Per-key edits at
  // identical carets may legitimately interleave letters instead of whole words.
  await Promise.all([page.keyboard.insertText('прогулка у моря '),friend.keyboard.insertText('и домашняя пицца ')]);
  await expect(content).toContainText('прогулка');await expect(content).toContainText('пицца');await expect(other).toContainText('прогулка');await expect(other).toContainText('пицца');
  const plain=locator=>locator.evaluate(el=>{const clone=el.cloneNode(true);clone.querySelectorAll('[class*=collaboration-cursor]').forEach(x=>x.remove());return clone.textContent;});
  // Both clients are still receiving final CRDT updates. Compare their current
  // contents on every attempt instead of freezing one client's interim text.
  await expect.poll(async()=>{
    const [local,remote]=await Promise.all([plain(content),plain(other)]);return local===remote;
  },{timeout:15000}).toBe(true);
  await expect(page.locator('#live-presence')).toContainText('realtimefriend');
  await expect(page.locator('#page-save-status')).toContainText('Сохранено');
  await page.reload();await expect(page.getByRole('textbox',{name:'Содержимое страницы'})).toContainText('пицца');
  await page.screenshot({path:'artifacts/shared-note-desktop.png',fullPage:true,animations:'disabled'});
  // Disconnect one network, edit in both clients, then merge unsent local edits.
  await extra.setOffline(true);await friendSocket.close({code:1001,reason:'Simulated connection loss'});await friendServer.close({code:1001,reason:'Simulated connection loss'});
  await expect(friend.locator('#page-save-status')).toContainText('Нет соединения');await other.click();await friend.keyboard.press('Control+End');await friend.keyboard.type('Список покупок ');await expect(other).toContainText('покупок');
  await page.getByRole('textbox',{name:'Содержимое страницы'}).click();await page.keyboard.press('Control+End');await page.keyboard.type('Суббота ');
  await extra.setOffline(false);
  await expect(page.getByRole('textbox',{name:'Содержимое страницы'})).toContainText('покупок',{timeout:20000});await expect(other).toContainText('Суббота',{timeout:20000});
  for(const p of [page,friend]){
    await appSection(p, 'Настройки');await p.getByRole('checkbox',{name:'Модуль: Чат',exact:true}).check();await p.getByRole('button',{name:'Сохранить модули',exact:true}).click();
    await appSection(p, 'Люди и группы');await (await familyRow(p,'Наш уютный дом')).getByRole('button',{name:'Чат',exact:true}).click();
  }
  await page.getByLabel('Сообщение',{exact:true}).fill('Давайте в субботу погуляем у моря 🌊');await page.getByRole('button',{name:'Отправить',exact:true}).click();
  await expect(friend.locator('.chat-messages')).toContainText('погуляем у моря');
  await friend.getByLabel('Сообщение',{exact:true}).fill('Отлично! А вечером приготовим пиццу 🍕');await friend.getByRole('button',{name:'Отправить',exact:true}).click();await expect(page.locator('.chat-messages')).toContainText('приготовим пиццу');
  await expect(page.locator('.chat-retention-inline')).toContainText('3 месяца');
  await page.locator('[data-chat-files]').setInputFiles(['tests/fixtures/blue.png','tests/fixtures/chat-video.mp4']);await expect(page.locator('.chat-drafts>span')).toHaveCount(2);await expect(page.getByRole('button',{name:'Отправить',exact:true})).toBeEnabled();
  await page.locator('[data-chat-files]').setInputFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Shared chat document')});await expect(page.locator('.chat-drafts>span')).toHaveCount(3);await expect(page.getByRole('button',{name:'Отправить',exact:true})).toBeEnabled();
  await page.getByLabel('Сообщение',{exact:true}).fill('Фотография, видео и документ');await page.getByRole('button',{name:'Отправить',exact:true}).click();await expect(page.locator('.chat-drafts>span')).toHaveCount(0);
  await expect(friend.locator('.chat-media-album img')).toHaveCount(1);await expect(friend.locator('.chat-media-album video')).toHaveCount(1);await expect.poll(()=>friend.locator('.chat-media-album img').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
  await expect.poll(()=>friend.locator('video').evaluate(v=>v.readyState)).toBeGreaterThan(0);await friend.locator('video').evaluate(v=>{v.muted=true;return v.play();});
  const chatDownload=friend.waitForEvent('download');await friend.getByRole('link',{name:/notes.txt/}).click();expect((await chatDownload).suggestedFilename()).toBe('notes.txt');
  await friend.getByRole('button',{name:'Открыть фотографию',exact:true}).click();await expect(friend.locator('.fp-viewer-stage img')).toBeVisible();await friend.getByRole('button',{name:'Закрыть',exact:true}).click();
  await expect(page.locator('#toast')).not.toHaveClass(/show/,{timeout:7000});
  await page.screenshot({path:'artifacts/family-chat-desktop.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'artifacts/family-chat-mobile.png',fullPage:true});await page.setViewportSize({width:1280,height:900});
  await page.reload();await appSection(page, 'Сообщения');await expect(page.locator('.chat-empty-selection')).toBeVisible();await page.locator(`[data-room="${family.id}"]`).click();await expect(page.locator('.chat-messages')).toContainText('приготовим пиццу');
  // Optional dashboard instances can independently select a conversation.
  const layout=await request(page,'/dashboard/layout');
  layout.instances.push({id:'family_chat_test',module_id:'chat',title:'Семейная беседа',hidden:false,size:'compact',config:{space_id:family.id}});
  await request(page,'/dashboard/layout','PUT',layout);
  await appSection(page, 'Главная');
  await expect(page.locator('[data-widget=chat]')).toContainText('Наш уютный дом');
  await page.locator('[data-widget=chat]').getByRole('button',{name:/Наш уютный дом/}).click();
  await expect(page.locator('.chat-messages')).toContainText('приготовим пиццу');
  // An already-open socket loses access when its participant is removed.
  const friendId=(await request(friend,'/me')).id;
  await request(page,`/social/families/${family.id}/members/${friendId}`,'DELETE');
  const forbidden=await extra.request.get('/api/notes/'+new URL(url).pathname.split('/').at(-1));expect(forbidden.status()).toBe(404);
  await expect(friend.getByLabel('Сообщение',{exact:true})).toBeDisabled({timeout:10000});
  expect(errors).toEqual([]);await extra.close();
}
