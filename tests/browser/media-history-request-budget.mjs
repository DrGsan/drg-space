// Request budget for opening, 100 loaded messages and an unchanged refresh.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
const require=createRequire(process.cwd()+'/package.json'),{build}=require('esbuild'),{chromium,webkit}=require('playwright');
const repo=process.cwd(),browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();const result=[];
try{
 const bundle=await build({entryPoints:[repo+'/web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'BudgetTest',write:false,loader:{'.css':'empty'}});
 const styles=(await Promise.all(['web/style.css','web/modules/chat/workspace.css','web/modules/chat/media.css','web/modules/family-photos/family-photos.css'].map(f=>readFile(repo+'/'+f,'utf8')))).join('\n');
 for(const height of [900,1600]){
  const page=await browser.newPage({viewport:{width:390,height},reducedMotion:'reduce'});await page.route('http://budget.test/**',r=>r.fulfill({contentType:'text/html',body:'<div class="chat-shell"><main id="fixture"></main></div>'}));await page.goto('http://budget.test/');await page.addStyleTag({content:styles+'\nhtml,body{margin:0;height:100%;overflow:hidden}.chat-shell{display:block!important;height:100dvh!important;width:100%!important}#fixture{height:100%;min-height:0;padding:0}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
   window.s={paths:[],rows:Array.from({length:300},(_,i)=>({id:i+1,group_id:'-1001',created:1700000000+i,sender:'Synthetic',text:'Short text',media:[]}))};
   const ctx={state:{me:{id:'viewer',role:'owner'},chatSpace:'telegram:-1001'},moduleEnabled:()=>true,toast(){},navigate(){},async api(path,method){s.paths.push({path,method});if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Synthetic',can_send:true}],status:{bot_messaging:true}};if(path.endsWith('/read'))return {ok:true};if(path.includes('/site-sends'))return {items:[]};if(path.includes('/messages')){const before=Number(new URL('http://x'+path).searchParams.get('before')||301),rows=s.rows.filter(m=>m.id<before).slice(-50);return {items:structuredClone(rows),more:rows[0]?.id>1};}throw Error(path);}};
   window.mounted=BudgetTest.mountTelegramChat(document.querySelector('#fixture'),ctx,'-1001',()=>{});
   window.snapshot=()=>({messages:document.querySelectorAll('[data-fp-message-id]').length,history:s.paths.filter(p=>p.path.includes('/messages')).length,groups:s.paths.filter(p=>p.path==='/family-photos/groups').length,queue:s.paths.filter(p=>p.path.includes('/site-sends')).length,reads:s.paths.filter(p=>p.path.endsWith('/read')).length});
  });
  await page.waitForFunction(()=>document.querySelectorAll('[data-fp-message-id]').length>=50);await page.waitForTimeout(500);const opening=await page.evaluate(()=>snapshot());
  if(opening.messages<100){await page.evaluate(()=>document.querySelector('[data-fp-history]').scrollTop=0);await page.waitForFunction(()=>document.querySelectorAll('[data-fp-message-id]').length>=100);await page.waitForTimeout(300);}
  const loaded100=await page.evaluate(()=>snapshot());await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await page.waitForTimeout(300);const refresh=await page.evaluate(()=>snapshot());result.push({height,opening,loaded100,refresh});await page.evaluate(()=>mounted.destroy());await page.close();
 }
}finally{await browser.close();}
for(const row of result){assert.deepEqual(row.opening,{messages:50,history:1,groups:1,queue:1,reads:1});assert.deepEqual(row.loaded100,{messages:100,history:2,groups:1,queue:1,reads:1});assert.deepEqual(row.refresh,{messages:100,history:4,groups:2,queue:2,reads:1});}
console.log(JSON.stringify({engine:process.env.DRG_BROWSER_ENGINE||'chromium',result}));
