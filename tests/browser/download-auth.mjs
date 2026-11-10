// Real encrypted server: retain the refresh cookie while expiring only access.
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

const origin='http://localhost:8056',secret='isolated-download-browser-secret-'.repeat(3);
const directory=await mkdtemp(tmpdir()+'/drg-download-auth-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8056','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:secret,SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,GUNICORN_WORKERS:'1',CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
async function expireAccess(context){
  const cookie=(await context.cookies()).find(x=>x.name==='drg_session');
  const [header,payload]=cookie.value.split('.'),claims=JSON.parse(Buffer.from(payload,'base64url'));
  for(const key of ['iat','nbf','exp'])claims[key]-=901;
  const unsigned=header+'.'+Buffer.from(JSON.stringify(claims)).toString('base64url');
  const key=createHmac('sha256',secret).update('drg-site:jwt:access:v1').digest();
  await context.addCookies([{...cookie,value:unsigned+'.'+createHmac('sha256',key).update(unsigned).digest('base64url')}]);
}
async function contents(download){
  expect(await download.failure()).toBeNull();
  return readFile(await download.path());
}
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Server did not start');
  const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
  browser=await (engine==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  async function post(path,data,csrf=''){
    const response=await context.request.post('/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  const password='Download-browser-password-123';
  let csrf=(await post('/setup',{login:'downloadowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await post('/2fa/enroll',{password},csrf);
  await post('/2fa/confirm',{code:totp(enrollment.secret)},csrf);
  csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  const body=Buffer.from('Исходные байты файла\nDownload authentication regression.\n');
  const file=await post('/files/upload?name='+encodeURIComponent('Проверка.txt'),body,csrf);
  const imageBody=await readFile('web/public/assets/icons/drg-d8-512.png');
  const image=await post('/files/upload?name=preview.png',imageBody,csrf);
  const share=await post('/files/'+file.id+'/shares',{mode:'authenticated',permission:'view'},csrf);
  const publicShare=await post('/files/'+file.id+'/shares',{mode:'public',permission:'view'},csrf);
  const passwordShare=await post('/files/'+file.id+'/shares',{mode:'password',password:'Publication-password-123',permission:'view'},csrf);
  const imageShare=await post('/files/'+image.id+'/shares',{mode:'authenticated',permission:'view'},csrf);
  await page.goto(share.url);await expect(page.locator('.shared-drive-panel')).toBeVisible();
  await page.locator('[data-file-menu]').click();
  await expireAccess(context);
  {
    const refreshed=page.waitForResponse(r=>r.url()===origin+'/api/refresh'&&r.request().method()==='POST');
    const first=page.waitForEvent('download');await page.locator('[data-shared-download]').click();
    expect((await refreshed).status()).toBe(200);expect(await contents(await first)).toEqual(body);
    expect(page.url()).toBe(share.url);await expect(page.locator('.shared-drive-panel')).toBeVisible();
    await page.locator('[data-shared-open]').click();await expect(page.locator('.file-editor-dialog textarea')).toBeVisible();
    await expireAccess(context);
    const textDownload=page.waitForEvent('download');await page.locator('.file-editor-dialog a').click();
    expect(await contents(await textDownload)).toEqual(body);expect(page.url()).toBe(share.url);
    await page.locator('.file-editor-dialog .modal-close').click();
    // ZIP and selection controls must use the same recovery path.
    for(const selector of ['.shared-drive-download','[data-shared-selected]']){
      await page.locator('[data-select-file]').check();await expireAccess(context);
      const pending=page.waitForEvent('download');await page.locator(selector).click();
      const zip=await contents(await pending);expect(zip.subarray(0,2).toString()).toBe('PK');
      expect(page.url()).toBe(share.url);
    }
    // Revoking access after the page opened must stop navigation, even with a valid session.
    csrf=(await (await context.request.get('/api/me')).json()).csrf;
    const revoked=await context.request.delete('/api/file-shares/'+share.token,{headers:{Origin:origin,'X-CSRF-Token':csrf}});
    expect(revoked.ok()).toBe(true);
    await page.locator('.shared-drive-download').click();
    await expect(page.locator('#toast')).toContainText('недоступ');expect(page.url()).toBe(share.url);

    // The preview toolbar and private Drive use native streaming as well.
    await page.goto(imageShare.url);await page.locator('[data-shared-open]').click();
    await expect(page.locator('.drive-preview-content img')).toBeVisible();await expireAccess(context);
    let pending=page.waitForEvent('download');await page.getByRole('link',{name:'Скачать файл',exact:true}).click();
    expect(await contents(await pending)).toEqual(imageBody);expect(page.url()).toBe(imageShare.url);
    await page.goto('/');await page.locator('.sidebar [data-view=files]').click();
    await expect(page.locator('.drive-panel')).toBeVisible();
    await page.locator('[data-file-menu="'+file.id+'"]').click();await expireAccess(context);
    pending=page.waitForEvent('download');await page.locator('[data-file-download]').click();
    expect(await contents(await pending)).toEqual(body);expect(page.url()).toBe(origin+'/');
    await page.locator('[data-select-file="'+file.id+'"]').check();await expireAccess(context);
    pending=page.waitForEvent('download');await page.locator('[data-download-selected]').click();
    expect((await contents(await pending)).subarray(0,2).toString()).toBe('PK');

    // A missing refresh token must keep the shared page and offer explicit login.
    await page.goto(imageShare.url);await expect(page.locator('.shared-drive-panel')).toBeVisible();await expireAccess(context);
    await context.clearCookies({name:'drg_refresh'});
    await page.locator('.shared-drive-download').click();
    await expect(page.getByRole('heading',{name:'Войдите в аккаунт',exact:true})).toBeVisible();
    expect(page.url()).toBe(imageShare.url);
    await page.locator('[data-download-login]').click();
    await expect(page.locator('#auth-title')).toHaveText('Войти в свой мир.');
    expect(page.url()).toBe(imageShare.url);

    // Public links keep working anonymously and never require account refresh.
    const guest=await browser.newContext({locale:'ru-RU',baseURL:origin}),publicPage=await guest.newPage();
    const refreshes=[];publicPage.on('request',r=>{if(r.url().endsWith('/api/refresh'))refreshes.push(r.url());});
    await publicPage.goto(publicShare.url);await expect(publicPage.locator('.shared-drive-panel')).toBeVisible();
    await publicPage.locator('[data-file-menu]').click();
    pending=publicPage.waitForEvent('download');await publicPage.locator('[data-shared-download]').click();
    expect(await contents(await pending)).toEqual(body);expect(refreshes).toEqual([]);
    expect(publicPage.url()).toBe(publicShare.url);
    await publicPage.goto(passwordShare.url);
    await publicPage.getByLabel('Пароль',{exact:true}).fill('Publication-password-123');
    await publicPage.getByRole('button',{name:'Открыть публикацию',exact:true}).click();
    await expect(publicPage.locator('.shared-drive-panel')).toBeVisible();
    await publicPage.locator('[data-file-menu]').click();
    pending=publicPage.waitForEvent('download');await publicPage.locator('[data-shared-download]').click();
    expect(await contents(await pending)).toEqual(body);expect(refreshes).toEqual([]);
    await guest.close();
    expect(errors).toEqual([]);
    console.log('PASS ('+engine+'): expired access recovers for shared/private files, ZIP selection and preview; original bytes; revoked access, expired session and anonymous public/password links.');
  }
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
