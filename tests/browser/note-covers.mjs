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
  const ctx=page.context(),password='Notes-cover-password-123';
  async function post(path,data,csrf=''){
    const response=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  let csrf=(await post('/setup',{login:'coverowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await post('/2fa/enroll',{password},csrf);
  await post('/2fa/confirm',{code:totp(enrollment.secret)},csrf);
  csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  const note=await post('/notes',{title:'Обложки',body:'Сохранённый текст'},csrf);
  const {readFile}=await import('node:fs/promises'),png=await readFile('tests/fixtures/blue.png');
  const initial=await ctx.request.post(origin+'/api/notes/'+note.id+'/assets/cover',{headers:{Origin:origin,'X-CSRF-Token':csrf,'Content-Type':'image/png'},data:png});
  expect(initial.ok()).toBe(true);const oldCover=(await initial.json()).cover;
  await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  await page.route('https://thumb.wikimedia.org/good.png',r=>r.fulfill({contentType:'image/png',body:png}));
  await page.route('https://thumb.wikimedia.org/broken.png',r=>r.fulfill({status:404,body:'missing'}));
  const photo=(id)=>({id,thumbnail:'https://thumb.wikimedia.org/'+id+'.png',title:id,attribution:{author:'Автор',license:'CC BY',source_url:'https://commons.wikimedia.org/'},selection_token:id});
  await page.route('**/api/note-cover-search?**',async r=>{
    const query=new URL(r.request().url()).searchParams.get('q');
    if(query==='failure')return r.fulfill({status:503,json:{detail:'Фотокаталог недоступен'}});
    const items=query==='broken'?[photo('broken')]:query==='configured'?[photo('good')]:[photo('good'),photo('broken')];
    await r.fulfill({json:{items,providers:query==='configured'?['commons','unsplash']:['commons']}});
  });
  await page.goto('/page/'+note.id);
  await page.getByRole('button',{name:'Изменить обложку',exact:true}).click();
  const dialog=page.getByRole('dialog'),provider=dialog.getByLabel('Каталог'),query=dialog.getByLabel('Поиск фотографий');
  await expect(dialog.locator('.cover-photo')).toHaveCount(1);
  await expect(dialog.locator('.cover-photo')).toBeEnabled();
  await expect(provider.locator('option')).toHaveCount(1);
  await expect(dialog.locator('[data-cover-provider-hint]')).toBeVisible();
  await query.fill('failure');await expect(dialog.locator('.cover-photo')).toHaveCount(0);
  await expect(dialog.locator('[data-cover-status]')).toHaveText('Фотокаталог недоступен');
  await query.fill('broken');await expect(dialog.locator('[data-cover-status]')).toContainText('Не удалось загрузить фотографии');
  await expect(dialog.locator('.cover-photo')).toHaveCount(0);
  await query.fill('configured');await expect(provider.locator('option')).toHaveCount(2);
  await expect(dialog.locator('[data-cover-provider-hint]')).toBeHidden();
  await provider.selectOption('unsplash');await expect(dialog.locator('.cover-photo')).toBeEnabled();
  await page.route('**/api/notes/'+note.id+'/select-cover',r=>r.fulfill({status:422,json:{detail:'Эта фотография недоступна'}}));
  await dialog.locator('.cover-photo').click();await expect(dialog.locator('.cover-photo')).toHaveCount(0);
  expect((await (await ctx.request.get(origin+'/api/notes/'+note.id)).json()).cover).toBe(oldCover);
  await page.unroute('**/api/notes/'+note.id+'/select-cover');
  await page.route('**/api/notes/'+note.id+'/select-cover',async r=>{
    expect(r.request().postDataJSON()).toEqual({token:'good'});
    const response=await ctx.request.post(origin+'/api/notes/'+note.id+'/assets/cover',{headers:{Origin:origin,'X-CSRF-Token':csrf,'Content-Type':'image/png'},data:png});
    await r.fulfill({json:await response.json()});
  });
  await query.fill('success');await expect(dialog.locator('.cover-photo')).toBeEnabled();await dialog.locator('.cover-photo').click();
  await expect(dialog).toHaveCount(0);await expect(page.locator('.page-cover img')).toBeVisible();
  expect((await (await ctx.request.get(origin+'/api/notes/'+note.id)).json()).cover).not.toBe(oldCover);
  expect((await (await ctx.request.get(origin+'/api/notes/'+note.id)).json()).body).toBe('Сохранённый текст');
  expect(errors).toEqual([]);
}

// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8037',directory=await mkdtemp(tmpdir()+'/drg-note-covers-');
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,isMobile:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Note covers passed: missing key hides Unsplash, broken previews disappear, stale results clear, failed selection preserves cover, configured provider and successful selection work.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
