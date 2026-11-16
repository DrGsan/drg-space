// Real wheel input while image dimensions / an older page arrive.
// ScrollTop writes during an ongoing gesture can cancel WebKit's momentum.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'ScrollTest',write:false,loader:{'.css':'empty'}});
const styles=(await Promise.all(['web/style.css','web/modules/chat/workspace.css','web/modules/chat/media.css','web/modules/family-photos/family-photos.css'].map(f=>readFile(f,'utf8')))).join('\n');
const portrait=await readFile('tests/fixtures/chat-portrait.png'),browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
try{
 const page=await browser.newPage({viewport:{width:390,height:844},reducedMotion:'reduce'}),images=[],errors=[];let releaseImages=false;page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://scroll.test/**',async r=>{if(r.request().url().includes('/photo/')){if(!releaseImages)await new Promise(done=>images.push(done));return r.fulfill({body:portrait,contentType:'image/png',headers:{'Cache-Control':'no-store'}});}return r.fulfill({contentType:'text/html',body:'<div class="chat-shell"><main id="fixture"></main></div>'});});
 await page.goto('http://scroll.test/');await page.addStyleTag({content:styles+'\nhtml,body{margin:0;height:100%;overflow:hidden}.chat-shell{display:block!important;height:100dvh!important;width:100%!important}#fixture{height:100%;min-height:0;padding:0}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  window.s={hold:true,release:null,reads:[],olderRequests:0,rows:Array.from({length:300},(_,i)=>({id:i+1,group_id:'-1001',created:1700000000+i,sender:'Synthetic',text:i%4?'Message '+i+'\nSecond line':'',media:i%4?[]:[{id:'photo'+i,kind:'photo',state:'ready',url:'/photo/'+i,thumbnail_url:'/photo/'+i}]}))};
  window.ctx={state:{me:{id:'viewer',role:'owner'},chatSpace:'telegram:-1001'},moduleEnabled:()=>true,toast(){},navigate(){},async api(path,method,body){if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Synthetic',can_send:true}],status:{bot_messaging:true}};if(path.endsWith('/read')){s.reads.push(body.message_id);return {ok:true};}if(path.includes('/site-sends'))return {items:[]};if(path.includes('/messages')){const before=Number(new URL('http://x'+path).searchParams.get('before')||301);if(before<301){s.olderRequests++;if(s.hold)await new Promise(done=>s.release=done);}const rows=s.rows.filter(m=>m.id<before).slice(-50);return {items:structuredClone(rows),more:rows[0]?.id>1};}throw Error(path);}};
  window.mounted=ScrollTest.mountTelegramChat(document.querySelector('#fixture'),ctx,'-1001',()=>{});window.h=()=>document.querySelector('[data-fp-history]');
 });
 await page.waitForFunction(()=>document.querySelectorAll('[data-fp-message-id]').length===50);
 await page.evaluate(()=>{
  const history=h();window.probe={lastInput:-Infinity,writes:[],mutations:0,input:0,applications:[]};
  const descriptor=Object.getOwnPropertyDescriptor(Element.prototype,'scrollTop');Object.defineProperty(history,'scrollTop',{get(){return descriptor.get.call(this);},set(value){if(performance.now()-probe.lastInput<120)probe.writes.push({delta:value-descriptor.get.call(this),at:performance.now()});return descriptor.set.call(this,value);}});
  history.addEventListener('wheel',()=>{probe.lastInput=performance.now();probe.input++;},{capture:true,passive:true});
  new MutationObserver(changes=>{const count=changes.filter(c=>c.type==='attributes'?c.target.matches('.chat-media-row,.chat-media-album,.chat-media-tile,.fp-chat-message'):[...c.addedNodes].some(n=>n.nodeType===1&&n.matches?.('[data-fp-message-id]'))).length;if(count){probe.applications.push(performance.now());if(performance.now()-probe.lastInput<120)probe.mutations+=count;}}).observe(history,{subtree:true,childList:true,attributes:true,attributeFilter:['style']});
  history.scrollTop=history.scrollHeight/2;
 });
 await page.mouse.move(200,400);await page.waitForTimeout(150);
 for(let i=0;i<12;i++){await page.mouse.wheel(0,-25);if(i===2){releaseImages=true;images.splice(0).forEach(done=>done());}await page.waitForTimeout(30);}
 await page.waitForTimeout(300);const decoding=await page.evaluate(()=>({input:probe.input,writes:probe.writes.length,mutations:probe.mutations,idleToUpdateMs:Math.round((probe.applications.find(t=>t>probe.lastInput)??NaN)-probe.lastInput)}));
 // Deliver an older page during the next real gesture, not before it.
 await page.evaluate(()=>{h().scrollTop=230;probe.lastInput=-Infinity;probe.writes=[];probe.mutations=0;probe.input=0;probe.applications=[];});await page.waitForTimeout(150);
 for(let i=0;i<12;i++){await page.mouse.wheel(0,-25);if(i>=5)await page.evaluate(()=>{if(s.release){s.hold=false;s.release();s.release=null;}});await page.waitForTimeout(30);}
 await page.waitForFunction(()=>document.querySelectorAll('[data-fp-message-id]').length>=100);await page.waitForTimeout(300);const prepend=await page.evaluate(()=>({input:probe.input,writes:probe.writes.length,mutations:probe.mutations,idleToUpdateMs:Math.round((probe.applications.find(t=>t>probe.lastInput)??NaN)-probe.lastInput)}));
 assert.equal(await page.evaluate(()=>s.olderRequests),1,'only one older page may be in flight during the gesture');
 await page.locator('#fp-message').fill('Keep draft');await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await page.waitForTimeout(200);assert.equal(await page.locator('#fp-message').inputValue(),'Keep draft');
 // Probe values are aggregate intervention counts, not FPS.
 console.log(JSON.stringify({engine:process.env.DRG_BROWSER_ENGINE||'chromium',decoding,prepend,errors}));
 assert(decoding.input>=8&&prepend.input>=8,'use actual wheel input');
 assert(decoding.idleToUpdateMs>=120&&decoding.idleToUpdateMs<650,'decoded geometry is applied promptly after scrolling settles');
 assert(prepend.idleToUpdateMs>=120&&prepend.idleToUpdateMs<650,'ready older history is inserted promptly after scrolling settles');
 assert.equal(decoding.writes,0,'decoded photos cannot write scrollTop during an ongoing gesture');
 assert.equal(prepend.writes,0,'older history cannot write scrollTop during an ongoing gesture');
 assert.equal(decoding.mutations,0,'decoded photo geometry waits until the gesture settles');
 assert.equal(prepend.mutations,0,'older history insertion waits until the gesture settles');
 // A held finger must keep updates deferred even after the wheel idle timeout.
 await page.evaluate(()=>{h().dispatchEvent(new Event('touchstart'));s.rows.at(-1).text='Updated while touch held';document.dispatchEvent(new Event('visibilitychange'));});
 await page.waitForTimeout(350);assert(!(await page.locator('[data-fp-message-id="300"]').innerText()).includes('Updated while touch held'));
 await page.evaluate(()=>h().dispatchEvent(new Event('touchend')));await page.waitForFunction(()=>document.querySelector('[data-fp-message-id="300"]').textContent.includes('Updated while touch held'));
 // Leaving while a redraw waits for gesture completion must discard that work.
 await page.evaluate(()=>{h().dispatchEvent(new Event('touchstart'));s.rows.at(-1).text='Never redraw after destroy';document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(80);
 const kept=await page.locator('[data-fp-history]').innerHTML();await page.evaluate(()=>mounted.destroy());await page.waitForTimeout(300);assert.equal(await page.locator('[data-fp-history]').innerHTML(),kept);
 assert.deepEqual(errors,[]);
}finally{await browser.close();}
