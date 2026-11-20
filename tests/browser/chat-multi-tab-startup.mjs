import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({stdin:{contents:`
  import {inboxController} from './web/modules/notifications/index.js';
  inboxController.attach=()=>{};
  export {mountChat,chatController} from './web/modules/chat/index.js';
`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'SpaceTabs',write:false,loader:{'.css':'empty'}});
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const browser=await (engine==='webkit'?webkit:chromium).launch();
const fixture=async (page,{group,slow,denied=false,leading=0,background=false})=>{
 await page.route('http://space-tabs.test/',route=>route.fulfill({contentType:'text/html',body:'<main id="app"></main>'}));
 await page.goto('http://space-tabs.test/');
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(({group,slow,denied,leading,background})=>{
  const gid='-1001',start=performance.now(),events={api:{},paints:[]};
  if(background){
    let hidden=true;
    Object.defineProperty(document,'hidden',{get:()=>hidden,configurable:true});
    window.showTab=()=>{hidden=false;document.dispatchEvent(new Event('visibilitychange'));};
  }
  const archive={id:gid,title:'Family Archive',can_send:true,history_complete:true,latest_message_id:1,last_message:null};
  const status={bot_messaging:true,bot_reactions:false,worker_online:true};
  const room={id:'native',name:'Native chat',kind:'family',members:[],unread:0};
  const message={id:1,created:Math.floor(Date.now()/1000),sender:'Synthetic',text:'Synthetic history',content_type:'text',media:[]};
  const host=document.querySelector('#app');
  new MutationObserver(()=>{
    const names=[...host.querySelectorAll('.chat-room-title strong')].map(e=>e.textContent);
    if(names.length&&!events.paints.some(p=>JSON.stringify(p.names)===JSON.stringify(names))){
      events.paints.push({ms:performance.now()-start,names});
    }
    if(!events.history&&host.querySelector('.fp-chat-message'))events.history=performance.now()-start;
  }).observe(host,{childList:true,subtree:true});
  const respond=async(path,value,delay)=>{
    events.api[path]??={started:performance.now()-start};
    if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
    events.api[path].finished=performance.now()-start;
    return value;
  };
  const ctx={
    state:{me:{id:'synthetic',role:'owner',login:'synthetic',settings:{}},spaces:[],chatSpace:group?'telegram:'+gid:null},
    moduleEnabled:name=>name==='family_photos',navigate(){},toast(){},push:{state:async()=>({enabled:true})},
    api(path){
      if(path==='/chat/rooms')return respond(path,{items:[room]},80);
      if(path==='/family-photos/groups?previews=1')return respond(path,{items:[archive],status},slow?1600:100);
      if(path==='/family-photos/groups')return respond(path,{items:denied?[]:[archive],status},400);
      if(path.endsWith('/messages'))return respond(path,{items:[message],more:false},leading||500);
      if(path.endsWith('/site-sends'))return respond(path,{items:[]},1800);
      if(path.endsWith('/read'))return respond(path,{},0);
      if(path==='/chat/typing')return respond(path,{rooms:{},server_time:Date.now()/1000},0);
      throw Error('Unexpected request '+path);
    }
  };
  window.measurement=events;
  window.mounted=SpaceTabs.mountChat(host,ctx,{listOnly:!group});
 },{group,slow,denied,leading,background});
};
try{
 for(const slow of [false,true]){
  const context=await browser.newContext({viewport:{width:1280,height:800}});
  try{
    const pages=await Promise.all(Array.from({length:6},()=>context.newPage()));
    await Promise.all(pages.map((page,i)=>fixture(page,{group:i<3,slow})));
    await Promise.all(pages.map((page,i)=>page.waitForFunction(group=>{
       const m=window.measurement;
       return m?.paints.some(p=>p.names.includes('Family Archive'))&&(!group||m.history&&m.api['/family-photos/groups/-1001/site-sends']?.finished);
    },i<3,{timeout:12000})));
    const samples=await Promise.all(pages.map(page=>page.evaluate(()=>window.measurement)));
    const lists=samples.map(m=>Math.round(m.paints[0].ms));
    const histories=samples.slice(0,3).map(m=>Math.round(m.history));
    console.log(JSON.stringify({engine,slow,lists,histories,api:samples[0].api}));
    assert(samples.every(m=>m.paints.every(p=>p.names.length===2)),'native and family rooms appear in the same first paint');
    for(const m of samples.slice(0,3)){
      assert(m.api['/family-photos/groups/-1001/messages'].started<m.api['/family-photos/groups'].finished,'overlap metadata and initial history');
      assert(m.history<m.api['/family-photos/groups/-1001/site-sends'].finished,'history paints before send queue');
      if(slow)assert(m.history<m.paints[0].ms,'history can appear while the conversation list is still loading');
    }
  }finally{await context.close();}
 }
 {
  const page=await browser.newPage();
  await fixture(page,{group:true,slow:false,denied:true,leading:100});
  await page.waitForFunction(()=>window.measurement?.api['/family-photos/groups']?.finished&&document.querySelector('[data-fp-chat-status]')?.textContent);
  assert.equal(await page.locator('.fp-chat-message').count(),0,'history must not be shown if group access was denied');
  await page.close();
 }
 {
  const page=await browser.newPage();
  await fixture(page,{group:false,slow:true,background:true});
  await page.evaluate(()=>mounted);
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('drg-family-read',{detail:{group_id:'-1001'}})));
  assert.equal(await page.locator('.chat-room').count(),0,'a read event cannot reveal a partial background list');
  await page.evaluate(()=>showTab());
  await page.waitForFunction(()=>measurement.paints.length>0);
  assert.equal(await page.locator('.chat-room').count(),2,'both groups appear together when a background tab becomes visible');
  await page.close();
 }
}finally{await browser.close();}
