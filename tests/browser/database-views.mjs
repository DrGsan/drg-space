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
const python=process.env.DRG_PYTHON||'.venv/bin/python';
const origin='http://127.0.0.1:8038',directory=await mkdtemp(tmpdir()+'/drg-notion-import-');
const env={...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-notion-import-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''};
const server=spawn(python,['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8038','tests.browser_app:app'],{env,stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));
try{
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok)break;}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  let csrf='';
  async function api(path,method='GET',data){
    const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  const password='Import-browser-password-123';
  csrf=(await api('/setup','POST',{login:'importowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll','POST',{password});
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});
  csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
  const database=await api('/databases','POST',{title:'Заметки'}),nid=database.id;
  let db=await api('/databases/'+nid);
  const properties=[{id:'tags',name:'Тег',type:'multi_select',options:['IT','Архив']},{id:'edited',name:'Last edited time',type:'text'}];
  const views=[{id:'notes',name:'Заметки',type:'list',visible:['tags','edited'],filter:{property:'tags',operator:'not_contains',value:'Архив'}},{id:'archive',name:'Архив',type:'list',visible:['tags','edited'],filter:{property:'tags',operator:'contains',value:'Архив'}}];
  await api('/databases/'+nid,'PUT',{properties,views,version:db.version});
  for(let i=0;i<20;i++)await api('/databases/'+nid+'/records','POST',{title:i===0?'LetterBoxD':(i<10?'Заметка '+i:'Архив '+i),properties:{tags:i<10?(i===0?['IT']:[]):['Архив'],edited:'February 18, 2022 4:02 PM'}});
  await page.goto('/page/'+nid);
  await expect(page.locator('.db-list-row')).toHaveCount(10);
  await expect(page.locator('.db-edited-time').first()).toHaveText('18/02/2022 16:02');
  await expect(page.locator('.db-tag').first()).toHaveText('IT');
  await expect(page.locator('#share-page')).toBeVisible();await page.locator('#share-page').click();
  await expect(page.locator('[name=permission]')).toBeDisabled();
  await page.locator('.modal-close').click();
  await page.getByRole('tab',{name:'Архив',exact:true}).click();await expect(page.locator('.db-list-row')).toHaveCount(10);
  await expect(page.locator('.db-list-row').first()).toContainText('Архив');
  await page.locator('#db-add-bottom').click();
  await expect(page.locator('[data-property=tags] option[value=Архив]')).toHaveJSProperty('selected',true);
  await page.locator('#record-title').fill('Archive new');await page.locator('#record-form [type=submit]').click();
  await expect(page.locator('.db-list-row')).toHaveCount(11);
  await page.getByRole('tab',{name:'Заметки',exact:true}).click();
  await page.getByRole('button',{name:'Поиск в базе',exact:true}).click();await page.locator('#db-search').fill('Letter');
  await expect(page.locator('.db-list-row')).toHaveCount(1);await page.locator('#db-search').fill('');await expect(page.locator('.db-list-row')).toHaveCount(10);
  await page.getByRole('button',{name:'Поиск в базе',exact:true}).click();
  await mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/database-list-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/database-list-mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({width:1440,height:1000});
  const guitars=await api('/databases','POST',{title:'Гитары'}),gid=guitars.id,g=await api('/databases/'+gid);
  await api('/databases/'+gid,'PUT',{properties:[{id:'brand',name:'Бренд',type:'select',options:['Yamaha','Ibanez']},{id:'price',name:'Цена',type:'number'}],views:[{id:'gallery',name:'Галерея',type:'gallery'}],version:g.version});
  const png=execFileSync(python,['-c',"import io,sys;from PIL import Image;s=io.BytesIO();Image.new('RGB',(700,350),'#55766f').save(s,format='PNG');sys.stdout.buffer.write(s.getvalue())"]);
  const uploaded=await context.request.post(origin+'/api/notes/'+gid+'/assets/cover',{headers:{Origin:origin,'X-CSRF-Token':csrf},data:png});expect(uploaded.ok()).toBe(true);
  let first;
  for(let i=0;i<9;i++){
    const r=await api('/databases/'+gid+'/records','POST',{title:'Guitar '+i,properties:{brand:i%2?'Ibanez':'Yamaha',price:65000}});first||=r.id;
    const image=await context.request.post(origin+'/api/notes/'+r.id+'/files?name=guitar.png',{headers:{Origin:origin,'X-CSRF-Token':csrf,'Content-Type':'image/png'},data:png});expect(image.ok()).toBe(true);
    const url=origin+(await image.json()).url,n=await api('/notes/'+r.id);
    await api('/notes/'+r.id,'PUT',{title:n.title,body:'',version:n.version,blocks:[{id:'img'+i,type:'image',props:{url},children:[]}]});
  }
  await page.goto('/page/'+gid);await expect(page.locator('.db-card-preview img')).toHaveCount(9);
  await expect.poll(()=>page.locator('.db-card-preview img').first().evaluate(i=>i.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('.page-cover img')).toBeVisible();
  await expect(page.locator('.db-card').first()).toContainText('65');await page.screenshot({path:'artifacts/database-gallery-desktop.png',fullPage:true});
  await page.locator('.db-card .db-open').first().click();await expect(page.locator('#page-title')).toHaveValue('Guitar 0');await expect(page.locator('#share-page')).toBeVisible();
  const link=await api('/notes/'+gid+'/shares','POST',{mode:'public'});
  const guest=await browser.newContext({locale:'ru-RU',baseURL:origin}),publicPage=await guest.newPage();
  await publicPage.goto(link.url);await expect(publicPage.locator('.db-card-preview img')).toHaveCount(9);
  await expect.poll(()=>publicPage.locator('.db-card-preview img').first().evaluate(i=>i.naturalWidth)).toBeGreaterThan(0);
  await publicPage.locator('.db-card .db-open').first().click();await expect(publicPage.locator('h1')).toContainText('Guitar 0');
  await expect.poll(()=>publicPage.locator('.markdown [data-content-type=image] img').first().evaluate(i=>i.naturalWidth)).toBeGreaterThan(0);
  await publicPage.getByRole('link',{name:'← К базе'}).click();await expect(publicPage.locator('.db-card')).toHaveCount(9);
  await api('/shares/'+link.token,'DELETE');await publicPage.reload();await expect(publicPage.locator('#shared-content')).toContainText('отозвана');
  expect(errors).toEqual([]);console.log('Database list/gallery/share passed: '+(process.env.DRG_BROWSER_ENGINE||'chromium'));
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));await rm(directory,{recursive:true,force:true});
}
