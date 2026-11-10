import {expect} from '@playwright/test';
async function api(page,path,method='GET',data){const result=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(c=>c.startsWith('drg_csrf='))?.slice(9)||'');const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:r.status,data:await r.json()};},{path,method,data});expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;}
export async function chatControlsFlow(page,browser,totp){
  const own=await api(page,'/me'),mods=(await api(page,'/modules')).enabled;
  await api(page,'/modules','PUT',{enabled:{...mods,chat:true}});
  const other=await browser.newContext({baseURL:'http://127.0.0.1:8001'}),peer=await other.newPage();
  const login='mention'+Date.now().toString(36),password='Chat-controls-password-123';await peer.goto('/');
  const invite=await api(page,'/invites','POST',{login});await api(peer,'/register','POST',{login,password,invite:invite.url.split('/').at(-1)});
  const enrollment=await api(peer,'/2fa/enroll','POST',{password});await api(peer,'/2fa/confirm','POST',{code:totp(enrollment.secret)});await api(peer,'/2fa/activate','POST',{saved_recovery_codes:true});
  await api(peer,'/modules','PUT',{enabled:{chat:true}});await api(peer,'/account/profile','PUT',{display_name:'Мама QA'});
  const family=await api(page,'/social/families','POST',{name:'Контроль истории QA'}),sid=family.id;
  const request=await api(page,`/social/families/${sid}/invites`,'POST',{login});await api(peer,`/social/requests/${request.id}/accept`,'POST');
  try{
    for(const view of [page,peer]){await view.goto('/#chat/'+sid);await view.reload();await expect(view.locator('.chat-header h2')).toHaveText('Контроль истории QA');}
    await page.bringToFront();const body=page.locator('#message-body');await body.fill('Привет @'+login.slice(0,5));
    const options=page.getByRole('listbox',{name:'Участники беседы'});await expect(options.getByRole('option')).toHaveCount(1);await expect(options).toContainText('Мама QA');await body.press('Enter');await expect(body).toHaveValue('Привет @'+login+' ');await expect(options).toBeHidden();
    await body.press('Enter');const mention=page.locator('.chat-message-text [data-chat-mention]');await expect(mention).toHaveText('@'+login);await mention.click();await expect(page.locator('.modal')).toContainText('Мама QA');await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    await page.locator('[data-chat-info]').click();await expect(page.locator('.modal')).toContainText('Мама QA (@'+login+')');await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    await page.locator('[data-chat-menu]').click();await page.getByRole('menuitem',{name:'Автоудаление сообщений'}).click();await page.getByRole('menuitemradio',{name:'1 день',exact:true}).click();
    await expect.poll(async()=>(await api(peer,`/chat/${sid}/messages`)).chat_ttl).toBe(86400);
    await body.fill('Срок один день');await body.press('Enter');await expect.poll(async()=>(await api(page,`/chat/${sid}/messages`)).items.length).toBe(2);
    const history=await api(page,`/chat/${sid}/messages`);expect(history.items[0].expires).toBe(0);expect(history.items[1].expires-history.items[1].created).toBe(86400);
    await peer.bringToFront();await expect(peer.locator('.chat-messages')).toContainText('Срок один день');await peer.locator('[data-chat-menu]').click();await expect(peer.locator('[data-clear-history]')).toHaveCount(0);
    await page.bringToFront();await page.locator('[data-chat-menu]').click();await page.getByRole('menuitem',{name:'Очистить историю'}).click();await expect(page.locator('.modal input[type=checkbox]')).toHaveCount(0);await page.getByRole('button',{name:'Отмена',exact:true}).click();await expect(page.locator('[data-message-id]')).toHaveCount(2);
    await page.locator('[data-chat-menu]').click();await page.getByRole('menuitem',{name:'Очистить историю'}).click();await page.getByRole('button',{name:'Очистить',exact:true}).click();await expect(page.locator('[data-message-id]')).toHaveCount(0);await expect(peer.locator('[data-message-id]')).toHaveCount(0,{timeout:15000});
    await page.locator('[data-chat-menu]').click();await page.getByRole('menuitem',{name:'Автоудаление сообщений'}).click();await page.getByRole('menuitemradio',{name:'Никогда',exact:true}).click();await expect(page.locator('[data-chat-menu-panel]')).toBeHidden();await expect(page.locator('.chat-retention-inline')).toHaveText('История за последние 3 месяца · Старые сообщения и вложения удаляются');
    await page.setViewportSize({width:390,height:844});await page.locator('[data-chat-menu]').click();await page.getByRole('menuitem',{name:'Автоудаление сообщений'}).click();const box=await page.locator('[data-chat-menu-panel]').boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(391);await page.screenshot({path:'artifacts/chat-history-menu-mobile.png'});
    // Simulate the iOS Home Screen signal and nonzero notch/home indicator.
    await page.evaluate(()=>{Object.defineProperty(navigator,'standalone',{configurable:true,value:true});document.documentElement.style.setProperty('--app-safe-top','59px');document.documentElement.style.setProperty('--app-safe-bottom','34px');window.dispatchEvent(new Event('pageshow'));});
    await expect(page.locator('html')).toHaveClass(/app-standalone/);expect(await page.evaluate(()=>({root:getComputedStyle(document.documentElement).position,body:getComputedStyle(document.body).position,top:document.querySelector('#app').getBoundingClientRect().top,bottom:document.querySelector('#app').getBoundingClientRect().bottom,scroll:document.scrollingElement.scrollHeight}))).toEqual({root:'fixed',body:'fixed',top:59,bottom:844,scroll:844});
    const composer=await page.locator('.chat-compose').boundingBox();expect(composer.y+composer.height).toBe(844);const field=await page.locator('.chat-compose textarea').boundingBox();expect(field.y+field.height).toBeLessThanOrEqual(810);
    await page.screenshot({path:'artifacts/chat-standalone-safe-area.png'});
  }finally{await api(page,'/social/spaces/'+sid,'DELETE');await other.close();await page.setViewportSize({width:1280,height:900});await page.evaluate(()=>{delete navigator.standalone;document.documentElement.style.removeProperty('--app-safe-top');document.documentElement.style.removeProperty('--app-safe-bottom');window.dispatchEvent(new Event('pageshow'));});}
}
