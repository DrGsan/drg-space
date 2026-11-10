import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium',manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));
const css=await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8'),bundle=await build({entryPoints:['web/modules/dashboard/widgets/clock.js'],bundle:true,format:'iife',globalName:'Clock',write:false});
const browser=await({chromium,webkit}[engine]).launch();
try{
 const page=await browser.newPage({viewport:{width:800,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://clock.test/**',r=>r.fulfill({contentType:'text/html',body:'<html data-theme="ocean" data-scheme="light"><body><section class="widget clock-panel"><div data-widget-body id="clocks"></div></section></body></html>'}));await page.goto('http://clock.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
 for(const [size,width,height] of [['1x1',260,230],['2x1',536,230],['1x2',260,506],['2x2',536,506],['2x1',280,230],['2x2',280,506]]){
  for(const mode of ['analog','digital'])for(const theme of mode==='analog'?['classic','minimal','station','roman','orbit','bold']:['classic','minimal','station','segments','flip','accent']){
   await page.evaluate(({size,width,height,mode,theme})=>{window.stopClock?.();const host=document.querySelector('#clocks');Object.assign(host.style,{width:width+'px',height:height+'px',flex:'none'});window.stopClock=Clock.mountClock(host,{instance:{size,config:{mode,theme,hour_format:'12h',date_format:'weekday',cities:[{timezone:'Europe/Moscow',label:'Москва'},{timezone:'Asia/Vladivostok',label:'Владивосток'},{timezone:'America/New_York',label:'Нью-Йорк'},{timezone:'Australia/Lord_Howe',label:'Остров Лорд-Хау'}]}}});},{size,width,height,mode,theme});
   await page.waitForTimeout(20);
   const geometry=await page.locator('.clock-widget').evaluateAll(clocks=>clocks.map(el=>{const b=el.getBoundingClientRect();return {width:b.width,height:b.height,scroll:el.scrollHeight,client:el.clientHeight,overflow:[...el.querySelectorAll('.clock-digits>*,.clock-face,.clock-details')].some(c=>{const r=c.getBoundingClientRect();return r.left<b.left-1||r.right>b.right+1||r.top<b.top-1||r.bottom>b.bottom+1;})};}));
   assert(geometry.every(g=>g.scroll<=g.client+1&&!g.overflow),JSON.stringify({engine,size,width,mode,theme,geometry}));
  }
 }
 await page.evaluate(()=>stopClock());assert.deepEqual(errors,[]);console.log(`Clock layout passed (${engine}): all 12 faces, four sizes, narrow world grids, 12-hour time, seconds and long dates fit.`);
}finally{await browser.close();}
