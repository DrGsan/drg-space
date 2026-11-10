import assert from 'node:assert/strict';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const folder=await mkdtemp(tmpdir()+'/drg-voice-');
let browser;
try {
  execFileSync('ffmpeg',['-nostdin','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=20','-c:a','aac',folder+'/voice.m4a']);
  const audio=await readFile(folder+'/voice.m4a'),manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));
  const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'FamilyVoice',write:false,loader:{'.css':'empty'}});
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://voice.test/**',route=>new URL(route.request().url()).pathname.startsWith('/voice')?route.fulfill({body:audio,contentType:'audio/mp4'}):route.fulfill({body:'<main id="fixture" class="chat-shell" style="height:100dvh"></main>',contentType:'text/html'}));
  await page.goto('http://voice.test/');
  await page.addStyleTag({content:await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8')});
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.clock.install();
  await page.evaluate(()=>{
    window.rows=[{id:1,created:1700000000,sender:'Анна',text:'',content_type:'voice',duration:20,media:[{id:'a'.repeat(32),kind:'voice',state:'ready',url:'/voice.ogg',playback_url:'/voice.m4a'}]}];
    window.denied=false;
    const ctx={state:{chatSpace:'telegram:-1001',me:{id:'owner'}},toast(){},navigate(){},async api(path){
      if(denied)throw Object.assign(Error('Доступ закрыт'),{status:403});
      if(path==='/family-photos/groups')return {items:[{id:'-1001',title:'Семья',history_complete:true}],status:{can_send:false}};
      if(path.includes('/messages'))return {items:structuredClone(rows),more:false};
      if(path.endsWith('/outbox'))return {items:[]};
      if(path.endsWith('/read'))return {ok:true};
      throw Error('Unexpected '+path);
    }};
    window.mounted=FamilyVoice.mountTelegramChat(document.querySelector('#fixture'),ctx,'-1001',()=>{});
  });
  const player=page.locator('audio');await player.waitFor({state:'attached'});
  assert.equal(await player.getAttribute('src'),null);assert.equal(await player.getAttribute('data-recording-source'),'/voice.m4a');
  assert.equal(await player.getAttribute('preload'),'none');
  await player.evaluate(audio=>{window.savedAudio=audio;});await page.locator('[data-recording-toggle]').click();
  await page.waitForFunction(()=>savedAudio.currentTime>.2);
  await page.evaluate(()=>rows.push({id:2,created:1700000001,sender:'Анна',text:'Продолжение',media:[]}));
  await page.clock.runFor(5100);await page.locator('[data-fp-message-id="2"]').waitFor();
  assert(await page.evaluate(()=>savedAudio===document.querySelector('audio')&&!savedAudio.paused&&savedAudio.currentTime>.2),'history polling preserves playing audio');
  for(const width of [320,390,900]){
    await page.setViewportSize({width,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'audio fits viewport');
  }
  await page.evaluate(()=>denied=true);await page.clock.runFor(5100);
  assert.equal(await player.count(),0);
  assert(await page.evaluate(()=>savedAudio.paused&&!savedAudio.getAttribute('src')),'revocation stops playback and releases source');
  await page.evaluate(()=>mounted.destroy());assert.deepEqual(errors,[]);
  console.log('Family voice passed: real AAC playback, stable polling, mobile widths and access-revocation cleanup.');
} finally {await browser?.close();await rm(folder,{recursive:true,force:true});}
