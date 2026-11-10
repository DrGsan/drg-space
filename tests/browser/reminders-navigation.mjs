import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page,browser){
  const password='Navigation-password-123',context=page.context(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
  await page.setViewportSize({width:390,height:844});
  async function post(ctx,path,data,csrf=''){
    const r=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.status(),await r.text()).toBe(200);return r.json();
  }
  async function activate(ctx,csrf){const enrollment=await post(ctx,'/2fa/enroll',{password},csrf);await post(ctx,'/2fa/confirm',{code:totp(enrollment.secret)},csrf);return (await post(ctx,'/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;}
  let csrf=(await post(context,'/setup',{login:'navowner',password,token:'browser-test-setup-key'})).csrf;
  csrf=await activate(context,csrf);await post(context,'/admin/access/unlock',{password},csrf);
  const peer=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,viewport:{width:390,height:844}});
  await peer.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
  try{
    const invite=await post(context,'/invites',{login:'navfriend'},csrf);
    let peerCsrf=(await post(peer,'/register',{login:'navfriend',password,invite:invite.url.split('/').at(-1)})).csrf;
    peerCsrf=await activate(peer,peerCsrf);
    const request=await post(context,'/social/friends',{login:'navfriend'},csrf);
    await post(peer,'/social/requests/'+request.id+'/accept',{},peerCsrf);
    const friend=(await (await peer.request.get(origin+'/api/me')).json()).id;
    const list=await post(context,'/task-lists',{name:'Совместные дела'},csrf);
    const parent=await post(context,'/tasks',{title:'Родитель',list_id:list.id},csrf);
    const child=await post(context,'/tasks',{title:'Подзадача',list_id:list.id,parent_id:parent.id},csrf);
    await post(context,'/task-lists/'+list.id+'/members',{user_id:friend},csrf);
    const peerPage=await peer.newPage();await peerPage.goto('/');await peerPage.locator('#open-inbox').click();
    const event=peerPage.locator('.inbox-item').filter({hasText:'Совместные дела'});
    await expect(event).toContainText('приглашает в список');
    await event.locator('[data-event-open]').click();await expect(event.locator('[data-inbox-accept]')).toBeVisible();
    await event.locator('[data-inbox-accept]').click();
    await expect.poll(async()=> (await (await peer.request.get(origin+'/api/task-lists')).json()).some(item=>item.id===list.id)).toBe(true);
    await peerPage.locator('[data-inbox-filter=all]').click();
    await event.locator('[data-event-open]').click();await expect(peerPage.locator('[data-task-view-title]')).toHaveText('Совместные дела');
    await peerPage.close();
    // The restricted room keeps its normal header but cannot load messages.
    const group=await post(context,'/social/families',{name:'Группа на удаление'},csrf);
    const membership=await post(context,'/social/families/'+group.id+'/invites',{login:'navfriend'},csrf);
    await post(peer,'/social/requests/'+membership.id+'/accept',{},peerCsrf);
    await post(context,'/chat/'+group.id+'/messages',{body:'Скрытая история',client_id:'private-before-deletion'},csrf);
    await page.goto('/#chat/'+group.id);await expect(page.locator('#message-body')).toBeVisible();
    const deletion=await context.request.delete(origin+'/api/social/spaces/'+group.id,{headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(deletion.ok()).toBeTruthy();
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(page.locator('#chat-messages .space-deletion-screen')).toBeVisible();
    await page.reload();await page.locator('[data-room="'+group.id+'"]').click();await expect(page.locator('#chat-messages .space-deletion-screen')).toBeVisible();
    await expect(page.locator('[data-chat-back]')).toBeVisible();await expect(page.locator('#message-body')).toHaveCount(0);
    await expect(page.locator('#chat-messages')).not.toContainText('Скрытая история');
    await page.screenshot({path:'artifacts/reminders-restricted-chat.png'});
    await page.locator('[data-chat-back]').click();await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    await page.locator('[data-room="'+group.id+'"]').click();
    await page.locator('#chat-messages').evaluate(el=>{
      const fire=(type,x)=>{const touch={identifier:1,target:el,clientX:x,clientY:170},event=new Event(type,{bubbles:true,cancelable:true});Object.defineProperties(event,{touches:{value:type==='touchend'?[]:[touch]},changedTouches:{value:[touch]}});el.dispatchEvent(event);};
      fire('touchstart',8);fire('touchmove',130);fire('touchend',160);
    });
    await expect(page.locator('.chat-shell')).not.toHaveClass(/room-open/);
    // Exercise both list and document headers in ru/en at narrow widths.
    const note=await post(context,'/notes',{title:'Заметка с длинным названием для проверки'},csrf);
    for(const language of ['ru','en']){
      const r=await context.request.put(origin+'/api/language',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:{language}});expect(r.ok()).toBeTruthy();
      for(const width of [320,390,430]){
        await page.setViewportSize({width,height:844});await page.goto('/');
        await page.locator('.mobile-app-tabs [data-mobile-view="notes"]').click();
        await expect(page.locator('#notes-space')).toBeVisible();
        await page.screenshot({path:`artifacts/reminders-notes-list-${language}-${width}.png`});
        const listBounds=await page.locator('#notes-space').boundingBox(),addBounds=await page.locator('#add-root-page').boundingBox();
        const navBounds=await page.locator('.notes-navigation').boundingBox();expect(Math.abs(listBounds.y-navBounds.y)).toBeLessThan(2);
        expect(listBounds.x+listBounds.width).toBeLessThanOrEqual(addBounds.x+1);
        await page.locator('[data-open-page="'+note.id+'"]').click();
        await expect(page.locator('#page-actions')).toBeVisible();
        await page.screenshot({path:`artifacts/reminders-notes-page-${language}-${width}.png`});
        for(const selector of ['#notes-main-menu','#favorite-page','#share-page','#page-actions','#open-inbox']){
          const button=page.locator(selector);await expect(button).toBeVisible();
          expect(await button.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),selector+' hit target').toBe(true);
        }
      }
    }
    expect(errors).toEqual([]);
  }finally{await peer.close();}
}

// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8033',directory=await mkdtemp(tmpdir()+'/drg-reminders-navigation-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8033','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page,browser);
  console.log('Reminder invitations, restricted chat navigation and mobile notes headers passed.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
