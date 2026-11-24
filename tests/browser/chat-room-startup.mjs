// Initial real chat list: parallel sources, one complete paint, no per-room history.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/chat/index.js'],bundle:true,format:'iife',globalName:'Startup',write:false,
 loader:{'.css':'empty'},plugins:[{name:'unrelated-inbox',setup(build){build.onLoad({filter:/\/modules\/notifications\/index\.js$/},()=>({contents:'export const inboxController={attach(){}};',loader:'js'}));}}]});
const browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
try{
 for(const standalone of [false,true])for(const first of ['native','media','unavailable','without-previews']){
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:standalone?390:1350,height:900}});
  await page.route('http://startup.test/**',r=>r.fulfill({contentType:'text/html',body:'<main id="host"></main>'}));await page.goto('http://startup.test/');
  await page.clock.install();
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(({standalone})=>{
   Object.defineProperty(navigator,'standalone',{value:standalone,configurable:true});
   window.s={paths:[],painted:[],pending:{}};
   const host=document.querySelector('#host');new MutationObserver(()=>{const names=[...host.querySelectorAll('.chat-room-title strong')].map(n=>n.textContent);if(names.length)s.painted.push(names);}).observe(host,{childList:true,subtree:true});
   const native={id:'native',name:'Native',kind:'family',members:[],unread:0,last_message:{id:1,created:100,body:'Native text'}};
   window.media=[{id:'-1001',title:'Family A',latest_message_id:1,last_message:{id:1,created:300,sender:'Synthetic',text:'A',media:[]}},
    {id:'-1002',title:'Family B',latest_message_id:2,last_message:{id:2,created:200,sender:'Synthetic',text:'B',media:[]}}];
   const ctx={state:{me:{id:'owner',role:'owner',settings:{}},spaces:[]},moduleEnabled:name=>name==='family_photos',toast(message){throw Error(message);},navigate(){},
    push:{state:async()=>({enabled:true})},api(path){s.paths.push(path);if(path==='/chat/rooms')return new Promise(resolve=>s.pending.native=()=>resolve({items:[native]}));if(path==='/family-photos/groups?previews=1')return new Promise((resolve,reject)=>{s.pending.media=()=>resolve({items:structuredClone(media)});s.pending.fail=(status=503)=>reject(Object.assign(Error('Synthetic unavailable'),{status}));s.pending.withoutPreviews=()=>resolve({items:media.map(group=>({...group,last_message:null})),previews_available:false});});throw Error('Unexpected '+path);}};
   window.mounted=Startup.mountChat(host,ctx);
  },{standalone});
  await page.waitForFunction(()=>s.pending.native&&s.pending.media);
  if(first==='unavailable'){await page.evaluate(()=>{s.pending.native();s.pending.fail();});}
  else if(first==='without-previews'){await page.evaluate(()=>{s.pending.native();s.pending.withoutPreviews();});}
  else{await page.evaluate(first=>s.pending[first](),first);assert.equal(await page.locator('.chat-room').count(),0,'no incomplete first list');await page.evaluate(first=>s.pending[first==='native'?'media':'native'](),first);}
  await page.evaluate(()=>mounted);
  const actual=await page.evaluate(()=>({names:[...document.querySelectorAll('.chat-room-title strong')].map(n=>n.textContent),painted:s.painted,paths:s.paths}));
  const expected=first==='unavailable'?['Native']:first==='without-previews'?['Native','Family A','Family B']:['Family A','Family B','Native'];
  assert.deepEqual(actual.names,expected);assert(actual.painted.every(names=>JSON.stringify(names)===JSON.stringify(expected)));assert.equal(actual.paths.length,2,'one catalog and one native list, no histories');
  const notice=page.locator('.chat-family-status'),preview=page.locator('[data-room="telegram:-1001"] .chat-room-preview');
  if(first==='without-previews')assert.match(await preview.innerText(),/Превью сообщения временно недоступно/);
  assert.equal(await notice.count(),first==='unavailable'?1:0);
  if(first==='unavailable'||first==='without-previews'){
   await page.clock.runFor(15100);await page.evaluate(()=>s.pending.media());
   assert.match(await preview.innerText(),/Synthetic: A/);
   assert.equal(await notice.count(),0,'the outage notice clears after recovery');
   await page.clock.runFor(15100);await page.evaluate(()=>s.pending.fail());
   assert.match(await preview.innerText(),/Превью сообщения временно недоступно/);
   assert.equal(await notice.count(),1,'warm catalog shows the outage and keeps group names');
   await page.clock.runFor(15100);await page.evaluate(()=>s.pending.fail(403));
   assert.equal(await page.locator('[data-room^="telegram:"]').count(),0,'authorization failure clears previous groups');
  }
  await page.evaluate(()=>Startup.chatController.destroy());console.log(JSON.stringify({standalone,first,...actual}));await page.close();
 }
}finally{await browser.close();}
