import {test,expect} from '@playwright/test';
import {createHmac} from 'node:crypto';
import {mkdirSync} from 'node:fs';
import {appSection} from './notes-ui.js';
function totp(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret)
    bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8)
    bytes.push(parseInt(bits.slice(i, i + 8), 2));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = createHmac("sha1", Buffer.from(bytes)).update(counter).digest();
  const offset = hash[19] & 15;
  return String((hash.readUInt32BE(offset) & 0x7fffffff) % 1000000).padStart(
    6,
    "0",
  );
}

async function api(page,path,method='GET',data){
 const response=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:r.status,body:await r.json()};},{path,method,data});expect(response.status,JSON.stringify(response.body)).toBe(200);return response.body;
}
test('tracker opt-in, complete import, edits, conflict, files and responsive navigation',async({page,browser})=>{
 test.setTimeout(180000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 mkdirSync('artifacts/tracker-review',{recursive:true});
 await page.setViewportSize({width:1440,height:1000});
 await page.route('**/api/widgets/home_rss/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  await page.goto("/");
  await page
    .getByLabel("Ключ первичной настройки")
    .fill("browser-test-setup-key");
  await page.getByLabel("Логин", { exact: true }).fill("demoowner");
  await page
    .getByLabel("Пароль", { exact: true })
    .fill("Test-long-password-123");
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await page.getByLabel("Подтвердите пароль").fill("Test-long-password-123");
  await page.getByRole("button", { name: "Настроить аутентификатор" }).click();
  const secret = await page.locator(".secret").textContent();
  await page.getByLabel("Код из приложения").fill(totp(secret));
  await page.getByRole("button", { name: "Проверить код" }).click();
  await expect(page.locator(".recovery")).toBeVisible();
  const recoveryCodes = (await page.locator(".recovery").textContent()).trim().split("\n");
  await page.getByLabel("Я сохранил резервные коды").check();
  await page.getByRole("button", { name: "Открыть пространство" }).click();

 await expect(page.locator('.workspace')).toBeVisible();
 expect((await api(page,'/modules')).enabled.tracker).toBe(false);
 await page.screenshot({path:'artifacts/tracker-review/before-desktop.png'});
 const list=await api(page,'/task-lists','POST',{name:'DrG Space (Баги)',retain_completed:true});
 const parent=await api(page,'/tasks','POST',{title:'Web App',list_id:list.id,notes:'Группа задач веб-приложения'});
 const child=await api(page,'/tasks','POST',{title:'Плавная загрузка виджетов',list_id:list.id,parent_id:parent.id,notes:'Описание и результаты проверки',tags:['Интерфейс'],priority:2});
 await api(page,'/tasks/'+child.id,'PATCH',{done:true});
 await appSection(page,'Напоминания');await page.locator('[data-task-list="'+list.id+'"]').click();
 await page.screenshot({path:'artifacts/tracker-review/before-desktop.png'});
 await api(page,'/modules','PUT',{enabled:{tracker:true}});
 const project=await api(page,'/tracker/import-reminders','POST',{list_id:list.id,name:'DrG Space',key:'DRG'});
 await page.reload();await appSection(page,'Задачи');
 await page.locator('[data-tracker-project="'+project.id+'"]').click();
 await expect(page.locator('.tracker-card')).toHaveCount(2);
 expect(await page.locator('.tracker-board').evaluate(el=>el.getBoundingClientRect().top)).toBeLessThan(175);
 await expect(page.locator('.tracker-priority')).toHaveCount(2);
 // Settings keep the custom key, crop artwork locally and preserve issue IDs.
 await page.locator('[data-tracker-settings]').click();
 const settings=page.locator('dialog[data-unused],dialog.tracker-dialog').first();
 await settings.locator('[name=key]').fill('SITE');
 await settings.locator('[data-avatar-file]').setInputFiles('app/static/assets/wallpapers/mountains.webp');
 const crop=page.locator('dialog.tracker-crop');await expect(crop).toBeVisible();
 await crop.locator('input[type=range]').fill('1.2');await crop.getByRole('button',{name:'Применить',exact:true}).click();
 await settings.getByRole('button',{name:'Сохранить',exact:true}).click();
 await expect(page.locator('.tracker-project-heading img')).toBeVisible();
 expect((await api(page,'/tracker/issues/'+parent.id)).key).toBe('SITE-1');
 // Groups are containers; only an explicit type change makes an epic.
 await page.locator('[data-tracker-groups]').click();await page.getByRole('button',{name:'Создать группу',exact:true}).click();
 const groupForm=page.locator('[data-group-form]');await groupForm.locator('[name=name]').fill('Web App');await groupForm.locator('[name=description]').fill('Заметки группы сохраняются отдельно от задач.');await groupForm.getByRole('button',{name:'Создать',exact:true}).click();
 await expect(groupForm).not.toBeVisible();
 await expect.poll(async()=> (await api(page,'/tracker/projects/'+project.id)).groups.length).toBe(1);
 const currentProject=await api(page,'/tracker/projects/'+project.id),group=currentProject.groups[0];
 await api(page,'/tracker/issues/'+parent.id,'PATCH',{version:1,group_id:group.id,kind:'epic'});
 await page.locator('[data-tracker-refresh]').click();
 await page.locator('[data-tracker-group]').selectOption(group.id);await expect(page.locator('.tracker-card')).toHaveCount(2);
 await expect(page.locator('.tracker-kind[data-kind=epic]')).toBeVisible();
 await page.locator('[data-tracker-group]').selectOption('*');
 await expect(page.locator('.tracker-card')).toHaveCount(2);
 await page.locator('.tracker-context summary').click();await expect(page.locator('.tracker-context-menu [data-tracker-context]')).toHaveCount(1);await page.locator('.tracker-context summary').click();
 await expect(page.locator('.tracker-access')).toHaveCount(0);
 await page.locator('[data-tracker-layout]').selectOption('list');await expect(page.locator('.tracker-list-row .tracker-priority')).toHaveCount(2);await page.locator('[data-tracker-layout]').selectOption('board');
 await expect(page.locator('[data-status-column=done]')).toContainText('Плавная загрузка виджетов');
 await page.screenshot({path:'artifacts/tracker-views-review/after-desktop.png'});
 await page.locator('[data-tracker-new-issue]').first().click();
 const dialog=page.locator('dialog.tracker-dialog');await dialog.getByLabel('Название',{exact:true}).fill('Новый раздел проектов');
 await dialog.getByLabel('Описание',{exact:true}).fill('Контексты, доска и карточка задачи.');
 await dialog.getByRole('button',{name:'Создать',exact:true}).click();
 await expect(page.locator('[data-tracker-edit] [name=title]')).toHaveValue('Новый раздел проектов');
 const issues=(await api(page,'/tracker/projects/'+project.id+'/issues')).items;
 const item=issues.find(i=>i.title==='Новый раздел проектов');
 await page.locator('[name=status]').selectOption('work');
 await page.locator('[name=priority]').selectOption('3');
 await page.locator('[name=due]').fill('2026-11-15');
 await page.locator('[name=tags]').fill('Интерфейс, Первый этап');
 await page.locator('[name=merge_request_url]').fill('https://git.example/team/site/merge_requests/56');
 await page.getByRole('button',{name:'Сохранить',exact:true}).click();
 await expect.poll(async()=> (await api(page,'/tracker/issues/'+item.id)).status).toBe('work');
 expect((await api(page,'/tracker/issues/'+item.id)).merge_request_url).toBe('https://git.example/team/site/merge_requests/56');
 await page.locator('[data-tracker-comment] textarea').fill('Проверим в тестовой ветке.');
 await page.locator('[data-tracker-comment] button').click();
 await expect(page.locator('.tracker-comment')).toContainText('Проверим в тестовой ветке.');
 await page.locator('[data-tracker-files]').setInputFiles({name:'check.txt',mimeType:'text/plain',buffer:Buffer.from('Tracker attachment')});
 await expect(page.locator('[data-tracker-attachment]')).toContainText('check.txt');
 await page.screenshot({path:'artifacts/tracker-views-review/after-desktop-issue.png',fullPage:true});
 // Another editor changes the version while the local form is dirty.
 const remote=await api(page,'/tracker/issues/'+item.id);await page.locator('textarea[name=description]').fill('Локальный черновик');
 await api(page,'/tracker/issues/'+item.id,'PATCH',{version:remote.version,title:'Изменено другим участником'});
 await page.getByRole('button',{name:'Сохранить',exact:true}).click();
 await expect(page.locator('[data-tracker-error]')).toBeVisible();
 await expect(page.locator('textarea[name=description]')).toHaveValue('Локальный черновик');
 expect((await api(page,'/tracker/issues/'+item.id)).description).not.toBe('Локальный черновик');
 await page.reload();await appSection(page,'Задачи');await expect(page.locator('.tracker-board')).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'artifacts/tracker-views-review/after-webapp.png'});
 await page.locator('[data-tracker-column=work]').click();
 await page.locator('[data-tracker-issue="'+item.id+'"]').click();
 await expect(page.locator('[name=title]')).toHaveValue('Изменено другим участником');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'artifacts/tracker-views-review/after-webapp-issue.png',fullPage:true});
 await page.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true,configurable:true}));
 await page.reload();await page.locator('[data-mobile-view=tracker]').click();
 await expect(page.locator('.tracker-board')).toBeVisible();
 expect(await page.locator('.tracker-card').first().evaluate(el=>el.getBoundingClientRect().top)).toBeLessThan(230);
 await page.locator('[data-tracker-search-toggle]').click();await page.locator('[data-tracker-search]').fill('Web App');
 await expect(page.locator('.tracker-card')).toHaveCount(1);await page.locator('[data-tracker-search-toggle]').click();
 await expect(page.locator('[data-tracker-search]')).not.toBeVisible();
 await page.screenshot({path:'artifacts/tracker-views-review/after-webapp.png'});
 await page.locator('[data-tracker-column=work]').click();await page.locator('[data-tracker-issue="'+item.id+'"]').click();
 await page.screenshot({path:'artifacts/tracker-views-review/after-webapp-issue.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 await page.evaluate(()=>Object.defineProperty(navigator,'standalone',{get:()=>false,configurable:true}));
 for(let i=0;i<24;i++)await api(page,'/tracker/projects/'+project.id+'/issues','POST',{title:'Завершённая задача '+(i+1),status:'done'});
 await page.reload();await appSection(page,'Задачи');
 await expect(page.locator('[data-status-column=done] .tracker-card')).toHaveCount(25);
 expect(await page.locator('[data-status-column=done]').evaluate(el=>el.scrollHeight>el.clientHeight)).toBe(true);
 expect(await page.locator('.tracker-board').evaluate(el=>el.getBoundingClientRect().bottom)).toBeLessThan(1020);
 await page.screenshot({path:'artifacts/tracker-views-review/after-desktop.png'});
 const phone=await browser.newContext({baseURL:'http://127.0.0.1:8001',storageState:await page.context().storageState(),viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'ru-RU'});
 try{
  await phone.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true,configurable:true}));
  const screen=await phone.newPage();await screen.goto('/');await screen.locator('[data-mobile-view=tracker]').click();
  await screen.locator('[data-tracker-project="'+project.id+'"]').click();
  expect(await screen.locator('.tracker-card').first().evaluate(el=>el.getBoundingClientRect().top)).toBeLessThan(235);
  expect(await screen.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await screen.screenshot({path:'artifacts/tracker-views-review/after-webapp.png'});
  await screen.setViewportSize({width:320,height:700});
  expect(await screen.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await screen.screenshot({path:'artifacts/tracker-views-review/board-webapp-320.png'});
 }finally{await phone.close();}
 await api(page,'/appearance','PUT',{theme:'ocean',mode:'dark',style:'glass',wallpaper:'mountains'});await page.reload();await appSection(page,'Задачи');await expect(page.locator('.tracker-board')).toBeVisible();
 await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');
 await page.locator('.tracker-context summary').click();await page.screenshot({path:'artifacts/tracker-views-review/after-desktop-dark.png'});await page.locator('.tracker-context summary').click();
 // New projections share canonical records, preserve hierarchy and versioned writes.
 const own=(await api(page,'/me')).id;
 const ios=await api(page,'/tracker/projects/'+project.id+'/groups','POST',{name:'iOS',description:'Нативное приложение'});
 const ordinary=await api(page,'/tracker/projects/'+project.id+'/issues','POST',{title:'Обычная задача с подзадачей',group_id:ios.id,priority:2,description:'Описание родителя сохранено.'});
 const leaf=await api(page,'/tracker/projects/'+project.id+'/issues','POST',{title:'Вложенная проверка',parent_id:ordinary.id,kind:'bug',priority:3,status:'work',description:'Полное описание вложенной проверки.'});
 await page.locator('[data-tracker-refresh]').click();
 await page.locator('[data-tracker-layout]').selectOption('hierarchy');
 await expect(page.locator('.tracker-hierarchy')).toBeVisible();
 await expect(page.locator('.tracker-tree-row [data-tracker-issue="'+leaf.id+'"]')).toBeVisible();
 await page.locator('[data-tracker-expand="issue:'+ordinary.id+'"]').click();
 await expect(page.locator('.tracker-tree-row [data-tracker-issue="'+leaf.id+'"]')).toHaveCount(0);
 await page.locator('[data-tracker-search]').fill('Вложенная проверка');
 await expect(page.locator('.tracker-tree-row')).toHaveCount(2);
 await expect(page.locator('.tracker-tree-row.is-context')).toContainText('Обычная задача с подзадачей');
 await expect(page.locator('.tracker-tree-row [data-tracker-issue="'+leaf.id+'"]')).toBeVisible();
 await page.locator('[data-tracker-search]').fill('');
 await expect(page.locator('.tracker-tree-row [data-tracker-issue="'+leaf.id+'"]')).toHaveCount(0);
 await page.locator('[data-tracker-expand="issue:'+ordinary.id+'"]').click();
 await page.locator('[data-tracker-expand="group:none"]').click();
 await page.screenshot({path:'artifacts/tracker-views-review/after-desktop-hierarchy.png'});
 await page.locator('[data-tracker-layout]').selectOption('table');
 const row=page.locator('[data-table-issue="'+leaf.id+'"]');
 await page.locator('[data-tracker-sort=priority]').click();
 await expect(page.locator('.tracker-table tbody tr').first().locator('[data-field=priority]')).toHaveValue('3');
 await row.locator('[data-field=status]').selectOption('review');
 await expect.poll(async()=> (await api(page,'/tracker/issues/'+leaf.id)).status).toBe('review');
 await expect(row.locator('[data-field=assignee]')).toBeEnabled();
 await row.locator('[data-field=assignee]').selectOption(':'+own);
 await expect.poll(async()=> (await api(page,'/tracker/issues/'+leaf.id)).assignee_id).toBe(own);
 await expect(row.locator('[data-field=due]')).toBeEnabled();
 await row.locator('[data-field=due]').fill('2026-11-18');
 await expect.poll(async()=> (await api(page,'/tracker/issues/'+leaf.id)).due).toBe('2026-11-18');
 await expect(row.locator('[data-field=priority]')).toBeEnabled();
 const stale=await api(page,'/tracker/issues/'+leaf.id);
 await api(page,'/tracker/issues/'+leaf.id,'PATCH',{version:stale.version,title:'Проверка изменена другим участником'});
 await row.locator('[data-field=priority]').selectOption('1');
 await expect(page.locator('[data-tracker-error]')).toBeVisible();
 await expect(row.locator('[data-field=priority]')).toHaveValue('1');
 expect((await api(page,'/tracker/issues/'+leaf.id)).priority).toBe(3);
 await page.locator('[data-tracker-error] [data-tracker-inline-open]').click();
 await expect(page.locator('[data-tracker-edit] [name=priority]')).toHaveValue('1');
 await expect(page.locator('[data-tracker-edit] [name=title]')).toHaveValue('Проверка изменена другим участником');
 await expect(page.locator('[data-tracker-edit] [name=description]')).toHaveValue('Полное описание вложенной проверки.');
 await page.getByRole('button',{name:'Сохранить',exact:true}).click();
 await expect.poll(async()=> (await api(page,'/tracker/issues/'+leaf.id)).priority).toBe(1);
 await page.locator('[data-tracker-back]').click();
 await expect(page.locator('[data-tracker-layout]')).toHaveValue('table');
 await page.reload();await appSection(page,'Задачи');
 await expect(page.locator('[data-tracker-layout]')).toHaveValue('table');
 await expect(page.locator('th[aria-sort=descending]')).toContainText('Приоритет');
 await page.screenshot({path:'artifacts/tracker-views-review/after-desktop-table.png'});
 await page.locator('[data-tracker-layout]').selectOption('board');
 await page.locator('.tracker-display-options summary').click();await page.locator('[data-tracker-lanes]').selectOption('group');
 await expect(page.locator('[data-tracker-lane="group:'+ios.id+'"]')).toContainText('Обычная задача с подзадачей');
 await expect(page.locator('[data-tracker-lane="group:'+ios.id+'"]')).toContainText('Проверка изменена другим участником');
 await page.screenshot({path:'artifacts/tracker-views-review/after-desktop-lanes.png'});
 await page.locator('.tracker-display-options summary').click();await page.locator('[data-tracker-lanes]').selectOption('epic');
 await expect(page.locator('[data-tracker-lane="epic:'+parent.id+'"]')).toContainText('Плавная загрузка виджетов');
 await expect(page.locator('[data-tracker-lane="epic:none"]')).toContainText('Обычная задача с подзадачей');
 // A cross-lane drop must not silently regroup a task or change its status.
 const source=page.locator('[data-tracker-issue="'+leaf.id+'"]'),target=page.locator('[data-tracker-lane="epic:'+parent.id+'"] [data-status-column=done]');
 await source.dragTo(target);expect((await api(page,'/tracker/issues/'+leaf.id)).status).toBe('review');
 await page.locator('.tracker-display-options summary').click();await page.locator('[data-tracker-lanes]').selectOption('group');
 const viewsPhone=await browser.newContext({baseURL:'http://127.0.0.1:8001',storageState:await page.context().storageState(),viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'ru-RU'});
 try{
  await viewsPhone.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true,configurable:true}));
  const screen=await viewsPhone.newPage();await screen.goto('/');await screen.locator('[data-mobile-view=tracker]').click();await screen.locator('[data-tracker-project="'+project.id+'"]').click();await expect(screen.locator('.tracker-loading')).toHaveCount(0);
  for(const [view,name] of [['hierarchy','hierarchy'],['table','table'],['board','lanes']]){
   await screen.locator('[data-tracker-layout]').selectOption(view);
   await expect(screen.locator(view==='hierarchy'?'.tracker-hierarchy':view==='table'?'.tracker-table':'.tracker-lanes')).toBeVisible();
   if(view==='board')await screen.locator('[data-tracker-column=review]').click();
   expect(await screen.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   await screen.screenshot({path:'artifacts/tracker-views-review/after-webapp-'+name+'.png'});
   await screen.setViewportSize({width:320,height:700});expect(await screen.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await screen.setViewportSize({width:390,height:844});
  }
 }finally{await viewsPhone.close();}
 // Shared filters combine across facets, survive reload and preserve each layout.
 const me=await api(page,'/me');
 for(const [title,status,priority] of [['Фильтры: новая задача','todo',3],['Фильтры: доработка','work',0],['Фильтры: завершённая задача','done',3],['Фильтры: низкий приоритет','todo',1]])await api(page,'/tracker/projects/'+project.id+'/issues','POST',{title,status,priority,assignee_id:me.id});
 await page.locator('[data-tracker-refresh]').click();await page.locator('[data-tracker-layout]').selectOption('board');
 await page.locator('.tracker-display-options summary').click();await page.locator('[data-tracker-lanes]').selectOption('none');
 await page.locator('[data-tracker-search]').fill('Фильтры');await expect(page.locator('.tracker-card')).toHaveCount(4);
 await page.locator('[data-tracker-unfinished]').click();await page.locator('[data-tracker-mine]').click();
 await page.locator('.tracker-filters-menu summary').click();
 for(const status of ['todo','work'])await page.locator('[data-tracker-filter=status][value='+status+']').check();
 for(const priority of [0,3])await page.locator('[data-tracker-filter=priority][value="'+priority+'"]').check();
 await page.keyboard.press('Escape');await expect(page.locator('.tracker-card')).toHaveCount(2);
 for(const [view,selector] of [['list','.tracker-list-row'],['hierarchy','.tracker-tree-row'],['table','.tracker-table tbody tr'],['board','.tracker-card']]){
  await page.locator('[data-tracker-layout]').selectOption(view);await expect(page.locator(selector)).toHaveCount(2);
 }
 await page.reload();await appSection(page,'Задачи');await expect(page.locator('.tracker-card')).toHaveCount(2);
 await expect(page.locator('[data-tracker-search]')).toHaveValue('Фильтры');
 await expect(page.locator('[data-tracker-mine]')).toHaveAttribute('aria-pressed','true');
 await page.screenshot({path:'artifacts/tracker-followups/after-desktop.png'});
 const filterPhone=await browser.newContext({baseURL:'http://127.0.0.1:8001',storageState:await page.context().storageState(),viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'ru-RU'});
 try{
  await filterPhone.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true,configurable:true}));
  const screen=await filterPhone.newPage();await screen.goto('/');await screen.locator('[data-mobile-view=tracker]').click();await screen.locator('[data-tracker-project="'+project.id+'"]').click();await expect(screen.locator('.tracker-card')).toHaveCount(2);
  await screen.screenshot({path:'artifacts/tracker-followups/after-mobile.png'});
  await screen.locator('.tracker-filters-menu summary').click();await screen.screenshot({path:'artifacts/tracker-followups/after-mobile-filters.png'});
  await screen.setViewportSize({width:320,height:700});expect(await screen.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }finally{await filterPhone.close();}

 await page.locator('[data-tracker-clear-filters]').click();
 await expect(page.locator('.tracker-filter-chip')).toHaveCount(0);
 // Losing access during an inline edit must not trap navigation forever.
 await page.locator('[data-tracker-layout]').selectOption('table');
 await page.route('**/api/tracker/issues/'+leaf.id,route=>route.fulfill({status:403,json:{detail:'Access revoked'}}));
 await page.locator('[data-table-issue="'+leaf.id+'"] [data-field=priority]').selectOption('3');
 await expect(page.locator('[data-tracker-inline-discard]')).toBeVisible();
 await page.locator('[data-tracker-error] [data-tracker-inline-open]').click();
 await expect(page.locator('[data-tracker-layout]')).toHaveCount(0);
 await page.unroute('**/api/tracker/issues/'+leaf.id);
 await page.locator('[data-tracker-inline-discard]').click();
 await expect(page.locator('[data-tracker-project="'+project.id+'"]').first()).toBeVisible();
 expect((await api(page,'/tracker/issues/'+leaf.id)).priority).toBe(1);
 expect(errors).toEqual([]);
});
