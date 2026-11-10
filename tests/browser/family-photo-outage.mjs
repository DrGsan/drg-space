// External archive outages must not delay local counters or require a gallery reload.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({stdin:{contents:`export {mountFamilyPhotos,familyPhotosController} from './web/modules/family-photos/index.js';
  export {chatUnreadController} from './web/core/chat-unread.js';`,resolveDir:process.cwd()},
  bundle:true,format:'iife',globalName:'Outage',write:false,loader:{'.css':'empty'}});
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true});
async function fixture(mode,{down=false,mediaDown=false}={}){
  const page=await browser.newPage({locale:'ru-RU'});page.setDefaultTimeout(5000);
  await page.route('http://localhost/**',route=>route.fulfill({contentType:'text/html',body:'<button><small data-chat-unread hidden></small></button><main></main>'}));
  await page.goto('http://localhost/');await page.clock.install();
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async ({mode,down,mediaDown})=>{
    const s=window.fixtureState={down,mediaDown,status:503,calls:[],familyEnabled:true,siteUnread:2,familyUnread:3,holdFamily:false};
    const group={id:'-1001',title:'Семья',history_complete:true};
    const ctx=window.fixtureContext={state:{me:{id:'owner',role:'owner'}},moduleEnabled:id=>id!=='family_photos'||s.familyEnabled,navigate(){},toast(){},
      async api(path,method,body,retry,headers,{signal}={}){
        s.calls.push(path);
        if(mode==='counter'){
          if(path==='/chat/unread')return {unread:s.siteUnread};
          if(s.holdFamily)await new Promise((resolve,reject)=>{s.releaseFamily=resolve;signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});});
          if(s.down)throw Object.assign(new Error('Недоступен'),{status:s.status});
          return {unread:s.familyUnread};
        }
        if(s.down||(s.mediaDown&&path.endsWith('/media')))throw Object.assign(new Error('Сервис семейных фото временно недоступен.'),{status:s.status});
        if(path==='/family-photos/groups')return {items:[group],status:{can_send:false,worker_online:true}};
        if(path.endsWith('/sync'))return {state:'active',pending:0,history_complete:true,batch:{}};
        if(path.endsWith('/media'))return {items:[{id:'a'.repeat(32),message_id:1,created:1700000000,kind:'photo',state:'pending',sender:'Анна'}],more:false};
        throw Error(path);
      }};
    if(mode==='counter'){s.holdFamily=true;Outage.chatUnreadController.attach(ctx);}
    else await Outage.mountFamilyPhotos(document.querySelector('main'),ctx);
  },{mode,down,mediaDown});
  return page;
}
try{
  for(const mediaDown of [false,true]){
    const page=await fixture('gallery',{down:!mediaDown,mediaDown});
    assert.match(await page.locator('[data-fp-main]').innerText(),/Сервис временно недоступен/);
    await page.clock.runFor(31000);
    const calls=await page.evaluate(()=>fixtureState.calls.filter(p=>p==='/family-photos/groups').length);
    assert.equal(calls,4,'one initial request and one retry every ten seconds');
    await page.evaluate(()=>{fixtureState.down=false;fixtureState.mediaDown=false;});
    await page.clock.runFor(11000);
    assert.equal(await page.locator('.fp-tile').count(),1,'gallery recovers without clicking retry');
    await page.evaluate(()=>Outage.familyPhotosController.destroy());await page.close();
  }
  for(const action of ['destroy','denied','manual']){
    const page=await fixture('gallery',{down:true});
    if(action==='destroy')await page.evaluate(()=>Outage.familyPhotosController.destroy());
    if(action==='denied'){await page.evaluate(()=>{fixtureState.status=403;});await page.clock.runFor(11000);assert.match(await page.locator('[data-fp-main]').innerText(),/Доступ закрыт/);}
    if(action==='manual'){await page.evaluate(()=>{fixtureState.down=false;});await page.locator('[data-fp-retry]').click();assert.equal(await page.locator('.fp-tile').count(),1);await page.evaluate(()=>Outage.familyPhotosController.destroy());}
    const before=await page.evaluate(()=>fixtureState.calls.length);await page.clock.runFor(31000);
    assert.equal(await page.evaluate(()=>fixtureState.calls.length),before,'no polling survives close, denied access, or replacement');
    await page.close();
  }
  const page=await fixture('counter');await page.clock.runFor(200);
  assert.equal(await page.locator('[data-chat-unread]').innerText(),'2','ordinary unread appears while archive is still pending');
  await page.evaluate(()=>{fixtureState.siteUnread=4;});await page.clock.runFor(20000);
  assert.equal(await page.locator('[data-chat-unread]').innerText(),'4','ordinary polling continues while archive is pending');
  assert.equal(await page.evaluate(()=>fixtureState.calls.filter(p=>p==='/family-photos/unread').length),1,'no parallel archive requests');
  await page.evaluate(()=>{fixtureState.holdFamily=false;fixtureState.releaseFamily();});await page.clock.runFor(500);
  assert.equal(await page.locator('[data-chat-unread]').innerText(),'7','independent totals are added once');
  await page.evaluate(()=>{fixtureState.down=true;});await page.clock.runFor(20000);
  assert.equal(await page.locator('[data-chat-unread]').innerText(),'4','archive failure preserves ordinary unread');
  await page.evaluate(()=>{fixtureState.down=false;});await page.clock.runFor(20000);
  assert.equal(await page.locator('[data-chat-unread]').innerText(),'7','archive counter recovers');
  await page.evaluate(()=>{fixtureState.holdFamily=true;});await page.clock.runFor(20000);
  await page.evaluate(()=>{fixtureState.familyEnabled=false;Outage.chatUnreadController.attach(fixtureContext);fixtureState.releaseFamily();});await page.clock.runFor(500);
  assert.equal(await page.locator('[data-chat-unread]').innerText(),'4','late archive response cannot restore a disabled module counter');
  await page.evaluate(()=>Outage.chatUnreadController.destroy());
  const calls=await page.evaluate(()=>fixtureState.calls.length);await page.clock.runFor(40000);
  assert.equal(await page.evaluate(()=>fixtureState.calls.length),calls,'destroy stops counter polling');await page.close();
  console.log('Family-photo outage passed: initial groups/media retry, automatic recovery, manual retry, close/access cancellation, independent unread and recovery.');
}finally{await browser.close();}
