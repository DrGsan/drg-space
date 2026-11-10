// Actual Files module, controlled clock and server responses; no working account.
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit,expect} from '@playwright/test';
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const bundle=await build({stdin:{contents:"import {mountFiles} from './web/modules/files/index.js';window.mountFiles=mountFiles;",resolveDir:process.cwd()},bundle:true,format:'iife',write:false,loader:{'.css':'empty'},plugins:[{name:'no-unrelated-inbox',setup(api){api.onLoad({filter:/web\/modules\/notifications\/index.js$/},()=>({contents:'export const inboxController={attach(){}};',loader:'js'}));}}]});
const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8')),css=await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8');
const browser=await ({chromium,webkit}[engine]).launch();
try{
 for(const view of ['active','recent','shared']){
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1100,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://files-refresh.test/**',r=>r.fulfill({contentType:'text/html',body:'<main id=app><div class=workspace><div id=files></div></div></main>'}));
  await page.goto('http://files-refresh.test/');await page.clock.install();await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async view=>{
   const skew=view==='recent'?3600:0,start=Date.now()/1000+skew,permanent={id:'keep',name:'Сохранить.txt',kind:'file',mime:'text/plain',size:10,created:start,updated:start,version:1,shared:true,shared_until:start+86400};
   window.fixture={skew,calls:0,deleted:false,deadline:start+60,hidden:false,blocked:false,fail:false,requests:[]};
   Object.defineProperty(document,'hidden',{configurable:true,get:()=>fixture.hidden});
   await mountFiles(document.querySelector('#files'),{state:{fileView:view,me:{id:'fixture'}},navigate:()=>{},toast:()=>{},async api(path){
    fixture.requests.push(path);if(!path.startsWith('/files?'))throw Error('Unexpected request '+path);fixture.calls++;
    if(fixture.fail)throw Error('Temporary error');
    return {items:[{...permanent},...(!fixture.deleted?[{id:'temporary',name:'Временная папка',kind:'folder',size:0,version:1,created:start,updated:start,delete_at:fixture.deadline,retention_version:1,shared:true,shared_until:fixture.deadline}]:[])],breadcrumbs:[],quota:{used_bytes:10,limit_bytes:null},max_file_bytes:50000000,trash_count:fixture.deleted?1:0,secret_enabled:false,server_time:Date.now()/1000+fixture.skew,refresh_at:fixture.deleted?null:fixture.deadline};
   }});
   window.workspace=document.querySelector('.drive-panel');window.keptRow=document.querySelector('[data-file-row=keep]');window.flicker=false;
   new MutationObserver(records=>{if(records.some(r=>[...r.addedNodes].some(n=>n.nodeType===1&&(n.matches('.drive-loading')||n.querySelector('.drive-loading')))))window.flicker=true;}).observe(document.body,{childList:true,subtree:true});
  },view);
  await page.locator('[data-select-file=keep]').check();
  await page.clock.runFor(59000);expect(await page.evaluate(()=>fixture.calls)).toBe(1);
  // Maintenance has not yet run: one check, then backoff instead of a tight loop.
  await page.clock.runFor(3000);await expect.poll(()=>page.evaluate(()=>fixture.calls)).toBe(2);
  await page.clock.runFor(25000);expect(await page.evaluate(()=>fixture.calls)).toBe(2);
  await page.evaluate(()=>fixture.deleted=true);await page.clock.runFor(6000);
  await expect(page.locator('[data-file-row=temporary]')).toHaveCount(0);expect(await page.evaluate(()=>fixture.calls)).toBe(3);
  await expect(page.locator('.file-trash-count')).toHaveText('1');await expect(page.locator('[data-select-file=keep]')).toBeChecked();
  expect(await page.evaluate(()=>document.querySelector('.drive-panel')===workspace&&keptRow.isConnected&&!flicker)).toBe(true);
  await page.clock.runFor(300000);expect(await page.evaluate(()=>fixture.calls)).toBe(3); // no remaining deadline, no idle polling
  // Returning after a hidden interval refreshes once, including new external changes.
  await page.evaluate(()=>{fixture.hidden=true;document.dispatchEvent(new Event('visibilitychange'));});await page.clock.runFor(120000);
  expect(await page.evaluate(()=>fixture.calls)).toBe(3);
  await page.evaluate(()=>{fixture.hidden=false;document.dispatchEvent(new Event('visibilitychange'));});await expect.poll(()=>page.evaluate(()=>fixture.calls)).toBe(4);
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));expect(await page.evaluate(()=>fixture.calls)).toBe(4);
  // A deadline which passes in the background is checked once upon return.
  await page.clock.runFor(16000);
  await page.evaluate(()=>{fixture.deleted=false;fixture.deadline=Date.now()/1000+fixture.skew+20;window.dispatchEvent(new Event('focus'));});
  await expect(page.locator('[data-file-row=temporary]')).toHaveCount(1);expect(await page.evaluate(()=>fixture.calls)).toBe(5);
  await page.evaluate(()=>{fixture.hidden=true;document.dispatchEvent(new Event('visibilitychange'));});await page.clock.runFor(65000);
  expect(await page.evaluate(()=>fixture.calls)).toBe(5);
  await page.evaluate(()=>{fixture.deleted=true;fixture.hidden=false;document.dispatchEvent(new Event('visibilitychange'));});
  await expect(page.locator('[data-file-row=temporary]')).toHaveCount(0);expect(await page.evaluate(()=>fixture.calls)).toBe(6);
  // An in-progress name editor is not destroyed by a deadline refresh.
  await page.clock.runFor(16000);await page.evaluate(()=>{fixture.deleted=false;fixture.deadline=Date.now()/1000+fixture.skew+20;window.dispatchEvent(new Event('focus'));});
  await expect(page.locator('[data-file-row=temporary]')).toHaveCount(1);expect(await page.evaluate(()=>fixture.calls)).toBe(7);
  await page.locator('[data-file-row=keep] [data-file-menu]').click();await page.locator('[data-file-rename]').click();await page.locator('.drive-name-input').fill('Несохранённое имя');
  await page.clock.runFor(65000);expect(await page.evaluate(()=>fixture.calls)).toBe(7);await expect(page.locator('.drive-name-input')).toHaveValue('Несохранённое имя');
  await page.evaluate(()=>fixture.deleted=true);await page.locator('.drive-name-input').press('Escape');await page.clock.runFor(16000);
  await expect(page.locator('[data-file-row=temporary]')).toHaveCount(0);expect(await page.evaluate(()=>fixture.calls)).toBe(8);
  // Leaving Files releases all timers/listeners, even when a deadline is pending.
  await page.evaluate(()=>document.querySelector('#app').replaceChildren());await page.clock.runFor(120000);
  await page.evaluate(()=>{document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('focus'));});
  expect(await page.evaluate(()=>fixture.calls)).toBe(8);expect(errors).toEqual([]);await page.close();
 }
 console.log('Files deadline reconciliation '+engine+' passed: unchanged DOM, selection, backoff, hidden/idle/disposal request counts');
}finally{await browser.close();}
