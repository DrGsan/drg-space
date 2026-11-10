import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {touchSequence,touchEvent} from './touch-gesture.mjs';
const bundle=await build({entryPoints:['web/core/mobile-back.js'],bundle:true,format:'iife',globalName:'MobileBack',write:false});
const css=await readFile('app/static/assets/app.css','utf8'),engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const browser=await (engine==='webkit'?webkit:chromium).launch({headless:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844},hasTouch:true});
 await page.setContent(`<!doctype html><style>${css}#pad{position:absolute;inset:80px 0 120px;background:var(--bg)}#form,#editor,#horizontal{position:absolute;left:0;top:180px;width:200px}#horizontal{overflow-x:auto}#horizontal>div{width:600px}</style><div id="app"></div>`);
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{document.documentElement.classList.add('app-standalone');Object.defineProperty(navigator,'standalone',{configurable:true,value:true});window.originalViewport=window.visualViewport;});
 async function fixture(kind='chat'){
  await page.evaluate(kind=>{
   window.unbind?.();window.getSelection().removeAllRanges();document.querySelectorAll('.modal-overlay').forEach(el=>el.remove());
   Object.defineProperty(window,'visualViewport',{configurable:true,value:originalViewport});
   const root=document.querySelector('#app');
   const cases={chat:'<div class="chat-shell room-open"><button data-chat-back>Назад</button><div id="pad">Проведите от левого края</div></div>',telegram:'<div class="chat-shell room-open"><button data-fp-back>Назад</button><div id="pad">Архив</div></div>',notes:'<div class="notes-shell"><button id="notes-main-menu">Заметки</button><div id="pad">Заметка</div></div>',tasks:'<div class="tasks-shell has-properties"><button data-close-task-properties>Назад</button><div id="pad">Дело</div></div>',secondary:'<div class="workspace" data-current-view="files"><button class="mobile-secondary-back">Назад</button><div id="pad">Настройки</div></div>'};
   root.innerHTML=cases[kind];window.backCount=0;root.querySelector('button').onclick=()=>backCount++;
   window.unbind=MobileBack.bindMobileBack(root);
  },kind);
 }
 const count=()=>page.evaluate(()=>backCount),swipe=(points=[[8,300],[45,303],[110,306]],options={})=>touchSequence(page,points,{selector:'#pad',...options});
 for(const kind of ['chat','telegram','notes','tasks','secondary']){await fixture(kind);await swipe();assert.equal(await count(),1,kind);assert.equal(await page.locator('.mobile-back-hint').count(),0);}
 for(const [name,points,options] of [
  ['middle',[[90,300],[200,300]],{}],['short',[[8,300],[48,301]],{}],['left',[[8,300],[1,300]],{}],
  ['vertical',[[8,300],[12,350],[110,350]],{}],['diagonal',[[8,300],[110,400]],{}],['cancel',[[8,300],[110,300]],{end:'touchcancel'}],['pinch',[[8,300],[110,300]],{fingers:2}],
 ]){await fixture();await swipe(points,options);assert.equal(await count(),0,name);assert.equal(await page.locator('.mobile-back-hint').count(),0,name);}
 for(const [name,html] of [['input','<input id="form">'],['editor','<div id="editor" contenteditable="true">Текст</div>'],['horizontal','<div id="horizontal"><div>Лента</div></div>']]){
  await fixture();await page.locator('#pad').evaluate((el,html)=>el.innerHTML=html,html);await swipe(undefined,{selector:'#'+({input:'form',editor:'editor',horizontal:'horizontal'}[name])});assert.equal(await count(),0,name);
 }
 await fixture();await page.evaluate(()=>{const input=document.createElement('input');document.querySelector('#pad').append(input);input.focus();const back=document.querySelector('[data-chat-back]');back.onclick=()=>{backCount++;window.focusedBack=document.activeElement===back;};});await swipe();assert.equal(await count(),1);assert(await page.evaluate(()=>focusedBack),'blur the editor before running back/save');
 await fixture();await page.evaluate(()=>window.getSelection().selectAllChildren(document.querySelector('#pad')));await swipe();assert.equal(await count(),0,'selection');
 for(const role of ['dialog','menu']){await fixture();await page.evaluate(role=>{const el=document.createElement('div');el.className='modal-overlay';el.setAttribute('role',role);document.body.append(el);},role);await swipe();assert.equal(await count(),0,role);}
 await fixture();await swipe([[8,300],[45,302]],{end:null});await page.waitForTimeout(1250);await touchEvent(page,'touchend',[110,304]);assert.equal(await count(),0,'long press');
 await fixture();await page.evaluate(()=>{window.ghostClicks=0;document.querySelector('#pad').onclick=()=>ghostClicks++;});await swipe();await page.locator('#pad').dispatchEvent('click',{clientX:110,clientY:306});assert.equal(await page.evaluate(()=>ghostClicks),0,'no click through after swipe');

 for(const change of ['dialog','replace','resize','zoom','multitouch','cleanup']){
  await fixture();await swipe([[8,300],[45,302]],{end:null});assert.equal(await page.locator('.mobile-back-hint').count(),1);
  await page.evaluate(change=>{
   if(change==='dialog'){const el=document.createElement('div');el.className='modal-overlay';document.body.append(el);}
   if(change==='replace'){const button=document.querySelector('[data-chat-back]');button.replaceWith(button.cloneNode(true));}
   if(change==='resize')window.dispatchEvent(new Event('resize'));
   if(change==='zoom')Object.defineProperty(window,'visualViewport',{configurable:true,value:{scale:2}});
   if(change==='cleanup')unbind();
  },change);
  if(change==='multitouch')await touchEvent(page,'touchmove',[70,302],{selector:'#pad',fingers:2});
  await touchEvent(page,'touchend',[110,304],{selector:'#pad'});
  assert.equal(await count(),0,change);assert.equal(await page.locator('.mobile-back-hint').count(),0,change);
 }
 await fixture();await swipe([[8,300],[45,302]],{end:null});await mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/mobile-back-gesture.png'});await page.evaluate(()=>unbind());
 await fixture();await page.setViewportSize({width:1440,height:900});await swipe();assert.equal(await count(),0,'desktop');await page.setViewportSize({width:390,height:844});
 await fixture();await page.evaluate(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:false}));await swipe();assert.equal(await count(),0,'ordinary browser');await page.evaluate(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:true}));
 // Trusted touch input checks browser scrolling/gesture dispatch, not just the
 // recognizer's synthetic event model.
 if(engine==='chromium'){
  await fixture();const cdp=await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:8,y:300}]});
  for(const x of [24,45,75,110])await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:302}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});assert.equal(await count(),1,'trusted touch');await cdp.detach();
 }
 console.log('Mobile back passed: native/Telegram/notes/tasks/secondary, thresholds, scroll/edit/selection/zoom/modal guards, cancellation, cleanup and desktop isolation.');
}finally{await browser.close();}
