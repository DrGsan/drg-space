import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'FamilyStickers',write:false,loader:{'.css':'empty'}});
const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));
const css=await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8'),png=await readFile('tests/fixtures/blue.png');
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:900,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let unavailableAudioReads=0;
 await page.route('http://sticker.test/**',route=>{
  if(route.request().url().includes('/media/'+'4'.repeat(32)))unavailableAudioReads++;
  return route.request().url().includes('/media/')?route.fulfill({body:png,contentType:'image/png'}):route.fulfill({body:'<main id="fixture" class="chat-shell" style="height:100dvh"></main>',contentType:'text/html'});
 });
 await page.goto('http://sticker.test/');await page.addStyleTag({content:css+'body{margin:0}#fixture{max-width:900px;display:block}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  const animated={v:'5.7.4',w:512,h:512,fr:30,ip:0,op:60,assets:[],layers:[{ddd:0,ind:1,ty:4,ip:0,op:60,st:0,bm:0,
   ks:{o:{a:0,k:100},r:{a:1,k:[{t:0,s:[-20],e:[20],i:{x:[.667],y:[1]},o:{x:[.333],y:[0]}},{t:30,s:[20],e:[-20],i:{x:[.667],y:[1]},o:{x:[.333],y:[0]}},{t:60,s:[-20]}]},p:{a:0,k:[256,256,0]},a:{a:0,k:[0,0,0]},s:{a:0,k:[100,100,100]}},
   shapes:[{ty:'rc',d:1,s:{a:0,k:[300,250]},p:{a:0,k:[0,0]},r:{a:0,k:40}},{ty:'fl',c:{a:0,k:[0,.7,.3,1]},o:{a:0,k:100},r:1}]}]};
  const media=(id,mime)=>({id:id.repeat(32),kind:'sticker',mime,state:'ready',url:'/api/family-photos/groups/-1001/media/'+id.repeat(32),
   ...(mime==='application/x-tgsticker'?{animation_url:'/api/family-photos/groups/-1001/media/'+id.repeat(32)+'?animation=1'}:{}),
   ...(mime==='video/webm'?{thumbnail_url:'/api/family-photos/groups/-1001/media/'+id.repeat(32)+'?thumbnail=1'}:{})});
  const rows=window.rows=['image/webp','video/webm','application/x-tgsticker'].map((mime,i)=>({id:i+1,created:1700000000+i,sender:'Анна',text:'',content_type:'sticker',custom_emoji:i===2,sticker_emoji:'😆',media:[media(String(i+1),mime)]}));
  rows.push({id:4,created:1700000004,sender:'Анна',text:'',content_type:'voice',duration:14,media:[{id:'4'.repeat(32),kind:'voice',state:'pending',duration:14,url:'/api/family-photos/groups/-1001/media/'+'4'.repeat(32)}]});
  window.animationReads=0;
  const ctx={state:{chatSpace:'telegram:-1001',me:{id:'owner'}},toast(){},navigate(){},modal(){throw Error('Sticker must not open photo viewer');},async api(path){
   if(path.endsWith('?animation=1')){animationReads++;return structuredClone(animated);}
   if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Семейная группа',history_complete:true}],status:{can_send:false,worker_online:true}};
   if(path.includes('/messages'))return {items:structuredClone(rows),more:false};
   if(path.endsWith('/outbox'))return {items:[]};
   if(path.endsWith('/read'))return {ok:true};
   throw Error('Unexpected '+path);
  }};
  window.mounted=FamilyStickers.mountTelegramChat(document.querySelector('#fixture'),ctx,'-1001',()=>{});
 });
 const canvas=page.locator('[data-fp-animation] canvas');await canvas.waitFor({timeout:10000});
 await page.waitForFunction(()=>{const c=document.querySelector('[data-fp-animation] canvas');return c&&c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0);});
 const firstFrame=await canvas.evaluate(c=>c.toDataURL());await page.waitForTimeout(250);assert.notEqual(await canvas.evaluate(c=>c.toDataURL()),firstFrame,'TGS animates');
 assert.equal(await page.locator('.fp-sticker-message').count(),3);assert.equal(await page.locator('.fp-emoji-message .fp-sticker-visual').evaluate(node=>node.clientWidth),112);

 assert.equal(await page.locator('.fp-sticker-message .chat-media-album,.fp-sticker-message video').count(),0);
 const unavailable=page.locator('[data-recording="voice"]');
 assert.match(await unavailable.innerText(),/0:14\s+Запись пока недоступна/);
 assert(await unavailable.locator('[data-recording-toggle]').isDisabled());
 assert(await unavailable.locator('[data-recording-speed]').isDisabled());
 assert.equal(await unavailable.locator('audio').getAttribute('src'),null);
 assert.equal(unavailableAudioReads,0,'unavailable recording is not downloaded');
 assert.equal(await page.locator('.fp-sticker-message').first().evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
 await page.evaluate(()=>{window.savedCanvas=document.querySelector('[data-fp-animation] canvas');rows.push({id:5,created:1700000005,sender:'Анна',text:'Следующее сообщение',media:[]});});
 await page.waitForFunction(()=>document.querySelector('[data-fp-message-id="5"]'),{timeout:10000});
 assert(await page.evaluate(()=>savedCanvas===document.querySelector('[data-fp-animation] canvas')),'polls preserve the player');
 assert.equal(await page.evaluate(()=>animationReads),1,'animation JSON fetched once');
 assert.equal(unavailableAudioReads,0,'polling does not download unavailable recording');
 for(const scheme of ['light','dark']){
  await page.evaluate(s=>document.documentElement.dataset.scheme=s,scheme);
  for(const width of [900,390,320]){
   await page.setViewportSize({width,height:1000});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await page.screenshot({path:`artifacts/family-stickers-${process.env.DRG_BROWSER_ENGINE||'chromium'}-${scheme}-${width}.png`});
  }
 }
 await page.emulateMedia({reducedMotion:'reduce'});await page.waitForTimeout(100);const paused=await canvas.evaluate(c=>c.toDataURL());await page.waitForTimeout(200);assert.equal(await canvas.evaluate(c=>c.toDataURL()),paused,'reduced motion pauses animation');await page.evaluate(()=>mounted.destroy());assert.equal(await canvas.count(),0);
 assert.deepEqual(errors,[]);console.log('Family stickers passed: transparent WebP/video previews, rendered TGS canvas, disabled unavailable recording without media requests, stable polling, themes, mobile widths and cleanup.');
}finally{await browser.close();}
