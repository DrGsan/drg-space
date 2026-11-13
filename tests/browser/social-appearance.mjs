import {readAppStyles} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/social/index.js'],bundle:true,format:'iife',globalName:'Social',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles(),png=await readFile('tests/fixtures/blue.png');
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:1280,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://social.test/**',r=>r.request().url().includes('/portrait')?r.fulfill({contentType:'image/png',body:png}):r.fulfill({contentType:'text/html',body:'<div class="workspace" data-current-view="social"><main class="content"><header class="topbar"></header><div id="view-content"></div></main></div>'}));
 await page.goto('http://social.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(async()=>{
  const owner={id:'owner',login:'drg',display_name:'Александр',online:true},friend={id:'friend',login:'anna',display_name:'Анна',avatar_url:'/portrait/friend'};
  window.socialState={uploads:[],spaces:[{id:'family',kind:'family',name:'Наша семья',owner:true,members:[owner,friend],notes:2},{id:'friends',kind:'friends',name:'Анна',owner:true,members:[owner,friend],notes:1}]};
  window.ctx={state:{me:owner},moduleEnabled:()=>true,navigate(){},toast(text){throw Error(text);},modal(markup){const layer=document.createElement('div');layer.className='modal-overlay';layer.innerHTML='<div class="modal"><button class="modal-close" aria-label="Закрыть">×</button>'+markup+'</div>';layer.querySelector('.modal-close').onclick=()=>layer.remove();document.body.append(layer);return layer;},async api(path,method='GET',body){
   if(path==='/social')return structuredClone({spaces:socialState.spaces,requests:[]});
   if(path==='/notes')return [];
   if(path==='/social/spaces/family/avatar'){
    socialState.uploads.push({method,type:body?.type,size:body?.size});
    socialState.spaces[0].avatar_url=method==='DELETE'?null:'/portrait/family?v='+socialState.uploads.length;
    return {avatar_url:socialState.spaces[0].avatar_url};
   }
   throw Error('Unexpected '+path);
  }};
  await Social.mountSocial(document.querySelector('#view-content'),ctx);
 });
 const family=page.locator('[data-social-name="Наша семья"]'),friends=page.locator('[data-social-name="Анна"]');
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:1000});
  for(const dark of [false,true]){
   await page.evaluate(dark=>{document.documentElement.dataset.scheme=dark?'dark':'light';document.documentElement.dataset.theme='ocean';},dark);
   for(const row of [family,friends])await row.evaluate(el=>el.open=false);
   const geometry=await Promise.all([family,friends].map(row=>row.locator(':scope>summary').evaluate(el=>({height:el.getBoundingClientRect().height,icon:el.querySelector('.person-avatar').getBoundingClientRect().width,text:getComputedStyle(el.querySelector('strong')).fontSize}))));
   assert.deepEqual(geometry[0],geometry[1],'family and friend summaries share their sizes');
   for(const row of [family,friends])await row.locator(':scope>summary').click();
   assert.equal(await family.locator('.member-chip').count(),0);
   assert.equal(await family.locator('[data-people-row]').count(),2);assert.equal(await friends.locator('.social-member').count(),1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:`artifacts/social-unified-${width}-${dark?'dark':'light'}.png`,fullPage:true});
  }
 }
 await family.locator('[data-family-avatar]').click();
 await page.locator('[data-family-photo]').setInputFiles('tests/fixtures/blue.png');
 await page.waitForFunction(()=>document.querySelector('[data-family-photo-status]').textContent==='Фото готово к сохранению');
 await page.waitForFunction(()=>{const img=document.querySelector('.family-avatar-picker img');return img?.complete&&img.naturalWidth>0;});
 assert.match(await page.locator('.family-avatar-picker img').getAttribute('src'),/^data:image\//,'local photo is visible before saving');
 assert.equal(await page.evaluate(()=>socialState.uploads.length),0,'a preview is not uploaded before saving');
 await page.locator('.family-avatar-form [type=submit]').click();
 await page.waitForFunction(()=>!document.querySelector('.modal-overlay')&&document.querySelector('[data-social-name="Наша семья"] summary img'));
 assert.equal(await page.evaluate(()=>socialState.uploads.length),1);
 assert.match(await page.evaluate(()=>socialState.uploads[0].type),/^image\//);
 await family.locator('[data-family-avatar]').click();await page.locator('[data-family-photo-remove]').click();
 await page.locator('.family-avatar-form [type=submit]').click();
 await page.waitForFunction(()=>socialState.uploads.length===2&&!document.querySelector('.modal-overlay'));
 assert.equal(await family.locator('summary img').count(),0);
 assert.equal(await page.evaluate(()=>socialState.uploads[1].method),'DELETE');
 await page.evaluate(async()=>{socialState.spaces[0].owner=false;await Social.mountSocial(document.querySelector('#view-content'),ctx);});
 assert.equal(await family.locator('[data-family-avatar]').count(),0,'only family owners receive photo controls');
 assert.deepEqual(errors,[]);
 console.log('Social appearance passed: matching summaries/members, 320–1280px, light/dark, avatar preview/save/remove and owner-only controls.');
}finally{await browser.close();}
