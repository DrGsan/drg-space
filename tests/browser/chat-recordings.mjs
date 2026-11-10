import assert from 'node:assert/strict';
import {readFile,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const engine=process.env.DRG_BROWSER_ENGINE||'chromium',folder=await mkdtemp(tmpdir()+'/drg-recording-test-');
const script=await build({stdin:{contents:"export {renderMarkup} from './web/core/dom.js'; export {chatComposerMarkup} from './web/modules/chat/composer.js'; export {voiceRecording,recordingActions} from './web/modules/chat/voice-recording.js'; export {voiceMarkup,videoNoteMarkup} from './web/modules/chat/recording-playback.js'; export {inlineVideos} from './web/modules/chat/video-preview.js';",resolveDir:process.cwd()},bundle:true,loader:{'.css':'empty'},format:'iife',globalName:'ChatRecordings',write:false});
const browser=await(engine==='webkit'?webkit:chromium).launch({headless:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:430,height:932}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));
 const video=await readFile('tests/fixtures/chat-video.mp4'),audio=await readFile('tests/fixtures/chat-audio.m4a');
 let audioRequests=0;
 await page.route('https://recordings.test/**',route=>{const url=new URL(route.request().url());if(url.pathname.endsWith('.mp4'))return route.fulfill({contentType:'video/mp4',body:video});if(url.pathname.endsWith('.m4a')){audioRequests++;return route.fulfill({contentType:'audio/mp4',body:audio});}return route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><main class="chat-shell" id="fixture"></main>'});});
 await page.goto('https://recordings.test/');
 await page.addStyleTag({content:(await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8')).replace(/@font-face\s*\{[^}]*\}/g,'')+'body{margin:0}.chat-shell{display:block;width:100%;height:auto}.chat-messages{height:400px;overflow:auto}.chat-message{flex:none}.modal-overlay{position:fixed;inset:0;display:grid;place-items:center;background:#0006}.modal{width:340px;padding:18px;background:white;color:black;border-radius:18px}'});
 await page.addScriptTag({content:script.outputFiles[0].text});
 await page.evaluate(()=>{
   window.trackSets=[];window.uploaded=[];window.audioContexts=[];window.trace=[];
   // Synthetic sources only: tests never activate the computer's camera or microphone.
   const fakeCapture=async options=>{trace.push('capture');
     const context=new AudioContext();await context.resume();audioContexts.push(context);const source=context.createOscillator(),gain=context.createGain(),destination=context.createMediaStreamDestination();source.frequency.value=440;gain.gain.value=.2;source.connect(gain).connect(destination);source.start();
     const stream=destination.stream;
     if(options.video){const canvas=document.createElement('canvas');canvas.width=canvas.height=480;const c=canvas.getContext('2d');c.fillStyle='#3982b9';c.fillRect(0,0,480,480);c.fillStyle='#ffd775';c.beginPath();c.arc(240,240,100,0,Math.PI*2);c.fill();const track=canvas.captureStream(15).getVideoTracks()[0];stream.addTrack(track);const tick=setInterval(()=>{c.fillStyle='#3982b9';c.fillRect(0,0,480,480);c.fillStyle='#ffd775';c.beginPath();c.arc(240+Math.sin(Date.now()/500)*20,240,100,0,Math.PI*2);c.fill();if(track.readyState==='ended')clearInterval(tick);},66);}
     trackSets.push(stream.getTracks());trace.push('ready');return stream;
   };Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:fakeCapture},configurable:true});const RealRecorder=MediaRecorder;window.MediaRecorder=new Proxy(RealRecorder,{construct(target,args){try{trace.push(['recorder',args[1]]);return Reflect.construct(target,args);}catch(error){trace.push(['recorder-error',error.name,error.message]);throw error;}}});
   window.ctx={modal(html){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.innerHTML='<section class="modal" role="dialog"><button class="modal-close" aria-label="Закрыть">×</button>'+html+'</section>';document.body.append(overlay);overlay.querySelector('.modal-close').onclick=()=>overlay.remove();return overlay.querySelector('.modal');}};
   document.querySelector('#fixture').innerHTML=ChatRecordings.chatComposerMarkup();
   window.actions=ChatRecordings.recordingActions(document.querySelector('form'),ctx,{isCurrent:()=>true,onFile:async(file,info)=>{uploaded.push({metadata:info,bytes:[...new Uint8Array(await file.arrayBuffer())],type:file.type});return true;}});
 });
 const hold=page.locator('[data-recording-hold]');
 // Holding then releasing stops into preview, without uploading.
 const box=await hold.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.locator('[data-voice-stop]:visible').waitFor({timeout:8000}).catch(async error=>{console.log(await page.locator('body').innerText(),errors,await page.evaluate(()=>trace));throw error;});await page.waitForTimeout(650);await page.mouse.up();
 await page.locator('[data-voice-preview]:visible').waitFor();assert.equal(await page.evaluate(()=>uploaded.length),0);
 await page.locator('[data-voice-attach]').click();await page.waitForFunction(()=>uploaded.length===1);
 assert.equal(await page.evaluate(()=>uploaded[0].metadata.kind),'voice');assert(await page.evaluate(()=>uploaded[0].metadata.waveform.length>0&&uploaded[0].metadata.waveform.every(n=>Number.isInteger(n)&&n>=0&&n<=31)));
 assert(await page.evaluate(()=>trackSets.flat().every(t=>t.readyState==='ended')));
 // Slide left cancels; no new upload and no live tracks.
 await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.locator('[data-voice-stop]:visible').waitFor({timeout:8000}).catch(async error=>{console.log(await page.locator('body').innerText(),errors,await page.evaluate(()=>trace));throw error;});await page.mouse.move(box.x-100,box.y+box.height/2);await page.mouse.up();await page.locator('.modal-overlay').waitFor({state:'detached'});
 assert.equal(await page.evaluate(()=>uploaded.length),1);assert(await page.evaluate(()=>trackSets.flat().every(t=>t.readyState==='ended')));
 // Keyboard starts a locked video note; accessible Stop/Cancel remain available.
 await page.locator('[data-recording-switch]').click();await hold.focus();await page.keyboard.press('Enter');
 await page.locator('[data-voice-stop]:visible').waitFor({timeout:8000}).catch(async error=>{console.log(await page.locator('body').innerText(),errors,await page.evaluate(()=>trace));throw error;});await page.waitForTimeout(700);await page.locator('[data-voice-stop]').click();await page.locator('[data-voice-preview]:visible').waitFor();await page.locator('[data-voice-attach]').click();await page.waitForFunction(()=>uploaded.length===2);
 for(const [index,kind] of ['voice','video_note'].entries()){
   const result=await page.evaluate(i=>uploaded[i],index);assert.equal(result.metadata.kind,kind);
   const path=folder+'/'+index+'.'+(result.type.includes('mp4')?'mp4':'webm');await writeFile(path,Buffer.from(result.bytes));
   const probe=JSON.parse(execFileSync('ffprobe',['-v','error','-show_entries','stream=codec_type,width,height:format=duration','-of','json',path],{encoding:'utf8'}));
   assert(Number(probe.format.duration)>.3);if(kind==='video_note'){const v=probe.streams.find(s=>s.codec_type==='video');assert.equal(v.width,480);assert.equal(v.height,480);}
 }
 await page.evaluate(()=>{actions.destroy();document.querySelector('#fixture').innerHTML='<div class="chat-messages"></div>';const host=document.querySelector('.chat-messages');host.innerHTML=ChatRecordings.voiceMarkup({id:'voice',kind:'voice',duration:2.2,waveform:[0,6,15,31,18,4],url:'/test.m4a'})+ChatRecordings.videoNoteMarkup({id:'one',kind:'video_note',duration:2,url:'/one.mp4'})+ChatRecordings.videoNoteMarkup({id:'two',kind:'video_note',duration:2,url:'/two.mp4'})+'<div style="height:900px;flex:none"></div>';window.chatMarkup=host.innerHTML;window.players=ChatRecordings.inlineVideos(host);players.sync();});
 assert.equal(audioRequests,0,'voice files stay lazy before a tap');
 await page.waitForFunction(()=>[...document.querySelectorAll('[data-video-note]')].filter(v=>!v.paused).length===1);
 await page.locator('[data-recording="voice"] [data-recording-toggle]').click();await page.waitForFunction(()=>!document.querySelector('audio').paused);
 assert(await page.locator('[data-video-note]').evaluateAll(vs=>vs.every(v=>v.paused)));
 await page.evaluate(()=>{window.savedVoice=document.querySelector('audio');ChatRecordings.renderMarkup(document.querySelector('.chat-messages'),chatMarkup+'<article>Новое сообщение</article>');players.sync();});assert(await page.evaluate(()=>savedVoice===document.querySelector('audio')&&!savedVoice.paused&&!!savedVoice.src),'polling preserves the active source and player');
 await page.locator('[data-recording-speed]').click();assert.equal(await page.locator('audio').evaluate(a=>a.playbackRate),1.5);
 await page.locator('[data-recording-speed]').click();assert.equal(await page.locator('audio').evaluate(a=>a.playbackRate),2);
 await page.locator('[data-recording="video_note"] [data-recording-toggle]').first().click();await page.waitForFunction(()=>[...document.querySelectorAll('[data-video-note]')].some(v=>!v.paused&&!v.muted));assert(await page.locator('audio').evaluate(a=>a.paused));
 await page.evaluate(()=>players.destroy());assert(await page.locator('video,audio').evaluateAll(ms=>ms.every(m=>m.paused)));
 assert.deepEqual(errors,[]);console.log(engine+': voice/video recording, real media containers, waveform, cancel, keyboard, explicit send, one muted autoplay and playback rates passed.');
}finally{await browser.close();await rm(folder,{recursive:true,force:true});}
