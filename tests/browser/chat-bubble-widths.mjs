import {readAppStyles,openTestOrigin} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';

const engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const script=await build({stdin:{contents:"export {albumMarkup,fitAlbums} from './web/modules/chat/media.js';",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'Media',write:false});
const browser=await(engine==='webkit'?webkit:chromium).launch({headless:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:430,height:932},isMobile:true,hasTouch:true});
 const portrait=await readFile('tests/fixtures/chat-portrait.png');
 await page.route('https://widths.test/**',route=>route.fulfill({contentType:'image/png',body:portrait}));
 await openTestOrigin(page);await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><html data-scheme="dark"><main class="chat-shell"><div class="chat-messages" style="height:900px"></div></main></html>');
 await page.addStyleTag({content:(await readAppStyles()).replace(/@font-face\s*\{[^}]*\}/g,'')+'body{margin:0}.chat-shell{display:block;width:100%}'});
 await page.addScriptTag({content:script.outputFiles[0].text});
 await page.evaluate(()=>{
  const host=document.querySelector('.chat-messages'),file=id=>({id,kind:'photo',url:'https://widths.test/'+id,state:'ready'});
  for(const source of ['native','telegram'])for(const side of ['incoming','outgoing','direct'])for(const kind of ['text','portrait','album']){
   const mine=side==='outgoing',direct=side==='direct';
   const content=kind==='text'?'<p class="chat-message-text">Длинное сообщение: '+('Сохраняем свободное поле и читаемый текст. '.repeat(7))+'<span class="chat-message-footer">22:28 ✓</span></p>':Media.albumMarkup(Array.from({length:kind==='album'?5:1},(_,i)=>file(source+side+kind+i)),'data-chat-media-id');
   host.insertAdjacentHTML('beforeend',`<article data-case="${source}-${side}-${kind}" class="chat-message group-end ${mine?'mine':''} ${direct?'is-direct-message':''} ${source==='telegram'?'fp-chat-message':''} ${kind==='text'?'':'has-visual-media media-only'}">${!mine&&!direct?'<div class="chat-message-avatar">А</div>':''}${content}</article>`);
  }
  host.querySelectorAll('img').forEach(image=>image.loading='eager');
  window.refit=()=>Media.fitAlbums(host);refit();
 });
 await page.waitForFunction(()=>[...document.images].every(image=>image.complete&&image.naturalWidth));
 for(const width of [320,390,430,760,1440]){
  await page.setViewportSize({width,height:932});await page.evaluate(()=>refit());
  const sizes=await page.evaluate(()=>{
   const host=document.querySelector('.chat-messages'),bounds=host.getBoundingClientRect();
   return [...host.querySelectorAll('.chat-message')].map(message=>{
    const r=message.getBoundingClientRect();return {id:message.dataset.case,width:r.width,left:r.left-bounds.left,right:bounds.right-r.right};
   });
  });
  for(const r of sizes){
   assert(r.left>=0&&r.right>=0,`${width}: ${r.id} stays in history`);
   if(width<=760){
    const outgoing=r.id.includes('outgoing');
    assert((outgoing?r.left:r.right)>=40,`${width}: ${r.id} keeps the opposite gutter`);
    if(!r.id.endsWith('text'))assert(r.width<=width*.74,`${width}: ${r.id} media stays narrower than text`);
   }
   const other=sizes.find(item=>item.id===r.id.replace('telegram','native'));
   assert(Math.abs(r.width-other.width)<1,'native and Telegram geometry match');
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(width===430){
   await page.evaluate(()=>document.querySelector('[data-case="telegram-incoming-album"]').scrollIntoView({block:'center'}));
   await mkdir('artifacts',{recursive:true});await page.screenshot({path:`artifacts/chat-bubble-widths-${engine}.png`});
  }
 }
 console.log('Bubble widths passed: native/Telegram, incoming/outgoing/direct, long text, portrait and albums, 320–1440px');
}finally{await browser.close();}
