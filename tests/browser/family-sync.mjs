import {readAppStyles} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'FamilySync',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles(),png=await readFile('tests/fixtures/blue.png');
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844}});const errors=[];let imageReads=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://family-sync.test/**',route=>{
    if(route.request().url().includes('/image/')){imageReads++;return route.fulfill({body:png,contentType:'image/png',headers:{'Cache-Control':'no-store'}});}
    return route.fulfill({body:'<main id="fixture" style="padding:12px"></main>',contentType:'text/html'});
  });
  await page.goto('http://family-sync.test/');await page.clock.install();
  await page.addStyleTag({content:css+'body{margin:0}#fixture{min-width:0}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{
    const state=window.testState={syncCalls:0,mediaCalls:0,fail:false,deny:false,hold:false,
      groups:[{id:'-1001',title:'Теюша 🤍',history_complete:true},{id:'-1002',title:'Лёвик',history_complete:true}],
      sync:{state:'active',server_time:1000,history_complete:true,pending:19,failed:0,rate_limited:0,
        batch:{total:22,completed:3,percent:15.7},eta_seconds:60,
        current:{name:'Очень-длинное-название-видео-из-семейной-группы.mp4',kind:'video',stage:'download',bytes:5*1024**2,total:10*1024**2,speed:1024**2,wait_until:0},
        cleanup:{pending:4,objects_removed:12,temporary_removed:2,temporary_bytes:4096}}};
    const ctx={state:{},moduleEnabled:()=>true,navigate(){},toast(){},modal(markup){const node=document.createElement('div');node.innerHTML='<button class="modal-close">Закрыть</button>'+markup;node.querySelector('button').onclick=()=>node.remove();document.body.append(node);return node;},async api(path){
      if(path==='/family-photos/groups')return structuredClone({items:state.groups});
      if(path.endsWith('/sync')){
        state.syncCalls++;
        if(state.deny)throw Object.assign(Error('Доступ закрыт'),{status:403});
        if(state.fail)throw Object.assign(Error('Недоступно'),{status:503});
        const result=structuredClone(state.sync);
        if(path.includes('-1002')){result.current.name='Другое-видео.mp4';result.pending=1;result.batch={total:1,completed:0,percent:50};}
        if(state.hold&&path.includes('-1001'))await new Promise(resolve=>state.release=resolve);
        return result;
      }
      if(path.endsWith('/media')){state.mediaCalls++;return {items:[{id:'a',group_id:'-1001',message_id:1,created:1700000000,kind:'photo',state:'ready',name:'a.png',sender:'Анна',thumbnail_url:'/image/a.png',url:'/image/a.png'},{id:'stopped',group_id:'-1001',message_id:5462,created:1775564224,kind:'video',state:'paused',name:'IMG_1283.MOV',sender:'Анна'}],more:false};}
      throw Error('Unexpected '+path);
    }};
    await FamilySync.mountFamilyPhotos(document.querySelector('#fixture'),ctx);
  });
  const syncText=async()=>{const details=page.locator('[data-fp-sync-details]');if(await details.getAttribute('open')===null)await details.locator(':scope>summary').click();return page.locator('.fp-sync').innerText();};
  await syncText();await page.locator('.fp-sync progress').first().waitFor();
  const stoppedTile=page.locator('.fp-tile.is-unavailable');
  assert.match(await stoppedTile.innerText(),/Это видео недоступно в ТГ/);
  assert.equal(await stoppedTile.evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(0, 0, 0)');
  assert.equal(await stoppedTile.locator('.fp-video-mark').count(),0);
  await page.locator('[data-fp-sync-details]>summary').click();await stoppedTile.click();assert.match(await page.locator('.fp-viewer-stage').innerText(),/Это видео недоступно в ТГ/);
  assert.equal(await page.locator('.fp-viewer-stage video').count(),0);await page.locator('.modal-close').click();await page.locator('[data-fp-sync-details]>summary').click();
  assert.match(await syncText(),/Скачиваем из Telegram/);
  assert.match(await syncText(),/Готово 3 из 22 · осталось 19/);
  assert.match(await syncText(),/МБ\/с/);
  assert.match(await syncText(),/без пауз Telegram/);
  assert.doesNotMatch(await page.locator('[data-fp-state]').innerText(),/Архив/);
  await page.evaluate(()=>{window.savedImage=document.querySelector('.fp-tile img');window.savedTile=document.querySelector('.fp-tile');savedTile.focus();});
  await page.waitForFunction(()=>savedImage.complete&&savedImage.naturalWidth);
  const initialReads=imageReads;
  await page.evaluate(()=>{testState.sync.current.bytes=8*1024**2;testState.sync.batch.percent=17;});
  await page.clock.runFor(2100);
  assert.equal(await page.locator('.fp-sync progress').first().getAttribute('value'),'17');
  assert(await page.evaluate(()=>document.querySelector('.fp-tile img')===savedImage&&document.activeElement===savedTile));
  assert.equal(imageReads,initialReads);assert.equal(await page.evaluate(()=>testState.mediaCalls),1,'fast status polling does not query gallery/history');
  assert.equal(await page.locator('.fp-sync-cleanup').count(),0,'gallery omits service diagnostics even while downloading');
  await page.evaluate(()=>{testState.sync.current.wait_until=1090;testState.sync.current.speed=0;testState.sync.eta_seconds=null;});
  await page.clock.runFor(2100);
  assert.match(await syncText(),/Telegram попросил подождать/);
  assert.match(await syncText(),/через 2 мин/);
  assert.doesNotMatch(await syncText(),/МБ\/с|без пауз Telegram/);
  await page.evaluate(()=>{testState.sync.pause={kind:'premium',scope:'media',seconds:17,until:1900,telegram_until:1017,code:'FLOOD_PREMIUM_WAIT'};testState.sync.state='paused';});
  await page.clock.runFor(2100);
  assert.match(await syncText(),/Telegram ограничил скорость скачивания/);
  assert.match(await syncText(),/Следующая попытка:.*через 15 мин/);
  assert.match(await syncText(),/паузу 17 сек/);
  assert.equal(await page.locator('.fp-sync-reason').count(),0,'service error codes stay out of the gallery');
  assert.equal(await page.locator('.fp-sync-file-progress').count(),0,'no fictitious transfer during persistent cooldown');
  await page.evaluate(()=>{window.pendingSync=structuredClone(testState.sync);testState.sync.current=null;testState.sync.pending=0;testState.sync.eta_seconds=null;testState.sync.batch={total:2,completed:2,percent:100};testState.sync.pause={kind:'flood',scope:'all',seconds:5,until:1900,code:'FLOOD_WAIT'};});
  await page.clock.runFor(2100);
  await syncText();assert.match(await page.locator('.fp-sync [data-fp-sync-title]').innerText(),/Все фото и видео загружены/);
  await syncText();assert.match(await page.locator('.fp-sync [data-fp-sync-title]').getAttribute('title'),/очередь пуста.*повторно не скачиваются.*общей паузы подключения Telegram/);
  assert.equal(await page.locator('.fp-sync progress').count(),0,'a global pause must not imply this group has pending files');
  await page.evaluate(()=>testState.sync.history_complete=false);await page.clock.runFor(2100);
  await syncText();assert.match(await page.locator('.fp-sync [data-fp-sync-title]').innerText(),/Очередь файлов этой группы пуста/);
  await syncText();assert.match(await page.locator('.fp-sync [data-fp-sync-title]').getAttribute('title'),/повторно не скачиваются/);
  assert.equal(await page.locator('.fp-sync progress').count(),0,'empty paused queue has no transfer progress even before the next history check');
  await page.screenshot({path:`artifacts/family-sync-${process.env.DRG_BROWSER_ENGINE||'chromium'}-complete-global-pause.png`});
  await page.evaluate(()=>testState.sync=window.pendingSync);await page.clock.runFor(2100);
  for(const scheme of ['light','dark'])for(const width of [900,390,320]){
    await page.evaluate(s=>document.documentElement.dataset.scheme=s,scheme);await page.setViewportSize({width,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${scheme} ${width}: no horizontal overflow`);
    await page.screenshot({path:`artifacts/family-sync-${process.env.DRG_BROWSER_ENGINE||'chromium'}-${scheme}-${width}.png`});
  }
  await page.evaluate(()=>{delete testState.sync.pause;testState.sync.state='active';});
  for(const [stage,label] of [['upload','Сохраняем файл'],['preview','Готовим превью']]){
    await page.evaluate(stage=>{testState.sync.current.wait_until=0;testState.sync.current.stage=stage;},stage);await page.clock.runFor(2100);
    assert.match(await syncText(),new RegExp(label));
  }
  const previousSync=await page.evaluate(()=>structuredClone(testState.sync));
  await page.evaluate(()=>{testState.sync={state:'idle',server_time:1000,history_complete:true,pending:0,failed:0,stopped:2,stopped_files:[{name:'IMG_1283.MOV',message_id:5462,created:1775564224,group_title:'Теюша 🤍',telegram_url:'https://t.me/c/1915828291/5462'},{name:'IMG_1807.MOV',message_id:5608,created:1778927904,group_title:'Теюша 🤍',telegram_url:'https://t.me/c/1915828291/5608'}],batch:{total:0,completed:0}};});
  await page.clock.runFor(2100);
  await syncText();assert.match(await page.locator('[data-fp-sync-title]').innerText(),/Автоматическая очередь пуста/);
  assert.equal(await page.locator('.fp-sync progress').count(),0);
  assert.equal(await page.locator('.fp-sync-problems').count(),0,'stopped-file diagnostics do not enlarge an idle status');
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:844});
    assert.equal(Math.round((await page.locator('[data-fp-sync-details]>summary').boundingBox()).height),width>760?38:32,'idle disclosure keeps its designed single-row control');
  }
  await page.evaluate(value=>testState.sync=value,previousSync);await page.clock.runFor(10100);
  await page.evaluate(()=>testState.fail=true);await page.clock.runFor(2100);
  assert.match(await syncText(),/Не удалось обновить/);
  assert.equal(await page.locator('.fp-sync progress').count(),0,'stale rates/progress disappear after failed status refresh');
  await page.evaluate(()=>{testState.fail=false;testState.hold=true;});await page.clock.runFor(2100);
  await page.locator('[data-fp-group-select]').selectOption('-1002');await syncText();
  await page.waitForFunction(()=>document.querySelector('.fp-sync-file')?.textContent.includes('Другое-видео'));
  await page.evaluate(()=>testState.release());
  assert.match(await page.locator('.fp-sync-file').innerText(),/Другое-видео/,'late previous-album result cannot overwrite selected album');
  await page.evaluate(()=>testState.deny=true);await page.clock.runFor(2100);
  assert.equal(await page.locator('.fp-tile').count(),0,'access revocation clears private photos');
  assert.equal(await page.locator('.fp-sync-file').count(),0,'access revocation clears current filename');
  const calls=await page.evaluate(()=>testState.syncCalls);await page.clock.runFor(6000);
  assert.equal(await page.evaluate(()=>testState.syncCalls),calls,'revoked polling stops');
  await page.evaluate(()=>FamilySync.familyPhotosController.destroy());
  assert.deepEqual(errors,[]);console.log('Family sync passed: live stage/bytes/ETA/pause, owner cleanup, preserved photos/focus, scoped races/revocation, light/dark 320–900px.');
}finally{await browser.close();}
