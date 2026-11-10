import {appSection} from './notes-ui.js';
import {expect} from '@playwright/test';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const summaryViews=['today','scheduled','all','flagged','important','completed'];
const summaryLabels=['Сегодня','В планах','Все','Флажок','Важные','Завершено'];
const summaryCounts=(tasks,day)=>summaryViews.map(view=>tasks.filter(task=>view==='completed'?task.done:!task.done&&(view==='today'?task.day&&task.day<=day:view==='scheduled'?task.day:view==='flagged'?task.flagged:view==='important'?Number(task.priority)===3:true)).length);
export async function taskWidgetFlow(page){
  const previous=await api(page,'/dashboard/layout'),modules=await api(page,'/modules'),viewport=page.viewportSize();
  const lists=[],tasks=[];const day=await page.evaluate(()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;});
  try{
    await api(page,'/modules','PUT',{enabled:{tasks:true}});
    const list=await api(page,'/task-lists','POST',{name:'Виджет: отдельный список',retain_completed:true});lists.push(list.id);
    const add=async data=>{const created=await api(page,'/tasks','POST',data);tasks.push(created.id);return created;};
    const own=await add({title:'Виджет без даты в личном списке'});
    const foreign=await add({title:'Виджет: важное в личном списке',day,priority:3});
    const undated=await add({title:'Виджет: поддело без даты',list_id:list.id,flagged:true,tags:['widget'],priority:3});
    const current=await add({title:'Виджет: сегодня в другом списке',list_id:list.id,day});
    const parent=await add({title:'Виджет: родитель с подзадачей',list_id:list.id,flagged:true});
    const child=await add({title:'Виджет: скрытая подзадача',list_id:list.id,parent_id:parent.id,flagged:false});
    const adjacent=offset=>{const date=new Date(day+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+offset);return date.toISOString().slice(0,10);};
    const overdue=await add({title:'Виджет: просроченное в выбранном списке',list_id:list.id,day:adjacent(-1)});
    const future=await add({title:'Виджет: будущее со средним приоритетом',list_id:list.id,day:adjacent(1),priority:2});
    const completed=await add({title:'Виджет: выполненное в выбранном списке',list_id:list.id,day,done:true,flagged:true,priority:3});
    await api(page,'/dashboard/layout','PUT',{version:previous.version,instances:[
      {id:'task_default_qa',module_id:'tasks',title:'Основной источник',size:'1x1',config:{view:'list',list_id:null}},
      {id:'task_list_qa',module_id:'tasks',title:'Отдельный источник',size:'2x1',config:{view:'list',list_id:list.id}},
      {id:'task_today_qa',module_id:'tasks',title:'Сегодняшний источник',size:'1x2',config:{view:'today',list_id:null}},
      {id:'task_flags_qa',module_id:'tasks',title:'Источник с флажком',size:'2x2',config:{view:'flagged',list_id:null}},
      {id:'task_summary_qa',module_id:'tasks',title:'Компактный выбранный источник',size:'compact',config:{view:'list',list_id:list.id}},
    ]});
    await page.goto('/');await appSection(page,'Главная');
    const item=id=>page.locator(`[data-widget-id=${id}]`);
    const summary=async(id,pool)=>{
      await expect(item(id).locator('[data-task-summary]')).toHaveCount(6);
      expect(await item(id).locator('[data-task-summary]').evaluateAll(buttons=>buttons.map(button=>button.dataset.taskSummary))).toEqual(summaryViews);
      for(const [index,label] of summaryLabels.entries())await expect(item(id).locator(`[data-task-summary=${summaryViews[index]}]`)).toContainText(label);
      await expect(item(id).locator('.task-summary-count')).toHaveText(summaryCounts(pool,day).map(String));
      await expect(item(id).locator('.task-list,.task-add')).toHaveCount(0);
    };
    const initial=await api(page,'/tasks');
    await summary('task_default_qa',initial.filter(task=>task.list_id===own.list_id));
    await summary('task_summary_qa',initial.filter(task=>task.list_id===list.id));
    await expect(item('task_summary_qa').locator('[data-task-open]')).toHaveText(list.name);
    await expect(item('task_default_qa')).not.toContainText('Виджет без даты в личном списке');
    await expect(item('task_default_qa')).not.toContainText('Виджет: поддело без даты');
    await expect(item('task_list_qa')).toContainText('Виджет: поддело без даты');
    await expect(item('task_today_qa')).toContainText('Виджет: сегодня в другом списке');
    await expect(item('task_today_qa')).not.toContainText('Виджет: поддело без даты');
    await expect(item('task_flags_qa')).toContainText('Виджет: поддело без даты');
    // Tile navigation keeps this widget's selected source; the regular sidebar
    // remains a way to deliberately switch to a global smart list.
    await item('task_default_qa').locator('[data-task-summary=all]').click();
    await expect(page.locator('[data-task-view-title]')).toHaveText('Все');
    await expect(page.locator(`[data-task-item="${own.id}"]`)).toBeVisible();
    await expect(page.locator(`[data-task-item="${undated.id}"]`)).toHaveCount(0);
    await page.locator('#tasks-main-menu').click();
    await item('task_summary_qa').locator('[data-task-summary=today]').click();
    await expect(page.locator('[data-task-view-title]')).toHaveText('Сегодня');
    await expect(page.locator('[data-task-view-count]')).toHaveText('2');
    for(const task of [current,overdue])await expect(page.locator(`[data-task-item="${task.id}"]`)).toBeVisible();
    for(const task of [own,foreign,future,completed])await expect(page.locator(`[data-task-item="${task.id}"]`)).toHaveCount(0);
    const selectedList=page.getByRole('combobox',{name:'Список нового напоминания',exact:true});
    if(await selectedList.count())await expect(selectedList).toHaveValue(list.id);
    await page.getByRole('searchbox',{name:'Поиск напоминаний',exact:true}).fill('Виджет');
    await expect(page.locator(`[data-task-item="${undated.id}"]`)).toBeVisible();
    await expect(page.locator(`[data-task-item="${foreign.id}"]`)).toHaveCount(0);
    await page.getByRole('searchbox',{name:'Поиск напоминаний',exact:true}).fill('');
    await expect(page.locator('[data-task-view-title]')).toHaveText('Сегодня');
    const addedTitle='Виджет: добавлено через компактный выбранный список';
    await page.getByRole('textbox',{name:'Название напоминания',exact:true}).fill(addedTitle);
    await page.getByRole('button',{name:'Добавить напоминание',exact:true}).click();
    await expect(page.getByRole('textbox',{name:'Название выбранного напоминания',exact:true})).toHaveValue(addedTitle);
    const added=(await api(page,'/tasks')).find(task=>task.title===addedTitle);tasks.push(added.id);
    expect([added.list_id,added.day]).toEqual([list.id,day]);
    await page.locator('#tasks-main-menu').click();
    await item('task_summary_qa').locator('[data-task-summary=completed]').click();
    await expect(page.locator('[data-task-view-title]')).toHaveText('Завершено');
    await expect(page.locator('[data-task-view-count]')).toHaveText('1');
    await expect(page.locator(`[data-task-item="${completed.id}"]`)).toBeVisible();
    await page.locator('[data-smart-view=all]').click();
    await expect(page.locator('[data-task-view-title]')).toHaveText('Все');
    await expect(page.locator(`[data-task-item="${foreign.id}"]`)).toBeVisible();
    await page.locator('#tasks-main-menu').click();
    // A global Today or Flagged source keeps its intersection even when a tile
    // selects another smart view. Opening a tile never rewrites saved config.
    for(const [config,view,present,absent] of [
      [{view:'today',list_id:null},'completed',[completed],[undated,overdue,future]],
      [{view:'flagged',list_id:null},'important',[undated],[foreign,completed,future]],
      [{view:'flagged',list_id:null},'all',[parent,undated],[foreign,completed,future,child]],
    ]){
      const before=await api(page,'/dashboard/layout');before.instances.find(widget=>widget.id==='task_summary_qa').config=config;
      await api(page,'/dashboard/layout','PUT',before);await page.goto('/');await appSection(page,'Главная');
      const pool=(await api(page,'/tasks')).filter(task=>config.view==='today'?task.day===day:task.flagged);
      await summary('task_summary_qa',pool);
      await item('task_summary_qa').locator(`[data-task-summary=${view}]`).click();
      await expect(page.locator('[data-task-view-title]')).toHaveText(summaryLabels[summaryViews.indexOf(view)]);
      for(const task of present)await expect(page.locator(`[data-task-item="${task.id}"]`)).toBeVisible();
      for(const task of absent)await expect(page.locator(`[data-task-item="${task.id}"]`)).toHaveCount(0);
      if(config.view==='flagged'&&view==='all'){
        await page.locator(`[data-expand-task="${parent.id}"]`).click();
        await expect(page.locator(`[data-task-item="${child.id}"]`)).toHaveCount(0);
      }
      expect((await api(page,'/dashboard/layout')).instances.find(widget=>widget.id==='task_summary_qa').config).toEqual(config);
      await page.locator('#tasks-main-menu').click();
    }
    const restoreSummary=await api(page,'/dashboard/layout');restoreSummary.instances.find(widget=>widget.id==='task_summary_qa').config={view:'list',list_id:list.id};
    await api(page,'/dashboard/layout','PUT',restoreSummary);await page.goto('/');await appSection(page,'Главная');
    for(const [view,expected] of [
      ['flagged',{flagged:true,priority:0,day:''}],
      ['important',{flagged:false,priority:3,day:''}],
      ['scheduled',{flagged:false,priority:0,day}],
    ]){
      await item('task_summary_qa').locator(`[data-task-summary=${view}]`).click();
      await expect(page.locator('[data-task-view-title]')).toHaveText(summaryLabels[summaryViews.indexOf(view)]);
      const title='Виджет: добавлено из плитки '+view;
      await page.getByRole('textbox',{name:'Название напоминания',exact:true}).fill(title);
      await page.getByRole('button',{name:'Добавить напоминание',exact:true}).click();
      await expect(page.getByRole('textbox',{name:'Название выбранного напоминания',exact:true})).toHaveValue(title);
      const created=(await api(page,'/tasks')).find(task=>task.title===title);tasks.push(created.id);
      expect(created.list_id).toBe(list.id);
      expect({flagged:created.flagged,priority:created.priority,day:created.day}).toEqual(expected);
      await expect(page.locator(`[data-task-item="${created.id}"]`)).toBeVisible();
      await page.locator('#tasks-main-menu').click();
    }
    expect((await api(page,'/dashboard/layout')).instances.find(widget=>widget.id==='task_summary_qa').config).toEqual({view:'list',list_id:list.id});
    await item('task_list_qa').getByRole('checkbox',{name:'Выполнено: Виджет: поддело без даты',exact:true}).check();
    await expect(item('task_list_qa').locator(`[data-task-row="${undated.id}"]`)).toHaveClass(/done/);
    await page.reload();
    await expect(item('task_list_qa').locator(`[data-task-row="${undated.id}"]`)).toHaveClass(/done/);
    // The child is hidden by this widget's filter, but belongs to the deletion.
    await expect(item('task_flags_qa')).not.toContainText('Виджет: скрытая подзадача');
    const deletions=[],capture=request=>{if(request.method()==='DELETE'&&new URL(request.url()).pathname==='/api/tasks/'+parent.id)deletions.push(request);};
    page.on('request',capture);
    try{
      const remove=item('task_flags_qa').locator(`[data-task-row="${parent.id}"] [data-delete-task]`);
      await remove.click();
      const dialog=page.getByRole('dialog');
      await expect(dialog.getByRole('heading',{name:'Удалить напоминание с вложенными напоминаниями?',exact:true})).toBeVisible();
      await dialog.getByRole('button',{name:'Нет',exact:true}).click();
      await expect(dialog).toHaveCount(0);await expect(remove).toBeEnabled();
      expect(deletions).toHaveLength(0);
      const preserved=await api(page,'/tasks');
      expect(preserved.some(task=>task.id===parent.id)).toBe(true);expect(preserved.some(task=>task.id===child.id)).toBe(true);
      await remove.click();await page.getByRole('dialog').getByRole('button',{name:'Да',exact:true}).click();
      await expect(item('task_flags_qa').locator(`[data-task-row="${parent.id}"]`)).toHaveCount(0);
      expect(deletions).toHaveLength(1);expect(deletions[0].postData()).toBeNull();
      const removed=await api(page,'/tasks');
      expect(removed.some(task=>task.id===parent.id||task.id===child.id)).toBe(false);
    }finally{page.off('request',capture);}
    await page.locator('#dashboard-lock').click();
    await item('task_list_qa').getByRole('button',{name:'Настроить виджет: Отдельный источник',exact:true}).click();
    await expect(page.getByLabel('Источник напоминаний',{exact:true})).toHaveValue('list');
    await expect(page.getByLabel('Список',{exact:true})).toHaveValue(list.id);
    await page.getByLabel('Источник напоминаний',{exact:true}).selectOption('all');
    await page.getByRole('button',{name:'Сохранить виджет',exact:true}).click();
    await expect(item('task_list_qa')).toContainText('Виджет без даты в личном списке');
    await expect(item('task_default_qa').locator('[data-task-open]')).toHaveText('Напоминания');
    const layout=await api(page,'/dashboard/layout');
    expect(layout.instances.find(item=>item.id==='task_list_qa').config).toEqual({view:'all',list_id:null});
    expect(layout.instances.find(item=>item.id==='task_today_qa').config).toEqual({view:'today',list_id:null});
    for(const width of [1280,1100,390,320]){
      await page.setViewportSize({width,height:900});await settle(page);
      for(const id of ['task_default_qa','task_list_qa','task_today_qa','task_flags_qa','task_summary_qa']){
        // Saving a widget remounts every dashboard widget. Each source loads
        // independently, so an existing wrapper and two animation frames do
        // not mean this particular widget has finished its API requests.
        await expect(item(id).locator('.task-widget-heading')).toBeVisible();
        const bounds=await item(id).locator('[data-widget-body]').evaluate(body=>{
          const rect=element=>{const value=element.getBoundingClientRect();return {left:value.left,right:value.right,top:value.top,bottom:value.bottom};};
          const grid=body.querySelector('.task-summary-grid');
          return {body:rect(body),form:body.querySelector('.task-add')?rect(body.querySelector('.task-add')):null,head:rect(body.querySelector('.task-widget-heading')),tiles:grid?[...grid.querySelectorAll('[data-task-summary]')].map(rect):null,overflow:body.scrollWidth>body.clientWidth+1,verticalOverflow:body.scrollHeight>body.clientHeight+1};
        });
        expect(bounds.overflow,JSON.stringify({width,id,bounds})).toBe(false);
        const list=item(id).locator('.task-list');
        if(await list.count())expect(await list.evaluate(el=>{el.scrollLeft=1000;return {overflow:el.scrollWidth>el.clientWidth,scroll:el.scrollLeft};})).toEqual({overflow:false,scroll:0});
        if(bounds.tiles){
          expect(bounds.form).toBeNull();expect(bounds.tiles).toHaveLength(6);
          expect(bounds.verticalOverflow,JSON.stringify({width,id,bounds})).toBe(false);
          for(const tile of bounds.tiles){
            expect(tile.left).toBeGreaterThanOrEqual(bounds.body.left-1);expect(tile.right).toBeLessThanOrEqual(bounds.body.right+1);
            expect(tile.top).toBeGreaterThanOrEqual(bounds.head.bottom-1);expect(tile.bottom,JSON.stringify({width,id,bounds})).toBeLessThanOrEqual(bounds.body.bottom+1);
          }
          for(let row=0;row<3;row++){
            expect(Math.abs(bounds.tiles[row*2].top-bounds.tiles[row*2+1].top)).toBeLessThanOrEqual(1);
            expect(bounds.tiles[row*2+1].left).toBeGreaterThan(bounds.tiles[row*2].right);
          }
        }else{
          expect(bounds.form.bottom,JSON.stringify({width,id,bounds})).toBeLessThanOrEqual(bounds.body.bottom+1);
          expect(bounds.form.left).toBeGreaterThanOrEqual(bounds.body.left-1);expect(bounds.form.right).toBeLessThanOrEqual(bounds.body.right+1);
          expect(bounds.form.top).toBeGreaterThanOrEqual(bounds.head.bottom-1);
        }
      }
      if(width===1280||width===390)await item('task_summary_qa').screenshot({path:`artifacts/tasks-summary-${width===1280?'desktop':'mobile'}.png`,animations:'disabled'});
    }
    await page.setViewportSize({width:1280,height:900});await settle(page);
    // A deleted explicit source stays unavailable and does not show the default.
    const revoked=await api(page,'/dashboard/layout');revoked.instances.find(item=>item.id==='task_list_qa').config={view:'list',list_id:list.id};
    await api(page,'/dashboard/layout','PUT',revoked);await api(page,'/task-lists/'+list.id,'DELETE');lists.splice(0,1);
    await page.goto('/');await appSection(page,'Главная');
    await expect(item('task_list_qa')).toContainText('Список удалён или доступ к нему закрыт.');
    await expect(item('task_list_qa')).not.toContainText('Виджет без даты в личном списке');
    await expect(item('task_summary_qa')).toContainText('Список удалён или доступ к нему закрыт.');
    await expect(item('task_summary_qa').locator('[data-task-summary]')).toHaveCount(0);
    await summary('task_default_qa',(await api(page,'/tasks')).filter(task=>task.list_id===own.list_id));
  }finally{
    for(const id of tasks){const remaining=await api(page,'/tasks');if(remaining.some(task=>task.id===id))await api(page,'/tasks/'+id,'DELETE');}
    for(const id of lists)await api(page,'/task-lists/'+id,'DELETE');
    const current=await api(page,'/dashboard/layout');await api(page,'/dashboard/layout','PUT',{...previous,version:current.version});
    await api(page,'/modules','PUT',{enabled:modules.enabled});
    await page.setViewportSize(viewport);await page.goto('/');await appSection(page,'Главная');
  }
  await taskWidgetPollingFlow(page);
}

export async function taskWidgetPollingFlow(page){
  // A separate document keeps clocks, permissions and existing app data intact.
  const root=fileURLToPath(new URL('../..',import.meta.url));
  const bundle=await build({stdin:{contents:`import {mountTasks} from './web/modules/dashboard/widgets/tasks.js';
    const originalSet=window.setInterval,originalClear=window.clearInterval;
    window.timers=new Set();window.setInterval=(fn,ms)=>{const id=originalSet(fn,ms);window.timers.add(id);return id;};window.clearInterval=id=>{window.timers.delete(id);originalClear(id);};
    window.requests=[];window.hidden=false;Object.defineProperty(document,'hidden',{get:()=>window.hidden,configurable:true});
    window.state={me:{id:'owner'},tasks:[]};window.lists=[];window.tasks=[];window.disposers=[];window.configured=0;
    const api=async path=>{window.requests.push(path);if(window.pending)await window.pending;return structuredClone(path==='/tasks'?window.tasks:window.lists);};
    window.mount=configs=>{window.disposers.forEach(fn=>fn());window.disposers=[];document.body.innerHTML='';
      for(const [i,config] of configs.entries()){const host=document.createElement('div');host.dataset.widgetId='qa_'+i;document.body.append(host);window.disposers.push(mountTasks(host,{state:window.state,instance:{id:'qa_'+i,config,size:config.size||'2x1'},api,configure:()=>window.configured++,navigate:()=>{},refresh:()=>{}}));}
    };`,resolveDir:root},bundle:true,format:'iife',platform:'browser',outfile:'/task-widget-polling-qa.js',write:false});
  const origin=new URL(page.url()).origin,prefix='/task-widget-polling-qa-'+Date.now();
  const htmlUrl=origin+prefix+'.html',scriptUrl=origin+prefix+'.js',styleUrl=origin+prefix+'.css';
  const script=bundle.outputFiles.find(file=>file.path.endsWith('.js')).text;
  const css=await readFile(root+'/web/style.css','utf8')+'\n'+bundle.outputFiles.find(file=>file.path.endsWith('.css')).text;
  const htmlRoute=route=>route.fulfill({contentType:'text/html; charset=utf-8',headers:{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"},body:`<!DOCTYPE html><html><head><meta charset="utf-8"><link rel="stylesheet" href="${styleUrl}"><script defer src="${scriptUrl}"></script></head><body></body></html>`});
  const scriptRoute=route=>route.fulfill({contentType:'application/javascript; charset=utf-8',body:script});
  const styleRoute=route=>route.fulfill({contentType:'text/css; charset=utf-8',body:css});
  await page.route(htmlUrl,htmlRoute);await page.route(scriptUrl,scriptRoute);await page.route(styleUrl,styleRoute);
  let frame;
  try{
    const navigated=page.waitForEvent('framenavigated',{predicate:item=>item.url()===htmlUrl});
    await page.evaluate(url=>{const iframe=document.createElement('iframe');iframe.name='task-widget-polling-qa';iframe.style.cssText='position:fixed;left:0;top:0;width:320px;height:700px;z-index:99999;background:white';iframe.src=url;document.body.append(iframe);},htmlUrl);
    frame=await navigated;
    await frame.waitForURL(htmlUrl);await frame.waitForFunction(()=>typeof window.mount==='function');
    await frame.evaluate(()=>{window.lists=[{id:'default_owner',is_default:true,can_manage:true,name:'Напоминания',shared:false},{id:'shared-list',name:'Общее',shared:true}];window.tasks=[{id:'private-task',title:'Личное без даты',list_id:'default_owner',day:'',done:0},{id:'shared-task',title:'Общее без даты',list_id:'shared-list',day:'',done:0}];window.mount([{view:'list',list_id:null},{view:'list',list_id:'shared-list'}]);});
    await expect(frame.locator('.task-widget-heading')).toHaveCount(2);
    expect(await frame.evaluate(()=>window.timers.size)).toBe(1);
    const before=await frame.evaluate(()=>window.requests.length);
    await frame.evaluate(()=>{window.hidden=true;document.dispatchEvent(new Event('visibilitychange'));});
    await frame.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    expect(await frame.evaluate(()=>window.requests.length)).toBe(before);
    await frame.evaluate(()=>{window.tasks[1].title='Друг изменил общую задачу';window.hidden=false;document.dispatchEvent(new Event('visibilitychange'));});
    await expect(frame.locator('[data-widget-id=qa_1]')).toContainText('Друг изменил общую задачу');
    expect(await frame.evaluate(()=>window.requests.length)).toBe(before+2);
    await expect(frame.locator('[data-widget-id=qa_0]')).toContainText('Личное без даты');
    await frame.evaluate(()=>{window.lists=window.lists.slice(0,1);window.tasks=window.tasks.slice(0,1);document.dispatchEvent(new Event('visibilitychange'));});
    await expect(frame.locator('.task-widget-unavailable')).toBeVisible();
    expect(await frame.evaluate(()=>window.timers.size)).toBe(0);
    await expect(frame.locator('[data-widget-id=qa_1]')).not.toContainText('Личное без даты');
    await frame.locator('[data-task-configure]').click();expect(await frame.evaluate(()=>window.configured)).toBe(1);
    // The compact summary uses the same shared-source polling and disposal as
    // the list sizes; receiving completion changes its counts without a reload.
    await frame.evaluate(()=>{
      window.lists=[{id:'default_owner',is_default:true,can_manage:true,name:'Напоминания',shared:false},{id:'shared-list',name:'Общее',shared:true}];
      window.tasks=[{id:'shared-summary',title:'Общее напоминание',list_id:'shared-list',day:'',done:0}];
      window.mount([{view:'list',list_id:'shared-list',size:'1x1'}]);
    });
    await expect(frame.locator('[data-task-summary]')).toHaveCount(6);
    await expect(frame.locator('[data-task-summary=all] .task-summary-count')).toHaveText('1');
    await expect(frame.locator('[data-task-summary=completed] .task-summary-count')).toHaveText('0');
    expect(await frame.evaluate(()=>window.timers.size)).toBe(1);
    const compactBefore=await frame.evaluate(()=>window.requests.length);
    await frame.evaluate(()=>{window.hidden=true;document.dispatchEvent(new Event('visibilitychange'));});
    await frame.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    expect(await frame.evaluate(()=>window.requests.length)).toBe(compactBefore);
    await frame.evaluate(()=>{window.tasks[0].done=1;window.hidden=false;document.dispatchEvent(new Event('visibilitychange'));});
    await expect(frame.locator('[data-task-summary=all] .task-summary-count')).toHaveText('0');
    await expect(frame.locator('[data-task-summary=completed] .task-summary-count')).toHaveText('1');
    expect(await frame.evaluate(()=>window.requests.length)).toBe(compactBefore+2);
    await frame.evaluate(()=>{window.lists=window.lists.slice(0,1);window.tasks=[];document.dispatchEvent(new Event('visibilitychange'));});
    await expect(frame.locator('.task-widget-unavailable')).toBeVisible();
    await expect(frame.locator('[data-task-summary]')).toHaveCount(0);
    expect(await frame.evaluate(()=>window.timers.size)).toBe(0);
    await frame.evaluate(()=>{window.pending=new Promise(resolve=>window.resolvePending=resolve);window.mount([{view:'list',list_id:null}]);window.disposers.forEach(fn=>fn());window.resolvePending();window.pending=null;});
    await frame.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(frame.locator('.task-widget-heading')).toHaveCount(0);
    expect(await frame.evaluate(()=>window.timers.size)).toBe(0);
    const disposed=await frame.evaluate(()=>window.requests.length);
    await frame.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    expect(await frame.evaluate(()=>window.requests.length)).toBe(disposed);
  }finally{
    if(frame&&!frame.isDetached())await frame.evaluate(()=>window.disposers?.forEach(fn=>fn()));
    const iframe=page.locator('iframe[name=task-widget-polling-qa]');if(await iframe.count())await iframe.evaluate(element=>element.remove());
    await page.unroute(htmlUrl,htmlRoute);await page.unroute(scriptUrl,scriptRoute);await page.unroute(styleUrl,styleRoute);
  }
}
