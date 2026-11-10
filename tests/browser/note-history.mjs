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
  await page.setViewportSize({width:1280,height:900});
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  const context=page.context(),password='History-browser-password-123';let csrf='';
  async function api(path,method='GET',data){
    const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  csrf=(await api('/setup','POST',{login:'historyowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll','POST',{password});
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});
  csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
  const para=(id,text)=>({id,type:'paragraph',content:[{type:'text',text,styles:{}}],children:[]});
  let note=await api('/notes','POST',{title:'История проекта',body:'Original',blocks:[para('same','Исходный текст'),{id:'code',type:'codeBlock',props:{language:'bash'},content:'echo original',children:[]},para('removed','Удалённый абзац')]});
  note=await api('/notes/'+note.id,'PUT',{title:'Проект сегодня',body:'Current',blocks:[para('same','Изменённый текст'),{id:'code',type:'codeBlock',props:{language:'bash'},content:'echo original',children:[]},para('added','Новый абзац')],version:note.version});
  await page.goto('/page/'+note.id);await page.getByRole('button',{name:'Действия страницы',exact:true}).click();
  await page.locator('[data-page-action=history]').click();
  await expect(page).toHaveURL(origin+'/page/'+note.id+'/history');
  const screen=page.locator('.note-history');await expect(screen).toBeVisible();
  await expect(screen.locator('[data-current]')).toBeVisible();
  await screen.locator('[data-revision]').first().click();
  await expect(screen.locator('.history-content')).toContainText('Исходный текст');
  await expect(screen.locator('.history-content [contenteditable=true]')).toHaveCount(0);
  await expect(screen.locator('[data-content-type=codeBlock]')).toContainText('echo original');
  await screen.getByLabel('Сравнить с текущей').check();
  await expect(screen.locator('[data-history-change=removed]')).toContainText('Удалённый абзац');
  await expect(screen.locator('[data-history-change=added]')).toContainText('Новый абзац');
  await expect(screen.locator('[data-history-change=before]')).toContainText('Исходный текст');
  await expect(screen.locator('[data-history-change=after]')).toContainText('Изменённый текст');
  await mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/note-history-comparison.png'});
  await screen.getByLabel('Группировать версии').selectOption('60');
  await screen.locator('.history-restore').click();await expect(screen.locator('.history-confirm')).toBeVisible();
  note=await api('/notes/'+note.id,'PUT',{title:'Параллельная правка',body:'Concurrent',blocks:[para('concurrent','Свежее содержимое')],version:note.version});
  await screen.locator('[data-confirm]').click();await expect(screen.locator('.history-status')).toContainText('Страница изменилась');
  expect((await api('/notes/'+note.id)).title).toBe('Параллельная правка');
  await screen.getByRole('button',{name:'Обновить историю'}).click();
  await expect(screen.locator('[data-revision]')).toHaveCount(2);
  await screen.getByLabel('Группировать версии').selectOption('0');
  await screen.locator('[data-revision]').last().click();await screen.locator('.history-restore').click();await screen.locator('[data-confirm]').click();
  await expect(screen).toHaveCount(0);await expect(page.locator('#page-title')).toHaveValue('История проекта');
  await expect.poll(async()=>(await api('/notes/'+note.id)).title).toBe('История проекта');
  await page.goto('/page/'+note.id+'/history');await expect(page.locator('.note-history [data-current]')).toBeVisible();
  await page.reload();await expect(page.locator('.note-history [data-current]')).toBeVisible();
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Версии',exact:true}).click();
  await page.locator('.note-history [data-revision]').first().click();await expect(page.locator('.history-content')).toContainText('Свежее содержимое');
  await page.screenshot({path:'artifacts/note-history-mobile.png'});
  await page.evaluate(()=>{document.documentElement.dataset.scheme='dark';window.dispatchEvent(new Event('drg-appearance'));});
  await expect(page.locator('.note-history')).toHaveCSS('background-color','rgb(29, 39, 54)');
  await page.screenshot({path:'artifacts/note-history-dark.png'});
  await page.getByRole('button',{name:'Закрыть историю'}).click();await expect(page).toHaveURL(origin+'/page/'+note.id);
  expect(errors).toEqual([]);
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8037',directory=await mkdtemp(tmpdir()+'/drg-note-history-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8037','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Note history passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
