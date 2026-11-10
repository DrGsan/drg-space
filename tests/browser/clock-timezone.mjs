import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {unlockDashboard} from './widget-edit-flow.js';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
const origin='http://127.0.0.1:8031',directory=await mkdtemp(tmpdir()+'/drg-clock-timezone-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8031','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-clock-timezone-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  async function post(context,path,data,csrf=''){
    const response=await context.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  const owner=await browser.newContext({locale:'ru-RU',baseURL:origin}),password='Clock-timezone-password-123';
  let csrf=(await post(owner,'/setup',{login:'clockowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await post(owner,'/2fa/enroll',{password},csrf);
  await post(owner,'/2fa/confirm',{code:totp(enrolled.secret)},csrf);
  csrf=(await post(owner,'/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  await post(owner,'/admin/access/unlock',{password},csrf);
  const invite=(await post(owner,'/invites',{login:'vladivostok'},csrf)).url.split('/').at(-1);
  await post(owner,'/admin/access/lock',{},csrf);
  async function context(timezoneId,storageState){
    const ctx=await browser.newContext({locale:'ru-RU',baseURL:origin,timezoneId,storageState,viewport:{width:1440,height:1000}});
    await ctx.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
    return ctx;
  }
  const reloadWhenSettled=page=>{
    const requests=new Set(),tracked=request=>['/api/presence','/api/calls/config'].includes(new URL(request.url()).pathname);
    page.on('request',request=>{if(tracked(request))requests.add(request);});
    page.on('requestfinished',request=>requests.delete(request));page.on('requestfailed',request=>requests.delete(request));
    return async()=>{
      await expect.poll(()=>page.locator('[data-widget-id=home_clock] [data-widget-body]').evaluate(el=>Number(getComputedStyle(el).opacity))).toBe(1);
      await expect.poll(()=>requests.size).toBe(0);
      await page.evaluate(()=>new Promise(requestAnimationFrame));
      await page.reload();
    };
  };
  const vlad=await context('Asia/Vladivostok');
  csrf=(await post(vlad,'/register',{login:'vladivostok',password,invite})).csrf;
  await post(vlad,'/2fa/skip',{},csrf);
  const page=await vlad.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const reload=reloadWhenSettled(page);
  await page.clock.setFixedTime(new Date('2026-10-16T12:34:56Z'));await page.goto('/');
  const clock=page.locator('[data-widget-id=home_clock]'),dialog=page.getByRole('dialog').last();
  await expect(clock.locator('.clock-region')).toHaveText('ВладивостокUTC+10:00');
  await expect(clock.locator('svg')).toHaveAttribute('aria-label','Владивосток · 22:34:56');
  await expect(clock.locator('.clock-widget')).toHaveAttribute('data-clock-period','night');
  expect((await (await vlad.request.get('/api/me')).json()).settings.dashboard).toBeUndefined();
  await reload();await expect(clock.locator('.clock-region')).toContainText('Владивосток');
  await unlockDashboard(page);await clock.getByRole('button',{name:/Настроить виджет/}).click();
  await expect(dialog.getByLabel('Регион часов')).toHaveValue('local');
  await expect(dialog.locator('.clock-preview .clock-region')).toContainText('Владивосток');
  await dialog.locator('[data-clock-mode=digital]').click();
  await dialog.getByRole('button',{name:'Сохранить виджет',exact:true}).click();
  await expect(clock.locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 22:34:56$'));
  const layout=await (await vlad.request.get('/api/dashboard/layout')).json();
  expect(layout.instances.find(w=>w.id==='home_clock').config.timezone).toBe('local');
  // The same saved automatic clock follows another device, including DST/date.
  const ny=await context('America/New_York',await vlad.storageState()),nyPage=await ny.newPage();
  const reloadNY=reloadWhenSettled(nyPage);
  await nyPage.clock.setFixedTime(new Date('2026-10-17T02:34:56Z'));await nyPage.goto('/');
  const nyClock=nyPage.locator('[data-widget-id=home_clock]');
  await expect(nyClock.locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 22:34:56$'));
  await expect(nyClock.locator('.clock-date')).toHaveText('16.10.2026');
  await expect(nyClock.locator('.clock-region')).toContainText('Нью-ЙоркUTC-04:00');
  // An explicitly chosen Moscow remains Moscow after saving and reloading.
  await unlockDashboard(page);await clock.getByRole('button',{name:/Настроить виджет/}).click();
  await dialog.getByLabel('Регион часов').selectOption('Europe/Moscow');
  await dialog.getByRole('button',{name:'Сохранить виджет',exact:true}).click();await reload();
  await expect(clock.locator('.clock-region')).toContainText('МоскваUTC+03:00');
  await expect(clock.locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 15:34:56$'));
  await reloadNY();await expect(nyClock.locator('.clock-region')).toContainText('МоскваUTC+03:00');
  await expect(nyClock.locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 05:34:56$'));
  expect(errors).toEqual([]);
  console.log('Clock timezone passed: invited signup, Vladivostok first visit/reload/preview/save, regional day/night, shared automatic settings in New York, DST/date and preserved explicit Moscow.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
