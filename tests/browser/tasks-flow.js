import {expect} from '@playwright/test';
import {appSection} from './notes-ui.js';

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));

async function completionRefreshRace(page,parent,child,states){
  const initialView=await page.locator('[data-task-view-title]').textContent();
  let armed=false,held=false,refreshReady=false,release;
  const gate=new Promise(resolve=>release=resolve),parentPath='**/api/tasks/'+parent.id;
  const parentMutation=async route=>{
    if(route.request().method()!=='PATCH'){await route.continue();return;}
    const response=await route.fetch();
    if(route.request().postDataJSON().done===true)armed=true;
    await route.fulfill({response});
  };
  const delayedRefresh=async route=>{
    // UI requests carry the device timezone; independent API checks do not.
    // Hold the cascade's committed snapshot so database readiness cannot be
    // mistaken for the Completed view having finished its async transition.
    if(route.request().method()==='GET'&&route.request().headers()['x-time-zone']&&armed&&!held){
      held=true;const response=await route.fetch();refreshReady=true;await gate;await route.fulfill({response});
    }else await route.continue();
  };
  await page.route(parentPath,parentMutation);await page.route('**/api/tasks',delayedRefresh);
  try{
    await page.getByRole('checkbox',{name:'Выполнено: '+parent.title,exact:true}).check();
    await expect.poll(states).toEqual([1,1]);await expect.poll(()=>refreshReady).toBe(true);
    await page.locator('[data-close-task-properties]').click();
    await page.locator('[data-smart-view=completed]').click();await settle(page);
    await expect(page.locator('[data-task-view-title]')).toHaveText(initialView);
    release();
    await expect(page.locator('[data-task-view-title]')).toHaveText('Завершено');
    const completedChild=page.getByRole('checkbox',{name:'Выполнено: '+child.title,exact:true});
    await expect(completedChild).toBeChecked();await completedChild.uncheck();
    await expect.poll(states).toEqual([0,0]);
  }finally{
    release();await page.unroute(parentPath,parentMutation);await page.unroute('**/api/tasks',delayedRefresh);
  }
}

export async function tasksFlow(page){
  const viewport=page.viewportSize(),suffix=Date.now().toString(36),listName='Задачи QA '+suffix;
  const lists=[];let releaseSave,delayedRoute,routePattern;
  const tasks=()=>api(page,'/tasks');
  const selectList=async id=>{
    const button=page.locator(`[data-task-list="${id}"]`);
    await button.waitFor({state:'attached'});
    if(!(await button.isVisible()))await page.locator('[data-toggle-tasks-sidebar]').click();
    await button.click();
  };
  try{
    await page.setViewportSize({width:1280,height:900});await settle(page);
    await appSection(page,'Напоминания');
    await expect(page.getByRole('heading',{name:'Напоминания',exact:true})).toBeVisible();
    await expect(page.locator('.sidebar')).toHaveCount(0);
    await expect(page.locator('.tasks-smart-view>span:last-child')).toHaveText(['Сегодня','В планах','Все','Флажок','Важные','Завершено']);

    await page.getByRole('button',{name:'Создать список',exact:true}).click();
    const create=page.locator('[data-create-list-form]');
    await expect(create.getByRole('checkbox',{name:'Не удалять выполненные напоминания',exact:true})).not.toBeChecked();
    await create.getByLabel('Название списка').fill(listName);
    await create.getByLabel('Иконка списка').selectOption('home');
    await create.getByLabel('Цвет списка').selectOption('sage');
    await create.getByRole('button',{name:'Создать список',exact:true}).click();
    await expect(page.getByRole('heading',{name:listName,exact:true})).toBeVisible();
    const list=(await api(page,'/task-lists')).find(item=>item.name===listName);lists.push(list.id);
    expect(list.retain_completed).toBe(false);

    const title='План проекта QA '+suffix,childTitle='Подзадача QA '+suffix;
    await page.getByLabel('Название напоминания',{exact:true}).fill(title);
    await page.getByRole('button',{name:'Добавить напоминание',exact:true}).click();
    await expect(page.getByLabel('Название выбранного напоминания')).toHaveValue(title);
    const parent=(await tasks()).find(task=>task.title===title);
    expect([parent.day,parent.time,parent.list_id]).toEqual(['','',list.id]);
    await page.getByLabel('Заметка напоминания').fill('Подробности проекта '+suffix);
    await page.getByLabel('URL напоминания').fill('https://example.com/project');
    await page.getByLabel('Теги напоминания').fill('#qa, #проект');
    await page.getByLabel('Приоритет напоминания').selectOption('3');
    await page.getByRole('checkbox',{name:'Флажок',exact:true}).check();
    const date=await page.evaluate(()=>{const day=new Date();day.setDate(day.getDate()+1);return `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`;});
    await page.getByRole('checkbox',{name:'В определённый день',exact:true}).check();
    await page.getByLabel('Дата напоминания',{exact:true}).fill(date);
    await page.getByRole('checkbox',{name:'В определённое время',exact:true}).check();
    await page.getByLabel('Время напоминания',{exact:true}).fill('10:30');
    await page.getByLabel('Время напоминания',{exact:true}).press('Control+Enter');
    await expect.poll(async()=>{const task=(await tasks()).find(item=>item.id===parent.id);return [task.notes,task.url,task.tags,task.priority,task.flagged,task.day,task.time];}).toEqual(['Подробности проекта '+suffix,'https://example.com/project',['qa','проект'],3,true,date,'10:30']);
    await page.locator('#tasks-main-menu').click();
    const calendar=page.locator('[data-widget=calendar]');
    await expect(calendar).toBeVisible();
    if(!(await calendar.locator(`[data-day="${date}"]`).count()))await calendar.locator('[data-shift="1"]').click();
    await calendar.locator(`[data-day="${date}"]`).click();
    await expect(calendar.locator(`[data-day="${date}"]`)).toHaveAttribute('aria-pressed','true');
    await calendar.locator('[data-reminders]').last().click();
    await expect(page.locator('.tasks-row-title[data-select-task="'+parent.id+'"]')).toBeVisible();
    await expect(page.locator('[data-task-view-title]')).not.toHaveText('Напоминания');
    await page.locator('.tasks-row-info[data-select-task="'+parent.id+'"]').click();
    await page.getByRole('checkbox',{name:'В определённый день',exact:true}).uncheck();
    await expect.poll(async()=>{const task=(await tasks()).find(item=>item.id===parent.id);return [task.day,task.time];}).toEqual(['','']);
    await expect(page.getByRole('checkbox',{name:'В определённое время',exact:true})).toBeDisabled();
    await selectList(list.id);
    await page.locator('.tasks-row-info[data-select-task="'+parent.id+'"]').click();

    await page.locator('[data-add-subtask]').click();
    await page.getByLabel('Название вложенного напоминания').fill(childTitle);
    await page.getByRole('button',{name:'Добавить вложенное напоминание',exact:true}).click();
    await expect(page.getByLabel('Название выбранного напоминания')).toHaveValue(childTitle);
    const child=(await tasks()).find(task=>task.title===childTitle);
    expect([child.parent_id,child.list_id,child.day]).toEqual([parent.id,list.id,'']);
    await completionRefreshRace(page,parent,child,async()=>{const all=await tasks();return [all.find(task=>task.id===parent.id).done,all.find(task=>task.id===child.id).done];});
    await page.locator('[data-smart-view=important]').click();
    await expect(page.locator('.tasks-row-title[data-select-task="'+parent.id+'"]')).toBeVisible();
    await page.locator('[data-smart-view=flagged]').click();
    await expect(page.locator('.tasks-row-title[data-select-task="'+parent.id+'"]')).toBeVisible();
    await page.getByLabel('Поиск напоминаний').fill('Подробности проекта '+suffix);
    await expect(page.locator('.tasks-row-title[data-select-task="'+parent.id+'"]')).toBeVisible();
    await page.getByLabel('Поиск напоминаний').fill('');
    await selectList(list.id);
    await page.getByRole('button',{name:'Настройки списка',exact:true}).click();
    await page.getByLabel('Название списка').fill(listName+' новое');
    await page.getByRole('checkbox',{name:'Не удалять выполненные напоминания',exact:true}).check();
    await expect.poll(async()=>{const current=(await api(page,'/task-lists')).find(item=>item.id===list.id);return [current.name,current.retain_completed];}).toEqual([listName+' новое',true]);
    await page.locator('[data-close-task-properties]').click();

    const destination=await api(page,'/task-lists','POST',{name:'Перенос QA '+suffix,retain_completed:true});lists.push(destination.id);
    await page.getByRole('button',{name:'Обновить напоминания',exact:true}).click();
    await page.locator('.tasks-row-info[data-select-task="'+parent.id+'"]').click();
    await page.getByLabel('Список напоминания',{exact:true}).selectOption(destination.id);
    await expect.poll(async()=>{const all=await tasks();return [all.find(task=>task.id===parent.id).list_id,all.find(task=>task.id===child.id).list_id];}).toEqual([destination.id,destination.id]);
    await selectList(destination.id);
    await expect(page.locator('[data-task-view-title]')).toHaveText(destination.name);
    const parentRow=page.locator('.tasks-row-title[data-select-task="'+parent.id+'"]');
    await page.locator('.tasks-row-info[data-select-task="'+parent.id+'"]').click();
    await expect(page.locator('[data-task-item="'+parent.id+'"] > .tasks-reminder-row')).toHaveClass(/is-selected/);
    await expect(page.getByLabel('Название выбранного напоминания')).toHaveValue(title);
    await expect(page.getByLabel('Список напоминания',{exact:true})).toHaveValue(destination.id);

    // A user can edit again while the first PATCH is on the wire, then leave.
    // Delay only its response; the second draft must survive navigation too.
    let reached;const firstSave=new Promise(resolve=>reached=resolve),gate=new Promise(resolve=>releaseSave=resolve);
    let delayed=false;delayedRoute=async route=>{
      if(route.request().method()!=='PATCH'||delayed){await route.continue();return;}
      delayed=true;reached();const response=await route.fetch();await gate;await route.fulfill({response});
    };
    routePattern='**/api/tasks/'+parent.id;await page.route(routePattern,delayedRoute);
    await page.getByLabel('Название выбранного напоминания').fill(title+' первый');
    await Promise.race([firstSave,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Autosave did not start')),10000))]);
    await page.getByLabel('Заметка напоминания').fill('Последняя версия '+suffix);
    await page.getByLabel('Название выбранного напоминания').fill(title+' итог');
    await page.locator('#tasks-main-menu').click();await settle(page);
    await expect(page.locator('.tasks-shell')).toBeVisible();
    releaseSave();await expect(page.locator('.sidebar')).toBeVisible();
    await expect.poll(async()=>{const task=(await tasks()).find(item=>item.id===parent.id);return [task.title,task.notes];}).toEqual([title+' итог','Последняя версия '+suffix]);
    await page.unroute(routePattern,delayedRoute);delayedRoute=null;

    await appSection(page,'Напоминания');await selectList(destination.id);
    await page.screenshot({path:'artifacts/tasks-desktop.png',fullPage:true,animations:'disabled'});
    for(const width of [390,320]){
      await page.setViewportSize({width,height:844});await settle(page);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.locator('[data-toggle-tasks-sidebar]').click();
      await expect(page.locator('.tasks-sidebar')).toBeVisible();
      await selectList(destination.id);
      await page.locator('.tasks-row-info[data-select-task="'+parent.id+'"]').click();
      await expect(page.getByLabel('Заметка напоминания')).toHaveValue('Последняя версия '+suffix);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.locator('[data-close-task-properties]').click();
    }
    await page.setViewportSize({width:390,height:844});await settle(page);
    await page.screenshot({path:'artifacts/tasks-mobile.png',fullPage:true,animations:'disabled'});
  }finally{
    releaseSave?.();if(delayedRoute)await page.unroute(routePattern,delayedRoute);
    for(const id of lists){const remaining=await api(page,'/task-lists');if(remaining.some(list=>list.id===id))await api(page,'/task-lists/'+id,'DELETE');}
    await page.setViewportSize(viewport);await settle(page);await page.goto('/');await appSection(page,'Главная');
  }
  await tasksFilteredTreeFlow(page);
}

export async function tasksFilteredTreeFlow(page){
  const viewport=page.viewportSize(),suffix=Date.now().toString(36),tag='nested-'+suffix;
  let list;
  try{
    await page.setViewportSize({width:1280,height:900});await page.goto('/');await appSection(page,'Главная');
    const day=await page.evaluate(()=>{const value=new Date();return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`;});
    list=await api(page,'/task-lists','POST',{name:'Вложенные фильтры QA '+suffix,retain_completed:true});
    const parent=await api(page,'/tasks','POST',{title:'Контекстный родитель '+suffix,list_id:list.id});
    const matched=await api(page,'/tasks','POST',{title:'Найденное поддело '+suffix,list_id:list.id,parent_id:parent.id,day,flagged:true,priority:3,tags:[tag]});
    const completed=await api(page,'/tasks','POST',{title:'Выполненное поддело '+suffix,list_id:list.id,parent_id:parent.id,done:true});
    const row=id=>page.locator(`[data-task-item="${id}"]>.tasks-reminder-row`);
    const matchingVisible=async task=>{
      await expect(row(task.id)).toBeVisible();await expect(row(task.id)).not.toHaveClass(/is-context/);
      await expect(row(parent.id)).toHaveClass(/is-context/);
      await expect(page.locator(`[data-expand-task="${parent.id}"]`)).toHaveAttribute('aria-expanded','true');
    };
    const freshTasks=async()=>{
      // A full document reload creates a new controller with no expanded IDs.
      await page.goto('/');await appSection(page,'Главная');await appSection(page,'Напоминания');
      await expect(page.locator(`[data-task-list="${list.id}"]`)).toBeVisible();
    };
    for(const view of ['today','scheduled','flagged','important','completed']){
      await freshTasks();await page.locator(`[data-smart-view="${view}"]`).click();
      await matchingVisible(view==='completed'?completed:matched);
      if(view==='flagged'){
        await page.locator(`[data-expand-task="${parent.id}"]`).click();
        await expect(row(matched.id)).toHaveCount(0);
        await page.getByRole('button',{name:'Обновить напоминания',exact:true}).click();await settle(page);
        await expect(page.locator(`[data-expand-task="${parent.id}"]`)).toHaveAttribute('aria-expanded','false');
        await expect(row(matched.id)).toHaveCount(0);
        await page.locator(`[data-expand-task="${parent.id}"]`).click();await matchingVisible(matched);
        await page.locator(`[data-expand-task="${parent.id}"]`).click();
        await page.locator('[data-smart-view=important]').click();await matchingVisible(matched);
      }
    }
    await freshTasks();await page.locator(`.tasks-sidebar [data-task-tag="${tag}"]`).click();await matchingVisible(matched);
    await freshTasks();await page.getByLabel('Поиск напоминаний').fill(matched.title);await matchingVisible(matched);
    await page.goto('/');await appSection(page,'Главная');
    await page.locator(`[data-widget=calendar] [data-day="${day}"]`).click();
    await page.locator('[data-widget=calendar] [data-reminders]').last().click();await matchingVisible(matched);
    // Ordinary list and All views still start with the normal collapsed tree.
    await freshTasks();await page.locator(`[data-task-list="${list.id}"]`).click();
    await expect(row(parent.id)).toBeVisible();await expect(row(matched.id)).toHaveCount(0);
    await expect(page.locator(`[data-expand-task="${parent.id}"]`)).toHaveAttribute('aria-expanded','false');
    await page.locator(`[data-expand-task="${parent.id}"]`).click();
    await page.locator(`[data-expand-task="${parent.id}"]`).click();
    await row(parent.id).locator('.tasks-row-info').click();
    await page.locator('[data-add-subtask]').click();
    const extraTitle='Новое поддело после сворачивания '+suffix;
    await page.getByLabel('Название вложенного напоминания').fill(extraTitle);
    await page.getByRole('button',{name:'Добавить вложенное напоминание',exact:true}).click();
    await expect(page.getByLabel('Название выбранного напоминания')).toHaveValue(extraTitle);
    const extra=(await api(page,'/tasks')).find(task=>task.title===extraTitle);
    await expect(row(extra.id)).toBeVisible();
    await expect(page.locator(`[data-expand-task="${parent.id}"]`)).toHaveAttribute('aria-expanded','true');
    await freshTasks();await page.locator('[data-smart-view=all]').click();
    await expect(row(parent.id)).toBeVisible();await expect(row(matched.id)).toHaveCount(0);
  }finally{
    if(list)await api(page,'/task-lists/'+list.id,'DELETE');
    await page.setViewportSize(viewport);await page.goto('/');await appSection(page,'Главная');
  }
}
