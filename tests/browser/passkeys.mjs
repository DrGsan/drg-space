// Chromium's native WebAuthn stack signs real challenges with a resident key.
import {expect} from '@playwright/test';
import {chromium} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac,createHash,randomBytes} from 'node:crypto';
import {appSection} from './notes-ui.js';
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const origin='http://localhost:8024',directory=await mkdtemp(tmpdir()+'/drg-passkeys-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8024','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-passkey-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser,page;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
 let ready=false;for(let i=0;i<100;i++){try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,100));}if(!ready)throw Error(output||'Server did not start');
 browser=await chromium.launch({headless:true});const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000}});await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
 const session=await context.newCDPSession(page);await session.send('WebAuthn.enable');const {authenticatorId}=await session.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'internal',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
 const password='Passkey-password-123';async function post(path,data,csrf=''){const r=await context.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();}
 let csrf=(await post('/setup',{login:'passkeyowner',password,token:'browser-test-setup-key'})).csrf;const enrollment=await post('/2fa/enroll',{password},csrf);const codes=(await post('/2fa/confirm',{code:totp(enrollment.secret)},csrf)).recovery_codes;await post('/2fa/activate',{saved_recovery_codes:true},csrf);
 const logout=async()=>{const me=await (await context.request.get(origin+'/api/me')).json();await post('/logout',{},me.csrf);await page.reload();await expect(page.locator('[data-passkey-login]')).toBeVisible();};
 const securityDesktop=async()=>{await appSection(page,'Настройки');await page.locator('#open-security').click();await expect(page.locator('[data-passkey-add]')).toBeVisible();};
 const modal=()=>page.locator('.modal-overlay').last();await mkdir('artifacts',{recursive:true});await page.goto('/');await securityDesktop();
 await page.locator('[data-passkey-add]').click();await modal().locator('[name=name]').fill('MacBook QA');await modal().locator('[name=password]').fill(password);await modal().locator('[name=code]').fill(codes[0]);await modal().locator('button[type=submit]').click();
 await expect(page.locator('.modal-overlay')).toHaveCount(0);await expect(page.locator('.passkey-row strong')).toHaveText('MacBook QA');await page.screenshot({path:'artifacts/passkey-settings-desktop.png'});
 const credentials=await session.send('WebAuthn.getCredentials',{authenticatorId});expect(credentials.credentials).toHaveLength(1);expect(credentials.credentials[0].isResidentCredential).toBe(true);
 // The app's system browser uses this exact page; only a PKCE-bound, one-use code returns to the app.
 const native=await browser.newContext({locale:'ru-RU'}),verifier=randomBytes(32).toString('base64url'),state=randomBytes(32).toString('base64url');
 const nativeStart=await native.request.post(origin+'/api/native/passkeys/start',{data:{challenge:createHash('sha256').update(verifier).digest('base64url'),state}});expect(nativeStart.ok()).toBeTruthy();
 let nativeGrant;
 await page.route('**/api/passkeys/verify',async route=>{const response=await route.fetch();nativeGrant=(await response.json()).native;await route.fulfill({response});});
 await page.goto((await nativeStart.json()).url);await expect(page.locator('#server')).toHaveText(new URL(origin).host);
 await page.screenshot({path:'artifacts/native-passkey-browser.png'});await page.locator('#continue').click();
 await expect.poll(()=>nativeGrant?.state).toBe(state);
 const exchanged=await native.request.post(origin+'/api/native/passkeys/exchange',{data:{code:nativeGrant.code,verifier}});expect(exchanged.ok(),await exchanged.text()).toBeTruthy();
 expect((await (await native.request.get(origin+'/api/me')).json()).login).toBe('passkeyowner');
 expect((await native.request.post(origin+'/api/native/passkeys/exchange',{data:{code:nativeGrant.code,verifier}})).status()).toBe(401);
 await page.unroute('**/api/passkeys/verify');await native.close();await page.goto('/');
 await logout();await page.screenshot({path:'artifacts/passkey-login-desktop.png'});await page.locator('[data-passkey-login]').click();await expect(page.locator('.workspace')).toBeVisible();await expect(page.locator('.sidebar [data-view=home]')).toHaveClass(/active/);
 // Installed phone UI uses the same native ceremony and opens Messages.
 await page.setViewportSize({width:390,height:844});await logout();await expect(page.locator('#auth-form [name=login]')).not.toBeFocused();await page.screenshot({path:'artifacts/passkey-login-mobile.png'});
 await page.locator('[data-passkey-login]').click();await expect(page.locator('.mobile-app-tabs [data-mobile-view=chat]')).toHaveAttribute('aria-current','page');
 await page.locator('.mobile-app-tabs [data-mobile-view=settings]').click();if(await page.locator('[data-preference-key=security]').getAttribute('open')===null)await page.locator('[data-preference-key=security]>summary').click();await page.locator('#open-security').click();
 await expect(page.locator('.passkey-row strong')).toHaveText('MacBook QA');expect(await page.locator('#view-content').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);await page.screenshot({path:'artifacts/passkey-settings-mobile.png'});
 await page.locator('[data-passkey-rename]').click();await modal().locator('[name=name]').fill('Мой ключ');await modal().locator('button[type=submit]').click();await expect(page.locator('.passkey-row strong')).toHaveText('Мой ключ');
 await page.locator('[data-passkey-delete]').click();await modal().locator('[name=password]').fill(password);await modal().locator('[name=code]').fill(codes[1]);await modal().locator('button[type=submit]').click();await expect(page.locator('.passkey-row')).toHaveCount(0);
 await logout();await page.locator('[data-passkey-login]').click();await expect(page.locator('[data-passkey-login-status]')).toContainText('Passkey не подтвердил вход');await expect(page.locator('[data-passkey-login]')).toBeEnabled();
 await page.locator('[name=login]').fill('passkeyowner');await page.locator('[name=password]').fill(password);await page.locator('#auth-form button[type=submit]').click();await page.locator('[name=code]').fill(codes[2]);await page.locator('#auth-form button[type=submit]').click();await expect(page.locator('.workspace')).toBeVisible();
 expect(errors).toEqual([]);console.log('Passkeys passed: native resident WebAuthn registration, desktop/installed-phone passwordless login, management, revocation and password + 2FA fallback.');
}catch(error){await page?.screenshot({path:'artifacts/passkey-failure.png'}).catch(()=>{});throw error;}
finally{await browser?.close();server.kill('SIGTERM');await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));await rm(directory,{recursive:true,force:true});}
