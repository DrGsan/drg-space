import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const bundle=await build({stdin:{contents:"export {voiceMarkup,videoNoteMarkup,recordingPlayers} from './web/modules/chat/recording-playback.js';export {renderMarkup} from './web/core/dom.js';",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'Queue',write:false,loader:{'.css':'empty'}});
const browser=await(engine==='webkit'?webkit:chromium).launch();
try{
 const page=await browser.newPage(),source=await readFile('tests/fixtures/chat-audio.m4a'),requests=[],errors=[];let failSecond=false;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://queue.test/**',route=>{const path=new URL(route.request().url()).pathname;if(path.endsWith('.m4a')){requests.push(path);return route.fulfill(failSecond&&path==='/2.m4a'?{status:403,body:''}:{contentType:'audio/mp4',body:source});}return route.fulfill({contentType:'text/html',body:'<!doctype html><main id="chat"></main>'});});
 await page.goto('https://queue.test/');await page.addScriptTag({content:bundle.outputFiles[0].text});
 const reset=async()=>{requests.length=0;await page.evaluate(()=>{window.players?.destroy();const host=document.querySelector('#chat');host.innerHTML=Queue.voiceMarkup({id:'1',kind:'voice',duration:.6,url:'/1.m4a'})+'<p>Текст</p>'+Queue.videoNoteMarkup({id:'video',kind:'video_note',url:'/skip.mp4'})+[2,3].map(n=>Queue.voiceMarkup({id:String(n),kind:'voice',duration:.6,url:`/${n}.m4a`})).join('');window.markup=host.innerHTML;window.players=Queue.recordingPlayers(host);players.sync();});};
 const button=n=>page.locator(`[data-chat-attachment-id="${n}"] [data-recording-toggle]`),audio=n=>page.locator(`[data-chat-attachment-id="${n}"] audio`);
 const finished=async n=>page.waitForFunction(id=>document.querySelector(`[data-chat-attachment-id="${id}"] audio`).ended,String(n));
 await reset();assert.deepEqual(requests,[]);await button(2).click();await finished(3);assert.deepEqual(requests,['/2.m4a','/3.m4a'],'penultimate plays itself and the last only');
 await reset();await page.locator('[data-recording-speed]').first().click();await button(1).click();await page.waitForFunction(()=>!document.querySelector('audio').paused);
 await page.evaluate(()=>{window.firstAudio=document.querySelector('audio');Queue.renderMarkup(document.querySelector('#chat'),markup+'<p>Обновление</p>');players.sync();});assert(await page.evaluate(()=>firstAudio===document.querySelector('audio')));
 await finished(3);assert.deepEqual(requests,['/1.m4a','/2.m4a','/3.m4a']);assert.equal(await audio(3).evaluate(a=>a.playbackRate),1.5,'queue preserves playback rate');
 await reset();await button(1).click();await page.waitForFunction(()=>!document.querySelector('audio').paused);await button(1).click();await page.waitForTimeout(800);assert.deepEqual(requests,['/1.m4a'],'pause does not advance');await button(1).click();await finished(3);
 await reset();await button(1).click();await button(3).click();await finished(3);assert(!requests.includes('/2.m4a'),'manual selection replaces queue');
 await reset();failSecond=true;await button(1).click();await page.locator('[data-chat-attachment-id="2"].recording-failed').waitFor();await page.waitForTimeout(300);assert(!requests.includes('/3.m4a'),'error stops queue');failSecond=false;
 await reset();await button(1).click();await page.evaluate(()=>players.destroy());await page.waitForTimeout(800);assert(!requests.includes('/2.m4a'),'leaving stops queue');
 await reset();await button(1).click();await page.evaluate(()=>{const call=document.createElement('div');call.className='voice-call';document.body.append(call);document.dispatchEvent(new Event('drg-call-change'));call.remove();});await page.waitForTimeout(800);assert(!requests.includes('/2.m4a'),'call stops queue');
 await reset();await button(1).click();await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(800);assert(!requests.includes('/2.m4a'),'background stops queue');
 await page.evaluate(()=>players.destroy());assert.deepEqual(errors,[]);console.log(engine+': ordered audio queue, penultimate/last, speed, stable polling, pause/resume, replacement, failure, background and cleanup passed');
}finally{await browser.close();}
