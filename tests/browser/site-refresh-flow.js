import {expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {appSection} from './notes-ui.js';

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}
async function request(context,path,method,data,csrf=''){
  const response=await context.request.fetch('/api'+path,{method,headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':csrf},data});
  expect(response.status(),await response.text()).toBe(200);return response.json();
}
async function upload(page,path){
  const csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||''));
  const response=await page.request.post('/api'+path,{headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':csrf,'Content-Type':'application/octet-stream'},data:readFileSync('tests/fixtures/blue.png')});
  expect(response.status(),await response.text()).toBe(200);return response.json();
}

const globalSelectors=['.workspace','#view-content','#open-inbox','#open-inbox svg','.sidebar .brand-mark','.user .avatar img'];
async function snapshot(page,selectors=[]){
  for(const selector of selectors)await expect(page.locator(selector)).toHaveCount(1);
  await page.evaluate(selectors=>{
    window.__siteRefreshSnapshot?.observer.disconnect();
    const saved=window.__siteRefreshSnapshot={nodes:selectors.map(selector=>({selector,node:document.querySelector(selector)})),attributes:[]};
    saved.observer=new MutationObserver(records=>saved.attributes.push(...records.map(record=>record.attributeName)));
    saved.observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-scheme','style']});
  },[...globalSelectors,...selectors]);
}
async function stable(page,{globalsOnly=false}={}){
  const state=await page.evaluate(({globalsOnly,count})=>{
    const saved=window.__siteRefreshSnapshot,nodes=globalsOnly?saved.nodes.slice(0,count):saved.nodes;
    return {nodes:Object.fromEntries(nodes.map(({selector,node})=>[selector,node===document.querySelector(selector)&&(!node||node.isConnected)])),attributes:saved.attributes};
  },{globalsOnly,count:globalSelectors.length});
  expect(state.attributes).toEqual([]);
  expect(state.nodes).toEqual(Object.fromEntries(Object.keys(state.nodes).map(key=>[key,true])));
}
async function tick(page,milliseconds,paths=[]){
  const responses=paths.map(path=>page.waitForResponse(response=>new URL(response.url()).pathname==='/api'+path&&response.request().method()==='GET'));
  await page.clock.fastForward(milliseconds);
  for(const response of await Promise.all(responses)){expect(response.status()).toBe(200);await response.finished();}
  await page.clock.runFor(50);
}

// Use a fresh context so fake time never touches TOTP or the surrounding flows.
// DOM references and attribute observers live only in this disposable test page.
export async function siteRefreshFlow(ownerPage,browser){
  const previous={me:await api(ownerPage,'/me'),modules:(await api(ownerPage,'/modules')).enabled,layout:await api(ownerPage,'/dashboard/layout')};
  const suffix=Date.now().toString(36),login='siteqa_'+suffix,errors=[],families=[];
  let context,peerContext,page,list,note,folder,event,friendship,avatarAdded=false;
  const preferences=previous.me.settings.notifications||{},appearance=previous.me.settings.appearance||{};
  try{
    await api(ownerPage,'/modules','PUT',{enabled:{notes:true,tasks:true,files:true,expenses:true}});
    await api(ownerPage,'/appearance','PUT',{...appearance,mode:'light'});
    await api(ownerPage,'/notifications/preferences','PUT',{sound:false,popups:false});
    if(!previous.me.settings.profile?.avatar_id){await upload(ownerPage,'/account/avatar');avatarAdded=true;}
    const invite=await api(ownerPage,'/invites','POST',{login});
    peerContext=await browser.newContext({baseURL:'http://127.0.0.1:8001'});
    const enrolled=await request(peerContext,'/register','POST',{login,password:'Site-QA-password-123',invite:new URL(invite.url).pathname.split('/').at(-1)});
    const peerCsrf=(await request(peerContext,'/2fa/skip','POST',undefined,enrolled.csrf)).csrf;
    const peer=await request(peerContext,'/me','GET');
    const relation=await api(ownerPage,'/social/friends','POST',{login});
    friendship=(await request(peerContext,'/social/requests/'+relation.id+'/accept','POST',undefined,peerCsrf)).space_id;
    const notifyOwner=async name=>{
      const family=await request(peerContext,'/social/families','POST',{name},peerCsrf);families.push(family.id);
      await request(peerContext,'/social/families/'+family.id+'/invites','POST',{login:previous.me.login},peerCsrf);return family.id;
    };
    const firstFamily=await notifyOwner('Уведомление без мерцания '+suffix);
    list=await api(ownerPage,'/task-lists','POST',{name:'Стабильный общий список '+suffix,retain_completed:true});
    const invited=await api(ownerPage,'/task-lists/'+list.id+'/members','POST',{user_id:peer.id});
    await request(peerContext,'/task-lists/'+list.id+'/invitations/'+invited.invitations.find(item=>item.recipient_id===peer.id).id+'/accept','POST',undefined,peerCsrf);
    let task=await api(ownerPage,'/tasks','POST',{title:'Стабильное напоминание '+suffix,list_id:list.id});
    note=await api(ownerPage,'/notes','POST',{title:'Стабильная заметка '+suffix,body:'Содержимое без фоновой перезагрузки',blocks:[{id:'site_refresh_block',type:'paragraph',content:[{type:'text',text:'Содержимое без фоновой перезагрузки',styles:{}}],children:[]}]});
    folder=await api(ownerPage,'/files/folders','POST',{name:'Стабильная папка '+suffix,parent_id:null});
    event=await api(ownerPage,'/expenses','POST',{title:'Стабильные расчёты '+suffix,participants:[{id:'me',name:'Я'},{id:'unknown',name:'Неизвестный человек'}],retention_days:30});
    await upload(ownerPage,'/expenses/'+event.id+'/photos');
    await api(ownerPage,'/dashboard/layout','PUT',{version:previous.layout.version,instances:[
      {id:'site_refresh_tasks',module_id:'tasks',title:'Общий список',size:'2x1',config:{view:'list',list_id:list.id}},
      {id:'site_refresh_clock',module_id:'clock',title:'Часы',size:'1x1',config:{mode:'analog',show_seconds:true}},
    ]});
    context=await browser.newContext({baseURL:'http://127.0.0.1:8001',storageState:await ownerPage.context().storageState(),viewport:{width:1280,height:900},timezoneId:'UTC'});
    page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    // Keep accelerated timers away from a midnight boundary: changing the day
    // legitimately changes the reminder view and is not a rendering regression.
    await page.clock.install({time:new Date(new Date().toISOString().slice(0,10)+'T12:00:00Z')});await page.goto('/');await appSection(page,'Главная');await page.bringToFront();
    await expect(page.locator('.user .avatar img')).toHaveCount(1);
    await expect.poll(()=>page.locator('.user .avatar img').evaluate(image=>image.complete&&image.naturalWidth>0)).toBe(true);

    await appSection(page,'Настройки');
    await expect(page.locator('#notification-settings [data-push-status]')).not.toContainText('Проверяем');
    const profileInput=page.getByLabel('Отображаемое имя');await profileInput.fill('Черновик имени '+suffix);
    await page.locator('#open-inbox').click();await expect(page.locator('.inbox-dialog')).toBeVisible();
    const firstNotice=(await api(page,'/notifications')).items.find(item=>item.payload.space_id===firstFamily);
    expect(firstNotice).toBeTruthy();
    await snapshot(page,['#profile-settings','#notification-settings','[data-profile-form] input[name=display_name]','.inbox-dialog [data-inbox-items]',`[data-inbox-id="${firstNotice.id}"]`]);
    await tick(page,61000);await stable(page);await expect(profileInput).toHaveValue('Черновик имени '+suffix);
    // A real new notification updates the badge and list while retaining the
    // bell SVG and older cards. Stable rendering must not freeze live data.
    const secondFamily=await notifyOwner('Новое настоящее уведомление '+suffix);
    await expect(page.locator('.inbox-dialog')).toContainText('Новое настоящее уведомление '+suffix);
    const secondNotice=(await api(page,'/notifications')).items.find(item=>item.payload.space_id===secondFamily);
    await expect(page.locator(`[data-inbox-id="${secondNotice.id}"]`)).toBeVisible();await stable(page);
    await page.locator('.inbox-dialog .modal-close').click();

    await appSection(page,'Файлы');await expect(page.locator(`[data-file-row="${folder.id}"]`)).toBeVisible();
    await snapshot(page,['.files-panel',`[data-file-row="${folder.id}"]`,'[data-new-folder]']);
    await tick(page,61000);await stable(page);

    await page.goto('/page/'+note.id);await expect(page.getByLabel('Название заметки')).toHaveValue(note.title);
    await expect(page.locator('#block-editor')).toContainText('Содержимое без фоновой перезагрузки');
    await snapshot(page,['.notes-shell','#pages-sidebar','#notion-page','#page-title','#block-editor .bn-editor','#notes-main-menu svg']);
    await tick(page,61000);await stable(page);

    await appSection(page,'Напоминания');await page.locator(`[data-task-list="${list.id}"]`).click();
    await page.locator(`.tasks-row-title[data-select-task="${task.id}"]`).click();
    await page.evaluate(()=>document.activeElement?.blur());
    await snapshot(page,['.tasks-shell',`.tasks-user-lists [data-task-list="${list.id}"]`,'[data-smart-view=all]',`[data-task-item="${task.id}"]`,'[data-add-task-title]','[data-task-properties]']);
    await tick(page,21000,['/tasks','/task-lists','/task-lists/friends']);await stable(page);
    task=await request(peerContext,'/tasks/'+task.id,'PATCH',{title:'Изменено другом '+suffix,version:task.version},peerCsrf);
    await tick(page,21000,['/tasks']);
    await expect(page.locator(`.tasks-row-title[data-select-task="${task.id}"]`)).toContainText('Изменено другом '+suffix);
    await expect(page.getByLabel('Название выбранного напоминания')).toHaveValue('Изменено другом '+suffix);await stable(page,{globalsOnly:true});

    await appSection(page,'Главная');const widget=page.locator('[data-widget-id=site_refresh_tasks]');
    await expect(widget.locator(`[data-task-row="${task.id}"]`)).toBeVisible();
    const widgetDraft=widget.getByLabel('Название напоминания',{exact:true});await widgetDraft.click();await widgetDraft.fill('Черновик виджета '+suffix);await expect(widgetDraft).toHaveValue('Черновик виджета '+suffix);
    await snapshot(page,['[data-widget-id=site_refresh_tasks] .task-widget-heading',`[data-widget-id=site_refresh_tasks] [data-task-row="${task.id}"]`,'[data-widget-id=site_refresh_tasks] [name=title]','[data-widget-id=site_refresh_clock] .clock-face']);
    const hand=page.locator('[data-widget-id=site_refresh_clock] [data-hand=second]'),oldAngle=await hand.getAttribute('transform');
    await tick(page,31000,['/tasks','/task-lists']);await stable(page);
    await expect(widgetDraft).toHaveValue('Черновик виджета '+suffix);await expect(hand).not.toHaveAttribute('transform',oldAngle);
    task=await request(peerContext,'/tasks/'+task.id,'PATCH',{title:'Новое название в виджете '+suffix,version:task.version},peerCsrf);
    await tick(page,31000,['/tasks']);await expect(widget).toContainText('Новое название в виджете '+suffix);await stable(page,{globalsOnly:true});

    await appSection(page,'Напоминания');await page.locator(`[data-task-list="${list.id}"]`).click();
    await page.evaluate(()=>document.activeElement?.blur());await api(page,'/task-lists/'+list.id,'DELETE');list=null;
    await tick(page,21000,['/tasks']);await expect(page.locator('[data-task-view-title]')).toHaveText('Напоминания');
    await expect(page.locator(`[data-task-item="${task.id}"]`)).toHaveCount(0);

    await appSection(page,'Раздельный счёт');
    const selectedEvent=page.locator(`[data-event-id="${event.id}"]`);await expect(selectedEvent).toBeVisible();
    if(await selectedEvent.getAttribute('aria-current')!=='true'){
      const loaded=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/expenses'&&response.request().method()==='GET');
      await selectedEvent.click();await (await loaded).finished();await page.clock.runFor(50);
    }
    await expect(selectedEvent).toHaveAttribute('aria-current','true');
    const photo=page.locator('.expense-photo-stack img');await photo.scrollIntoViewIfNeeded();
    await expect.poll(()=>photo.evaluate(image=>image.complete&&image.naturalWidth>0)).toBe(true);
    await page.evaluate(()=>document.activeElement?.blur());
    await snapshot(page,[`.expense-event-link[data-event-id="${event.id}"]`,'[data-event]>section','[data-add-expense]','.expense-photo-stack img']);
    await tick(page,61000,['/expenses']);await stable(page);
    event=(await api(page,'/expenses')).find(item=>item.id===event.id);
    await api(page,'/expenses/'+event.id+'/entries','POST',{event_version:event.version,amount:12345,payer:'me',allocations:{me:12345},category:'food',note:'Новый платёж из другого устройства '+suffix});
    await tick(page,61000,['/expenses']);await expect(page.locator('.expense-history')).toContainText('Новый платёж из другого устройства '+suffix);await stable(page,{globalsOnly:true});
    expect(errors).toEqual([]);
  }finally{
    if(page&&!page.isClosed()){await page.clock.resume();await page.evaluate(()=>{window.__siteRefreshSnapshot?.observer.disconnect();delete window.__siteRefreshSnapshot;});}
    await context?.close();
    if(list)await api(ownerPage,'/task-lists/'+list.id,'DELETE');
    if(event)await api(ownerPage,'/expenses/'+event.id,'DELETE');
    if(note)await api(ownerPage,'/notes/'+note.id,'DELETE');
    if(folder){await api(ownerPage,'/files/'+folder.id,'DELETE');await api(ownerPage,'/files/'+folder.id+'/permanent','DELETE');}
    if(peerContext){const cookies=await peerContext.cookies(),csrf=decodeURIComponent(cookies.find(cookie=>cookie.name==='drg_csrf')?.value||'');for(const id of families)await request(peerContext,'/social/spaces/'+id,'DELETE',undefined,csrf);await peerContext.close();}
    if(friendship)await api(ownerPage,'/social/spaces/'+friendship,'DELETE');
    const current=await api(ownerPage,'/dashboard/layout');await api(ownerPage,'/dashboard/layout','PUT',{version:current.version,instances:previous.layout.instances});
    await api(ownerPage,'/modules','PUT',{enabled:previous.modules});
    await api(ownerPage,'/appearance','PUT',appearance);
    await api(ownerPage,'/notifications/preferences','PUT',{sound:preferences.sound??true,popups:preferences.popups??true});
    if(avatarAdded)await api(ownerPage,'/account/avatar','DELETE');
  }
}
