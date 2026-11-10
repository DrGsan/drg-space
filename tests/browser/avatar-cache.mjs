import {expect} from '@playwright/test';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({stdin:{contents:`export {personAvatar} from './web/core/people.js';export {renderMarkup} from './web/core/dom.js';export {clearAvatarCache} from './web/core/avatar-cache.js';`,resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'Avatars',write:false});
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
try{
 const page=await browser.newPage({locale:'ru-RU'});let requests=[];
 const {readFile}=await import('node:fs/promises'),image=await readFile('tests/fixtures/blue.png');
 await page.route('http://avatar.test/**',route=>{const url=new URL(route.request().url());if(url.pathname.startsWith('/api/')){requests.push(url.searchParams.get('v'));return route.fulfill({contentType:'image/png',headers:{'Cache-Control':'private, no-store'},body:image});}return route.fulfill({contentType:'text/html',body:'<main></main>'});});
 await page.goto('http://avatar.test/');await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(()=>{window.friend={id:'friend',login:'friend',avatar_url:'/api/users/friend/avatar?v=one'};window.draw=count=>Avatars.renderMarkup(document.querySelector('main'),Array.from({length:count},(_,i)=>`<article data-message-id="${i}">${Avatars.personAvatar(friend)}<p>Сообщение ${i}</p></article>`).join(''));draw(1);});
 await expect.poll(()=>page.locator('img').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
 await page.evaluate(()=>window.originalAvatar=document.querySelector('img'));
 for(const count of [2,10,40,120]){await page.evaluate(count=>draw(count),count);await expect(page.locator('img')).toHaveCount(count);}
 await expect.poll(()=>page.locator('img').evaluateAll(images=>images.every(img=>img.complete&&img.naturalWidth>0))).toBe(true);
 expect(requests).toEqual(['one']);expect(await page.evaluate(()=>originalAvatar===document.querySelector('img'))).toBe(true);
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('drg-presence-change',{detail:[{...friend,avatar_url:'/api/users/friend/avatar?v=two'}]})));
 await expect.poll(()=>page.locator('img').evaluateAll(images=>new Set(images.map(img=>img.src)).size===1&&images.every(img=>img.complete&&img.naturalWidth>0))).toBe(true);
 await page.evaluate(()=>draw(121));await expect(page.locator('img')).toHaveCount(121);
 await expect.poll(()=>page.locator('img').evaluateAll(images=>images.every(img=>img.complete&&img.naturalWidth>0))).toBe(true);
 expect(requests).toEqual(['one','two']);
 await page.evaluate(()=>document.dispatchEvent(new CustomEvent('drg-presence-change',{detail:[{...friend,avatar_url:null}]})));
 await expect(page.locator('img')).toHaveCount(0);await page.evaluate(()=>draw(122));await expect(page.locator('img')).toHaveCount(0);
 await page.evaluate(()=>{Avatars.clearAvatarCache();draw(1);});
 await expect.poll(()=>page.locator('img').evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);expect(requests).toEqual(['one','two','one']);
 console.log('Avatar cache passed: 122 messages, one request per version, stable DOM, photo change/removal and session reset.');
}finally{await browser.close();}
