import {readAppStyles} from './fixtures.mjs';
// Real UI modules with in-memory API replies; no live Telegram/account traffic.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({stdin:{contents:"export * from './web/modules/family-photos/index.js';export * from './web/modules/chat/index.js';",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'History',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles();
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
try{
 for(const mode of ['gallery','telegram','native']){
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:mode==='native'?1100:390,height:844}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://history.test/**',r=>r.fulfill({body:'<div id="scroll"><main id="fixture"></main></div>',contentType:'text/html'}));
  await page.goto('http://history.test/');
  await page.addStyleTag({content:css+'html,body{margin:0;height:100%;overflow:hidden}#scroll{height:100dvh;overflow:auto}#fixture{padding:12px}#fixture.chat-shell{padding:0;height:100dvh;max-width:none}.chat-shell .chat-main{min-height:0}.page-edge{width:100%}'});
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async mode=>{
   const state=window.fixtureState={olderCalls:[],hold:true,fail:false,toasts:[],active:0,maxActive:0};
   const now=Math.floor(Date.now()/1000),me={id:'owner',login:'owner',name:'Owner',role:'owner',settings:{}};
   const group={id:'-1001',title:'Семейная группа',kind:'supergroup',history_complete:true,media_pending:0};
   const room={id:'room',name:'Друг',kind:'friends',members:[me,{id:'friend',login:'friend',name:'Друг'}],unread:0};
   const rows=Array.from({length:150},(_,i)=>({id:i+1,created:now-200+i,sender:'friend',sender_id:'friend',text:'Сообщение '+(i+1),body:'Сообщение '+(i+1),media:[],attachments:[]}));
   const files=Array.from({length:72},(_,i)=>({id:'f'+(72-i),group_id:group.id,message_id:72-i,created:now-i,kind:'video',state:'paused',name:'clip.mp4',sender:'Анна'}));
   const ctx={state:{me,chatSpace:mode==='native'?'room':'telegram:-1001'},moduleEnabled:()=>false,navigate(){},toast(text){state.toasts.push(text);},
    async api(path,method='GET'){
     const url=new URL(path,'http://history.test'),before=Number(url.searchParams.get('before'));
     if(path==='/chat/rooms')return {items:[room]};
     if(path==='/chat/typing')return {rooms:{},server_time:now};
     if(path==='/notifications')return {items:[],unread:0};
     if(path==='/me')throw Error('Offline test transport');
     if(path==='/family-photos/groups')return {items:[group],status:{worker_online:true,can_send:false}};
     if(path.endsWith('/sync'))return {state:'idle',server_time:now,history_complete:true,pending:0,batch:{},cleanup:{}};
     if(path.endsWith('/outbox'))return {items:[]};
     if(path.endsWith('/read'))return {ok:true,reads:{}};
     if(url.pathname.endsWith('/messages')||url.pathname.endsWith('/media')){
      const media=url.pathname.endsWith('/media');
      if(url.searchParams.has('after'))return {items:[],edits:[],more:false,edit_more:false,server_time:now};
      if(before){
       state.olderCalls.push(before);state.active++;state.maxActive=Math.max(state.maxActive,state.active);
       try{
        if(state.hold)await new Promise(resolve=>state.release=resolve);
        if(state.fail)throw Error('Временная ошибка');
       }finally{state.active--;}
      }
      const source=(media?files:rows).filter(r=>!before||(media?r.message_id:r.id)<before);
      return {items:structuredClone(media?source.slice(0,24):source.slice(-50)),more:source.length>(media?24:50),server_time:now,edit_cursor:0};
     }
     return {};
    }};
   window.testCtx=ctx;
   const host=document.querySelector('#fixture');
   if(mode==='gallery')await History.mountFamilyPhotos(host,ctx);
   else if(mode==='telegram'){host.classList.add('chat-shell');window.mounted=History.mountTelegramChat(host,ctx,group.id,()=>{});}
   else await History.mountChat(host,ctx,{listOnly:false});
  },mode);
  const selector=mode==='gallery'?'.fp-tile':mode==='telegram'?'[data-fp-message-id]':'[data-message-id]';
  await page.locator(selector).first().waitFor();
  await pause(150);
  assert.deepEqual(await page.evaluate(()=>fixtureState.olderCalls),[],'opening a full page does not fetch older history');
  assert.equal(await page.locator('[data-fp-more],[data-fp-older],#older-messages').count(),0);
  const scroll=mode==='gallery'?'#scroll':'.chat-messages';
  await page.evaluate(({scroll,mode,selector})=>{
   const list=document.querySelector(scroll);list.scrollTop=mode==='gallery'?list.scrollHeight:0;
   const rect=list.getBoundingClientRect(),anchor=[...document.querySelectorAll(selector)].find(el=>el.getBoundingClientRect().bottom>rect.top);
   window.anchor={node:anchor,offset:anchor.getBoundingClientRect().top-rect.top};
  },{scroll,mode,selector});
  await page.waitForFunction(()=>fixtureState.olderCalls.length===1);
  for(let i=0;i<5;i++)await page.locator(scroll).dispatchEvent('scroll');
  assert.equal(await page.evaluate(()=>fixtureState.olderCalls.length),1,'one pending request despite repeated scrolling');
  await page.evaluate(()=>{fixtureState.hold=false;fixtureState.release();});
  await page.waitForFunction(({selector,mode})=>document.querySelectorAll(selector).length===(mode==='gallery'?48:100),{selector,mode});
  assert.equal(await page.evaluate(()=>fixtureState.maxActive),1);
  if(mode!=='gallery')assert(Math.abs(await page.evaluate(scroll=>anchor.node.getBoundingClientRect().top-document.querySelector(scroll).getBoundingClientRect().top-anchor.offset,scroll))<2,'prepending history preserves the visible message');
  // A transient failure does not hammer the API; scrolling retries the same cursor.
  await page.evaluate(({scroll,mode})=>{fixtureState.fail=true;const list=document.querySelector(scroll);list.scrollTop=mode==='gallery'?list.scrollHeight:0;},{scroll,mode});
  await page.waitForFunction(()=>fixtureState.toasts.some(t=>t==='Временная ошибка')||document.querySelector('.fp-chat-status')?.textContent==='Временная ошибка');
  const failedCalls=await page.evaluate(()=>fixtureState.olderCalls.length);await pause(200);
  assert.equal(await page.evaluate(()=>fixtureState.olderCalls.length),failedCalls,'failed pages do not spin');
  await page.evaluate(scroll=>{fixtureState.fail=false;document.querySelector(scroll).dispatchEvent(new Event('scroll'));},scroll);
  await page.waitForFunction(({selector,mode})=>document.querySelectorAll(selector).length===(mode==='gallery'?72:150),{selector,mode});
  assert.equal(await page.evaluate(()=>fixtureState.maxActive),1);
  const finished=await page.evaluate(()=>fixtureState.olderCalls.length);
  for(let i=0;i<4;i++)await page.locator(scroll).dispatchEvent('scroll');
  await pause(100);assert.equal(await page.evaluate(()=>fixtureState.olderCalls.length),finished,'no requests after the end of history');
  await page.evaluate(mode=>{if(mode==='gallery')History.familyPhotosController.destroy();else if(mode==='native')History.chatController.destroy();else mounted.destroy();},mode);
  assert.deepEqual(errors,[]);
  await page.close();
 }
 console.log('Automatic history passed: nested gallery scroll, native/Telegram top edges, single-flight requests, stable anchors, failure/retry, final page and cleanup.');
}finally{await browser.close();}
