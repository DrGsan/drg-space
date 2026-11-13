import {readAppStyles,openTestOrigin} from './fixtures.mjs';
// Desktop WebKit cannot open the real iOS keyboard. Exercise its separate
// visual viewport resize/pan against the production CSS and viewport binding.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/chat/viewport.js'],bundle:true,format:'iife',globalName:'ChatViewport',write:false});
const css=await readAppStyles();
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
 for(const telegram of [false,true]){
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844}});
  await openTestOrigin(page);await page.setContent(`<!doctype html><style>${css}</style><div id="app-wallpaper" style="background-image:linear-gradient(30deg,#dbb78e,#b7c9bf)"></div><div id="app-status-background"></div><div id="app"><div class="workspace chat-open"><main class="content"><div id="view-content"><div class="chat-shell room-open"><div class="chat-workspace"><aside class="chat-sidebar"></aside><main class="chat-main">${telegram?'<div class="fp-chat">':''}<header class="chat-header"><h2>Тест клавиатуры</h2></header><div class="chat-messages">${Array.from({length:40},(_,i)=>`<article class="chat-message"><p>Сообщение ${i}</p></article>`).join('')}</div><form class="chat-compose"><div class="chat-compose-content"><div class="chat-composer-shell"><textarea rows="1" aria-label="Сообщение"></textarea></div></div></form>${telegram?'</div>':''}</main></div></div></div></main><nav class="mobile-app-tabs"><button>Сообщения</button></nav></div></div>`);
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
   document.documentElement.classList.add('app-standalone');document.documentElement.dataset.interface='glass';document.documentElement.dataset.theme='amber';document.documentElement.dataset.scheme='light';
   document.documentElement.style.setProperty('--app-safe-top','59px');document.documentElement.style.setProperty('--app-safe-bottom','34px');
   const viewport=window.testViewport=new EventTarget();Object.assign(viewport,{height:844,width:390,scale:1,offsetTop:0});
   Object.defineProperty(window,'visualViewport',{configurable:true,value:viewport});
   window.unbindViewport=ChatViewport.bindChatViewport(document.querySelector('.chat-shell'));
   window.keptInput=document.querySelector('textarea');keptInput.value='Черновик сообщения';keptInput.focus();keptInput.setSelectionRange(2,7);
   const history=document.querySelector('.chat-messages');history.scrollTop=history.scrollHeight;
  });
  const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const bounds=()=>page.evaluate(()=>{
   const app=document.querySelector('#app'),header=document.querySelector('.chat-header'),composer=document.querySelector('.chat-compose'),history=document.querySelector('.chat-messages');
   return {top:app.getBoundingClientRect().top,bottom:app.getBoundingClientRect().bottom,header:header.getBoundingClientRect().top,composer:composer.getBoundingClientRect().bottom,overflow:app.scrollHeight-app.clientHeight,scroll:app.scrollTop,following:history.scrollHeight-history.scrollTop-history.clientHeight<2,rootScroll:document.scrollingElement.scrollTop};
  });
  assert.equal(await page.locator('.mobile-app-tabs').isVisible(),false);
  await settle();let box=await bounds();assert.equal(box.top,59);assert.equal(box.bottom,844);assert.equal(box.overflow,0);
  for(const [height,offset,type] of [[432,0,'resize'],[432,120,'scroll'],[380,170,'scrollend'],[844,170,'resize'],[432,100,'resize']]){
   await page.evaluate(({height,offset,type})=>{Object.assign(testViewport,{height,offsetTop:offset});testViewport.dispatchEvent(new Event(type));},{height,offset,type});
   await settle();box=await bounds();const pan=height<744?offset:0;
   assert.equal(box.top,pan+59);assert.equal(box.bottom,pan+height);
   assert(box.header>=pan+59);assert.equal(box.composer,pan+height);assert(box.following);assert.equal(box.overflow,0);assert.equal(box.scroll,0);assert.equal(box.rootScroll,0);
   assert(await page.evaluate(()=>document.activeElement===keptInput&&keptInput===document.querySelector('textarea')&&keptInput.value==='Черновик сообщения'&&keptInput.selectionStart===2&&keptInput.selectionEnd===7));
  }
  // Scrolling old messages remains local, including while the keyboard pans.
  await page.evaluate(()=>{document.querySelector('.chat-messages').scrollTop=200;testViewport.offsetTop=130;testViewport.dispatchEvent(new Event('scroll'));});
  await settle();assert.equal(await page.locator('.chat-messages').evaluate(el=>el.scrollTop),200);
  const beforeZoom=await page.locator('#app').getAttribute('style');
  const heightBeforeZoom=await page.evaluate(()=>document.documentElement.style.getPropertyValue('--chat-viewport-height'));
  await page.evaluate(()=>{Object.assign(testViewport,{scale:1.5,height:250,offsetTop:200});testViewport.dispatchEvent(new Event('resize'));});
  await settle();assert.equal(await page.evaluate(()=>document.documentElement.style.getPropertyValue('--chat-viewport-height')),heightBeforeZoom);assert.equal(await page.locator('#app').getAttribute('style'),beforeZoom);
  await page.evaluate(()=>{keptInput.blur();Object.assign(testViewport,{scale:1,height:844,offsetTop:130});window.dispatchEvent(new Event('pageshow'));});
  await settle();box=await bounds();assert.equal(box.top,59);assert.equal(box.bottom,844);
  await page.screenshot({path:`artifacts/chat-viewport-${telegram?'telegram':'native'}.png`});
  await page.evaluate(()=>{unbindViewport();document.querySelector('#app').innerHTML='<div style="height:1800px">Другой раздел</div>';document.querySelector('#app').scrollTop=200;testViewport.height=300;testViewport.dispatchEvent(new Event('resize'));});
  await settle();assert.equal(await page.locator('html').evaluate(el=>el.classList.contains('chat-viewport')),false);assert.equal(await page.evaluate(()=>document.documentElement.style.getPropertyValue('--chat-viewport-height')),'');assert.equal(await page.locator('#app').evaluate(el=>el.scrollTop),200);
  await page.close();
 }
 console.log('Chat viewport passed: safe areas, keyboard resize/pan/dismiss, local history scroll, draft/caret, zoom, cleanup, native and Telegram.');
}finally{await browser.close();}
