import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';

const engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const script=await build({stdin:{contents:"export {albumMarkup,fitAlbums} from './web/modules/chat/media.js'; export {inlineVideos} from './web/modules/chat/video-preview.js'; export {openFamilyViewer} from './web/modules/family-photos/index.js';",resolveDir:process.cwd()},bundle:true,loader:{'.css':'empty'},format:'iife',globalName:'Media',write:false});
const browser=await(engine==='webkit'?webkit:chromium).launch({headless:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:430,height:932},isMobile:true,hasTouch:true});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const bytes=await readFile('tests/fixtures/chat-video.mp4');
 await page.route('https://gifs.test/**',route=>route.fulfill({contentType:'video/mp4',body:bytes}));
 await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><html data-scheme="dark"><main class="chat-shell"><div class="chat-messages" style="height:700px;overflow:auto"></div></main></html>');
 await page.addStyleTag({content:(await readFile('app/static/assets/app.css','utf8')).replace(/@font-face\s*\{[^}]*\}/g,'')+'body{margin:0}.chat-shell{display:block;width:100%}.chat-message.has-visual-media{width:180px!important}.chat-media-row{height:90px!important;aspect-ratio:auto!important}.chat-media-album.is-single .chat-media-tile>video{object-fit:cover}'});
 await page.addScriptTag({content:script.outputFiles[0].text});
 await page.evaluate(()=>{
  const host=document.querySelector('.chat-messages');window.gifFiles=[];
  for(let id=1;id<=5;id++){
   const file={id:String(id),kind:id<=3?'animation':'video',mime:'video/mp4',url:'https://gifs.test/'+id,state:'ready',created:1700000000};gifFiles.push(file);
   host.insertAdjacentHTML('beforeend',`<article class="chat-message has-visual-media">${Media.albumMarkup([file],'data-fp-chat-file')}</article>`);
  }
  host.insertAdjacentHTML('beforeend','<div style="height:900px;flex-shrink:0">Конец</div>');
  window.previews=Media.inlineVideos(host);previews.sync();
 });
 await page.waitForFunction(()=>[...document.querySelectorAll('[data-animation-preview]')].every(v=>!v.paused&&v.currentTime>0));
 assert.equal(await page.locator('.chat-gif-mark').count(),3);
 assert.equal(await page.locator('.is-animation .chat-media-play,.is-animation .chat-video-progress,.is-animation [data-video-remaining]').count(),0);
 assert.equal(await page.locator('.is-animation video[controls]').count(),0);
 assert(await page.locator('.is-animation video').evaluateAll(videos=>videos.every(v=>v.loop&&v.muted&&getComputedStyle(v).visibility==='visible')));
 await page.waitForFunction(()=>[...document.querySelectorAll('[data-video-preview]')].filter(v=>!v.paused).length===1);
 const moments=await page.locator('[data-animation-preview]').evaluateAll(videos=>videos.map(v=>v.currentTime));
 await page.waitForTimeout(400);
 assert(await page.locator('[data-animation-preview]').evaluateAll((videos,old)=>videos.every((v,i)=>v.currentTime!==old[i]&&!v.paused),moments));
 await mkdir('artifacts',{recursive:true});await page.screenshot({path:`artifacts/chat-gif-${engine}.png`});
 await page.evaluate(()=>{
  window.viewer=Media.openFamilyViewer({modal(html){const d=document.createElement('div');d.className='modal-overlay';d.innerHTML='<div class="modal"><button class="modal-close">×</button>'+html+'</div>';d.querySelector('button').onclick=()=>d.remove();document.body.append(d);return d;}},gifFiles,0);
 });
 await page.waitForFunction(()=>!document.querySelector('.fp-viewer-stage video').paused);
 assert.equal(await page.locator('.fp-video-controls').count(),0);
 assert(await page.locator('.fp-viewer-stage video').evaluate(v=>v.loop&&v.muted&&!v.controls));
 assert(await page.locator('.chat-media-tile video').evaluateAll(videos=>videos.every(v=>v.paused)));
 await page.evaluate(()=>viewer.destroy());
 await page.waitForFunction(()=>[...document.querySelectorAll('[data-animation-preview]')].every(v=>!v.paused));
 await page.evaluate(()=>{document.querySelector('.chat-messages').scrollTop=1000;});
 await page.waitForFunction(()=>[...document.querySelectorAll('.chat-media-tile video')].every(v=>v.paused));
 await page.evaluate(()=>{document.querySelector('.chat-messages').scrollTop=0;});
 await page.waitForFunction(()=>[...document.querySelectorAll('[data-animation-preview]')].every(v=>!v.paused));
 await page.evaluate(()=>previews.destroy());
 assert(await page.locator('.chat-media-tile video').evaluateAll(videos=>videos.every(v=>v.paused)));
 assert.deepEqual(errors,[]);
 console.log(`GIF playback passed (${engine}): three concurrent loops, independent ordinary video, no video UI, viewer and offscreen cleanup.`);
}finally{await browser.close();}
