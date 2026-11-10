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
 const errors=[];page.on('pageerror',e=>errors.push(e.message));const password='Subpages-test-password-123';
 async function api(path,method='GET',data,csrf=''){const r=await page.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();}
 let csrf=(await api('/setup','POST',{login:'subpageowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password},csrf);await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
 await page.setViewportSize({width:1280,height:900});
 for(const shared of [false,true]){
  const space=shared?await api('/social/families','POST',{name:'Subpage family'},csrf):null;
  let parent=await api('/notes','POST',{title:shared?'Shared parent':'Private parent',space_id:space?.id||null,blocks:[{id:'kept_text',type:'paragraph',content:'Сохранённый текст родительской страницы.'}]},csrf);
  const first=await api('/notes','POST',{title:'First child',parent_id:parent.id},csrf),second=await api('/notes','POST',{title:'Missing shortcut',parent_id:parent.id},csrf);
  // Reproduce the existing defect: two children and only one rich shortcut.
  if(!shared)parent=await api('/notes/'+parent.id,'PUT',{title:parent.title,body:'Сохранённый текст родительской страницы.',version:parent.version,blocks:[...parent.blocks,{id:'first_link',type:'pageLink',props:{pageId:first.id,title:first.title}}]},csrf);
  await page.goto('/page/'+parent.id);
  await expect(page.locator('.inline-page-link')).toHaveCount(2);
  await expect(page.locator('#block-editor')).toContainText('Сохранённый текст родительской страницы.');
  await expect(page.locator('.inline-page-link')).not.toContainText(['Страница недоступна','Страница недоступна']);
  await page.locator('[data-tree-row="'+parent.id+'"]').hover();await page.locator('[data-add-child="'+parent.id+'"]').click();
  await expect(page.locator('#page-title')).toHaveValue('');
  const all=await api('/notes'),child=all.find(n=>n.parent_id===parent.id&&![first.id,second.id].includes(n.id));expect(child).toBeTruthy();
  const grandchild=await api('/notes','POST',{title:'Nested child',parent_id:child.id},csrf);
  await page.locator('[data-crumb="'+parent.id+'"]').click();await expect(page.locator('.inline-page-link')).toHaveCount(3);
  await expect(page.locator('[data-open-page="'+child.id+'"]')).toBeVisible();
  const block=page.locator('.bn-block-outer[data-id="subpage_'+child.id+'"]');await block.locator('.inline-page-link').hover();
  await page.locator('.bn-side-menu [draggable=true]').click();await page.getByRole('menuitem',{name:'Удалить',exact:true}).click();
  await expect(page.locator('.inline-page-link')).toHaveCount(2);
  await expect.poll(async()=>(await page.request.get(origin+'/api/notes/'+child.id)).status()).toBe(404);
  await expect.poll(async()=>(await page.request.get(origin+'/api/notes/'+grandchild.id)).status()).toBe(404);
  await expect(page.locator('[data-open-page="'+child.id+'"]')).toHaveCount(0);
  await page.keyboard.press('ControlOrMeta+z');await expect(page.locator('.inline-page-link')).toHaveCount(3);
  await expect.poll(async()=>(await page.request.get(origin+'/api/notes/'+child.id)).status()).toBe(200);
  await expect(page.locator('[data-open-page="'+child.id+'"]')).toBeVisible();
  await page.reload();await expect(page.locator('.inline-page-link')).toHaveCount(3);
  // The inline slash command must replace its block, not append a duplicate.
  await page.locator('[data-id=kept_text] .bn-inline-content').evaluate(el=>{el.focus();const range=document.createRange();range.selectNodeContents(el);range.collapse(false);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);});await page.keyboard.press('Enter');await page.keyboard.type('/подстраница');
  await page.getByRole('option',{name:/Подстраница/}).click();
  await expect(page.locator('#page-title')).toHaveValue('');
  await page.locator('[data-crumb="'+parent.id+'"]').click();await expect(page.locator('.inline-page-link')).toHaveCount(4);
  await expect(page.locator('#block-editor')).toContainText('Сохранённый текст родительской страницы.');
  await page.locator('.inline-page-link').filter({hasText:'First child'}).click();await expect(page.locator('#page-title')).toHaveValue('First child');await expect(page.locator('#block-editor .bn-editor')).toBeVisible();
  page.once('dialog',dialog=>dialog.accept());await page.locator('#page-actions').click();await page.locator('[data-page-action=trash]').click();
  await page.locator('[data-open-page="'+parent.id+'"]').click();await expect(page.locator('.inline-page-link')).toHaveCount(3);
  await expect(page.locator('#block-editor')).not.toContainText('Страница недоступна');await expect(page.locator('#block-editor')).toContainText('Сохранённый текст родительской страницы.');
  await page.screenshot({path:'artifacts/subpages-'+(shared?'shared':'private')+'.png'});
 }
 expect(errors).toEqual([]);
}
const origin='http://127.0.0.1:8040',directory=await mkdtemp(tmpdir()+'/drg-note-subpages-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8040','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Subpage shortcuts passed in '+browser.browserType().name());
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
