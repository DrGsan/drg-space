import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn,spawnSync} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {appSection,setAdminPassword} from './notes-ui.js';

const baseline=process.env.DRG_INVITES_BASELINE==='1',engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const directory=await mkdtemp(tmpdir()+'/drg-invites-'),port=engine==='webkit'?8063:8061,origin='http://127.0.0.1:'+port;
const out='artifacts/invitations-review'+(engine==='webkit'?'/webkit':'');await mkdir(out,{recursive:true});
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:'+port,'tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-invites-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));const exited=new Promise(resolve=>server.once('exit',resolve));
try{
  let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,200));}
  if(!ready)throw Error(output);
  browser=await ({chromium,webkit}[engine]).launch({headless:true});const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1280,height:900},colorScheme:'light'});const page=await context.newPage();
  let csrf='';const password='Invites-test-password-123';
  const send=async(path,body,method='POST')=>{const response=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});expect(response.ok(),path+' '+await response.text()).toBe(true);return response.json();};
  csrf=(await send('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
  const {secret}=await send('/2fa/enroll',{password});const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join('');const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
  await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
  await send('/admin/access/unlock',{password});await send('/appearance',{wallpaper:'mountains',mode:'light'},'PUT');setAdminPassword(page,password);
  await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));await page.goto('/');
  async function open(label='Управление'){await appSection(page,label);for(const row of await page.locator('.preference-row').all())if(await row.getAttribute('data-preference-key')!=='registration'&&await row.getAttribute('open')!==null)await row.locator(':scope>summary').click();await page.locator('[data-invite-member]').waitFor();}
  await open();
  if(baseline){
    for(let i=0;i<2;i++){await page.locator('[data-invite-member]').click();await page.getByRole('textbox',{name:'Ссылка приглашения'}).waitFor();await page.locator('.modal-close').click();}
    await page.locator('[data-preference-key="registration"]').screenshot({path:out+'/before-desktop.png'});
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await open();await page.locator('[data-preference-key="registration"]').screenshot({path:out+'/before-webapp.png'});
  }
  else{
    const create=async(friend=false)=>{
      await page.locator('[data-invite-member]').click();const form=page.locator('[data-create-invitation]');
      await expect(form.locator('input[type=checkbox]')).not.toBeChecked();
      if(friend)await form.locator('input[type=checkbox]').check();
      if(friend)await page.locator('.modal').screenshot({path:out+'/after-create-desktop.png'});
      await form.locator('[type=submit]').click();await expect(page.locator('[data-create-invitation]')).toHaveCount(0);
    };
    await create();await expect(page.locator('[data-invitation-id]')).toHaveCount(1);
    await create(true);await expect(page.locator('[data-invitation-id]')).toHaveCount(2);
    const items=(await send('/invites',undefined,'GET')).invitations,friend=items.find(item=>item.add_creator_as_friend),plain=items.find(item=>!item.add_creator_as_friend);
    await page.reload();await open();await expect(page.locator('[data-invitation-id]')).toHaveCount(2);
    if(engine==='chromium')await context.grantPermissions(['clipboard-read','clipboard-write']);
    const friendRow=page.locator(`[data-invitation-id="${friend.id}"]`);await friendRow.locator('[data-copy-invitation]').click();
    if(engine==='chromium')await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(friend.url);
    await page.locator('[data-preference-key="registration"]').screenshot({path:out+'/after-desktop.png'});
    const overflow=async()=>expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
    for(const width of [390,320]){await page.setViewportSize({width,height:844});await open();await overflow();await page.locator('[data-preference-key="registration"]').screenshot({path:out+`/after-webapp-${width}.png`});}
    await send('/language',{language:'en'},'PUT');await send('/appearance',{mode:'dark'},'PUT');await page.reload();await open('Administration');await overflow();await expect(page.locator('[data-invitation-list]')).toContainText('Adds you as a friend');
    await page.locator('[data-preference-key="registration"]').screenshot({path:out+'/after-webapp-320-en-dark.png'});
    await send('/language',{language:'ru'},'PUT');await send('/appearance',{mode:'light'},'PUT');await page.setViewportSize({width:1280,height:900});await page.reload();await open();
    const guestContext=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:1280,height:900}}),guest=await guestContext.newPage();
    await guest.goto(friend.url);await expect(guest.locator('.invitation-friendship-disclosure')).toContainText('@alexander');await expect(guest.locator('.auth-submit')).toBeEnabled();
    await guest.locator('.auth-main').screenshot({path:out+'/after-register-desktop.png'});
    await guest.setViewportSize({width:390,height:844});await guest.locator('.auth-main').screenshot({path:out+'/after-register-webapp.png'});
    await guest.locator('[name=login]').fill('new_member');await guest.locator('[name=password]').fill('Friend-password-123');await guest.locator('.auth-submit').click();await guest.locator('#skip-enrollment').waitFor();
    const before=await guest.request.get('/api/me');expect((await before.json()).needs_enrollment).toBe(true);
    expect((await send('/invites',undefined,'GET')).invitations.some(item=>item.id===friend.id)).toBe(false);
    // A stale active-row action can still cancel the pending friendship safely.
    await send('/invites/'+friend.id,undefined,'DELETE');
    await page.reload();await open();await expect(page.locator('[data-invitation-id]')).toHaveCount(1);
    await create();await expect(page.locator('[data-invitation-id]')).toHaveCount(2);
    const revoke=(await send('/invites',undefined,'GET')).invitations.find(item=>item.id!==plain.id);
    await page.locator(`[data-invitation-id="${revoke.id}"] [data-revoke-invitation]`).click();await page.locator('[data-revoke-invitation-form] [type=submit]').click();await expect(page.locator(`[data-invitation-id="${revoke.id}"]`)).toHaveCount(0);await expect(page.locator('[data-invitation-id]')).toHaveCount(1);
    await page.locator('[data-preference-key=registration]').screenshot({path:out+'/after-active-only-desktop.png'});await page.setViewportSize({width:390,height:844});await page.locator('[data-preference-key=registration]').screenshot({path:out+'/after-active-only-webapp.png'});
    await guest.locator('#skip-enrollment').click();await expect(guest.locator('#toast')).toContainText('Автоматическая дружба отменена');
    expect((await (await guest.request.get('/api/me')).json()).needs_enrollment).toBe(false);
    expect((await guest.request.get('/api/social')).ok()).toBe(true);
    const anonymous=await browser.newContext({baseURL:origin,locale:'en-US'}),registration=await anonymous.newPage();await registration.goto(plain.url);await expect(registration.locator('.auth-submit')).toBeEnabled();await expect(registration.locator('.invitation-friendship-disclosure')).toHaveCount(0);
    await anonymous.close();await guestContext.close();
    // Shorten only this disposable database fixture, leaving the real 7-day policy intact.
    const ending=await send('/invites',{add_creator_as_friend:false});
    const fixture=(code,...args)=>{const result=spawnSync(process.env.DRG_PYTHON||'.venv/bin/python',['-c',code,directory,...args],{env:{...process.env,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU='},encoding:'utf8'});if(result.status!==0)throw Error(result.stderr);return JSON.parse(result.stdout);};
    fixture(`import sys,time,json
from app.database import Database
from app.registration_invites import read,write,PREFIX,SECONDS
with Database(sys.argv[1],url='').connection() as db:
 db.execute('BEGIN IMMEDIATE');value=read(db,PREFIX+sys.argv[2]);expiry=int(time.time())+5;value.update(expires=expiry,created=expiry-SECONDS);write(db,PREFIX+sys.argv[2],value);db.execute('UPDATE invites SET expires=? WHERE token=?',(expiry,sys.argv[2]))
print(json.dumps({'expires':expiry}))`,ending.id);
    await page.reload();await open();await expect(page.locator(`[data-invitation-id="${ending.id}"]`)).toHaveCount(1);
    await expect(page.locator(`[data-invitation-id="${ending.id}"]`)).toHaveCount(0,{timeout:10000});await expect(page.locator(`[data-invitation-id="${plain.id}"]`)).toHaveCount(1);
    const removed=fixture(`import sys,json
from app.database import Database
from app.registration_invites import read,PREFIX
with Database(sys.argv[1],url='').connection() as db:
 print(json.dumps({'row':bool(db.execute('SELECT 1 FROM invites WHERE token=?',(sys.argv[2],)).fetchone()),'metadata':bool(read(db,PREFIX+sys.argv[2]))}))`,ending.id);expect(removed).toEqual({row:false,metadata:false});
    expect((await page.request.get('/api/invites/'+ending.url.split('/').at(-1))).status()).toBe(404);

  }
  console.log('Invitation '+(baseline?'before':'after')+' '+engine+' passed.');
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
