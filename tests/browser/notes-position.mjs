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
  await page.setViewportSize({width:390,height:844});
  await page.context().addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:true}));
  await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  const password='Notes-position-password-123',context=page.context();
  async function post(path,data,csrf=''){
    const response=await context.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  let csrf=(await post('/setup',{login:'notesowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await post('/2fa/enroll',{password},csrf);
  await post('/2fa/confirm',{code:totp(enrollment.secret)},csrf);
  csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  const first=await post('/notes',{title:'Первая заметка',body:'Сохранённый текст первой заметки'},csrf);
  const second=await post('/notes',{title:'Вторая заметка',body:'Сохранённый текст второй заметки'},csrf);
  await page.goto('/');
  const tabs=page.locator('.mobile-app-tabs'),notesTab=tabs.locator('[data-mobile-view=notes]');
  const list=async()=>{await expect(page.locator('#pages-sidebar')).toBeVisible();await expect(page.locator('#notion-page')).toBeHidden();};
  const editor=async title=>{await expect(page.locator('#page-title')).toHaveValue(title);await expect(page.locator('#pages-sidebar')).toBeHidden();await expect(page.locator('#notion-page')).toBeVisible();};
  await notesTab.click();await list();
  // Leave the page list and return repeatedly: selected pages must stay closed.
  for(const note of [second,first,second]){
    await page.locator('[data-open-page="'+note.id+'"]').first().click();await editor(note.title);
    await tabs.locator('[data-mobile-view=tasks]').click();await notesTab.click();await editor(note.title);
    await notesTab.click();await editor(note.title);
    await page.locator('#notes-main-menu').click();await list();
    await tabs.locator('[data-mobile-view=tasks]').click();await notesTab.click();await list();
    expect(await page.locator('#page-title').count()).toBe(0);
    await notesTab.click();await list();expect(await page.locator('#page-title').count()).toBe(0);
  }
  // Going back to the list must finish saving before remembering that pane.
  await page.locator('[data-open-page="'+second.id+'"]').first().click();await editor(second.title);
  let release;
  const pending=async route=>{if(route.request().method()==='PUT'&&route.request().postDataJSON()?.title==='Правка перед переходом')await new Promise(resolve=>release=resolve);await route.continue();};
  await page.route('**/api/notes/'+second.id,pending);
  await page.locator('#page-title').fill('Правка перед переходом');
  await page.locator('#notes-main-menu').click();await expect.poll(()=>typeof release).toBe('function');
  await expect(page.locator('#notion-page')).toBeVisible();release();await list();
  await page.unroute('**/api/notes/'+second.id,pending);
  await tabs.locator('[data-mobile-view=tasks]').click();await notesTab.click();await list();
  expect(new URL(page.url()).pathname).toBe('/');
  await page.reload();await list();await expect(page.locator('#page-title')).toHaveCount(0);
  expect((await (await context.request.get(origin+'/api/notes/'+second.id)).json()).title).toBe('Правка перед переходом');
  // An explicit page link overrides list mode, including a desktop widget link.
  await page.goto('/page/'+first.id);await editor(first.title);await page.reload();await editor(first.title);
  await page.locator('#notes-main-menu').click();await list();
  await tabs.locator('[data-mobile-view=tasks]').click();await notesTab.click();await list();
  await page.setViewportSize({width:1280,height:900});
  await page.locator('#notes-main-menu').click();await page.locator('[data-note="'+second.id+'"]').first().click();
  await expect(page.locator('#page-title')).toHaveValue('Правка перед переходом');
  expect(errors).toEqual([]);
}

// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8029',directory=await mkdtemp(tmpdir()+'/drg-notes-position-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8029','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,isMobile:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Notes position passed: mobile list/editor survive tab switches and repeated taps, pending edits save, explicit links/reloads still open the requested page.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
