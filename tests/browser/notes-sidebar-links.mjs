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
  await page.setViewportSize({width:1280,height:900});
  const context=page.context(),password='Paste-menu-password-123';
  async function api(path,method='GET',data,token=''){
    const r=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':token},data});
    expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  }
  let csrf=(await api('/setup','POST',{login:'pasteowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll','POST',{password},csrf);
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);
  csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
  await api('/admin/access/unlock','POST',{password},csrf);
  const invited=await api('/invites','POST',{login:'friend'},csrf);
  const guest=await browser.newContext({locale:'ru-RU',baseURL:origin});
  async function guestApi(path,method='GET',data,token=''){
    const r=await guest.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':token},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  }
  let guestCsrf=(await guestApi('/register','POST',{login:'friend',password,invite:invited.url.split('/').at(-1)})).csrf;
  const ge=await guestApi('/2fa/enroll','POST',{password},guestCsrf);
  await guestApi('/2fa/confirm','POST',{code:totp(ge.secret)},guestCsrf);
  guestCsrf=(await guestApi('/2fa/activate','POST',{saved_recovery_codes:true},guestCsrf)).csrf;
  const invite=await api('/social/friends','POST',{login:'friend'},csrf);
  await guestApi('/social/requests/'+invite.id+'/accept','POST',undefined,guestCsrf);
  const family=await api('/social/families','POST',{name:'Семья Сашков'},csrf);
  const spaces=(await api('/social')).spaces,sid=spaces.find(s=>s.kind==='friends').id;
  const privatePage=await api('/notes','POST',{title:'Мои заметки',body:'https://example.com/a24'},csrf);
  const shared=await api('/notes','POST',{title:'Общие заметки',space_id:sid},csrf);
  const friendPage=await guestApi('/notes','POST',{title:'Планы друга',space_id:sid},guestCsrf);
  const familyPage=await api('/notes','POST',{title:'Семейные планы',space_id:family.id},csrf);
  let previewRequests=0;
  const image='data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAwT/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCEAD//2Q==';
  await page.route('**/api/note-link-preview',async route=>{previewRequests++;await route.fulfill({contentType:'application/json',body:JSON.stringify({title:'A24',description:'A list of films compiled on Letterboxd',site:'Letterboxd',image,available:true})});});
  await page.goto('/page/'+privatePage.id);
  await expect(page.locator('.note-link-card')).toContainText('A24');
  await expect(page.locator('.note-link-card img')).toBeVisible();
  await expect.poll(()=>page.locator('.note-link-card img').evaluate(img=>img.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('#notes-space option')).toHaveCount(2);
  await expect(page.locator('[data-sidebar-section=shared]')).toContainText('Общие заметки');
  await expect(page.locator('[data-sidebar-section=shared]')).toContainText('Планы друга');
  await expect(page.locator('[data-sidebar-section=private]')).toContainText('Мои заметки');
  await expect(page.locator('#page-tree')).not.toContainText('Семейные планы');
  await page.screenshot({path:'artifacts/notes-sidebar-links.png',fullPage:true});
  await page.locator('[data-open-page="'+shared.id+'"]').click();
  await expect(page.locator('#notes-space')).toHaveValue('private');
  await expect(page.locator('#page-participants .person-avatar')).toHaveCount(2);
  await page.screenshot({path:'artifacts/notes-shared-participants.png',fullPage:true});
  await page.locator('#page-participants').click();
  await expect(page.locator('.page-owner-label')).toHaveText('Владелец');
  await page.locator('.modal-close').click();
  await page.locator('#notes-space').selectOption(family.id);
  await expect(page.locator('#page-tree')).toContainText('Семейные планы');
  await expect(page.locator('#page-tree')).not.toContainText('Мои заметки');
  await page.locator('#add-root-page').click();
  await page.locator('#page-title').fill('В семье');
  await expect.poll(async()=>(await api('/notes')).some(n=>n.title==='В семье'&&n.space_id===family.id)).toBe(true);
  await page.locator('#notes-space').selectOption('private');
  await page.locator('#customize-sidebar').click();
  await page.locator('[data-up=private]').click();
  await page.locator('[data-visible=recent]').check();
  await page.getByLabel('Добавить ярлык страницы').selectOption(privatePage.id);
  await page.screenshot({path:'artifacts/notes-sidebar-customize.png',fullPage:true});
  await page.locator('[data-sidebar-done]').click();
  expect(await page.locator('[data-sidebar-section]').evaluateAll(nodes=>nodes.map(n=>n.dataset.sidebarSection))).toEqual(['private','shared','recent']);
  await page.reload();
  await expect(page.locator('[data-sidebar-section=recent]')).toBeVisible();
  await page.locator('[data-create-scope=private]').click();
  await page.locator('#page-title').fill('Личная после общей');
  await expect.poll(async()=>(await api('/notes')).some(n=>n.title==='Личная после общей'&&n.space_id===null)).toBe(true);
  await page.locator('#search-pages').fill('заметки');
  await expect(page.locator('.note-search-result')).toHaveCount(2);
  await page.locator('#search-pages').fill('');
  await page.locator('[data-open-page="'+privatePage.id+'"]').first().click();
  const stored=await api('/notes/'+privatePage.id);
  expect(JSON.stringify(stored.blocks)).not.toContain('A24');
  expect(stored.body).toContain('https://example.com/a24');
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});
    await expect(page.locator('.note-link-card')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
    await page.locator('#toggle-pages-sidebar').click();
    await expect(page.locator('#customize-sidebar')).toBeVisible();
    await page.locator('#customize-sidebar').click();
    await page.locator('[data-sidebar-done]').click();
    await page.locator('#toggle-pages-sidebar').click();
    await page.screenshot({path:'artifacts/notes-sidebar-links-'+width+'.png'});
  }
  expect(previewRequests).toBeGreaterThan(0);expect(errors).toEqual([]);
  await guest.close();
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8032',directory=await mkdtemp(tmpdir()+'/drg-notes-sidebar-links-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8032','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Notes sidebar and link previews passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
