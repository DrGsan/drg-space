import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'Family',write:false,loader:{'.css':'empty'}});
const adminBundle=await build({entryPoints:['web/modules/admin/family-groups.js'],bundle:true,format:'iife',globalName:'FamilyAdmin',write:false,loader:{'.css':'empty'}});
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://family-export.test/**',async route=>{
  const name=new URL(route.request().url()).pathname.split('/').at(-1);
  if(name==='download')return route.fulfill({body:Buffer.from('ZIP fixture'),contentType:'application/zip',headers:{'Content-Disposition':'attachment; filename="photos.zip"'}});
  if(/\.(woff2?|ttf)$/.test(name)){
   try{return await route.fulfill({body:await readFile('app/static/assets/'+name),contentType:name.endsWith('woff2')?'font/woff2':name.endsWith('woff')?'font/woff':'font/ttf'});}catch{return route.abort();}
  }
  return route.fulfill({contentType:'text/html; charset=utf-8',body:'<main class="workspace"><div id="gallery"></div><div class="preferences-surface"><details class="preference-row" open><summary>Семейные фото</summary><section class="panel admin-family-photos"><div id="management"></div></section></details></div></main>'});
 });
 await page.goto('https://family-export.test/');
 await page.addStyleTag({content:(await readFile('app/static/assets/app.css','utf8')).replace(/@font-face\s*\{[^}]*\}/g,'')});
 await page.addStyleTag({content:'.workspace{display:block;max-width:1100px;margin:auto;padding:16px}'});
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.addScriptTag({content:adminBundle.outputFiles[0].text});
 await page.evaluate(async()=>{
  window.calls=[];window.jobs=[];window.groups=[{id:'-1001',title:'Первая семейная группа',enabled:true,telegram_id:'-1001',history_complete:true,media_pending:0},{id:'-1002',title:'Вторая семейная группа',enabled:true,telegram_id:'-1002',history_complete:false,media_pending:1}];
  window.ctx={state:{me:{id:'a'.repeat(32),role:'owner'}},moduleEnabled:()=>true,navigate(){},toast(){},modal(html){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.innerHTML='<section class="modal" role="dialog"><button class="modal-close">×</button>'+html+'</section>';overlay.querySelector('button').onclick=()=>overlay.remove();document.body.append(overlay);return overlay;},async api(path,method='GET',body){
   calls.push({path,method,body});
   if(path==='/family-photos/admin')return structuredClone({groups:groups.map(g=>{const ready=jobs.find(j=>j.state==='ready'&&j.groups[0]===g.id);return {...g,...(ready?{export_ready:{id:ready.id,expires:ready.expires,download_url:ready.download_url}}:{})};}),status:{group_management:true,group_copies:true,photo_exports:true},server_time:Math.floor(Date.now()/1000)});
   if(path==='/family-photos/groups')return structuredClone({items:groups,status:{group_copies:true,photo_exports:true}});
   if(path.endsWith('/copy')){groups.push({id:'export:'+'b'.repeat(24),title:'Первая семейная группа · Копия',kind:'export',enabled:true,copy_source:'-1001',copy_created:Math.floor(Date.now()/1000),copy_complete:true,history_complete:true,media_pending:0});return {id:groups.at(-1).id};}
   if(path.endsWith('/sync'))return {state:'idle',history_complete:true,pending:0,batch:{},cleanup:{}};
   if(path.endsWith('/media'))return {items:[],more:false};
   if(path.endsWith('/cancel')){jobs[0].state='cleaning';return {ok:true};}
   if(path==='/family-photos/admin/exports'){
    if(method==='POST'){jobs.push({id:'d'.repeat(32),state:'running',created:Math.floor(Date.now()/1000),done:0,count:20,bytes:0,total_bytes:10*1024**3,incomplete:true,groups:body.groups});return jobs.at(-1);}
    return structuredClone({items:jobs,server_time:Math.floor(Date.now()/1000)});
   }
   throw Error('Unexpected request '+path);
  }};
  await Family.mountFamilyPhotos(document.querySelector('#gallery'),ctx);
  document.querySelector('#gallery').hidden=true;
  window.reloadManagement=async()=>{window.stop?.();const data=await ctx.api('/family-photos/admin');window.stop=FamilyAdmin.mountFamilyGroups(document.querySelector('#management'),ctx,data,reloadManagement);};
  await reloadManagement();
 });
 await expect(page.locator('#gallery [data-fp-copy],#gallery [data-fp-exports]')).toHaveCount(0);
 const original=page.locator('[data-fp-managed-group="-1001"]');
 assert.deepEqual(await original.locator('[data-fp-action]').evaluateAll(items=>items.map(i=>i.dataset.fpAction)),['disable','export','copy','delete']);
 await expect(original.getByRole('button',{name:'Сделать копию',exact:true})).toBeVisible();
 await original.getByRole('button',{name:'Сделать копию',exact:true}).click();
 await page.getByRole('dialog').getByRole('button',{name:'Сделать копию',exact:true}).click();
 const copied=page.locator('[data-fp-managed-group="export:bbbbbbbbbbbbbbbbbbbbbbbb"]');
 await expect(copied).toBeVisible();
 await expect(copied.locator('[data-fp-action=copy]')).toHaveCount(0);
 await expect(copied.locator('[data-fp-action=export]')).toHaveCount(1);
 await page.screenshot({path:'artifacts/family-admin-actions-desktop.png',fullPage:true});
 await original.getByRole('button',{name:'Подготовить архив',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.locator('[name=groups],[data-fp-all]')).toHaveCount(0);
 await expect(dialog).toContainText('Первая семейная группа');
 await dialog.locator('[name=videos]').check();
 await dialog.getByRole('button',{name:'Подготовить архив',exact:true}).click();
 await expect(dialog).toContainText('Запрос принят: 20 файлов');
 assert.equal(await page.evaluate(()=>calls.filter(c=>c.method==='POST'&&c.path.endsWith('/exports')).at(-1).body.groups.length),1);
 await page.evaluate(()=>{jobs[0].state='ready';jobs[0].expires=Math.floor(Date.now()/1000)+604800;jobs[0].ready_at=Math.floor(Date.now()/1000);jobs[0].done=20;jobs[0].bytes=jobs[0].total_bytes;jobs[0].size=jobs[0].total_bytes+1000;jobs[0].download_url='/api/family-photos/admin/exports/'+jobs[0].id+'/download';});
 await expect(dialog.getByRole('link',{name:'Скачать ZIP'})).toBeVisible({timeout:5000});
 for(const width of [1440,768,390,320])for(const scheme of ['light','dark']){
  await page.setViewportSize({width,height:1000});await page.evaluate(s=>document.documentElement.dataset.scheme=s,scheme);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await dialog.locator('.admin-accounts-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:`artifacts/family-exports-${width}-${scheme}.png`,fullPage:true});
 }
 await dialog.locator('.modal-close').click();
 await page.evaluate(()=>reloadManagement());
 const readyLink=original.getByRole('link',{name:'Скачать архив',exact:true});
 await expect(readyLink).toBeVisible();
 // Observe the native link intent without depending on the isolated browser's download network process.
 await page.evaluate(()=>document.addEventListener('click',event=>{const link=event.target.closest('[data-fp-download]');if(link){window.downloadIntent={url:link.href,cancelled:event.defaultPrevented,native:link.hasAttribute('download')};event.preventDefault();}}));
 await readyLink.click();const intent=await page.evaluate(()=>downloadIntent);
 assert(intent.url.endsWith('/api/family-photos/admin/exports/'+'d'.repeat(32)+'/download'));assert(intent.native&&!intent.cancelled);
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await original.getByRole('button',{name:'Подготовить новый архив',exact:true}).click();
 const newDialog=page.getByRole('dialog');await expect(newDialog.locator('form')).toBeVisible();
 await newDialog.getByRole('button',{name:'Удалить архив',exact:true}).click();await expect(newDialog).toContainText('Очищаем');
 assert.deepEqual(errors,[]);
 await page.evaluate(async()=>{Family.familyPhotosController.destroy();ctx.state.me.role='member';await Family.mountFamilyPhotos(document.querySelector('#gallery'),ctx);});
 await expect(page.locator('#gallery [data-fp-copy],#gallery [data-fp-exports]')).toHaveCount(0);
 await page.evaluate(()=>{Family.familyPhotosController.destroy();stop();});
 console.log('Family copies and exports passed: admin table only, no gallery actions, no copy of copy, group/video selection, progress, ready link, cancellation, responsive table 320–1440, light/dark');
}finally{await browser.close();}
