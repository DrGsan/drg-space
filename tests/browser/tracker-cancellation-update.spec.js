import {test,expect} from './installed-test.mjs';
import {createHmac} from 'node:crypto';
import {mkdirSync} from 'node:fs';
import {appSection} from './notes-ui.js';

function totp(secret){
 let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
 const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
 return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

test('cancellation, independent children, projections and privileged update hint',async({page})=>{
 test.setTimeout(120000);const before=process.env.DRG_REVIEW_BEFORE==='1',phase=before?'before':'after';
 mkdirSync('artifacts/task-cancellation-update',{recursive:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setViewportSize({width:1440,height:1000});
 await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
 await page.route('**/api/app-update',r=>r.fulfill({json:{available:true,version:'9.0.0',url:'https://github.com/DrGsan/drg-space/releases/tag/v9.0.0'}}));
 let csrf='';const origin='http://127.0.0.1:8001',password='Review-password-123';
 async function api(path,method='GET',data){
  const r=await page.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
  expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
 }
 csrf=(await api('/setup','POST',{login:'reviewowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});
 csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await api('/appearance','PUT',{theme:'ocean',mode:'light',style:'glass',wallpaper:'dunes',seasonal:'off'});
 await api('/modules','PUT',{enabled:{tracker:true}});
 const project=await api('/tracker/projects','POST',{name:'DrG Space',key:'SPACE'});
 const create=data=>api('/tracker/projects/'+project.id+'/issues','POST',data);
 const epic=await create({title:'Обновление рабочего пространства',kind:'epic',status:'work'});
 const task=await create({title:'Старый вариант интерфейса',parent_id:epic.id,description:'Сохраняем описание и историю решений.'});
 const child=await create({title:'Проверить доступность на телефоне',parent_id:task.id,status:'work'});
 await create({title:'Согласовать требования',parent_id:epic.id,status:'done'});
 await page.goto('/');await appSection(page,'Задачи');await page.locator('[data-tracker-project="'+project.id+'"]').click();
 await expect(page.locator('.tracker-card')).toHaveCount(4);
 if(!before){
  await expect(page.locator('[data-status-column=cancelled]')).toBeVisible();
  await page.locator('[data-tracker-issue="'+task.id+'"]').click();
  await page.locator('select[name=status]').selectOption('cancelled');
  await page.getByLabel('Причина отмены (необязательно)').fill('Выбрали новый вариант');
  await page.getByRole('button',{name:'Сохранить',exact:true}).click();
  await expect.poll(async()=> (await api('/tracker/issues/'+task.id+'?include_cancelled=true')).status).toBe('cancelled');
  expect((await api('/tracker/issues/'+child.id)).status).toBe('work');
  expect((await api('/tracker/issues/'+epic.id)).progress).toEqual({total:1,done:1,cancelled:1});
  await page.locator('[data-tracker-back]').click();
  await expect(page.locator('[data-status-column=cancelled]')).toContainText(task.title);
  await expect(page.locator('[data-tracker-issue="'+epic.id+'"] .tracker-cancelled-count')).toContainText('1');
  await page.locator('[data-tracker-unfinished]').click();
  await expect(page.locator('[data-tracker-issue="'+task.id+'"]')).toHaveCount(0);
  await page.locator('[data-tracker-unfinished]').click();
 }
 await page.screenshot({path:'artifacts/task-cancellation-update/'+phase+'-board.png'});
 if(!before){
  for(const layout of ['list','hierarchy','table']){
   await page.locator('[data-tracker-layout]').selectOption(layout);
   await expect(page.locator(layout==='table'?'[data-table-issue="'+task.id+'"]':'[data-tracker-issue="'+task.id+'"]')).toBeVisible();
  }
  await page.locator('[data-table-issue="'+task.id+'"] [data-field=status]').selectOption('work');
  await expect.poll(async()=> (await api('/tracker/issues/'+task.id+'?include_cancelled=true')).status).toBe('work');
  const reopened=await api('/tracker/issues/'+task.id);
  expect(reopened.cancellation_reason).toBeUndefined();
  expect(reopened.history.some(h=>h.payload.cancellation_reason?.after==='Выбрали новый вариант')).toBe(true);
  await page.locator('[data-tracker-layout]').selectOption('board');
 }
 await page.setViewportSize({width:390,height:844});
 if(!before){await page.locator('[data-tracker-column=work]').click();await expect(page.locator('[data-status-column=work]')).toContainText(child.title);}
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'artifacts/task-cancellation-update/'+phase+'-mobile.png'});
 await appSection(page,'Настройки');
 for(const row of await page.locator('.preference-row').all())if(await row.getAttribute('data-preference-key')!=='about'&&await row.getAttribute('open')!==null)await row.locator(':scope > summary').click();
 const aboutRow=page.locator('[data-preference-key=about]');if(await aboutRow.getAttribute('open')===null)await aboutRow.locator(':scope > summary').click();
 await page.locator('[data-app-version]').scrollIntoViewIfNeeded();await expect(page.locator('[data-app-version]')).toBeVisible();
 if(!before){
  await expect(page.locator('[data-app-update]')).toContainText('Доступно обновление 9.0.0');
  await expect(page.locator('[data-app-update] a')).toHaveAttribute('href','https://github.com/DrGsan/drg-space/releases/tag/v9.0.0');
  const version=await page.locator('[data-app-version]').boundingBox(),update=await page.locator('[data-app-update]').boundingBox();
  expect(update.y).toBeGreaterThanOrEqual(version.y+version.height);
 }
 await page.screenshot({path:'artifacts/task-cancellation-update/'+phase+'-about-mobile.png'});
 if(!before){
  await page.setViewportSize({width:1440,height:1000});
  const sidebarVersion=await page.locator('[data-sidebar-version]').boundingBox(),sidebarUpdate=await page.locator('[data-sidebar-update]').boundingBox();
  expect(sidebarUpdate.y).toBeGreaterThanOrEqual(sidebarVersion.y+sidebarVersion.height);
  await page.screenshot({path:'artifacts/task-cancellation-update/after-about.png'});
  await api('/language','PUT',{language:'en'});
  await page.reload();await appSection(page,'Settings');
  const about=page.locator('[data-preference-key=about]');if(await about.getAttribute('open')===null)await about.locator(':scope > summary').click();
  await expect(page.locator('[data-app-update]')).toContainText('Update 9.0.0 available');
  await api('/appearance','PUT',{mode:'dark'});await page.reload();await appSection(page,'Settings');
  await page.locator('[data-app-version]').scrollIntoViewIfNeeded();await expect(page.locator('[data-app-update]')).toBeVisible();
  await page.screenshot({path:'artifacts/task-cancellation-update/after-about-dark-en.png'});
  await page.route('**/api/app-update',r=>r.fulfill({json:{available:false}}));
  await page.reload();await appSection(page,'Settings');
  await expect(page.locator('[data-app-update]')).toBeHidden();
 }
 expect(errors).toEqual([]);
});
