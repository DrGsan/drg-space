import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn,execFileSync} from 'node:child_process';
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
  const guest=await post('/admin/users',{admin_password:password,login:'avatarfriend',password:'Avatar-friend-password-123'},csrf);
  execFileSync(process.env.DRG_PYTHON||'.venv/bin/python',['-c',`import io,sys; from PIL import Image; from pathlib import Path
im=Image.new('RGB',(6000,4000),'red'); im.paste('blue',(3000,0,6000,4000)); exif=Image.Exif(); exif[274]=6
out=io.BytesIO(); im.save(out,format='MPO',save_all=True,append_images=[Image.new('RGB',(6000,4000),'green')],exif=exif)
Path(sys.argv[1]).write_bytes(out.getvalue()+bytes(11*1024*1024))`,directory+'/camera.jpeg']);
  const source=process.env.DRG_AVATAR_FILE||directory+'/camera.jpeg';
  // WebKit does not expose Blob bodies in request.postDataBuffer(). Observe
  // their byte length before native fetch while keeping the real HTTP request.
  await page.addInitScript(()=>{
    window.__avatarUploads=[];const native=window.fetch;
    window.fetch=function(url,options){
      if(String(url).endsWith('/api/account/avatar')&&options?.method==='POST'&&options.body instanceof Blob)window.__avatarUploads.push(options.body.size);
      if(String(url).includes('/api/admin/users/')&&String(url).endsWith('/profile')&&options?.method==='PUT'){const data=JSON.parse(options.body);if(data.avatar)window.__avatarUploads.push(atob(data.avatar).length);}
      return native.apply(this,arguments);
    };
  });
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  await page.goto('/');await page.locator('.sidebar [data-view=settings]').click();
  await page.locator('[data-preference-key=profile]>summary').click();
  const response=page.waitForResponse(r=>r.url().endsWith('/api/account/avatar')&&r.request().method()==='POST');
  await page.locator('[data-avatar-file]').setInputFiles(source);expect((await response).status()).toBe(200);
  await expect(page.locator('[data-avatar-status]')).toHaveText('Аватар сохранён');
  await expect.poll(()=>page.locator('.profile-avatar img').evaluate(im=>im.naturalWidth)).toBe(256);
  await page.locator('.sidebar [data-view=admin]').click();await page.locator('[data-preference-key=accounts]>summary').click();
  const row=page.locator('[data-admin-user="'+guest.id+'"]');await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-profile]').click();
  const dialog=page.getByRole('dialog');await dialog.locator('[name=avatar_file]').setInputFiles(directory+'/camera.jpeg');
  await expect(dialog.getByAltText('Предпросмотр аватара')).toBeVisible();
  const dimensions=await dialog.getByAltText('Предпросмотр аватара').evaluate(im=>({width:im.naturalWidth,height:im.naturalHeight}));
  expect(dimensions.width).toBeGreaterThan(0);expect(dimensions.width).toBeLessThan(dimensions.height);expect(dimensions.height).toBeLessThanOrEqual(1024);
  await dialog.locator('button[type=submit]').click();await expect(dialog).toHaveCount(0);
  await expect.poll(()=>row.locator('img').evaluate(im=>im.naturalWidth)).toBe(256);
  const uploads=()=>page.evaluate(()=>window.__avatarUploads);
  expect(await uploads()).toHaveLength(2);expect(Math.max(...await uploads())).toBeLessThan(500000);
  const before=await (await ctx.request.get(origin+'/api/users/'+guest.id+'/avatar')).body();
  await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-profile]').click();await dialog.locator('[name=avatar_file]').setInputFiles({name:'broken.jpeg',mimeType:'image/jpeg',buffer:Buffer.from('not a photo')});
  await expect(dialog.locator('[data-avatar-preview]')).toContainText('Не удалось открыть фото');
  await dialog.locator('button[type=submit]').click();
  await expect(dialog).toBeVisible();expect(await uploads()).toHaveLength(2);
  expect(await (await ctx.request.get(origin+'/api/users/'+guest.id+'/avatar')).body()).toEqual(before);
  expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8031',directory=await mkdtemp(tmpdir()+'/drg-avatar-upload-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8031','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Avatar upload passed: camera JPEG/MPO, large source compressed before upload, own/admin forms, preview and invalid-file preservation.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
