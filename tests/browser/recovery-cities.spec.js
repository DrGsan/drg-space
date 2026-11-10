import {test,expect} from '@playwright/test';
import {createHmac} from 'node:crypto';
import {mkdirSync} from 'node:fs';
import {appSection} from './notes-ui.js';

function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const h=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),o=h[19]&15;return String((h.readUInt32BE(o)&0x7fffffff)%1000000).padStart(6,'0');}
test('list recovery and worldwide city configuration',async({page})=>{
 test.setTimeout(120000);const dir='artifacts/recovery-cities-review';mkdirSync(dir,{recursive:true});
 let csrf='';const post=async(path,data={})=>{const r=await page.request.post('/api'+path,{headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':csrf},data});expect(r.status(),await r.text()).toBe(200);return r.json();};
 const password='Recovery-test-password-123';csrf=(await post('/setup',{login:'review',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await post('/2fa/enroll',{password});await post('/2fa/confirm',{code:totp(enrollment.secret)});csrf=(await post('/2fa/activate',{saved_recovery_codes:true})).csrf;
 const list=await post('/task-lists',{name:'Поездка на Кипр'});await post('/tasks',{title:'Забронировать отель',list_id:list.id});await post('/tasks',{title:'Купить билеты',list_id:list.id,done:true});
 await page.route('**/api/widgets/*/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
 await page.goto('/');await appSection(page,'Напоминания');await page.locator(`[data-task-list="${list.id}"]`).click();await page.locator('[data-list-settings]').click();
 await page.locator('[data-delete-task-list]').click();await expect(page.locator('.tasks-inline-confirm')).toContainText('24');
 await page.screenshot({path:dir+'/after-reminders-confirm.png'});
 await page.locator('[data-confirm-delete-list]').click();await expect(page.locator('[data-restore-list]')).toBeVisible();
 await page.screenshot({path:dir+'/after-reminders.png'});
 await page.locator('[data-restore-list]').click();await expect(page.locator('[data-task-view-title]')).toHaveText('Поездка на Кипр');
 await expect(page.locator('[data-task-item]')).toHaveCount(1);
 await page.locator('[data-close-task-panel]').click().catch(async()=>{await page.keyboard.press('Escape');});
 await appSection(page,'Главная');await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
 let dialog=page.getByRole('dialog').last();await dialog.getByLabel('Новый виджет').selectOption('clock');await dialog.getByRole('button',{name:'Добавить виджет',exact:true}).click();await dialog.locator('[data-configure]').last().click();
 await dialog.locator('[data-city-query]').fill('Лимасол');await expect(dialog.locator('[data-city="0"]')).toContainText('Кипр');await page.screenshot({path:dir+'/after-clock-search.png'});await dialog.locator('[data-city="0"]').click();
 await expect(dialog.locator('[name=clock_timezone]')).toHaveValue('Asia/Nicosia');
 await page.screenshot({path:dir+'/after-clock.png'});
 await dialog.getByRole('button',{name:'Сохранить виджет',exact:true}).click();
 dialog=page.getByRole('dialog').last();await dialog.getByRole('button',{name:'Сохранить настройки',exact:true}).click();
 await expect(dialog).not.toBeVisible();await expect.poll(async()=>{const layout=await (await page.request.get('/api/dashboard/layout')).json();return layout.instances.filter(x=>x.module_id==='clock').map(x=>x.config.timezone);}).toContain('Asia/Nicosia');
 const enable=await page.request.put('/api/modules',{headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':csrf},data:{enabled:{weather:true}}});expect(enable.status()).toBe(200);
 await page.reload();await page.getByRole('button',{name:'Настроить главную',exact:true}).click();dialog=page.getByRole('dialog').last();await dialog.getByLabel('Новый виджет').selectOption('weather');await dialog.getByRole('button',{name:'Добавить виджет',exact:true}).click();await dialog.locator('[data-configure]').last().click();
 await dialog.locator('[data-city-query]').fill('Лимасол');await expect(dialog.locator('[data-city="0"]')).toContainText('Кипр');await page.screenshot({path:dir+'/after-weather.png'});await dialog.locator('[data-city="0"]').click();await dialog.getByRole('button',{name:'Сохранить виджет',exact:true}).click();dialog=page.getByRole('dialog').last();await dialog.getByRole('button',{name:'Сохранить настройки',exact:true}).click();await expect(dialog).not.toBeVisible();
 await expect.poll(async()=>{const saved=(await (await page.request.get('/api/dashboard/layout')).json()).instances;return saved.some(x=>x.module_id==='weather' && x.config.latitude>34.6 && x.config.latitude<34.8 && x.config.city==='Лимасол');}).toBe(true);
 await page.setViewportSize({width:390,height:844});await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));await page.reload();
 await page.getByRole('button',{name:'Дела',exact:true}).click();await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator(`[data-task-list="${list.id}"]`).click();await page.locator('[data-list-settings]').click();await page.locator('[data-delete-task-list]').click();await page.locator('[data-confirm-delete-list]').click();await page.locator('[data-toggle-tasks-sidebar]').click();
 await expect(page.locator('[data-restore-list]')).toBeVisible();await page.screenshot({path:dir+'/after-webapp-reminders.png'});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
