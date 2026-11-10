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
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const ctx=page.context(),password='Section-header-password-123';
  async function post(path,data,csrf=''){
    const r=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  }
  let csrf=(await post('/setup',{login:'avatarowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await post('/2fa/enroll',{password},csrf);await post('/2fa/confirm',{code:totp(enrolled.secret)},csrf);csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  await post('/admin/access/unlock',{password},csrf);
  const guest=await post('/admin/users',{admin_password:password,login:'avatarfriend',add_friend:true,password:'Avatar-friend-password-123'},csrf);
  const group=await post('/social/families',{name:'Группа для проверки'},csrf);
  const call=async(path,method='GET',data)=>{const r=await ctx.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();};
  await call('/appearance','PUT',{style:'glass',wallpaper:'warm',mode:'light'});
  await page.goto('/');await page.locator('.sidebar [data-view=settings]').click();await page.locator('[data-preference-key=profile]>summary').click();
  const gallery=page.locator('[data-preference-key=profile] [data-avatar-presets]');
  for(const category of ['abstract','nature','animals','space']){
    await gallery.locator(`[data-avatar-category=${category}]`).click();await expect(gallery.locator('[data-avatar-preset]')).toHaveCount(9);
    const selected=gallery.locator(`[data-avatar-preset=${category}-3]`);await selected.click();await expect(selected).toHaveAttribute('aria-pressed','true');
    await expect.poll(()=>page.locator('.profile-avatar img').evaluate(im=>im.naturalWidth)).toBe(256);
    expect((await call('/me')).settings.profile.avatar_preset).toBe(category+'-3');
  }
  await page.screenshot({path:'artifacts/avatar-profile-desktop.png',fullPage:true});
  await page.locator('[data-remove-avatar]').click();await expect(page.locator('.profile-avatar img')).toHaveCount(0);
  await gallery.locator('[data-avatar-category=animals]').click();await gallery.locator('[data-avatar-preset=animals-9]').click();await expect(gallery.locator('[data-avatar-preset=animals-9]')).toHaveAttribute('aria-pressed','true');
  await page.locator('.sidebar [data-view=admin]').click();await page.locator('[data-preference-key=accounts]>summary').click();
  const row=page.locator('[data-admin-user="'+guest.id+'"]');await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-profile]').click();
  const dialog=page.getByRole('dialog');await dialog.locator('[data-avatar-category=abstract]').click();await dialog.locator('[data-avatar-preset=abstract-2]').click();await dialog.locator('button[type=submit]').click();await expect(dialog).toHaveCount(0);
  expect((await call('/admin/users')).users.find(u=>u.id===guest.id).profile.avatar_preset).toBe('abstract-2');
  await page.setViewportSize({width:390,height:844});await page.locator('.sidebar [data-view=settings]').click();await page.locator('[data-preference-key=profile]>summary').click();await page.screenshot({path:'artifacts/avatar-profile-mobile.png',fullPage:true});
  if(process.env.DRG_AVATAR_ONLY==='1'){expect(errors).toEqual([]);return;}
  await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true}));
  await page.reload();
  await page.evaluate(()=>{document.documentElement.style.setProperty('--app-safe-top','59px');document.documentElement.style.setProperty('--app-safe-bottom','34px');});
  await page.locator('.mobile-app-tabs [data-mobile-view=chat]').click();
  await expect(page.locator('.chat-room').first()).toBeVisible();
  const rooms=(await call('/chat/rooms')).items;
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:900});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    for(const room of rooms){
      const link=page.locator(`[data-room="${room.id}"]`);
      if(!await link.isVisible()){await expect(page.locator('[data-chat-back]')).toBeVisible();await page.locator('[data-chat-back]').click();}
      await expect(link).toBeVisible();
      await page.locator(`[data-room="${room.id}"]`).click();
      await expect(page.locator('.chat-compose')).toBeVisible();
      await expect.poll(()=>page.locator('.chat-compose').evaluate(el=>Math.abs(el.getBoundingClientRect().bottom-visualViewport.height-visualViewport.offsetTop))).toBeLessThanOrEqual(1);
      await expect.poll(()=>page.locator('#chat-status').innerText()).not.toBe('Подключение…');
      await page.screenshot({path:`artifacts/chat-${width}-${room.kind}.png`});
      if(width===390)await page.locator('[data-chat-back]').click();
    }
  }
  expect(errors).toEqual([]);
}

const origin='http://127.0.0.1:8053',directory=await mkdtemp(tmpdir()+'/drg-avatar-presets-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8053','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:false,isMobile:false}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log(process.env.DRG_AVATAR_ONLY==='1'?'Avatar preset scenarios passed (composer not run).':'Avatar preset and composer scenarios passed.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
