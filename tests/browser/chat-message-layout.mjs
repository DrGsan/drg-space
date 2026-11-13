import {readAppStyles} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {build} from 'esbuild';
import {mkdir} from 'node:fs/promises';

const engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const bundle=await build({stdin:{contents:`import {inboxController} from './web/modules/notifications/index.js';inboxController.attach=()=>{};export {mountChat,chatController} from './web/modules/chat/index.js';`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'ChatLayout',write:false,loader:{'.css':'empty'}});
const css=(await readAppStyles()).replace(/@font-face\s*\{[^}]*\}/g,'');
const browser=await(engine==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844},isMobile:true,hasTouch:true,timezoneId:'Europe/Moscow'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true}));
  await page.route('https://chat-layout.test/**',r=>r.fulfill({contentType:'text/html; charset=utf-8',body:'<meta name="viewport" content="width=device-width, initial-scale=1"><html class="app-standalone" data-scheme="dark"><body><main id="app"><div class="workspace chat-open"><main class="content"><section id="view-content"></section></main></div></main></body></html>'}));
  await page.goto('https://chat-layout.test/');
  await page.clock.install({time:new Date('2026-10-21T20:14:00+03:00')});
  await page.addStyleTag({content:css});
  await page.addStyleTag({content:'.workspace{display:block!important;height:100dvh!important;width:100%}.content{margin:0!important;padding:0!important;height:100%}#view-content{height:100%}#app{position:fixed;inset:0}'});
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{
    const stamp=Date.now()/1000,members=[{id:'me',login:'Я'},{id:'peer',login:'Анна'}];
    const bodies=['Прям новый надо?\nИли с пробегом по Японии?','С пробегом','С минимальным)','Ахахах','Ну короче хотя бы в районе какой суммы','Спасибо','Ок','Скину ему телефон твой','Передал, свяжется с тобой'];
    window.layoutFixture={messages:bodies.map((body,i)=>({id:i+1,sender_id:i<4?'peer':i===4?'me':'peer',sender:i<4?'Анна':i===4?'Я':'Анна',body,created:stamp-86400+(i<6?i*20:86400+(i-6)*20)+(i===8?600:0),attachments:[]}))};
    const rooms=[{id:'direct',name:'Анна',kind:'friends'},{id:'family',name:'Семейная группа',kind:'family'},...['week','month','year'].map(id=>({id,name:id,kind:'family'}))].map((r,i)=>({...r,members,pinned:false,favorite:false,unread:0,last_message:{created:[stamp,stamp-86400,stamp-3*86400,stamp-20*86400,new Date('2025-08-15T12:00:00+03:00').getTime()/1000][i],sender:'Анна',body:'Последнее сообщение'}}));
    const ctx={state:{me:{id:'me',login:'Я',settings:{}}},moduleEnabled:()=>true,navigate(){},toast(){},async api(path,method='GET'){
      if(path==='/chat/rooms')return {items:structuredClone(rooms)};
      if(path==='/chat/typing')return {rooms:{},server_time:Date.now()/1000};
      if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Архивная группа',can_send:false}],status:{can_send:false,worker_online:true}};
      if(path.includes('/messages'))return {items:path.includes('after=')?[]:structuredClone(layoutFixture.messages).map(m=>path.startsWith('/family-photos/')?{...m,text:m.body,media:[]}:m),more:false,reads:{},server_time:Date.now()/1000,edit_cursor:0};
      if(path.endsWith('/outbox'))return {items:[]};
      if(path.endsWith('/read'))return {ok:true};
      if(path==='/me')return ctx.state.me;
      throw Error('Unexpected API '+path);
    }};
    await ChatLayout.mountChat(document.querySelector('#view-content'),ctx);
  });
  await expect(page.locator('[data-room=direct] time')).toHaveText('20:14');
  await expect(page.locator('[data-room=family] time')).toHaveText('вт');
  await expect(page.locator('[data-room=week] time')).toHaveText('вс');
  await expect(page.locator('[data-room=month] time')).toHaveText('01.10');
  await expect(page.locator('[data-room=year] time')).toHaveText('15.08.25');
  const styles=[];
  for(const width of [390,320,1440])for(const scheme of ['dark','light'])for(const sid of ['direct','family','telegram:-1001']){
    await page.setViewportSize({width,height:844});
    await page.locator('html').evaluate((el,scheme)=>el.dataset.scheme=scheme,scheme);
    await page.locator(`[data-room="${sid}"]`).click();
    const prefix=sid.startsWith('telegram:')?'data-fp-message-id':'data-message-id';
    const message=id=>page.locator(`[${prefix}="${id}"]`);
    await expect(message(9)).toBeVisible();
    const geometry=await page.evaluate(prefix=>{
      const messages=[...document.querySelectorAll('['+prefix+']')];
      return messages.map(el=>{
        const r=el.getBoundingClientRect(),p=el.querySelector('.chat-message-text'),f=el.querySelector('.chat-message-footer'),fr=f.getBoundingClientRect();
        const text=document.createRange();text.setStart(p.firstChild,0);text.setEnd(p.firstChild,p.firstChild.textContent.length);
        const rects=[...text.getClientRects()],last=rects.at(-1),style=getComputedStyle(el);
        return {id:Number(el.getAttribute(prefix)),top:r.top,bottom:r.bottom,x:r.x,right:r.right,width:r.width,footer:{x:fr.x,y:fr.y,bottom:fr.bottom,right:fr.right},last:{x:last.x,y:last.y,bottom:last.bottom,right:last.right},grouped:el.classList.contains('grouped'),end:el.classList.contains('group-end'),padding:style.padding,font:getComputedStyle(p).fontSize};
      });
    },prefix);
    for(const m of geometry){
      assert(m.x>=0&&m.right<=width+1,'bubble stays within viewport');
      assert(m.footer.x>=m.x&&m.footer.right<=m.right+1,'timestamp stays within bubble');
      assert(m.footer.bottom<=m.bottom+1,'timestamp stays above bubble bottom');
      assert(m.footer.y>m.last.y+1,'timestamp sits slightly below the text');
      if(m.id!==5)assert(m.footer.right-m.last.right>=0,'timestamp follows the last text line');
    }
    assert(geometry[1].top-geometry[0].bottom>=3.9,'successive messages never touch');
    assert(geometry[4].top-geometry[3].bottom>=11.9,'sender change has a larger gap');
    assert(geometry[1].grouped&&!geometry[0].end&&geometry[3].end,'group boundaries are shared');
    assert(!geometry[6].grouped,'new calendar day starts a new sequence');
    assert(!geometry[8].grouped&&geometry[8].top-geometry[7].bottom>=11.9,'a pause separates messages even from the same sender');
    await expect(page.locator('.chat-day')).toHaveCount(2);
    await expect(page.locator('.chat-day')).toHaveText(['20 октября','Сегодня']);
    styles.push({width,scheme,sid,padding:geometry[1].padding,font:geometry[1].font});
    if(width===390&&scheme==='dark'){
      await message(1).scrollIntoViewIfNeeded();
      await page.screenshot({path:`artifacts/chat-layout-${engine}-${sid.split(':')[0]}.png`});
    }
    if(width<=760)await page.locator(sid.startsWith('telegram:')?'[data-fp-back]':'[data-chat-back]').click();
  }
  for(const width of [390,320,1440])for(const scheme of ['dark','light']){
    const matching=styles.filter(s=>s.width===width&&s.scheme===scheme);
    assert.equal(new Set(matching.map(s=>s.padding+';'+s.font)).size,1,'all chat kinds share bubble padding and type');
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-fp-back]').click();
  await page.locator('[data-room=direct]').focus();
  const kept=await page.locator('[data-room=direct]').elementHandle();
  await page.clock.setSystemTime(new Date('2026-10-22T00:00:01+03:00'));
  await page.clock.runFor(1000);
  await expect(page.locator('[data-room=direct] time')).toHaveText('ср');
  assert(await kept.evaluate(el=>el.isConnected&&document.activeElement===el),'midnight updates labels without replacing focused rooms');
  assert.deepEqual(errors,[]);
  await page.evaluate(()=>ChatLayout.chatController.destroy());
  console.log('Chat layout passed: list date ages, shared direct/group/Telegram geometry, visible gaps, calendar separators, timestamps, iOS standalone, 320–1440px, light/dark');
}finally{await browser.close();}
