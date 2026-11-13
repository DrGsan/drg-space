import {test,expect} from './installed-test.mjs';
import {createHmac} from 'node:crypto';
import {mkdirSync} from 'node:fs';
import {appSection} from './notes-ui.js';
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}

test('owner can schedule project deletion and restore its complete contents',async({page})=>{
 test.setTimeout(120000);const directory='artifacts/project-deletion-review';mkdirSync(directory,{recursive:true});
 let csrf='';const request=async(path,method='POST',body={})=>{const response=await page.request.fetch('/api'+path,{method,headers:{Origin:new URL(test.info().project.use.baseURL).origin,'X-CSRF-Token':csrf},data:method==='GET'?undefined:body});expect(response.status(),await response.text()).toBe(200);return response.json();};
 const password='Project-recovery-password-123';csrf=(await request('/setup','POST',{login:'review',password,token:'browser-test-setup-key'})).csrf;const enrollment=await request('/2fa/enroll','POST',{password});await request('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await request('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await request('/modules','PUT',{enabled:{tracker:true}});
 const project=await request('/tracker/projects','POST',{name:'DrG Space',key:'SITE',description:'Приложение, сайт и семейные фото'});
 await request('/tracker/projects/'+project.id+'/issues','POST',{title:'Проверить мобильное приложение',kind:'task',priority:3});
 await request('/tracker/projects/'+project.id+'/issues','POST',{title:'Сохранить выполненную задачу',status:'done',kind:'bug',priority:2});
 await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
 await page.setViewportSize({width:1280,height:900});await page.goto('/');await appSection(page,'Задачи');await page.locator('[data-tracker-project="'+project.id+'"]').click();await expect(page.locator('.tracker-card')).toHaveCount(2);await page.locator('[data-tracker-settings]').click();
 await expect(page.locator('[data-tracker-delete-project]')).toBeVisible();
 await page.screenshot({path:directory+'/after-desktop-settings.png'});
 await page.locator('[data-tracker-delete-project]').click();
 const confirmation=page.locator('[data-project-delete-form]');
 await expect(confirmation.locator('[type=submit]')).toBeDisabled();
 await confirmation.locator('[name=confirmation]').fill('Неверное название');await expect(confirmation.locator('[type=submit]')).toBeDisabled();
 await confirmation.locator('[name=confirmation]').fill(project.name);await expect(confirmation.locator('[type=submit]')).toBeEnabled();
 const concurrent=await request('/tracker/projects/'+project.id,'GET');await request('/tracker/projects/'+project.id,'PATCH',{version:concurrent.version,name:concurrent.name,description:concurrent.description});
 await confirmation.locator('[type=submit]').click();await expect(page.locator('.tracker-dialog-error button')).toBeVisible();await page.locator('.tracker-dialog-error button').click();await expect(confirmation.locator('[name=confirmation]')).toHaveValue('');await expect(confirmation.locator('[type=submit]')).toBeDisabled();await confirmation.locator('[name=confirmation]').fill(project.name);
 await page.screenshot({path:directory+'/after-desktop-confirm.png'});
 await confirmation.locator('[type=submit]').click();
 const restore=page.locator('[data-tracker-restore-project="'+project.id+'"]');
 await expect(restore).toBeVisible();await expect(page.locator('[data-tracker-project="'+project.id+'"]')).toHaveCount(0);
 await expect(page.locator('[data-project-delete-countdown]')).toContainText('д.');
 await page.screenshot({path:directory+'/after-desktop-projects.png'});
 const catalog=await request('/tracker','GET');expect(catalog.projects.some(x=>x.id===project.id)).toBe(false);expect(catalog.deleted_projects.some(x=>x.id===project.id)).toBe(true);
 expect((await page.request.get('/api/tracker/projects/'+project.id)).status()).toBe(404);
 await page.setViewportSize({width:390,height:844});await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));await page.reload();
 await page.getByRole('button',{name:'Задачи',exact:true}).click();await expect(restore).toBeVisible();await page.screenshot({path:directory+'/after-webapp-projects.png'});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await restore.click();await expect(page.locator('.tracker-project-heading h1')).toHaveText('DrG Space');await expect(page.locator('.tracker-card')).toHaveCount(2);
 const restored=await request('/tracker/projects/'+project.id,'GET');expect(restored.counts.todo).toBe(1);expect(restored.counts.done).toBe(1);
 await page.locator('[data-tracker-settings]').click();await page.locator('[data-tracker-delete-project]').scrollIntoViewIfNeeded();await page.screenshot({path:directory+'/after-webapp-settings.png'});await page.locator('[data-tracker-delete-project]').click();await page.locator('[name=confirmation]').fill(project.name);await page.screenshot({path:directory+'/after-webapp-confirm.png'});
 await page.locator('[data-cancel-project-delete]').click();expect((await request('/tracker/projects/'+project.id,'GET')).name).toBe(project.name);
 await page.setViewportSize({width:1280,height:900});
 await page.route('**/api/tracker/projects/'+project.id+'/issues?*',async route=>{const response=await route.fetch();const body=await response.json();body.project.role='manager';body.project.can_delete=false;await route.fulfill({response,json:body});});
 await page.locator('[data-tracker-refresh]').click();await page.locator('[data-tracker-settings]').click();await expect(page.locator('[data-tracker-delete-project]')).toHaveCount(0);

});
