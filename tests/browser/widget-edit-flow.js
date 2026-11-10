import {appSection} from './notes-ui.js';
import {expect} from '@playwright/test';

export async function unlockDashboard(page){
  const lock=page.locator('#dashboard-lock');
  await expect(lock).toBeEnabled();
  if(await lock.getAttribute('aria-pressed')!=='true')await lock.click();
  await expect(lock).toHaveAttribute('aria-pressed','true');
}

async function api(page,path,method='GET',data){
  const response=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const result=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:result.status,data:await result.json()};
  },{path,method,data});
  expect(response.status,JSON.stringify(response.data)).toBe(200);return response.data;
}

export async function widgetEditFlow(page){
  const original=await api(page,'/dashboard/layout'),modules=(await api(page,'/modules')).enabled;
  const instances=[
    {id:'edit_clock',module_id:'clock',title:'Часы для проверки',size:'1x1',config:{label:'Без изменений'}},
    {id:'edit_notes_a',module_id:'notes',title:'Первый виджет заметок',size:'1x1',config:{}},
    {id:'edit_notes_b',module_id:'notes',title:'Второй виджет заметок',size:'1x1',config:{}},
    {id:'edit_tasks',module_id:'tasks',title:'Черновик задачи',size:'2x1',config:{}},
    {id:'edit_hidden_calendar',module_id:'calendar',title:'Скрытый календарь',hidden:true,size:'1x1',config:{}},
  ];
  const widget=id=>page.locator(`[data-widget-id="${id}"]`),dialog=()=>page.getByRole('dialog').last(),lock=()=>page.locator('#dashboard-lock');
  const controls=()=>page.locator('#widget-grid .widget-toolbar [data-move-widget],#widget-grid .widget-toolbar [data-configure],#widget-grid .widget-toolbar [data-remove-widget]');
  let note,failure,slow,release,nativeDialogs=0;
  const native=dialog=>{nativeDialogs++;dialog.dismiss();};page.on('dialog',native);
  try{
    await api(page,'/modules','PUT',{enabled:{notes:true,tasks:true}});
    note=(await api(page,'/notes','POST',{title:'Данные сохраняются после удаления виджета',body:'Этот текст должен остаться в разделе заметок.'})).id;
    const current=await api(page,'/dashboard/layout');await api(page,'/dashboard/layout','PUT',{version:current.version,instances});
    await page.goto('/');await appSection(page,'Главная');await expect(widget('edit_notes_a')).toBeVisible();
    await expect(lock()).toHaveAttribute('aria-pressed','false');
    for(const control of await controls().all())await expect(control).toBeHidden();
    await lock().click();await expect(lock()).toHaveAttribute('aria-pressed','true');
    for(const control of await controls().all())await expect(control).toBeVisible();

    // A saved configuration rebuilds the widgets without leaving edit mode.
    await widget('edit_notes_a').locator('[data-configure]').click();
    await dialog().getByLabel('Название виджета').fill('Переименованный виджет');
    await dialog().getByRole('button',{name:'Сохранить виджет',exact:true}).click();
    await expect(widget('edit_notes_a').getByRole('heading')).toHaveText('Переименованный виджет');
    await expect(lock()).toHaveAttribute('aria-pressed','true');
    for(const control of await controls().all())await expect(control).toBeVisible();

    // Removing another card must preserve unsent input and running clock DOM.
    const taskDraft=widget('edit_tasks').getByRole('textbox',{name:'Название напоминания',exact:true});
    await taskDraft.fill('Ещё не отправленная задача');
    await widget('edit_clock').locator('.clock-widget').evaluate(el=>{el.dataset.removalMarker='preserved';window.__widgetEditClock=el;});
    const beforeCancel=await api(page,'/dashboard/layout');
    await widget('edit_notes_a').locator('[data-remove-widget]').click();
    await expect(dialog().getByRole('heading')).toHaveText('Вы уверены, что хотите удалить виджет?');
    await dialog().getByRole('button',{name:'Нет',exact:true}).click();
    await expect(widget('edit_notes_a')).toBeVisible();expect(await api(page,'/dashboard/layout')).toEqual(beforeCancel);
    await expect(taskDraft).toHaveValue('Ещё не отправленная задача');
    expect(nativeDialogs).toBe(0);

    // A server failure retains both the rendered instance and the saved layout.
    failure=async route=>{if(route.request().method()==='PUT')await route.fulfill({status:503,json:{detail:'Не удалось сохранить удаление'}});else await route.continue();};
    await page.route('**/api/dashboard/layout',failure);
    await widget('edit_notes_a').locator('[data-remove-widget]').click();await dialog().getByRole('button',{name:'Да',exact:true}).click();
    await expect(page.locator('#toast')).toHaveText('Не удалось сохранить удаление');
    await expect(dialog().getByRole('button',{name:'Да',exact:true})).toBeEnabled();
    await expect(widget('edit_notes_a')).toBeVisible();await expect(lock()).toHaveAttribute('aria-pressed','true');
    expect(await api(page,'/dashboard/layout')).toEqual(beforeCancel);
    await expect(taskDraft).toHaveValue('Ещё не отправленная задача');
    await dialog().getByRole('button',{name:'Нет',exact:true}).click();
    await page.unroute('**/api/dashboard/layout',failure);failure=null;

    // Deletion waits for an already pending reorder and uses its updated version.
    let started;const firstStarted=new Promise(resolve=>started=resolve),held=new Promise(resolve=>release=resolve),requests=[];
    slow=async route=>{
      if(route.request().method()==='PUT'){
        requests.push(route.request().postDataJSON());
        if(requests.length===1){started();await held;}
      }
      await route.continue();
    };
    await page.route('**/api/dashboard/layout',slow);
    await widget('edit_notes_b').locator('[data-move-widget]').press('ArrowUp');await firstStarted;
    await widget('edit_notes_a').locator('[data-remove-widget]').click();await dialog().getByRole('button',{name:'Да',exact:true}).click();
    await expect(dialog().getByRole('button',{name:'Да',exact:true})).toBeDisabled();
    expect(requests).toHaveLength(1);await expect(widget('edit_notes_a')).toBeVisible();
    release();await expect(widget('edit_notes_a')).toHaveCount(0);await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(requests).toHaveLength(2);expect(requests[1].version).toBe(requests[0].version+1);
    const expected=requests[0].instances.filter(instance=>instance.id!=='edit_notes_a');
    const saved=await api(page,'/dashboard/layout');expect(saved.instances).toEqual(expected);
    expect(saved.instances.map(instance=>instance.id)).toEqual(['edit_clock','edit_notes_b','edit_tasks','edit_hidden_calendar']);
    expect((await api(page,'/notes/'+note)).body).toBe('Этот текст должен остаться в разделе заметок.');
    await expect(taskDraft).toHaveValue('Ещё не отправленная задача');
    await expect(widget('edit_clock').locator('.clock-widget')).toHaveAttribute('data-removal-marker','preserved');
    expect(await widget('edit_clock').locator('.clock-widget').evaluate(el=>el===window.__widgetEditClock)).toBe(true);
    expect(await page.locator('#widget-grid').evaluate(el=>{
      const width=el.getBoundingClientRect().width,columns=width>=840?4:width>=440?2:1;
      return Math.abs(Number.parseFloat(el.style.getPropertyValue('--grid-cell'))-(width-(columns-1)*16)/columns)<1;
    })).toBe(true);
    await expect(widget('edit_notes_b')).toBeVisible();await expect(lock()).toHaveAttribute('aria-pressed','true');
    for(const control of await controls().all())await expect(control).toBeVisible();
    await page.unroute('**/api/dashboard/layout',slow);slow=null;

    await lock().click();await expect(lock()).toHaveAttribute('aria-pressed','false');
    for(const control of await controls().all())await expect(control).toBeHidden();
    await page.reload();await expect(widget('edit_notes_a')).toHaveCount(0);await expect(widget('edit_notes_b')).toBeVisible();
    await expect(lock()).toHaveAttribute('aria-pressed','false');for(const control of await controls().all())await expect(control).toBeHidden();
    await page.setViewportSize({width:390,height:844});await lock().click();
    for(const control of await controls().all())await expect(control).toBeVisible();
    expect(await page.locator('#widget-grid .widget-toolbar').evaluateAll(headers=>headers.every(header=>{
      const bounds=header.getBoundingClientRect();return [...header.children].every(child=>{const rect=child.getBoundingClientRect();return rect.left>=bounds.left-1&&rect.right<=bounds.right+1;});
    }))).toBe(true);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(nativeDialogs).toBe(0);
  }finally{
    release?.();
    if(failure)await page.unroute('**/api/dashboard/layout',failure);
    if(slow)await page.unroute('**/api/dashboard/layout',slow);
    page.off('dialog',native);
    if(note){await api(page,'/notes/'+note,'DELETE');await api(page,'/notes/'+note+'/permanent','DELETE');}
    const current=await api(page,'/dashboard/layout');await api(page,'/dashboard/layout','PUT',{...original,version:current.version});
    await api(page,'/modules','PUT',{enabled:modules});await page.setViewportSize({width:1280,height:900});await page.goto('/');await appSection(page,'Главная');
  }
}
