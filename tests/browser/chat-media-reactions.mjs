import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {readAppStyles} from './fixtures.mjs';
const baselineRevision=process.env.DRG_MEDIA_REACTIONS_BASELINE,baseline=Boolean(baselineRevision),engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const output=process.env.DRG_MEDIA_REACTIONS_REVIEW_OUT||`artifacts/chat-media-reactions/${baseline?'before':'after'}-${engine}`;await mkdir(output,{recursive:true});
const plugins=baseline?[{name:'accepted-source',setup(api){api.onLoad({filter:/web\/modules\/(chat|family-photos)\/index\.js$/},args=>({contents:execFileSync('git',['show',baselineRevision+':'+args.path.slice(process.cwd().length+1)],{encoding:'utf8'}),loader:'js'}));}}]:[];
const bundle=await build({stdin:{contents:`import {inboxController} from './web/modules/notifications/index.js';inboxController.attach=()=>{};export {mountChat,chatController} from './web/modules/chat/index.js';export {modal} from './web/core/modal.js';`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'MediaReactions',write:false,loader:{'.css':'empty'},plugins});
const css=(baseline?await readFile(process.env.DRG_MEDIA_REACTIONS_BASELINE_CSS,'utf8'):await readAppStyles()).replace(/@font-face\s*\{[^}]*\}/g,'');
const browser=await (engine==='webkit'?webkit:chromium).launch({headless:true}),metrics=[];
try{
 for(const language of ['ru','en']){
  const page=await browser.newPage({locale:language==='ru'?'ru-RU':'en-GB',viewport:{width:390,height:844},isMobile:true,hasTouch:true,timezoneId:'Europe/Moscow'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,value:true}));
  await page.route('https://reactions.test/**',route=>{
   if(route.request().url().includes('/photo/'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="720" height="480" viewBox="0 0 720 480"><rect width="720" height="480" fill="#86aec0"/><circle cx="540" cy="110" r="52" fill="#f5dda3"/><path d="M0 400 240 110 500 480H0" fill="#467879"/><path d="M210 480 490 210 720 440V480" fill="#315a67"/></svg>'});
   return route.fulfill({contentType:'text/html',body:'<meta name="viewport" content="width=device-width,initial-scale=1"><html class="app-standalone" data-theme="ocean" data-scheme="dark"><body><main id="app"><div class="workspace chat-open"><main class="content"><section id="view-content"></section></main></div></main></body></html>'});
  });
  await page.goto('https://reactions.test/');await page.clock.install({time:new Date('2026-11-14T15:00:00+03:00')});await page.evaluate(language=>localStorage.setItem('drg-language',language),language);
  await page.addStyleTag({content:css});await page.addStyleTag({content:'.workspace{display:block!important;height:100dvh!important;width:100%}.content{margin:0!important;padding:0!important;height:100%}#view-content{height:100%}#app{position:fixed;inset:0}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{
   const stamp=Math.floor(Date.now()/1000)-10000,me={id:'me',login:'owner',display_name:'Александр'},peer={id:'peer',login:'anna',display_name:'Анна'},members=[me,peer];
   const messages=[{id:1,sender_id:'peer',sender:'anna',body:'Предыдущее сообщение',created:stamp,attachments:[],reactions:[]}],cases=[];
   const emojis=['❤️','👍','🔥','😁','🎉','👏','😮','🤔'];
   for(const count of [1,4])for(const caption of [false,true])for(const mine of [false,true])for(const reactions of [0,1,8]){
    const id=messages.length+1,person=mine?me:peer;
    const attachments=Array.from({length:count},(_,i)=>({id:`photo-${id}-${i}`,kind:'photo',mime:'image/png',name:'Тестовое фото.png',size:1024,url:`https://reactions.test/photo/${id}-${i}`,state:'ready'}));
    const reactionRows=emojis.slice(0,reactions).map((emoji,i)=>({emoji,count:i===0?2:1,people:i===0?[me,peer]:[peer]}));
    const message={id,sender_id:person.id,sender:person.login,sender_profile:person,body:caption?'Снимки с прогулки\nПодпись остаётся внутри сообщения.':'',created:stamp+id*80,edited:stamp+id*80+20,attachments,reactions:reactionRows,reaction_revision:1,revision:1};
    messages.push(message,{id:id+1,sender_id:'peer',sender:'anna',sender_profile:peer,body:'Следующее сообщение',created:stamp+(id+1)*80,attachments:[],reactions:[]});cases.push({id,count,caption,mine,reactions});
   }
   for(const mixed of [false,true]){
    const id=messages.length+1,photo={id:'extra-photo-'+id,kind:'photo',name:'Тестовое фото.png',mime:'image/png',size:1024,url:'https://reactions.test/photo/'+id,state:'ready'};
    const attachments=[photo,...(mixed?[{id:'document-'+id,kind:'file',name:'Инструкция.txt',mime:'text/plain',size:17,url:'https://reactions.test/document/'+id}]:[])];
    messages.push({id,sender_id:'me',sender:'owner',sender_profile:me,body:'',created:stamp+id*80,edited:stamp+id*80+20,attachments,reactions:[{emoji:'❤️',count:2,people:[me,peer]}],revision:1,reaction_revision:1,...(!mixed?{reply:{id:1,sender:'anna',sender_profile:peer,body:'Предыдущее сообщение'}}:{})},
      {id:id+1,sender_id:'peer',sender:'anna',sender_profile:peer,body:'Следующее сообщение',created:stamp+(id+1)*80,attachments:[],reactions:[]});
    cases.push({id,count:1,caption:false,mine:true,reactions:1,mixed,reply:!mixed});
   }
   window.mediaFixture={messages,cases,requests:[],me,peer};
   const rooms=[{id:'direct',name:'Анна',kind:'friends'},{id:'family',name:'Тестовая группа',kind:'family'}].map(r=>({...r,members,pinned:false,favorite:false,unread:0,last_message:messages.at(-1)}));
   const ctx={state:{me:{...me,settings:{}}},moduleEnabled:()=>true,navigate(){},modal:MediaReactions.modal,toast(text){mediaFixture.lastToast=text;},async api(path,method='GET',body){
    if(path==='/chat/rooms')return {items:structuredClone(rooms)};
    if(path==='/chat/typing')return {rooms:{},server_time:Date.now()/1000};
    if(path==='/family-photos/groups'||path==='/family-photos/groups?previews=1')return {items:[{id:'-1001',title:'Тестовый архив',can_send:true,latest_message_id:messages.at(-1).id,last_message:null}],status:{can_send:true,worker_online:true,bot_messaging:true}};
    if(path.endsWith('/reactions')&&method==='POST'){
     mediaFixture.requests.push({path,body});const id=Number(path.split('/').at(-2)),message=messages.find(m=>m.id===id),group=message.reactions.find(r=>r.emoji===body.emoji);
     if(group.people.some(p=>p.id==='me'))group.people=group.people.filter(p=>p.id!=='me');else group.people.push(me);group.count=group.people.length;message.reaction_revision++;
     return {message:structuredClone(message),reactions:structuredClone(message.reactions)};
    }
    if(path.includes('/messages'))return {items:path.includes('after=')?[]:structuredClone(messages).map(m=>path.startsWith('/family-photos/')?{...m,group_id:'-1001',site_actor_id:m.sender_id==='me'?'me':null,reply_to:m.reply?.id,site_profile:m.sender_profile,text:m.body,media:m.attachments,site_reactions:m.reactions,reactions:[]}:m),more:false,reads:{},server_time:Date.now()/1000,edit_cursor:0};
    if(path.endsWith('/site-sends'))return {items:[{id:'pending-photo',state:'queued',created:stamp+messages.length*80,text:'Отправляется фото',media:[{id:'pending-file',kind:'photo',url:'https://reactions.test/photo/pending',name:'Тестовое фото.png',state:'ready'}]}]};if(path.endsWith('/outbox'))return {items:[]};if(path.endsWith('/read'))return {ok:true};if(path==='/me')return ctx.state.me;
    throw Error('Unexpected API '+path);
   }};
   await MediaReactions.mountChat(document.querySelector('#view-content'),ctx);
  });
  for(const sid of ['direct','family','telegram:-1001']){
   await page.locator(`[data-room="${sid}"]`).click();const attr=sid.startsWith('telegram:')?'data-fp-message-id':'data-message-id';
   await expect(page.locator(`[${attr}] .chat-media-album`)).toHaveCount(26);
   await expect(page.locator(`[${attr}] .chat-message-edited`).first()).toHaveText(language==='ru'?'изменено':'modified');
   await page.locator(`[${attr}] img`).evaluateAll(images=>images.forEach(image=>image.loading='eager'));
   await page.waitForFunction(()=>[...document.querySelectorAll('.chat-media-album img')].every(image=>image.complete&&image.naturalWidth));
   for(const width of [320,390,760,1280])for(const scheme of ['light','dark']){
    await page.setViewportSize({width,height:844});await page.evaluate(scheme=>document.documentElement.dataset.scheme=scheme,scheme);
    const data=await page.evaluate(({attr,baseline})=>mediaFixture.cases.map(c=>{
     const article=document.querySelector(`[${attr}="${c.id}"]`),bubble=article.querySelector('.chat-media-bubble'),reactions=article.querySelector('.chat-reactions'),next=document.querySelector(`[${attr}="${c.id+1}"]`),footer=article.querySelector('.chat-message-footer');
     const rect=el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
     return {...c,article:rect(article),bubble:bubble&&rect(bubble),strip:reactions?rect(reactions):{height:0},next:rect(next),footer:rect(footer),footers:article.querySelectorAll('.chat-message-footer').length,background:getComputedStyle(article).backgroundColor,reactionRows:new Set([...(reactions?.children||[])].map(el=>Math.round(el.getBoundingClientRect().top))).size,metaHidden:getComputedStyle(article.querySelector('.chat-message-meta')||article).display==='none'};
    }),{attr,baseline});
    for(const m of data){
     assert(m.article.left>=-1&&m.article.right<=width+1,'message stays within the viewport');
     assert(m.next.top>=m.article.bottom-1,'reactions never overlap the next message');
     if(!baseline){
      assert.equal(m.footers,1,'timestamp appears once');
      assert.equal(m.background,'rgba(0, 0, 0, 0)','no bubble background behind media reactions');assert(m.bubble,'visual content has a separate bubble');
      assert(m.footer.top>=m.bubble.top-1&&m.footer.bottom<=m.bubble.bottom+1,'timestamp remains inside the content');
      if(m.strip.height)assert(m.strip.top>=m.bubble.bottom+4,'reactions sit below the content background');
      if(m.reactions===0)assert.equal(m.strip.height,0,'empty reactions reserve no row');
      if(m.reactions===8&&width<=390)assert(m.reactionRows>1,'many reactions wrap');
      if(sid==='direct'&&!m.mine)assert(m.metaHidden,'direct conversations keep sender labels hidden');
     }
    }
    if(!baseline&&sid.startsWith('telegram:')){
     const pending=page.locator('[data-fp-pending="pending-photo"]');await expect(pending).toBeVisible();
     const layout=await pending.evaluate(el=>{const r=el.getBoundingClientRect(),b=el.querySelector('.chat-media-bubble').getBoundingClientRect();return {right:r.right,left:r.left,background:getComputedStyle(el).backgroundColor,bodyBottom:b.bottom,bottom:r.bottom};});
     assert(layout.left>=-1&&layout.right<=width+1&&layout.bodyBottom<=layout.bottom+1,'pending media keeps its content inside the message');assert.equal(layout.background,'rgba(0, 0, 0, 0)');
    }
    metrics.push({language,sid,width,scheme,cases:data});
    if(language==='ru'&&width===390&&sid==='telegram:-1001'){
     for(const caption of [false,true]){
      const target=data.find(m=>m.count===4&&m.caption===caption&&!m.mine&&m.reactions===1);
      await page.locator(`[${attr}="${target.id}"]`).evaluate(el=>el.scrollIntoView({block:'center'}));
      await page.screenshot({path:`${output}/telegram-${caption?'caption':'album'}-${scheme}.png`,animations:'disabled'});
     }
    }
   }
   if(!baseline){
    await page.setViewportSize({width:390,height:844});
    const target=await page.evaluate(()=>mediaFixture.cases.find(c=>c.count===4&&c.caption&&!c.mine&&c.reactions===1));
    const article=page.locator(`[${attr}="${target.id}"]`),button=article.locator('[data-reaction-toggle]').first();
    await article.evaluate(el=>el.scrollIntoView({block:'center'}));await expect(button).toHaveAttribute('aria-pressed','true');
    await button.click();await expect(button).toHaveAttribute('aria-pressed','false');await button.click();await expect(button).toHaveAttribute('aria-pressed','true');
    await article.locator('[data-reaction-people]').first().click();await expect(page.locator('.reaction-person-list')).toContainText('Анна');await page.locator('.modal-close').click();
    assert(await page.evaluate(()=>mediaFixture.requests.slice(-2).every(r=>r.body.emoji==='❤️')),'same reaction payload after moving the strip');
   }
   if((await page.viewportSize()).width<=760)await page.locator(sid.startsWith('telegram:')?'[data-fp-back]':'[data-chat-back]').click();
  }
  assert.deepEqual(errors,[]);await page.evaluate(()=>MediaReactions.chatController.destroy());await page.close();
 }
 await writeFile(output+'/metrics.json',JSON.stringify(metrics,null,2));
 console.log(`Media reactions ${baseline?'baseline':'fixed'} ${engine}: 2 languages × 3 chat kinds × 4 widths × 2 themes × 26 cases; ${baseline?'baseline captured':'toggle/people preserved'}`);
}finally{await browser.close();}
