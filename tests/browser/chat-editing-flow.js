import {expect} from '@playwright/test';
import {appSection} from './notes-ui.js';

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}

export async function chatEditingFlow(page,peer,sid,suffix,blockedLiveBodies){
  const original='Исправляемый текст '+suffix,corrected='Исправленный текст '+suffix;
  const sent=await api(page,`/chat/${sid}/messages`,'POST',{body:original,client_id:'edit-browser-'+suffix});
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  const message=page.locator(`[data-message-id="${sent.id}"]`),body=page.getByLabel('Сообщение',{exact:true});
  await expect(message).toContainText(original);await message.hover();await message.click({button:'right'});await page.getByRole('menuitem',{name:'Ответить',exact:true}).click();await body.fill('Черновик следующего сообщения');
  await page.locator('[data-chat-files]').setInputFiles('tests/fixtures/blue.png');await expect(page.locator('[data-chat-drafts]>span')).toHaveCount(1);
  await message.locator('.chat-message-text').click({button:'right'});
  const menu=page.getByRole('menu',{name:'Действия сообщения'});
  await expect(menu).toBeVisible();await expect(menu.getByRole('menuitem')).toHaveText(['Ответить','Копировать текст','Изменить','Удалить']);
  // A late media/layout adjustment must not dismiss a menu the user is using.
  await page.locator('#chat-messages').evaluate(list=>{list.scrollTop=Math.max(0,list.scrollTop-1);});await expect(menu).toBeVisible();
  const box=await menu.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.y+box.height).toBeLessThanOrEqual(page.viewportSize().height);
  await menu.getByRole('menuitem',{name:'Изменить',exact:true}).click();
  await expect(page.locator('[data-chat-edit]')).toContainText('Редактирование');await expect(body).toHaveValue(original);await expect(page.locator('[data-chat-drafts]')).toBeHidden();await expect(page.locator('[data-attach-chat]')).toBeDisabled();
  await body.fill('Отменяемая правка');await page.getByRole('button',{name:'Отменить редактирование',exact:true}).click();
  await expect(body).toHaveValue('Черновик следующего сообщения');await expect(page.locator('[data-chat-drafts]')).toBeVisible();await expect(page.locator('[data-chat-reply]')).toBeVisible();await expect(message.locator('.chat-message-edited')).toHaveCount(0);
  await message.hover();await message.click({button:'right'});
  await menu.getByRole('menuitem',{name:'Изменить',exact:true}).click();await body.fill(corrected);
  await page.screenshot({path:'artifacts/chat-editing-desktop.png',fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'Сохранить изменения',exact:true}).click();
  await expect(message).toContainText(corrected);await expect(message.locator('.chat-message-edited')).toHaveText('изменено');await expect(body).toHaveValue('Черновик следующего сообщения');
  await expect(page.locator('[data-chat-reply]')).toContainText(corrected);await expect(page.locator('[data-chat-drafts]>span')).toHaveCount(1);
  const saved=(await api(page,`/chat/${sid}/messages`)).items.find(m=>m.id===sent.id);expect(saved.created).toBe(sent.created);expect(saved.revision).toBeGreaterThan(0);
  // A second editor cannot silently overwrite a concurrent device's revision.
  await message.locator('.chat-message-text').click({button:'right'});await menu.getByRole('menuitem',{name:'Изменить',exact:true}).click();await body.fill('Несохранённая конфликтующая правка');
  const remote=await api(page,`/chat/${sid}/messages/${sent.id}`,'PUT',{body:'Правка с другого устройства',revision:saved.revision});
  await expect(message).toContainText(remote.body);await page.getByRole('button',{name:'Сохранить изменения',exact:true}).click();
  await expect(page.locator('#toast')).toContainText('другом устройстве');await expect(body).toHaveValue('Несохранённая конфликтующая правка');await page.getByRole('button',{name:'Отменить редактирование',exact:true}).click();
  // An incoming edit updates its existing bubble and its loaded reply quote.
  const incoming=await api(peer,`/chat/${sid}/messages`,'POST',{body:'Исходный текст друга',client_id:'edit-peer-'+suffix});
  const incomingNode=page.locator(`[data-message-id="${incoming.id}"]`);await expect(incomingNode).toBeVisible();
  const response=await api(page,`/chat/${sid}/messages`,'POST',{body:'Ответ на редактируемый текст',reply_to:incoming.id,client_id:'edit-reply-'+suffix});
  const reply=page.locator(`[data-message-id="${response.id}"]`);await expect(reply).toBeVisible();
  await api(peer,`/chat/${sid}/messages/${incoming.id}`,'PUT',{body:'Изменённый текст друга',revision:incoming.revision});
  await expect(incomingNode).toContainText('Изменённый текст друга');await expect(incomingNode.locator('.chat-message-edited')).toHaveText('изменено');await expect(reply.locator('.chat-reply-quote')).toContainText('Изменённый текст друга');
  if(blockedLiveBodies){
    const missed='Правка после пропуска живого события '+suffix;blockedLiveBodies.add(missed);
    try{
      const current=(await api(peer,`/chat/${sid}/messages`)).items.find(m=>m.id===incoming.id);
      await api(peer,`/chat/${sid}/messages/${incoming.id}`,'PUT',{body:missed,revision:current.revision});
      await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
      await expect(incomingNode).toContainText(missed);await expect(reply.locator('.chat-reply-quote')).toContainText(missed);
    }finally{blockedLiveBodies.delete(missed);}
  }
  await incomingNode.locator('.chat-message-text').click({button:'right'});await expect(menu.getByRole('menuitem',{name:'Изменить',exact:true})).toHaveCount(0);await expect(menu.getByRole('menuitem',{name:'Удалить',exact:true})).toHaveCount(0);await menu.press('Escape');await expect(menu).toHaveCount(0);
  const viewport=page.viewportSize();
  try{
    await page.setViewportSize({width:390,height:844});await message.scrollIntoViewIfNeeded();
    const text=message.locator('.chat-message-text');const r=await text.boundingBox();
    await text.dispatchEvent('pointerdown',{pointerType:'touch',isPrimary:true,pointerId:1,clientX:r.x+10,clientY:r.y+10});
    await expect(menu).toBeVisible({timeout:2000});await text.dispatchEvent('pointerup',{pointerType:'touch',isPrimary:true,pointerId:1});
    const mobileBox=await menu.boundingBox();expect(mobileBox.x).toBeGreaterThanOrEqual(0);expect(mobileBox.x+mobileBox.width).toBeLessThanOrEqual(390);
    await page.screenshot({path:'artifacts/chat-message-menu-mobile.png',fullPage:true,animations:'disabled'});
    await menu.getByRole('menuitem',{name:'Изменить',exact:true}).click();await expect(body).toHaveValue(remote.body);await body.press('Escape');await expect(body).toHaveValue('Черновик следующего сообщения');
  }finally{await page.setViewportSize(viewport);}
  await page.locator('[data-remove-draft]').click();await expect(page.locator('[data-chat-drafts]>span')).toHaveCount(0);
  await page.reload();await page.locator('.chat-shell,.sidebar').first().waitFor({state:'visible'});if(!await page.locator('.chat-shell').count())await appSection(page,'Сообщения');await page.locator(`[data-room="${sid}"]`).click();await expect(page.locator(`[data-message-id="${sent.id}"] .chat-message-text`)).toContainText(remote.body);await expect(page.locator(`[data-message-id="${sent.id}"] .chat-message-edited`)).toHaveText('изменено');
  // The existing deletion action must require confirmation and remove only
  // the selected own message for both accounts, preserving neighbouring text.
  await message.locator('.chat-message-text').click({button:'right'});await menu.getByRole('menuitem',{name:'Удалить',exact:true}).click();
  await page.locator('[data-cancel-delete]').click();await expect(message).toContainText(remote.body);
  await message.locator('.chat-message-text').click({button:'right'});await menu.getByRole('menuitem',{name:'Удалить',exact:true}).click();
  await page.locator('[data-confirm-delete]').click();await expect(message).toHaveCount(0);
  expect((await api(peer,`/chat/${sid}/messages`)).items.some(item=>item.id===sent.id)).toBe(false);
  await expect(incomingNode).toBeVisible();await expect(reply).toBeVisible();
}
