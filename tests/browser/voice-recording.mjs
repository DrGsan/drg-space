import {readAppStyles} from './fixtures.mjs';
import {expect} from '@playwright/test';
import {chromium} from 'playwright';
import {build} from 'esbuild';
import {mkdir} from 'node:fs/promises';
const bundle=await build({stdin:{contents:`export {voiceRecording} from './web/modules/chat/voice-recording.js';export {attachmentMenu} from './web/modules/chat/attachment-menu.js';`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'VoiceTest',write:false,loader:{'.css':'empty'}});
const browser=await chromium.launch({headless:true,args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
try{
 const context=await browser.newContext({locale:'ru-RU',permissions:['microphone'],viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('https://voice.test/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="ru" data-theme="slate" data-scheme="dark"><head><meta charset="utf-8"></head><body><button id="attach">Вложения</button></body></html>'}));
 await page.goto('https://voice.test');await page.addStyleTag({content:await readAppStyles()});await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{
  window.attempts=0;window.files=[];
  window.ctx={modal(html){const d=document.createElement('div');d.className='modal-overlay';d.innerHTML='<section class="modal" role="dialog"><button class="modal-close" aria-label="Закрыть">×</button>'+html+'</section>';d.querySelector('button').onclick=()=>d.remove();document.body.append(d);return d;}};
  document.querySelector('#attach').onclick=()=>VoiceTest.attachmentMenu(document.querySelector('#attach'),mode=>{if(mode==='voice')window.capture=VoiceTest.voiceRecording(ctx,{onFile:async file=>{attempts++;files.push({name:file.name,size:file.size,type:file.type});return attempts>1;}});});
 });
 await page.locator('#attach').click();await page.getByRole('menuitem',{name:'Голосовое сообщение'}).click();
 await expect(page.getByRole('dialog')).toContainText('До 10 минут');await page.getByRole('button',{name:'Начать запись',exact:true}).click();
 await expect(page.locator('[data-voice-status]')).toHaveText('Запись закреплена. Нажмите «Остановить», когда закончите.');await page.waitForTimeout(600);await page.getByRole('button',{name:'Остановить запись'}).click();
 await expect(page.locator('audio')).toBeVisible();expect(await page.evaluate(()=>attempts)).toBe(0);
 await page.getByRole('button',{name:'Добавить к сообщению'}).click();await expect(page.locator('[data-voice-status]')).toContainText('Она сохранена здесь');await expect(page.locator('audio')).toBeVisible();
 await mkdir('artifacts',{recursive:true});await page.screenshot({path:'artifacts/ios-09-web-voice-dark.png',fullPage:true});
 await page.getByRole('button',{name:'Добавить к сообщению'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
 expect(await page.evaluate(()=>files.length)).toBe(2);expect(await page.evaluate(()=>files[0].size)).toBeGreaterThan(0);expect(await page.evaluate(()=>files[0].size===files[1].size)).toBe(true);
 // A late microphone permission response must release the newly returned stream.
 await page.evaluate(async()=>{
  const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);window.lateStream=await original({audio:true});
  navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.grantLate=()=>resolve(lateStream));
  window.capture=VoiceTest.voiceRecording(ctx,{onFile:async()=>true});
 });
 await page.getByRole('button',{name:'Начать запись',exact:true}).click();await expect(page.locator('[data-voice-status]')).toHaveText('Подключаем микрофон…');await page.getByRole('button',{name:'Закрыть',exact:true}).click();await page.evaluate(()=>grantLate());
 await expect.poll(()=>page.evaluate(()=>lateStream.getTracks().every(track=>track.readyState==='ended'))).toBe(true);
 expect(errors).toEqual([]);console.log('Voice recording: shared attachment menu, virtual microphone, preview, explicit attach, retained retry and late-permission cleanup passed.');
}finally{await browser.close();}
