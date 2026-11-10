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

async function scenario(page){
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1280,height:900});
  const context=page.context(),password='Paste-menu-password-123';
  function apiFor(context){return async(path,method='GET',data,token='')=>{
    const r=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':token},data});
    expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  };}
  const api=apiFor(context);
  let csrf=(await api('/setup','POST',{login:'pasteowner',password,token:'browser-test-setup-key'})).csrf;
  async function activate(api,csrf){const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);return (await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;}
  csrf=await activate(api,csrf);await api('/admin/access/unlock','POST',{password},csrf);
  async function register(login){
    const invited=await api('/invites','POST',{login},csrf),context=await browser.newContext({locale:'ru-RU',baseURL:origin}),apiOther=apiFor(context);
    const result=await apiOther('/register','POST',{login,password,invite:invited.url.split('/').at(-1)});
    return {context,api:apiOther,csrf:await activate(apiOther,result.csrf),user:await apiOther('/me')};
  }
  const friend=await register('friend'),guest=await register('guest');
  for(const contact of [friend,guest]){const request=await api('/social/friends','POST',{login:contact.user.login},csrf);await contact.api('/social/requests/'+request.id+'/accept','POST',undefined,contact.csrf);}
  const family=await api('/social/families','POST',{name:'Семья Сашков'},csrf);
  const invitation=await api('/social/families/'+family.id+'/invites','POST',{login:'friend'},csrf);
  await friend.api('/social/requests/'+invitation.id+'/accept','POST',undefined,friend.csrf);
  const root=await api('/notes','POST',{title:'Общие заметки',space_id:family.id},csrf);
  const child=await api('/notes','POST',{title:'Подстраница',space_id:family.id,parent_id:root.id},csrf);
  const secret=await api('/notes','POST',{title:'Другие семейные планы',space_id:family.id},csrf);
  await page.goto('/page/'+root.id);
  await expect(page.locator('#page-title')).toBeEnabled();
  await page.locator('#page-participants').click();
  const panel=page.getByRole('dialog',{name:'Доступ к странице'});
  await expect(panel).toBeVisible();
  await expect(panel.locator('.note-access-member')).toHaveCount(2);
  const friendRole=()=>page.locator('[data-access-role="'+friend.user.id+'"]');
  await friendRole().click();
  await expect(page.getByRole('menuitem',{name:'Удалить доступ',exact:true})).toBeVisible();
  await expect(page.locator('.note-access-role-menu')).not.toContainText('Наследовать');
  await page.screenshot({path:'artifacts/notes-access-menu.png',fullPage:true});
  await page.getByRole('menuitemradio',{name:/Просмотр/}).click();
  await expect(friendRole()).toContainText('Просмотр');
  expect((await friend.api('/notes/'+root.id)).role).toBe('viewer');
  // The invited account is already editing a private note. Sharing must update
  // its navigation without reloading or replacing that editor.
  const guestPage=await guest.context.newPage(),guestErrors=[];guestPage.on('pageerror',e=>guestErrors.push(e.message));
  const privateNote=await guest.api('/notes','POST',{title:'Личная открытая заметка',body:'Мой текст остаётся в редакторе'},guest.csrf);
  await guestPage.goto('/page/'+privateNote.id);
  await expect(guestPage.locator('#page-save-status')).toContainText('Сохранено');
  const originalEditor=await guestPage.locator('#block-editor').elementHandle();
  const catalogRequests=[];guestPage.on('request',request=>{if(request.url().includes('/notes?catalog=true'))catalogRequests.push(request.url());});
  await panel.locator('summary').click();await panel.locator('[name=friend][value=guest]').check();
  await panel.getByRole('button',{name:'Добавить',exact:true}).click();
  await expect(panel.locator('.note-access-member')).toHaveCount(3);
  expect((await guest.api('/social')).spaces.map(s=>s.kind)).toEqual(['friends']);
  expect((await guest.api('/notes')).map(n=>n.id).sort()).toEqual([root.id,child.id,privateNote.id].sort());
  await expect(guestPage.locator('[data-sidebar-section=shared] [data-open-page="'+root.id+'"]').first()).toBeVisible({timeout:12000});
  expect(await originalEditor.evaluate(el=>el.isConnected)).toBe(true);
  await expect(guestPage.locator('#page-title')).toHaveValue('Личная открытая заметка');
  await expect(guestPage.locator('#block-editor')).toContainText('Мой текст остаётся в редакторе');
  expect(catalogRequests.length).toBeGreaterThan(0);
  await guestPage.goto('/page/'+root.id);
  await expect(guestPage.locator('#page-participants .person-avatar')).toHaveCount(3);
  await expect(guestPage.locator('#notes-space')).toHaveValue('private');
  await expect(guestPage.locator('[data-sidebar-section=shared]')).toContainText('Общие заметки');
  await expect(guestPage.locator('#page-tree')).not.toContainText('Другие семейные планы');
  await expect(guestPage.locator('#page-save-status')).toContainText('Сохранено');
  await page.locator('[data-access-role="'+guest.user.id+'"]').click();
  await page.getByRole('menuitem',{name:'Удалить доступ',exact:true}).click();
  await expect(panel.locator('.note-access-member')).toHaveCount(2);
  await expect.poll(async()=>(await guest.api('/notes')).map(n=>n.id)).toEqual([privateNote.id]);
  const denied=await guest.context.request.get(origin+'/api/notes/'+root.id);expect(denied.status()).toBe(404);
  await expect(guestPage.locator('#page-title')).toBeDisabled({timeout:15000});
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    await expect(panel).toBeVisible();
    await friendRole().click();const menu=page.locator('.note-access-role-menu');await expect(menu).toBeVisible();
    for(const el of [panel,menu]){const box=await el.boundingBox();expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);expect(box.y+box.height).toBeLessThanOrEqual(844);}
    await page.screenshot({path:'artifacts/notes-access-menu-'+width+'.png'});
    await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);
  }
  await page.setViewportSize({width:1280,height:900});
  await friendRole().click();await page.getByRole('menuitem',{name:'Удалить доступ',exact:true}).click();
  await expect(panel.locator('.note-access-member')).toHaveCount(1);
  expect((await friend.api('/social')).spaces[0].members).toHaveLength(2);
  expect((await friend.api('/notes')).map(n=>n.id)).toEqual([secret.id]);
  await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);
  expect(errors).toEqual([]);expect(guestErrors).toEqual([]);
  await friend.context.close();await guest.context.close();
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8033',directory=await mkdtemp(tmpdir()+'/drg-notes-access-');
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Notes access menu passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
