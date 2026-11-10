import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
const phase=process.env.DRG_DRAG_PHASE||'after',engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const dir=await mkdtemp(tmpdir()+'/drg-reminder-drag-'),origin='http://127.0.0.1:8066';
const server=spawn('.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8066','tests.browser_app:app'],{env:{...process.env,DATA_DIR:dir,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-reminder-drag-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
const exited=new Promise(resolve=>server.once('exit',resolve));let output='',browser;server.stderr.on('data',d=>output=(output+d).slice(-3000));
try{
 let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,200));}if(!ready)throw Error(output);
 browser=await({chromium,webkit}[engine]).launch(); const context=await browser.newContext({baseURL:origin,viewport:{width:1440,height:950},locale:'ru-RU'});
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let csrf='';const password='Reminder-preview-password-123';
 const api=async(path,data,method='POST')=>{const r=await context.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});if(!r.ok())throw Error(path+' '+await r.text());return r.json();};
 csrf=(await api('/setup',{login:'preview',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await api('/2fa/enroll',{password}),bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 await api('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await api('/appearance',{theme:'ocean',style:'glass',mode:'light',wallpaper:'mountains',seasonal:'off'},'PUT');
 const list=await api('/task-lists',{name:'Семейные планы',retain_completed:true});
 const first=await api('/tasks',{title:'Подготовиться к поездке',list_id:list.id,notes:'Проверить документы и билеты',priority:2});
 const child=await api('/tasks',{title:'Забронировать гостиницу',list_id:list.id,parent_id:first.id});
 const cases=await api('/tasks',{title:'Собрать чемоданы',list_id:list.id});const food=await api('/tasks',{title:'Купить продукты',list_id:list.id,flagged:true});const call=await api('/tasks',{title:'Позвонить родителям',list_id:list.id});
 const out='artifacts/reminder-drag/'+phase+'/'+engine;await mkdir(out,{recursive:true});
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:width===390?844:950});await page.goto('/');await page.locator('.workspace').waitFor();
  const tab=page.locator('[data-mobile-view=tasks]');if(await tab.isVisible())await tab.click();else await page.locator('[data-view=tasks]').click();
  await page.locator('.tasks-shell').waitFor();if(width===390)await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator('[data-task-list="'+list.id+'"]').click();
  const parentTitle=page.locator('[data-toggle-task-children="'+first.id+'"]'),nested=page.locator('[data-task-item="'+child.id+'"]');
  await parentTitle.click();await expect(nested).toBeVisible();await expect(page.locator('.tasks-properties')).toBeHidden();
  await parentTitle.click();await expect(nested).toHaveCount(0);
  await parentTitle.focus();await page.keyboard.press('Enter');await expect(nested).toBeVisible();
  await page.locator('[data-expand-task="'+first.id+'"]').click();await expect(nested).toHaveCount(0);
  if(width===390&&engine==='chromium'){
   const touch=await context.newCDPSession(page);
   for(const expanded of [true,false]){
    const b=await parentTitle.boundingBox();await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:b.x+b.width/2,y:b.y+b.height/2}]});await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await expect(parentTitle).toHaveAttribute('aria-expanded',String(expanded));await expect(page.locator('.tasks-properties')).toBeHidden();
   }
   await touch.detach();
  }
  await page.locator('.tasks-row-info[data-select-task="'+first.id+'"]').click();await expect(page.locator('[data-task-properties]')).toBeVisible();await page.locator('[data-close-task-properties]').click();
  if(width===390)await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator('[data-tasks-search]').fill(first.title);if(width===390)await page.locator('[data-tasks-main-menu]').click();
  await parentTitle.click();await expect(nested).toBeVisible();await expect(page.locator('.tasks-properties')).toBeHidden();
  if(width===390)await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator('[data-tasks-search]').fill('');if(width===390)await page.locator('[data-tasks-main-menu]').click();
  await parentTitle.click();await expect(nested).toHaveCount(0);
  await page.locator('[data-expand-task="'+first.id+'"]').click();await page.waitForTimeout(700);
  await page.screenshot({path:out+'/'+width+'-screen.png',animations:'disabled'});
 }
 const ids=async()=>page.locator('.tasks-reminder-list > [data-task-item]').evaluateAll(rows=>rows.map(r=>r.dataset.taskItem));
 const rows=async()=>api('/tasks',undefined,'GET');
 const drag=async(source,target,zone='inside',cancel=false)=>{
  const sourceHandle=page.locator('[data-move-task="'+source+'"]'),targetRow=page.locator('[data-task-item="'+target+'"] > .tasks-reminder-row');
  const a=await sourceHandle.boundingBox(),b=await targetRow.boundingBox();
  await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(a.x+a.width/2+12,a.y+a.height/2,{steps:3});
  await page.mouse.move(b.x+b.width/2,b.y+b.height*(zone==='before'?.1:zone==='after'?.9:.5),{steps:10});
  await expect(page.locator('.tasks-drag-ghost')).toBeVisible();
  if(!cancel)await page.screenshot({path:out+'/drag-'+zone+'.png'});
  if(cancel)await page.keyboard.press('Escape');await page.mouse.up();await expect(page.locator('.tasks-drag-ghost')).toHaveCount(0);
 };
 await expect.poll(ids).toEqual([first.id,cases.id,food.id,call.id]);
 await drag(call.id,first.id,'before');await expect.poll(ids).toEqual([call.id,first.id,cases.id,food.id]);await expect(page.locator('.tasks-shell')).not.toHaveClass(/is-saving/);
 await drag(cases.id,first.id);await expect.poll(async()=>(await rows()).find(t=>t.id===cases.id).parent_id).toBe(first.id);await expect.poll(ids).toEqual([call.id,first.id,food.id]);await expect(page.locator('.tasks-shell')).not.toHaveClass(/is-saving/);
 const beforeCancel=await rows();await drag(food.id,call.id,'before',true);expect(await rows()).toEqual(beforeCancel);
 // The keyboard-accessible move form also extracts and orders a child.
 await page.locator('[data-move-task="'+cases.id+'"]').focus();await page.keyboard.press('Enter');
 await expect(page.locator('[data-move-form]')).toBeVisible();await page.locator('[data-move-parent]').selectOption('');await page.locator('[data-move-before]').selectOption(call.id);await page.locator('[data-move-form] button[type=submit]').click();
 await expect.poll(ids).toEqual([cases.id,call.id,first.id,food.id]);
 await expect(page.locator('.tasks-properties')).toBeHidden();
 // A concurrent edit rejects the entire move, without an optimistic shuffle.
 await page.locator('[data-move-task="'+food.id+'"]').click();
 await api('/tasks/'+call.id,{notes:'Changed on another device'},'PATCH');await page.locator('[data-move-before]').selectOption(cases.id);await page.locator('[data-move-form] button[type=submit]').click();
 await expect(page.locator('[data-tasks-feedback]')).toContainText('Список уже изменился');
 expect(await ids()).toEqual([cases.id,call.id,first.id,food.id]);
 await page.locator('[data-close-task-properties]').click();await expect(page.locator('.tasks-properties')).toBeHidden();await page.locator('[data-refresh-tasks]').click();await expect(page.locator('[data-task-item="'+call.id+'"] > .tasks-reminder-row')).toContainText('Changed on another device');
 // A failed request leaves the visible and persisted order unchanged.
 await page.route('**/api/tasks/*/move',route=>route.fulfill({status:503,json:{detail:'Network unavailable'}}));
 await drag(food.id,cases.id,'before');await expect(page.locator('[data-tasks-feedback]')).toContainText('Network unavailable');expect(await ids()).toEqual([cases.id,call.id,first.id,food.id]);await page.unroute('**/api/tasks/*/move');
 await page.reload();await page.locator('[data-mobile-view=tasks]').click();await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator('[data-task-list="'+list.id+'"]').click();await expect.poll(ids).toEqual([cases.id,call.id,first.id,food.id]);
 // A real touch drag is delivered through Chromium's input protocol.
 if(engine==='chromium'){
  const touch=await context.newCDPSession(page),a=await page.locator('[data-move-task="'+food.id+'"]').boundingBox(),b=await page.locator('[data-task-item="'+cases.id+'"] > .tasks-reminder-row').boundingBox();
  await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:a.x+a.width/2,y:a.y+a.height/2}]});
  for(let step=1;step<=12;step++)await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:a.x+a.width/2+(b.x+b.width/2-a.x-a.width/2)*step/12,y:a.y+a.height/2+(b.y+3-a.y-a.height/2)*step/12}]});
  await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.detach();
 }else await drag(food.id,cases.id,'before');
 await expect.poll(ids).toEqual([food.id,cases.id,call.id,first.id]);
 for(const width of [320,390,430,1440])for(const language of ['en','ru']){
  await api('/language',{language},'PUT');await page.setViewportSize({width,height:844});await page.reload();await page.locator('.workspace').waitFor();
  const nav=page.locator('[data-mobile-view=tasks]');if(!await page.locator('.tasks-shell').count()){if(await nav.isVisible())await nav.click();else await page.locator('[data-view=tasks]').click();}
  if(width<700)await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator('[data-task-list="'+list.id+'"]').click();
  const overflow=await page.locator('.tasks-center').evaluate(el=>el.scrollWidth>el.clientWidth+1);expect(overflow).toBe(false);
  await page.locator('[data-move-task="'+food.id+'"]').click();await expect(page.locator('[data-move-form]')).toBeVisible();
  await expect(page.locator('[data-move-form] button[type=submit]')).toHaveText(language==='en'?'Move':'Переместить');
  await page.screenshot({path:out+'/'+language+'-'+width+'-move.png',animations:'disabled'});await page.locator('[data-close-task-properties]').click();
 }
 for(const style of ['classic','glass']){
  await api('/appearance',{theme:'ocean',style,mode:'dark',wallpaper:'mountains',seasonal:'off'},'PUT');await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('.workspace').waitFor();
  if(!await page.locator('.tasks-shell').count())await page.locator('[data-mobile-view=tasks]').click();
  await page.locator('[data-toggle-tasks-sidebar]').click();await page.locator('[data-task-list="'+list.id+'"]').click();await page.locator('[data-move-task="'+food.id+'"]').click();
  const contrast=await page.locator('[data-move-form] button[type=submit]').evaluate(el=>{
   const lum=value=>{const rgb=value.match(/[\d.]+/g).slice(0,3).map(Number).map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];};
   const c=getComputedStyle(el),a=lum(c.color),b=lum(c.backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
  });expect(contrast).toBeGreaterThan(4.5);await page.screenshot({path:out+'/'+style+'-dark-move.png',animations:'disabled'});await page.locator('[data-close-task-properties]').click();
 }
 await page.setViewportSize({width:1440,height:844});await page.reload();await page.locator('.workspace').waitFor();
 if(!await page.locator('.tasks-shell').count())await page.locator('[data-view=tasks]').click();
 await page.locator('[data-task-list="'+list.id+'"]').click();
 // Holding a dragged row at the edge scrolls a long list and can be cancelled.
 for(let index=0;index<35;index++)await api('/tasks',{title:'Дополнительное дело '+index,list_id:list.id});
 await page.locator('[data-refresh-tasks]').click();await expect(page.locator('[data-task-item]')).toHaveCount(39);
 const saved=await rows(),area=page.locator('[data-task-rows]'),areaBox=await area.boundingBox(),handleBox=await page.locator('[data-move-task="'+food.id+'"]').boundingBox();
 await page.mouse.move(handleBox.x+12,handleBox.y+12);await page.mouse.down();await page.mouse.move(areaBox.x+areaBox.width/2,areaBox.y+areaBox.height-6,{steps:15});
 await expect.poll(()=>area.evaluate(el=>el.scrollTop)).toBeGreaterThan(100);await page.keyboard.press('Escape');await page.mouse.up();expect(await rows()).toEqual(saved);
 await writeFile(out+'/report.json' ,JSON.stringify({phase,engine,errors},null,2));if(errors.length)throw Error(errors.join('\n'));console.log('Baseline screenshots captured',phase,engine);
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(dir,{recursive:true,force:true});}
