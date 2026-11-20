// Wheel scrolling, without clicking play: a poster covers pending/failed autoplay.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const bundle=await build({stdin:{contents:"export {inlineVideos} from './web/modules/chat/video-preview.js';export {albumMarkup} from './web/modules/chat/media.js';export {renderMarkup} from './web/core/dom.js';",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'Preview',write:false,loader:{'.css':'empty'}});
const styles=(await Promise.all(['web/modules/chat/media.css','web/modules/family-photos/family-photos.css'].map(p=>readFile(p,'utf8')))).join('\n');
const bytes=await readFile('tests/fixtures/chat-video.mp4');
const browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
const posterPixel=[70,125,145,255];
async function tilePixel(page){
 const box=await page.locator('.chat-media-tile').boundingBox();
 const clip={x:Math.floor(box.x+box.width*.27),y:Math.floor(box.y+box.height*.7),width:1,height:1};
 const png=await page.screenshot({clip});
 return page.evaluate(async encoded=>{
  const image=new Image();image.src='data:image/png;base64,'+encoded;await image.decode();
  const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
  const context=canvas.getContext('2d');context.drawImage(image,0,0,1,1);
  return Array.from(context.getImageData(0,0,1,1).data);
 },png.toString('base64'));
}
try{
 for(const fallback of [false,true])for(const fail of [false,true]){
  let release;const gate=new Promise(done=>release=done);let originalRequests=0;
  const page=await browser.newPage({viewport:{width:390,height:800}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://preview-scroll.test/**',async route=>{
   const url=route.request().url();
   if(url.includes('/original')){originalRequests++;await gate;return fail?route.fulfill({status:503}):route.fulfill({contentType:'video/mp4',body:bytes});}
   if(url.includes('/poster'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><path fill="#467d91" d="M0 0h320v180H0z"/></svg>'});
   return route.fulfill({contentType:'text/html',body:'<main id="history" class="fp-chat-history" style="height:600px;overflow:auto"></main>'});
  });
  await page.goto('http://preview-scroll.test/');await page.addStyleTag({content:styles+'\nbody{margin:0}.chat-media-album{width:320px}.chat-media-row{aspect-ratio:16/9}.chat-media-tile{background:#000}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(({fallback})=>{
   Object.defineProperty(navigator,'standalone',{value:true,configurable:true});
   if(fallback){HTMLVideoElement.prototype.requestVideoFrameCallback=undefined;HTMLVideoElement.prototype.cancelVideoFrameCallback=undefined;}
   const h=document.querySelector('#history');h.innerHTML='<div style="height:800px"></div>'+Preview.albumMarkup([{id:'one',kind:'video',width:320,height:180,url:'/original',thumbnail_url:'/poster'}],'data-fp-chat-file',{imagePosters:true})+'<div style="height:800px"></div>';
   window.video=h.querySelector('video');window.poster=h.querySelector('[data-video-poster]');window.player=Preview.inlineVideos(h);window.observed={unsafeFrames:0,frames:0};
   const sample=()=>{if(!window.player)return;observed.frames++;if(video.closest('.chat-media-tile').classList.contains('is-preview-playing')&&video.readyState<2)observed.unsafeFrames++;requestAnimationFrame(sample);};requestAnimationFrame(sample);player.sync();
  },{fallback});
  await page.waitForFunction(()=>poster.naturalWidth>0);assert.equal(originalRequests,0,'offscreen video must not start loading');
  await page.locator('#history').hover();await page.mouse.wheel(0,750);await page.waitForFunction(()=>!video.paused);await page.waitForTimeout(180);
  const held=await page.evaluate(()=>({readyState:video.readyState,poster:getComputedStyle(poster).visibility,rect:poster.closest('.chat-media-tile').getBoundingClientRect().toJSON()}));
  assert.equal(held.readyState,0);assert.equal(held.poster,'visible','slow autoplay must not uncover a black video surface');
  assert.deepEqual(await tilePixel(page),posterPixel);
  if(process.env.DRG_MEDIA_REVIEW_OUT){await mkdir(process.env.DRG_MEDIA_REVIEW_OUT,{recursive:true});await page.screenshot({path:process.env.DRG_MEDIA_REVIEW_OUT+'/held-'+fallback+'-'+fail+'.png'});}
  // Leave and re-enter while play() is still waiting; no stale callback may hide it.
  await page.mouse.wheel(0,750);await page.waitForFunction(()=>video.paused);await page.mouse.wheel(0,-750);await page.waitForFunction(()=>!video.paused);
  assert.equal(await page.evaluate(()=>getComputedStyle(poster).visibility),'visible');
  release();
  if(fail){await page.waitForFunction(()=>!!video.error);assert.equal(await page.evaluate(()=>getComputedStyle(poster).visibility),'visible','a failed original keeps its poster');}
  else{
   const playing=()=>!video.paused&&video.readyState>=2&&video.closest('.chat-media-tile').classList.contains('is-preview-playing');
   await page.waitForFunction(playing);
   const frame=await tilePixel(page);
   assert.ok(frame[0]<55&&frame[1]>100&&frame[2]>100,'muted video should paint over its poster: '+frame.join(','));
   assert.equal(await page.evaluate(()=>video.muted),true);
   // Simulate the inline surface dropping out while the media clock still runs.
   const lostSurface=await page.addStyleTag({content:'[data-video-preview],[data-video-frame]{opacity:0!important}'});
   const lastTime=await page.evaluate(()=>video.currentTime);
   await page.waitForFunction(lastTime=>!video.paused&&video.currentTime!==lastTime,lastTime);
   assert.deepEqual(await tilePixel(page),posterPixel,'a missing video surface should reveal the poster during playback');
   await lostSurface.evaluate(style=>style.remove());
   assert.deepEqual(await tilePixel(page),frame,'the visible frame should resume covering the poster');
   await page.mouse.wheel(0,750);await page.waitForFunction(()=>video.paused);assert.equal(await page.evaluate(()=>getComputedStyle(poster).visibility),'visible','leaving returns to the poster');
   await page.mouse.wheel(0,-750);await page.waitForFunction(playing);
   await page.evaluate(()=>document.dispatchEvent(new CustomEvent('drg-media-viewer',{detail:{open:true}})));await page.waitForFunction(()=>video.paused);assert.equal(await page.evaluate(()=>getComputedStyle(poster).visibility),'visible');
   assert.deepEqual(await tilePixel(page),posterPixel,'the poster should cover the paused video when the viewer opens');
  }
  const result=await page.evaluate(()=>({observed,rect:poster.closest('.chat-media-tile').getBoundingClientRect().toJSON(),posterNodes:document.querySelectorAll('[data-video-poster]').length}));
  assert.equal(result.observed.unsafeFrames,0);assert.equal(result.posterNodes,1);assert.equal(result.rect.width,held.rect.width);assert.equal(result.rect.height,held.rect.height);assert.deepEqual(errors,[]);
  if(process.env.DRG_MEDIA_REVIEW_OUT){await mkdir(process.env.DRG_MEDIA_REVIEW_OUT,{recursive:true});await page.screenshot({path:process.env.DRG_MEDIA_REVIEW_OUT+'/scroll-'+fallback+'-'+fail+'.png'});}
  await page.evaluate(()=>{player.destroy();window.player=null;});console.log(JSON.stringify({engine:process.env.DRG_BROWSER_ENGINE||'chromium',fallback,fail,originalRequests,unsafeFrames:result.observed.unsafeFrames,posterNodes:result.posterNodes}));await page.close();
 }

 // Keep the browser's native video surface transparent and verify movement
 // from two decoded colors in screenshots of the actual visible tile.
 const moving=await readFile('tests/fixtures/chat-preview-motion.mp4');
 const page=await browser.newPage({viewport:{width:390,height:800}});
 let originalRequests=0;
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://moving-preview.test/**',route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/motion'){originalRequests++;return route.fulfill({contentType:'video/mp4',body:moving});}
  if(url.pathname==='/poster')return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"><path fill="#467d91" d="M0 0h160v90H0z"/></svg>'});
  return route.fulfill({contentType:'text/html',body:'<main id="history" class="fp-chat-history" style="height:600px;overflow:auto"></main>'});
 });
 await page.goto('http://moving-preview.test/');
 await page.addStyleTag({content:styles+'\nbody{margin:0}.chat-media-album{width:320px}.chat-media-row{aspect-ratio:16/9}video[data-video-preview]{opacity:0!important}'});
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  const host=document.querySelector('#history');
  const album=Preview.albumMarkup([{id:'one',kind:'video',width:160,height:90,url:'/motion',thumbnail_url:'/poster'}],'data-fp-chat-file',{imagePosters:true});
  window.draw=text=>Preview.renderMarkup(host,'<div style="height:800px"></div>'+album+'<div style="height:800px">'+text+'</div>');
  draw('original text');
  window.player=Preview.inlineVideos(host);player.sync();
 });
 const host=page.locator('#history');
 await host.hover();await page.mouse.wheel(0,750);
 await page.waitForSelector('.has-painted-video-frame>[data-video-frame]');
 const painted=page.locator('[data-video-frame]');
 const colors=new Set(),deadline=Date.now()+5500;
 while(colors.size<2&&Date.now()<deadline){
  const [red,green,blue]=await tilePixel(page);
  if(green>145&&red<75&&blue<100)colors.add('green');
  if(red>145&&green<110&&blue>55)colors.add('pink');
  await page.waitForTimeout(60);
 }
 assert.equal(colors.size,2,'both decoded colors must appear even with the native video element transparent');
 assert.equal(await page.locator('video').evaluate(v=>v.muted&&!v.paused),true);
 assert.equal(originalRequests,1,'the canvas should use the existing video stream');
 assert.ok(await page.locator('[data-video-progress]').evaluate(el=>parseFloat(el.style.width)>0));
 const sameFrame=await painted.evaluate(canvas=>{
  const before=canvas.width;
  draw('refreshed text');player.sync();
  return canvas===document.querySelector('[data-video-frame]')&&canvas.width===before;
 });
 assert.equal(sameFrame,true,'a history refresh must preserve the live frame');
 await page.mouse.wheel(0,750);
 await page.waitForFunction(()=>document.querySelector('video').paused&&document.querySelector('[data-video-frame]').width===0);
 assert.deepEqual(errors,[]);
 await page.evaluate(()=>player.destroy());
 await page.close();
 console.log(JSON.stringify({engine:process.env.DRG_BROWSER_ENGINE||'chromium',visibleDecodedColors:colors.size,originalRequests}));
}finally{await browser.close();}
