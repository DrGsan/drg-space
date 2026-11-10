// Exercise the real encrypted server and every form kept by the new login UI.
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const origin='http://localhost:8048',directory=await mkdtemp(tmpdir()+'/drg-branding-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8048','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-branding-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser,page;server.stderr.on('data',data=>output=(output+data).slice(-4000));
const errors=[];
function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
async function send(context,path,data,csrf='',method='POST'){
  const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
  expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
}
async function checkedPage(context){
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  return page;
}
async function geometry(page){
  expect(await page.evaluate(()=>{
    const layout=document.querySelector('.auth-layout'),box=layout.getBoundingClientRect();
    const app=document.getElementById('app');
    return document.documentElement.scrollWidth<=innerWidth&&app.scrollWidth<=app.clientWidth&&
      [...layout.querySelectorAll('h1,input,button,.brand')].every(el=>{
        const b=el.getBoundingClientRect();
        return b.left>=box.left-.5&&b.right<=box.right+.5&&
          (!el.matches('input,button')||b.height>=44);
      });
  })).toBe(true);
  await expect(page.locator('.auth-header .brand-mark')).toHaveJSProperty('complete',true);
  expect(await page.locator('.auth-header .brand-mark').evaluate(img=>img.naturalWidth)).toBe(256);
}
try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}if(!ready)throw Error(output||'Server did not start');
  browser=await (engine==='webkit'?webkit:chromium).launch({headless:true});await mkdir('artifacts',{recursive:true});
  const owner=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000}});
  page=await checkedPage(owner);await page.goto('/');
  await expect(page.locator('#auth-title')).toHaveText('Создайте своё пространство');
  await geometry(page);await page.screenshot({path:`artifacts/branding-${engine}-setup.png`,fullPage:true});
  const password='Branding-password-123';
  await page.getByLabel('Ключ первичной настройки').fill('browser-test-setup-key');
  await page.getByLabel('Логин',{exact:true}).fill('brandowner');await page.getByLabel('Пароль',{exact:true}).fill(password);
  await page.locator('#auth-form button[type=submit]').click();
  await expect(page.getByRole('heading',{name:'Настроим 2FA'})).toBeVisible();
  let me=await (await owner.request.get(origin+'/api/me')).json();
  const enrollment=await send(owner,'/2fa/enroll',{password},me.csrf);
  const codes=(await send(owner,'/2fa/confirm',{code:totp(enrollment.secret)},me.csrf)).recovery_codes;
  await send(owner,'/2fa/activate',{saved_recovery_codes:true},me.csrf);
  me=await (await owner.request.get(origin+'/api/me')).json();
  const appearance={theme:'rose',mode:'dark',day_start:'07:00',night_start:'22:00'};
  await send(owner,'/appearance',appearance,me.csrf,'PUT');
  await send(owner,'/admin/access/unlock',{password},me.csrf);
  const invite=await send(owner,'/invites',{},me.csrf);
  await send(owner,'/logout',{},me.csrf);
  // Both browser and installed phone canvases must retain the public palette
  // even when the device remembers a different account theme.
  for(const installed of [false,true])for(const scheme of ['light','dark']){
    const context=await browser.newContext({locale:'ru-RU',baseURL:origin,colorScheme:scheme,viewport:{width:1440,height:1000}});
    await context.addInitScript(({installed,scheme})=>{
      Object.defineProperty(navigator,'standalone',{configurable:true,value:installed});
      try{localStorage.setItem('drg-appearance',JSON.stringify({theme:'rose',mode:scheme,day_start:'07:00',night_start:'22:00'}));}catch{}
      if(installed)document.addEventListener('DOMContentLoaded',()=>{document.documentElement.style.setProperty('--app-safe-top','59px');document.documentElement.style.setProperty('--app-safe-bottom','34px');},{once:true});
    },{installed,scheme});
    const target=await checkedPage(context);await target.goto('/');await expect(target.locator('#auth-title')).toHaveText('Войти в свой мир.');
    const supported=await target.locator('[data-passkey-login]').count();
    if(supported){
      expect(await target.locator('[data-passkey-login]').evaluate(button=>button.compareDocumentPosition(document.querySelector('[name=login]'))&Node.DOCUMENT_POSITION_FOLLOWING)).toBeTruthy();
      if(scheme==='dark')await expect(target.locator('.auth-submit')).toHaveCSS('color','rgb(237, 246, 252)');
    }
    for(const width of [1440,768,390,320]){
      await target.setViewportSize({width,height:width<800?844:1000});
      if(width<=760){await target.reload();await expect(target.locator('#auth-title')).toHaveText('Войти в свой мир.');}
      await geometry(target);
      if(width<=760)await expect(target.locator('[name=login]')).not.toBeFocused();
      if(width===1440&&!installed||width===390)await target.screenshot({path:`artifacts/branding-${engine}-${installed?'installed':'browser'}-${scheme}-${width}.png`,fullPage:true});
    }
    await expect(target.locator('meta[name=theme-color]')).toHaveAttribute('content',scheme==='dark'?'#111a21':'#eff5f8');
    expect(await target.evaluate(()=>JSON.parse(localStorage.getItem('drg-appearance')).theme)).toBe('rose');
    if(supported){
      await target.route('**/api/passkeys/options',r=>r.fulfill({status:503,json:{detail:'Подтверждение временно недоступно. Повторите попытку или войдите с паролем.'}}));
      await target.locator('[data-passkey-login]').click();await expect(target.locator('[data-passkey-login-status]')).toContainText('Подтверждение временно недоступно');
      await expect(target.locator('.auth-submit')).toBeEnabled();await geometry(target);
    }
    await context.close();
  }
  await page.evaluate(value=>localStorage.setItem('drg-appearance',JSON.stringify(value)),appearance);await page.reload();
  await page.getByLabel('Логин',{exact:true}).fill('brandowner');await page.getByLabel('Пароль',{exact:true}).fill(password);
  await page.locator('#auth-form button[type=submit]').click();
  await expect(page.locator('#auth-title')).toHaveText('Подтвердите вход');await geometry(page);
  await page.screenshot({path:`artifacts/branding-${engine}-2fa.png`,fullPage:true});
  await page.getByLabel('Код 2FA или резервный код').fill(codes[0]);await page.locator('#auth-form button[type=submit]').click();
  await expect(page.locator('.workspace')).toBeVisible();
  await expect(page.locator('.sidebar .brand')).toHaveAttribute('aria-label','DrG Space');
  await expect(page.locator('.sidebar .brand-mark')).toHaveAttribute('src','/assets/icons/drg-d8-logo.png');
  await expect(page.locator('meta[name=theme-color]')).toHaveAttribute('content','#23151c');
  me=await (await owner.request.get(origin+'/api/me')).json();expect(me.settings.appearance.theme).toBe('rose');
  const manifestResponse=await page.request.get('/assets/manifest.webmanifest'),manifest=await manifestResponse.json();
  expect(manifest.name).toBe('DrG Space');expect(manifest.id).toBe('/');expect(manifest.scope).toBe('/');
  for(const icon of manifest.icons){const response=await page.request.get(icon.src);expect(response.ok()).toBe(true);const bytes=await response.body();expect(bytes.readUInt32BE(16)+'x'+bytes.readUInt32BE(20)).toBe(icon.sizes);}
  // Password-only browsers still have a real form, without an empty divider.
  const fallback=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:320,height:700}});
  await fallback.addInitScript(()=>Object.defineProperty(window,'PublicKeyCredential',{configurable:true,value:undefined}));
  const guest=await checkedPage(fallback);await guest.goto('/');
  await expect(guest.locator('[data-passkey-login]')).toHaveCount(0);await expect(guest.locator('.auth-divider')).toHaveCount(0);
  await expect(guest.locator('.auth-submit')).toContainText('Войти');await geometry(guest);
  await guest.goto(invite.url);await expect(guest.locator('#auth-title')).toHaveText('Добро пожаловать');await geometry(guest);
  await guest.getByLabel('Логин',{exact:true}).fill('brandguest');await guest.getByLabel('Пароль',{exact:true}).fill(password);
  await guest.getByLabel('Подсказка пароля, необязательно').fill('Памятная дата');
  await guest.getByRole('button',{name:'Принять приглашение'}).click();
  await expect(guest.getByRole('heading',{name:'Настроим 2FA'})).toBeVisible();await guest.getByRole('button',{name:'Настроить позже'}).click();
  await expect(guest.locator('.workspace')).toBeVisible();await fallback.close();
  await send(owner,'/admin/access/unlock',{password},me.csrf);
  await send(owner,'/admin/registration',{enabled:true,admin_password:password},me.csrf,'PUT');
  const publicContext=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:390,height:844}}),publicPage=await checkedPage(publicContext);
  await publicPage.goto('/');await publicPage.locator('#public-register').click();
  await expect(publicPage.locator('#auth-title')).toHaveText('Добро пожаловать');await geometry(publicPage);
  await publicPage.getByLabel('Логин',{exact:true}).fill('brandpublic');await publicPage.getByLabel('Пароль',{exact:true}).fill(password);
  await publicPage.locator('#auth-form button[type=submit]').click();await expect(publicPage.getByRole('heading',{name:'Настроим 2FA'})).toBeVisible();
  await publicContext.close();expect(errors).toEqual([]);
  console.log(`DrG Space branding passed (${engine}): setup, password/2FA, invitations, public registration, passkey failure/fallback, legacy account themes, icons and responsive browser/PWA layouts.`);
}catch(error){await page?.screenshot({path:`artifacts/branding-${engine}-failure.png`,fullPage:true}).catch(()=>{});throw error;}
finally{await browser?.close();server.kill('SIGTERM');await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));await rm(directory,{recursive:true,force:true});}
