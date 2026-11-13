import {test,expect} from './installed-test.mjs';
import {createHmac} from 'node:crypto';
import {mkdirSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {appSection} from './notes-ui.js';
import {notesMenusFlow} from './notes-menus-flow.js';
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}


test.use({hasTouch:true});

test('read-only file downloads, localized releases and independent search clearing',async({page})=>{
 test.setTimeout(120000);const before=process.env.DRG_REVIEW_BEFORE==='1',phase=before?'before':'after',directory='artifacts/note-downloads-release-language-search';mkdirSync(directory,{recursive:true});
 let csrf='';const origin='http://127.0.0.1:8001',password='Review-password-123';
 const api=async(path,method='GET',data)=>{const r=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),await r.text()).toBeTruthy();return r.json();};
 csrf=(await api('/setup','POST',{login:'downloadreview',password,token:'browser-test-setup-key'})).csrf;const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await api('/appearance','PUT',{theme:'ocean',mode:'light',style:'glass',wallpaper:'dunes',seasonal:'off'});
 await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
 await api('/modules','PUT',{enabled:{tracker:true}});
 const note=await api('/notes','POST',{title:'Кубер · демонстрация',body:''}),files=[];
 for(const [i,name] of ['TEST.conf','Тестовый файл с пробелами.yaml','manual.pdf'].entries()){
  const bytes=Buffer.from('Demo attachment '+i+'\nNo real credentials.\n'),r=await page.request.post('/api/notes/'+note.id+'/files?name='+encodeURIComponent(name),{headers:{Origin:origin,'X-CSRF-Token':csrf,'Content-Type':'application/octet-stream'},data:bytes});expect(r.ok(),await r.text()).toBeTruthy();files.push({...await r.json(),bytes});
 }
 const blocks=files.map((file,i)=>({id:'file-'+i,type:'file',props:{name:file.name,url:origin+file.url},children:[]}));
 await api('/notes/'+note.id,'PUT',{title:note.title,body:'',blocks});const original=await api('/notes/'+note.id);
 await page.route('**/api/notes/'+note.id,async route=>{const r=await route.fetch(),data=await r.json();await route.fulfill({json:{...data,role:'viewer'}});});
 await page.goto('/page/'+note.id);await expect(page.locator('[data-note-file-menu]')).toHaveCount(3);
 await page.screenshot({path:directory+'/file-after-desktop.png'});
 for(const [i,file]of files.entries()){
  const action=page.locator('[data-note-file-menu="file-'+i+'"]');if(i===0){await action.focus();await expect(action).toBeFocused();await action.press('Enter');}else await action.click();const menu=page.locator('#note-file-menu');await expect(menu.getByRole('menuitem')).toHaveCount(1);await expect(menu.locator('input,[data-page-font],[data-page-small]')).toHaveCount(0);await expect(menu.getByRole('menuitem')).toBeFocused();if(i===0)await page.screenshot({path:directory+'/file-menu-after-desktop.png'});const event=page.waitForEvent('download');await menu.getByRole('menuitem',{name:'Скачать',exact:true}).press('Enter');const download=await event;expect(download.suggestedFilename().normalize('NFC')).toBe(file.name.normalize('NFC'));expect(await readFile(await download.path())).toEqual(file.bytes);
 }
 expect((await api('/notes/'+note.id)).blocks).toEqual(original.blocks);expect((await api('/notes/'+note.id)).version).toBe(original.version);
 await page.route('**'+files[0].url,r=>r.request().method()==='HEAD'?r.fulfill({status:404,json:{detail:'unavailable'}}):r.continue());
 await page.locator('[data-note-file-menu="file-0"]').click();await page.locator('#note-file-menu').getByRole('menuitem',{name:'Скачать',exact:true}).click();await expect(page.locator('.note-file-error').first()).toBeVisible();
 await page.setViewportSize({width:390,height:844});await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true,configurable:true}));await page.reload();await expect(page.locator('[data-note-file-menu]')).toHaveCount(3);await page.screenshot({path:directory+'/file-after-webapp.png'});await page.locator('[data-note-file-menu="file-2"]').tap();await expect(page.locator('#note-file-menu')).toBeVisible();const popup=await page.locator('#note-file-menu').boundingBox();expect(popup.x).toBeGreaterThanOrEqual(0);expect(popup.x+popup.width).toBeLessThanOrEqual(390);await page.screenshot({path:directory+'/file-menu-after-webapp.png'});const tapped=page.waitForEvent('download');await page.locator('#note-file-menu').getByRole('menuitem',{name:'Скачать',exact:true}).tap();const mobileDownload=await tapped;expect(await readFile(await mobileDownload.path())).toEqual(files[2].bytes);
 await page.setViewportSize({width:1280,height:900});await page.reload();await appSection(page,'Настройки');
 const about=page.locator('[data-preference-key=about]');await about.scrollIntoViewIfNeeded();await page.screenshot({path:directory+'/release-'+phase+'-ru.png'});
 if(!before){await expect(page.locator('[data-app-release]')).toBeVisible();expect(await page.locator('[data-release-changes]').innerText()).toMatch(/[А-Яа-я]/);}
 if(!before){
  const table=JSON.parse(await readFile('locales/ru/release_notes.json','utf8')),prefix='release5_0_2';
  const historical={version:'5.0.2',date:'2026-11-11',title:table[prefix+'Title'],changes:[1,2,3].map(i=>table[prefix+'Change'+i])};
  const route=async r=>{const response=await r.fetch(),data=await response.json();await r.fulfill({json:{...data,app_version:historical.version,app_release:historical}});};
  await page.route('**/api/me',route);await page.reload();await appSection(page,'Настройки');
  await expect(page.locator('[data-app-release] .muted')).toHaveText(historical.title);await expect(page.locator('[data-release-changes] li')).toHaveText(historical.changes);await page.locator('[data-app-release]').scrollIntoViewIfNeeded();await page.screenshot({path:directory+'/release-5.0.2-after-ru.png'});
  await page.unroute('**/api/me',route);await page.reload();
 }
 const project=await api('/tracker/projects','POST',{name:'DrG Space',key:'SPACE'});await api('/tracker/projects/'+project.id+'/issues','POST',{title:'Проверить github',priority:3});await api('/tracker/projects/'+project.id+'/issues','POST',{title:'Другая задача',priority:1,status:'done'});
 await appSection(page,'Задачи');await page.locator('[data-tracker-project="'+project.id+'"]').click();await page.locator('[data-tracker-unfinished]').click();await page.locator('[data-tracker-search]').fill('github');await expect(page.locator('.tracker-card')).toHaveCount(1);await page.screenshot({path:directory+'/search-'+phase+'-desktop.png'});
 if(!before){
  const search=page.locator('[data-tracker-search]'),clear=page.locator('[data-tracker-search-clear]');
  const centered=async()=>{await expect(search).toBeVisible();await expect(clear).toBeVisible();const input=await search.boundingBox(),button=await clear.boundingBox();expect(Math.abs(input.y+input.height/2-button.y-button.height/2)).toBeLessThan(1.1);expect(input.x+input.width).toBeLessThanOrEqual(button.x+1);};
  await centered();await clear.focus();await page.keyboard.press('Enter');await expect(search).toHaveValue('');await expect(search).toBeFocused();await expect(clear).toBeHidden();
  // Clearing search retains the independent unfinished filter.
  await expect(page.locator('.tracker-card')).toHaveCount(1);await page.locator('[data-tracker-unfinished]').click();await expect(page.locator('.tracker-card')).toHaveCount(2);
  await search.fill('github');await expect(page.locator('.tracker-card')).toHaveCount(1);
  await page.evaluate(()=>document.documentElement.style.zoom='1.5');await centered();await page.evaluate(()=>document.documentElement.style.zoom='');
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await expect(search).toBeVisible();await expect(search).toHaveValue('github');await expect(page.locator('.tracker-card')).toHaveCount(1);await centered();await page.screenshot({path:directory+'/search-after-webapp.png'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await clear.click();await expect(search).toHaveValue('');await expect(clear).toBeHidden();
  await api('/language','PUT',{language:'en'});await api('/appearance','PUT',{mode:'dark'});await page.reload();
  await page.setViewportSize({width:1280,height:900});await appSection(page,'Settings');
  const enAbout=page.locator('[data-preference-key=about]');if(await enAbout.getAttribute('open')===null)await enAbout.locator(':scope > summary').click();
  await enAbout.scrollIntoViewIfNeeded();expect(await page.locator('[data-app-release]').innerText()).not.toMatch(/[А-Яа-я]/);await page.screenshot({path:directory+'/release-after-dark-en.png'});
  await page.setViewportSize({width:390,height:844});await page.goto('/page/'+note.id);await expect(page.locator('[data-note-file-menu]').first()).toHaveAttribute('aria-label','Actions for TEST.conf');await page.locator('[data-note-file-menu]').first().click();await expect(page.locator('#note-file-menu').getByRole('menuitem',{name:'Download',exact:true})).toBeVisible();
  await page.screenshot({path:directory+'/file-after-dark-en-webapp.png'});
  await page.keyboard.press('Escape');await expect(page.locator('#note-file-menu')).toHaveCount(0);
  await api('/language','PUT',{language:'ru'});await api('/appearance','PUT',{mode:'light'});await page.setViewportSize({width:1280,height:900});await page.reload();await expect(page.locator('html')).toHaveAttribute('lang','ru');await expect(page.locator('.workspace')).toBeVisible();
  await test.step('Existing full page and block menus remain available',()=>notesMenusFlow(page));
 }

});
