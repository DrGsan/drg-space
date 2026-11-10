import {expect,chromium,webkit} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const origin='http://127.0.0.1:8053',directory=await mkdtemp(tmpdir()+'/drg-chat-widget-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8053','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-chat-widget-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FAMILY_PHOTO_URL:'',FAMILY_PHOTO_TOKEN:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}if(!ready)throw Error(output||'Test server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1920,height:1100}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  let csrf='';const api=async(path,data,method='POST')=>{const r=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();};
  const password='Isolated-widget-test-password-123';csrf=(await api('/setup',{login:'widgetowner',password,token:'browser-test-setup-key'})).csrf;
  const enroll=await api('/2fa/enroll',{password});await api('/2fa/confirm',{code:totp(enroll.secret)});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
  const room=await api('/social/families',{name:'Семейная беседа'}),me=await api('/me',undefined,'GET');
  const layout=await api('/dashboard/layout',undefined,'GET'),sizes=['1x1','1x2','2x1','2x2'];
  await api('/dashboard/layout',{version:layout.version,instances:sizes.map(size=>({id:'chat_qa_'+size,module_id:'chat',size,title:'Беседы '+size,config:{space_id:null}}))},'PUT');
  let unread=8,fail=false;
  const stamp=Math.floor(Date.now()/1000),rooms=Array.from({length:30},(_,i)=>({id:i?`fixture-${i}`:room.id,kind:i%2?'friends':'family',name:i?`Очень длинное название группы ${i}`:'Семейная беседа',members:i%2?[{id:me.id,login:me.login},{id:'peer-'+i,login:'peer'+i,display_name:'Собеседник '+i}]:[],pinned:i===0,favorite:false,unread:i===0?8:i===1?150:0,last_message:{id:30-i,created:stamp-i*3600,body:'Полезное последнее сообщение с достаточно длинным содержимым для проверки переноса строк.',sender:'Анна',attachments:[]}}));
  const handler=route=>route.fulfill({status:fail?503:200,json:fail?{detail:'Временная ошибка'}:{items:rooms.map((r,i)=>i? r:{...r,unread}),unread}});
  await page.route('**/api/chat/rooms',handler);await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  await page.goto('/');await expect(page.locator('[data-widget=chat]')).toHaveCount(4);
  const item=size=>page.locator('[data-widget-id="chat_qa_'+size+'"]');
  for(const width of [1920,1280,768,390,320]){
    await page.setViewportSize({width,height:1100});
    for(const size of sizes){
      await expect.poll(()=>item(size).locator('[data-widget-chat]').count()).toBeGreaterThan(0);
      await expect.poll(()=>item(size).evaluate(section=>{const body=section.querySelector('[data-widget-body]'),list=section.querySelector('.widget-chat-list'),limit=list.getBoundingClientRect();return body.scrollHeight<=body.clientHeight+1&&body.scrollWidth<=body.clientWidth+1&&[...list.querySelectorAll('[data-widget-chat]')].every(button=>{const b=button.getBoundingClientRect();return b.top>=limit.top-1&&b.bottom<=limit.bottom+1&&b.right<=limit.right+1});})).toBe(true);
      expect(await item(size).locator('[data-widget-chat]').count()).toBeLessThan(30);
      await expect(item(size).locator('.widget-chat-all')).toHaveText('Все 30 ↗');
      await expect(item(size).locator('.widget-chat-preview')).toHaveCount(size==='1x1'?0:await item(size).locator('[data-widget-chat]').count());
      expect(await item(size).locator('.widget-chat-list').getAttribute('data-layout')).toBe(size==='1x1'?'avatars':'rows');
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(width===1920||width===390){await mkdir('artifacts',{recursive:true});await page.screenshot({path:`artifacts/chat-widget-${process.env.DRG_BROWSER_ENGINE||'chromium'}-${width}.png`,fullPage:true});}
  }
  await page.setViewportSize({width:1920,height:1100});
  await expect(item('2x1').locator('[data-widget-chat]').nth(1)).toContainText('Собеседник 1');
  await expect(item('1x1').locator('.widget-chat-unread').nth(1)).toHaveText('99+');
  await item('1x1').locator('[data-widget-chat]').first().focus();unread=0;
  await page.evaluate(()=>document.dispatchEvent(new Event('drg-inbox-change')));
  await expect(item('1x1').locator('[data-widget-chat]').first().locator('.widget-chat-unread')).toHaveCount(0);
  await expect(item('1x1').locator('[data-widget-chat]').first()).toBeFocused();
  await item('1x1').locator('[data-widget-chat]').first().press('Enter');
  await expect(page.locator('#chat-messages')).toBeVisible();await expect(page.locator('[data-chat-info]')).toContainText('Семейная беседа');
  await page.locator('#chat-main-menu').click();await item('1x1').locator('.widget-chat-all').click();
  await expect(page.locator('.chat-empty-selection')).toBeVisible();await expect(page.locator('#chat-messages')).toHaveCount(0);
  await page.locator('#chat-main-menu').click();await page.locator('.sidebar [data-view=notes]').click();
  const order=await page.locator('.pages-sidebar-footer').evaluate(el=>[...el.children].map(e=>e.id));expect(order).toEqual(['customize-sidebar','pages-tools','show-trash']);
  await page.locator('#notes-main-menu').click();
  fail=true;await page.reload();await expect(item('1x1').locator('[data-chat-retry]')).toBeVisible();fail=false;await item('1x1').locator('[data-chat-retry]').click();await expect(item('1x1').locator('[data-widget-chat]').first()).toBeVisible();
  // Several resize/remount cycles must never duplicate the header action.
  await page.reload();await expect(page.locator('.widget-chat-all')).toHaveCount(4);
  // The optional archive joins the same list without holding up native rooms.
  await page.route('**/api/modules',async route=>{const response=await route.fetch(),body=await response.json();body.enabled.family_photos=true;body.catalog.push({id:'family_photos',kind:'module',title:'Семейные фото'});await route.fulfill({response,json:body});});
  await page.route('**/api/family-photos/**',route=>route.fulfill({json:route.request().url().endsWith('/groups')?{items:[{id:'archive-qa',title:'Архивная беседа',unread:3}]}:route.request().url().includes('/messages?')?{items:[{id:901,created:stamp+100,text:'Сообщение из архива',sender:'Анна',content_type:'text',media:[]}]}:{unread:3}}));
  await page.reload();await expect(item('1x1').locator('.widget-chat-all')).toHaveText('Все 31 ↗');await expect(item('1x1').locator('[data-widget-chat="telegram:archive-qa"]')).toBeVisible();
  await expect(item('2x1').locator('[data-widget-chat="telegram:archive-qa"]')).toContainText('Сообщение из архива');
  const current=await api('/dashboard/layout',undefined,'GET');current.instances[0].config.space_id=room.id;await api('/dashboard/layout',current,'PUT');
  await page.reload();await expect(item('1x1').locator('[data-widget-chat]')).toHaveCount(1);await expect(item('1x1').locator('[data-widget-chat]')).toHaveAttribute('data-widget-chat',room.id);
  expect(errors).toEqual([]);
  console.log('Chat widget passed: four sizes at five widths, complete rows without scrolling, previews, names, unread refresh, focus, room/all navigation, retry, remount cleanup and notes footer order.');
}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});}
