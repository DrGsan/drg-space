// API geometry reserves natural single-photo/video frames before any preview bytes.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'MediaTest',write:false,loader:{'.css':'empty'}});
const styles=(await Promise.all(['web/style.css','web/modules/chat/workspace.css','web/modules/chat/media.css','web/modules/family-photos/family-photos.css'].map(f=>readFile(f,'utf8')))).join('\n');
const videoBytes=await readFile('tests/fixtures/chat-video.mp4');
const portrait=await readFile('tests/fixtures/chat-portrait.png'),portraitRatio=portrait.readUInt32BE(16)/portrait.readUInt32BE(20);
const wide=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="450"><path fill="#467d91" d="M0 0h900v450H0z"/></svg>');
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium,browser=await engine.launch();
try{
 for(const width of [390,1350]){
  const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'}),requests=[],errors=[];let playVideo=false;const gates=[];let release=false;page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://media-layout.test/**',async r=>{
   const url=r.request().url();if(url.includes('/original/')){requests.push({original:true});return playVideo?r.fulfill({body:videoBytes,contentType:'video/mp4'}):r.fulfill({status:503});}
   if(url.includes('/thumb/')){const start=Date.now();if(!release)await new Promise(done=>gates.push(done));requests.push({url,duration:Date.now()-start});return r.fulfill({body:url.includes('wide')?wide:portrait,contentType:url.includes('wide')?'image/svg+xml':'image/png',headers:{'Cache-Control':'no-store'}});}
   return r.fulfill({contentType:'text/html',body:'<div class="chat-shell"><main id="fixture"></main></div>'});
  });
  await page.goto('http://media-layout.test/');await page.addStyleTag({content:styles+'\nhtml,body{margin:0;height:100%;overflow:hidden}.chat-shell{display:block!important;height:100dvh!important;width:100%!important}#fixture{height:100%;min-height:0;padding:0}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
   window.s={rows:[],paths:[]};window.ctx={state:{me:{id:'viewer',role:'owner'},chatSpace:'telegram:-1001'},moduleEnabled:()=>true,toast(){},navigate(){},async api(path){s.paths.push(path);if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Synthetic',can_send:true}],status:{bot_messaging:true}};if(path.endsWith('/read'))return {ok:true};if(path.includes('/site-sends'))return {items:[]};if(path.includes('/messages'))return {items:structuredClone(s.rows),more:false};throw Error(path);}};
   window.show=(kind,shape,album=false)=>{window.mounted?.destroy();s.rows=Array.from({length:album?4:1},(_,i)=>({id:i+1,group_id:'-1001',created:1700000000+i,sender:'Synthetic',text:'',album_id:album?'one':'',media:[{id:kind+shape+i,kind,width:shape==='wide'?900:360,height:shape==='wide'?450:640,state:'ready',url:'/original/'+kind+shape+i,thumbnail_url:'/thumb/'+kind+'-'+shape+i}]}));window.mounted=MediaTest.mountTelegramChat(document.querySelector('#fixture'),ctx,'-1001',()=>{});};
  });
  for(const kind of ['photo','video'])for(const shape of ['portrait','wide']){
   release=false;const start=Date.now();await page.evaluate(([kind,shape])=>show(kind,shape),[kind,shape]);await page.waitForSelector('[data-chat-attachment-id="'+kind+shape+'0"] img');
   const before=await page.locator('.chat-media-tile').boundingBox();assert(Math.abs(before.width/before.height-(shape==='wide'?2:portraitRatio))<.025,'natural frame must be correct before image loading');release=true;gates.splice(0).forEach(done=>done());
   await page.waitForFunction(()=>{const i=document.querySelector('.chat-media-tile>img');return i?.complete&&i.naturalWidth>0;});
   const dimensions=await page.evaluate(async()=>{const media=document.querySelector('.chat-media-tile>img');await new Promise(done=>setTimeout(done,180));const tile=media.closest('.chat-media-tile'),box=tile.getBoundingClientRect();return {width:box.width,height:box.height,ratio:media.naturalWidth/media.naturalHeight,provisional:!!tile.closest('.is-provisional'),row:tile.parentElement.getAttribute('style')};});
   const after=await page.locator('.chat-media-tile').boundingBox();for(const k of ['x','y','width','height'])assert(Math.abs(after[k]-before[k])<1,'single '+kind+' cannot shift on '+k+' after preview load');
   const expected=shape==='wide'?2:portraitRatio;
   if(process.env.DRG_MEDIA_REVIEW_OUT){await mkdir(process.env.DRG_MEDIA_REVIEW_OUT,{recursive:true});await page.screenshot({path:process.env.DRG_MEDIA_REVIEW_OUT+'/'+width+'-'+kind+'-'+shape+'.png'});}
   assert(Math.abs(dimensions.width/dimensions.height-expected)<.025,`${kind} ${shape} must fit its natural frame while visible: ${JSON.stringify(dimensions)}`);
   assert(!dimensions.provisional,'loaded previews cannot stay in an indefinite provisional frame');
   assert(Date.now()-start<2000,'120 ms thumbnail must not wait for scrolling or playback to obtain its final frame');
   const count=requests.length;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await page.waitForTimeout(150);assert.equal(requests.length,count,'unchanged refresh must not refetch previews');
  }
  await page.evaluate(()=>show('video','portrait',true));await page.waitForSelector('.chat-media-album');await page.waitForFunction(()=>document.querySelector('.chat-media-album.is-portrait-stack')&&[...document.querySelectorAll('[data-video-poster]')].every(i=>i.naturalHeight>0));await page.waitForTimeout(180);
  assert.equal(requests.filter(r=>r.original).length,0,'video proportions must use small posters, never original video downloads');
  // The poster layer must yield to actual playback, then return when paused.
  playVideo=true;await page.evaluate(()=>show('video','portrait'));await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>{const v=document.querySelector('.chat-media-tile>video');return v&&!v.paused&&v.readyState>=2;});
  assert.equal(await page.locator('.chat-media-tile>video').evaluate(v=>getComputedStyle(v).visibility),'visible');
  assert.equal(await page.locator('[data-video-poster]').evaluate(i=>getComputedStyle(i).visibility),'hidden');
  await page.emulateMedia({reducedMotion:'reduce'});await page.waitForFunction(()=>document.querySelector('.chat-media-tile>video').paused);
  assert.equal(await page.locator('[data-video-poster]').evaluate(i=>getComputedStyle(i).visibility),'visible');
  assert.deepEqual(errors,[]);await page.evaluate(()=>mounted.destroy());await page.close();
  console.log(JSON.stringify({engine:process.env.DRG_BROWSER_ENGINE||'chromium',width,scenarios:'correct natural frame before bytes, unchanged frame after photo/video poster load, portrait/wide, refresh and actual playback/pause',thumbnailRequests:requests.filter(r=>!r.original).length,maxThumbnailResponseMs:Math.max(...requests.filter(r=>!r.original).map(r=>r.duration)),originalRequestsBeforePlayback:0,playback:'poster hides for actual video and returns on pause'}));
 }
}finally{await browser.close();}
