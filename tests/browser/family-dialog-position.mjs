import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundles=await Promise.all([
 build({entryPoints:['web/modules/admin/family-groups.js'],bundle:true,format:'iife',globalName:'FamilyGroups',write:false,loader:{'.css':'empty'}}),
 build({entryPoints:['web/core/modal.js'],bundle:true,format:'iife',globalName:'Dialogs',write:false,loader:{'.css':'empty'}})
]);
const css=(await readFile('app/static/assets/app.css','utf8')).replace(/@font-face\s*\{[^}]*\}/g,'');
const browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
 for(const scrolling of ['panel','document']){
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1440,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('https://family-dialog.test/',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8">'}));
  await page.goto('https://family-dialog.test/');
  await page.setContent('<meta charset="utf-8"><main id="screen"><div style="height:700px">Начало управления</div><details class="preference-row" open><summary>Семейные фото</summary><section class="panel admin-family-photos"><div id="fixture"></div></section></details><div style="height:900px">Конец управления</div></main>');
  await page.addStyleTag({content:css});
  await page.addStyleTag({content:`body{margin:0}#screen{max-width:1100px;margin:auto;padding:16px;${scrolling==='panel'?'height:100dvh;overflow:auto;':''}}`});
  for(const bundle of bundles)await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.clock.install();
  await page.evaluate(()=>{
   window.refreshes=0;window.exportRefreshes=0;window.posts=0;
   window.data={status:{group_management:true,group_copies:true,photo_exports:true},server_time:Math.floor(Date.now()/1000),groups:Array.from({length:7},(_,i)=>({id:'-100'+i,title:'Семейная группа '+i,enabled:true,telegram_id:'-100'+i,photos:100,videos:20,media_bytes:1234567,history_complete:true,state:'online'}))};
   data.groups.push({id:'export:'+'a'.repeat(24),title:'Сохранённая копия',enabled:false,copy_created:1700000000,copy_complete:true,history_complete:true});
   window.ctx={state:{me:{role:'owner'}},modal:Dialogs.modal,toast(){},async api(path,method='GET'){
    if(method!=='GET'){posts++;throw Error('Closing a dialog must not mutate data');}
    if(path==='/family-photos/admin'){refreshes++;if(window.holdAdmin)return new Promise(resolve=>{window.finishAdmin=()=>{holdAdmin=false;resolve(structuredClone(data));};});return structuredClone(data);}
    if(path==='/family-photos/admin/exports'){exportRefreshes++;return {items:[],server_time:Math.floor(Date.now()/1000)};}
    throw Error('Unexpected request '+path);
   }};
   window.stop=FamilyGroups.mountFamilyGroups(document.querySelector('#fixture'),ctx,data,()=>{});
  });
  const scroll=()=>page.evaluate(()=>({document:window.scrollY,panel:document.querySelector('#screen').scrollTop}));
  for(const width of [1440,390,320]){
   await page.setViewportSize({width,height:900});
   for(const action of ['copy','export'])for(const closing of ['button','escape','backdrop']){
    const trigger=page.locator('[data-fp-managed-group="-1006"] [data-fp-action='+action+']');
    await trigger.scrollIntoViewIfNeeded();await trigger.evaluate(button=>{window.trigger=button;button.focus({preventScroll:true});});
    const before=await scroll(),initial=await page.evaluate(()=>refreshes);
    await trigger.click();
    const overlay=page.locator('.modal-overlay'),box=page.getByRole('dialog');await expect(box).toBeVisible();
    const rect=await box.boundingBox();assert(Math.abs(rect.x+rect.width/2-width/2)<=1,'dialog is horizontally centered');
    assert(Math.abs(rect.y+rect.height/2-900/2)<=1,'dialog is vertically centered');
    assert.deepEqual(await scroll(),before,'opening does not scroll the page');
    await page.clock.runFor(22000);
    assert.equal(await page.evaluate(()=>refreshes),initial,'management polling pauses while a dialog is open');
    assert(await page.evaluate(()=>window.trigger.isConnected),'trigger remains connected across polling deadlines');
    if(closing==='button')await overlay.locator('.modal-close').click();
    else if(closing==='escape')await page.keyboard.press('Escape');
    else await page.mouse.click(2,2);
    await expect(box).toHaveCount(0);
    assert.deepEqual(await scroll(),before,'closing preserves page position');
    assert(await page.evaluate(()=>document.activeElement===window.trigger),'focus returns to the original button');
    const exportsBefore=await page.evaluate(()=>exportRefreshes);
    await page.clock.runFor(10000);
    assert((await page.evaluate(()=>refreshes))>initial,'management polling resumes after close');
    assert.equal(await page.evaluate(()=>exportRefreshes),exportsBefore,'closed export dialog stops polling');
    assert.deepEqual(await scroll(),before,'the next management poll preserves page position');
   }
  }
  // A request begun before opening may finish while the dialog is already visible.
  await page.evaluate(()=>{window.holdAdmin=true;});await page.clock.runFor(10000);
  assert(await page.evaluate(()=>typeof window.finishAdmin==='function'));
  const source=page.locator('[data-fp-managed-group="-1006"] [data-fp-action=copy]');
  await source.scrollIntoViewIfNeeded();await source.evaluate(button=>{window.trigger=button;button.focus({preventScroll:true});});
  const heldScroll=await scroll();await source.click();
  await page.evaluate(async()=>{finishAdmin();await Promise.resolve();});
  assert(await page.evaluate(()=>window.trigger.isConnected),'in-flight refresh does not replace the dialog trigger');
  await page.keyboard.press('Escape');assert.deepEqual(await scroll(),heldScroll);
  await page.evaluate(()=>{data.groups[5].enabled=false;});await page.clock.runFor(10000);
  const disabled=page.locator('[data-fp-managed-group="-1005"]');
  await expect(disabled).toContainText('Синхронизация остановлена');
  await expect(disabled.locator('[data-fp-action=copy]')).toHaveCount(1);
  await disabled.locator('[data-fp-action=copy]').click();await expect(page.getByRole('dialog')).toContainText('Семейная группа 5');await page.keyboard.press('Escape');
  const copied=page.locator('[data-fp-managed-group="export:aaaaaaaaaaaaaaaaaaaaaaaa"]');
  await expect(copied).toContainText('Копия скрыта');await expect(copied).not.toContainText('Синхронизация');
  assert.equal(await page.evaluate(()=>posts),0);assert.deepEqual(errors,[]);
  await page.evaluate(()=>stop());await page.close();
 }
 console.log('Family dialogs passed: actual shared modal, centered 320–1440px, stable document/panel scroll, close/Escape/backdrop, polling lifecycle, hidden-copy caption');
}finally{await browser.close();}
