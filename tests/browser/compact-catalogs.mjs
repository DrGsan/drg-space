import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundle=await build({stdin:{contents:"export {mountSocial} from './web/modules/social/index.js';export {auditLog} from './web/modules/social/lifecycle.js';export {mountFamilyPhotoAccess} from './web/modules/admin/family-photos.js';",resolveDir:process.cwd()},bundle:true,format:'iife',globalName:'Catalogs',write:false,loader:{'.css':'empty'}});
const css=await readFile('app/static/assets/app.css','utf8');
const browser=process.env.DRG_BROWSER_ENGINE==='webkit'?await webkit.launch({headless:true}):await chromium.launch({headless:true,channel:process.env.DRG_BROWSER_CHANNEL||undefined});
await mkdir('artifacts',{recursive:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://catalogs.test/**',route=>route.fulfill({body:'<div class="workspace" data-current-view="social"><aside class="sidebar"></aside><main class="content"><header class="topbar"></header><div id="view-content"></div></main></div>',contentType:'text/html'}));
 await page.goto('http://catalogs.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(async()=>{
   const people=Array.from({length:25},(_,i)=>({id:String(i).padStart(32,'0'),login:'person-'+i,display_name:'Человек '+i,space_role:i?'member':'owner',joined_at:1792500000}));
   const templates=['family','group','project','organization'];
   const spaces=Array.from({length:25},(_,i)=>({id:'space-'+i,kind:'family',template:templates[i%4],name:'Пространство '+i,members:people,notes:i,owner:true,manage:true,role:'owner',teams:{},advanced_available:i%4>=2}));
   spaces.push(...Array.from({length:12},(_,i)=>({id:'friend-'+i,kind:'friends',name:'Контакт '+i,members:[people[0],people[i+1]],notes:i,owner:false,manage:false})));
   const photoUsers=people.slice(1).map(p=>({id:p.id,login:p.login,profile:p,role:'member',enabled:false,groups:[]}));
   const ctx=window.fixture={state:{me:people[0]},moduleEnabled:()=>true,navigate(){},toast(){},async api(path,method='GET',data){
     if(path==='/social')return {spaces,requests:[]};if(path==='/notes')return [];
     if(path==='/family-photos/admin')return {users:photoUsers,groups:[{id:'one',title:'Общий фотоархив'}]};
     if(path.startsWith('/family-photos/admin/users/'))return data;
     throw Error('Unexpected request: '+path);
   }};
   await Catalogs.mountSocial(document.querySelector('#view-content'),ctx);
 });
 const spaces=page.locator('.social-catalog').filter({has:page.getByLabel('Поиск пространств',{exact:true})});
 const contacts=page.locator('.social-contact-catalog');
 await expect(spaces.locator('[data-social-name]:visible')).toHaveCount(10);await expect(contacts.locator('[data-social-name]:visible')).toHaveCount(10);
 await spaces.getByRole('button',{name:'Следующая страница: Поиск пространств',exact:true}).click();await expect(spaces.locator('.catalog-pagination [role=status]')).toHaveText('11–20 из 25');
 await spaces.getByLabel('Поиск пространств',{exact:true}).fill('Пространство 24');await expect(spaces.locator('[data-social-name]:visible')).toHaveCount(1);
 const family=spaces.locator('[data-social-name="Пространство 24"]');await family.locator(':scope>summary').click();
 await expect(family.locator('[data-people-row]')).toHaveCount(10);await expect(family.getByRole('columnheader',{name:'Команды',exact:true})).toHaveCount(0);
 await family.getByLabel('Поиск участников',{exact:true}).fill('person-24');await expect(family.locator('[data-people-row]')).toHaveCount(1);
 await page.getByRole('button',{name:'Обновить приглашения',exact:true}).click();await expect(spaces.getByLabel('Поиск пространств',{exact:true})).toHaveValue('Пространство 24');
 await expect(family.locator('[data-people-row]')).toHaveCount(1);
 for(const width of [1440,1024,768,700,390,320])for(const scheme of ['light','dark']){
   await page.setViewportSize({width,height:1000});await page.evaluate(s=>document.documentElement.dataset.scheme=s,scheme);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert(await family.locator('.space-table-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   await page.screenshot({path:`artifacts/compact-social-${width}-${scheme}.png`,fullPage:true});
 }
 await spaces.getByLabel('Поиск пространств',{exact:true}).fill('');await spaces.getByLabel('Фильтр: Поиск пространств',{exact:true}).selectOption('organization');
 await expect(spaces.locator('[data-social-name]:visible')).toHaveCount(6);
 await contacts.getByLabel('Поиск контактов',{exact:true}).fill('person-12');await expect(contacts.locator('[data-social-name]:visible')).toHaveCount(1);
 await contacts.locator('[data-social-name]:visible>summary').click();await expect(contacts.locator('.social-member:visible')).toHaveCount(1);
 await page.evaluate(()=>{
   const host=document.querySelector('#view-content');host.innerHTML='<details class="preference-row" open><summary>Семейные фото</summary><div class="panel admin-family-photos" id="photos"></div></details>';
   fixture.state.me.role='owner';Catalogs.mountFamilyPhotoAccess(document.querySelector('#photos'),fixture);
 });
 await expect(page.locator('[data-fp-user]:visible')).toHaveCount(10);
 const first=page.locator('[data-fp-user]:visible').first();await first.locator('summary').click();await first.locator('[name=enabled]').check();await first.locator('[name=group]').check();
 await page.getByLabel('Поиск доступа к фото',{exact:true}).fill('person-24');await expect(page.locator('[data-fp-user]:visible')).toHaveCount(1);
 await page.getByLabel('Поиск доступа к фото',{exact:true}).fill('');await expect(first.locator('[name=group]')).toBeChecked();
 await first.getByRole('button',{name:'Сохранить доступ',exact:true}).click();await expect(first.locator('[data-fp-user-state]')).toHaveText('Включён');
 await expect(first.locator('[data-fp-user-groups]')).toHaveText('Общий фотоархив');
 await page.getByRole('button',{name:'Следующая страница: Поиск доступа к фото',exact:true}).click();await expect(page.locator('.catalog-pagination [role=status]')).toHaveText('11–20 из 24');
 for(const width of [1440,768,390,320]){
   await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:`artifacts/compact-photo-access-${width}.png`,fullPage:true});
 }
 await page.evaluate(async()=>{
   fixture.api=async()=>({events:[{created:1792500000,event:'member_removed',person:{login:'person-24',display_name:'Человек 24'},details:{},display:{page:'Длинное название общего документа '.repeat(8)}}],next:null});
   await Catalogs.auditLog(document.querySelector('#view-content'),fixture,{id:'space-0'});
 });
 for(const width of [1440,768,320]){
   await page.setViewportSize({width,height:1000});assert(await page.locator('.space-audit-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   await expect(page.locator('.space-audit-table')).toContainText('Длинное название общего документа');
   await page.screenshot({path:`artifacts/compact-audit-${width}.png`,fullPage:true});
 }
 assert.deepEqual(errors,[]);console.log('Compact catalogs passed: 25 spaces, 12 contacts, 25 family members, 24 photo access entries; search/type filter/pagination, preserved draft and refresh, 320–1440px.');
}finally{await browser.close();}
