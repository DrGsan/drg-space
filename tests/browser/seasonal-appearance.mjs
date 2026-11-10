// Real application and isolated SQLCipher account; no production data is used.
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdir,mkdtemp,rm,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {appSection} from './notes-ui.js';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const directory=await mkdtemp(tmpdir()+'/drg-seasonal-'),origin='http://127.0.0.1:'+(process.env.DRG_SEASONAL_PORT||(engine==='webkit'?'8063':'8062'));
const out=process.env.DRG_SEASONAL_OUT||('artifacts/four-task-review/holiday'+(engine==='webkit'?'/webkit':''));await mkdir(out,{recursive:true});
const themes=JSON.parse(await readFile('app/seasonal_themes.json','utf8'));
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind',new URL(origin).host,'tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-seasonal-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));const exited=new Promise(resolve=>server.once('exit',resolve));
const report={engine,screenshots:[],matrix:[],headers:[]};
try{
  let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,200));}
  if(!ready)throw Error(output);
  browser=await ({chromium,webkit}[engine]).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000},colorScheme:'light',timezoneId:'Europe/Moscow'});
  await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,get:()=>true}));
  const page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/calendar/holidays?*',route=>route.fulfill({json:{events:[],has_data:false,status:'unavailable',next_check_at:9999999999}}));
  await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  let csrf='',language='ru';const password='Seasonal-test-password-123';
  const send=async(path,body,method='POST')=>{const r=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
  csrf=(await send('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
  const {secret}=await send('/2fa/enroll',{password}),alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
  await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
  await send('/modules',{enabled:{tracker:true}},'PUT');
  const note=await send('/notes',{title:'Планы на выходные',body:'Выбрать маршрут для прогулки.'});
  const project=await send('/tracker/projects',{name:'DrG Space — личные задачи',key:'SITE',description:'Проверка праздничной шапки'});
  await send('/appearance',{wallpaper:'mountains',mode:'light',style:'glass',seasonal:'off'},'PUT');
  await page.clock.setFixedTime(new Date('2026-10-31T09:00:00Z'));await page.goto('/');
  if(process.env.DRG_SEASONAL_FOCUS==='tracker'){
    await send('/appearance',{seasonal:'auto'},'PUT');await page.reload();await appSection(page,'Задачи');await page.locator('[data-tracker-project="'+project.id+'"]').click();await expect(page.locator('.tracker-loading')).toHaveCount(0);
    for(const width of [390,320]){
      const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760;
      await page.setViewportSize({width,height:844});if(crossed)await expect.poll(()=>old.evaluate(node=>node.isConnected)).toBe(false);
      await page.locator('.tracker-chrome.is-project').waitFor();await expect(page.locator('.tracker-loading')).toHaveCount(0);
      const header=page.locator('.tracker-chrome');await expect(header.locator(':scope>.seasonal-decoration')).toBeVisible();
      const collisions=await header.evaluate(host=>{
        const r=host.querySelector('.seasonal-motif').getBoundingClientRect();return [...host.querySelectorAll('button')].filter(b=>b.getClientRects().length).filter(b=>{const q=b.getBoundingClientRect();return Math.min(r.right,q.right)-Math.max(r.left,q.left)>.5&&Math.min(r.bottom,q.bottom)-Math.max(r.top,q.top)>.5;}).map(b=>b.getAttribute('aria-label')||b.textContent);
      });expect(collisions,'Tracker decoration must stay off button backgrounds').toEqual([]);
      for(const button of await header.locator('button:visible').all())await expect.poll(()=>button.evaluate(b=>{const r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===b||b.contains(hit);}),{timeout:5000}).toBe(true);
      await header.screenshot({path:out+'/mobile-tracker-'+width+'-header.png',animations:'disabled'});await page.screenshot({path:out+'/mobile-tracker-'+width+'-full.png',animations:'disabled'});
    }
    expect(errors).toEqual([]);await writeFile(out+'/tracker-focused.json',JSON.stringify({passed:true,engine,widths:[390,320],buttonOverlap:false}));console.log('Holiday tracker focused passed:',engine);
  }else{
  const saved=async()=>(await send('/appearance',undefined,'GET')).preferences;
  async function appearance(){
    await page.locator('.workspace').waitFor({state:'visible'});
    await page.locator('[data-mobile-view=settings],.sidebar nav').first().waitFor({state:'attached'});
    if(await page.locator('[data-mobile-view=settings]').isVisible())await page.locator('[data-mobile-view=settings]').click();
    else if(language==='ru')await appSection(page,'Настройки');
    else{await page.locator('.sidebar nav').getByRole('button',{name:'Settings',exact:true}).click();await page.locator('[data-preference-key="appearance"]').waitFor();}
    for(const row of await page.locator('.preference-row').all()){
      const wantsOpen=await row.getAttribute('data-preference-key')==='appearance';
      if((await row.getAttribute('open')!==null)!==wantsOpen)await row.locator(':scope>summary').click();
    }
    await page.locator('[data-seasonal-preview]').waitFor();
  }
  const preview=theme=>page.locator('[data-seasonal-preview]').selectOption(theme);
  async function resize(width){
    const crossed=(page.viewportSize().width<=760)!==(width<=760),old=await page.locator('.workspace').elementHandle();
    await page.setViewportSize({width,height:width===1440?1000:844});
    if(crossed&&old)await expect.poll(()=>old.evaluate(node=>node.isConnected)).toBe(false);
    await appearance();
  }
  const layers=page.locator('.seasonal-decoration');
  async function assertTheme(theme){
    if(!theme)await expect(layers).toHaveCount(0);
    else{await expect(page.locator('.seasonal-preview-header>.seasonal-decoration')).toHaveAttribute('data-seasonal-theme',theme);expect(await layers.evaluateAll((nodes,id)=>nodes.every(n=>n.dataset.seasonalTheme===id),theme)).toBe(true);}
  }
  const geometry=()=>page.locator('.content>.topbar:visible,#view-content>.section-heading:visible,.seasonal-preview-header').evaluateAll(hosts=>hosts.map(host=>{
    const b=host.getBoundingClientRect();return {width:b.width,height:b.height,controls:[...host.querySelectorAll('button,strong,select')].map(e=>{const r=e.getBoundingClientRect();return [e.tagName,e.textContent,r.x-b.x,r.y-b.y,r.width,r.height];})};
  }));
  async function capture(label){
    for(const [part,selector] of [['header','.content>.topbar:visible,#view-content>.section-heading:visible'],['settings','.appearance-seasonal']]){
      const path=out+'/'+label+'-'+part+'.png';await page.locator(selector).screenshot({path,animations:'disabled'});report.screenshots.push(path);
    }
    if(label.includes('before-off')||label.includes('after-halloween')){
      await page.locator('.content>.topbar:visible,#view-content>.section-heading:visible').scrollIntoViewIfNeeded();
      const path=out+'/'+label+'-full.png';await page.screenshot({path,animations:'disabled'});report.screenshots.push(path);
    }
  }
  await appearance();const persistedBefore=await saved();
  for(const width of [1440,390]){
    await resize(width);await preview('');await assertTheme('');await capture('before-off-'+width);
    for(const theme of themes){
      const before=await geometry();await preview(theme.id);await assertTheme(theme.id);expect(await geometry()).toEqual(before);
      const frame=page.locator('.seasonal-shell-decoration'),ambient=frame.locator('.seasonal-ambient');
      await expect(frame).toHaveAttribute('data-seasonal-particles',theme.particles);
      expect(await ambient.count()).toBe(theme.particles==='none'?0:24);
      const names=await ambient.evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).animationName));
      expect(names.every(name=>name===(theme.particles==='stars'?'seasonal-star-drift':['hearts','notes'].includes(theme.particles)?'seasonal-rise':'seasonal-fall'))).toBe(true);
      // Scroll must preserve the animation nodes rather than restart their clocks.
      await frame.evaluate(node=>{node.__ambient=node.querySelector('.seasonal-ambient');});
      await page.evaluate(()=>document.dispatchEvent(new Event('scroll',{bubbles:true})));
      expect(await frame.evaluate(node=>node.__ambient===node.querySelector('.seasonal-ambient'))).toBe(true);
      const headerLayer=page.locator('.content>.topbar:visible>.seasonal-decoration,#view-content>.section-heading:visible>.seasonal-decoration');
      if(!await headerLayer.isVisible())console.log('Header layer diagnostics',await headerLayer.evaluate(e=>({rect:e.getBoundingClientRect().toJSON(),host:e.parentElement.getBoundingClientRect().toJSON(),styles:{display:getComputedStyle(e).display,visibility:getComputedStyle(e).visibility,position:getComputedStyle(e).position,inset:getComputedStyle(e).inset,margin:getComputedStyle(e).margin},hostClass:e.parentElement.className})));
      await expect(headerLayer).toBeVisible();
      const ids=await page.locator('.seasonal-decoration [id],.seasonal-shell-decoration [id]').evaluateAll(nodes=>nodes.map(n=>n.id));expect(new Set(ids).size).toBe(ids.length);
      await expect(page.locator('[data-appearance-status]')).toHaveText('');expect(await saved()).toEqual(persistedBefore);
      await capture('after-'+theme.id+'-'+width);
    }
    console.log('Holiday review images ready:',width,out);
  }
  // A preview never becomes a preference, even when another setting is saved.
  await page.locator('[name=seasonal]').selectOption('auto');await preview('space');
  await page.locator('[data-appearance] [type=submit]').click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');
  expect((await saved()).seasonal).toBe('auto');await expect(page.locator('[data-seasonal-preview]')).toHaveValue('space');await assertTheme('space');
  await preview('newyear');await page.locator('[data-appearance-reset]').click();await expect(page.locator('[data-seasonal-preview]')).toHaveValue('newyear');await assertTheme('newyear');
  const phrases=JSON.parse(await readFile('locales/ru/dashboard.json','utf8'));
  for(const theme of themes){
    await preview(theme.id);await page.locator('[data-mobile-open=home]').click();
    await expect(page.locator('[data-day-greeting]')).toHaveText(phrases[theme.phrase_key]);
    if(theme.id==='starwars')await page.screenshot({path:out+'/home-starwars-greeting.png',animations:'disabled'});
    const end=page.locator('.seasonal-preview-control button');await end.click();
    await expect(page.locator('[data-day-greeting]')).toHaveText(phrases.holidayHalloweenPhrase);
    await appearance();
  }
  await preview('space');await page.locator('[data-mobile-open=home]').click();await expect(page.locator('.content>.topbar>.seasonal-decoration')).toHaveAttribute('data-seasonal-theme','space');await appearance();await assertTheme('space');
  await preview('space');await page.reload();await appearance();await assertTheme('halloween');
  await page.locator('[name=seasonal]').selectOption('off');await page.locator('[data-appearance] [type=submit]').click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');await assertTheme('');
  await send('/appearance',{theme:'sage'},'PUT');expect((await saved()).seasonal).toBe('off');
  // All catalog themes must remain within each translated layout and palette.
  for(const lang of ['ru','en']){
    language=lang;await send('/language',{language},'PUT');await page.setViewportSize({width:1440,height:1000});await page.reload();await appearance();
    expect(await page.locator('[data-seasonal-preview] option').count()).toBe(themes.length+1);
    for(const style of ['classic','glass'])for(const mode of ['light','dark'])for(const width of [320,390,1440]){
      await resize(width);await page.locator('[name=style][value='+style+']').check();await page.locator('[name=mode]').selectOption(mode);
      for(const theme of themes){await preview(theme.id);await assertTheme(theme.id);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);}
      report.matrix.push({language,style,mode,width});
    }
  }
  await preview('newyear');await page.emulateMedia({reducedMotion:'reduce'});
  expect(await layers.locator('*').evaluateAll(nodes=>nodes.every(node=>getComputedStyle(node).animationName==='none'))).toBe(true);
  const transforms=()=>layers.locator('.seasonal-particle').evaluateAll(nodes=>nodes.map(node=>getComputedStyle(node).transform));
  const still=await transforms();await page.waitForTimeout(150);expect(await transforms()).toEqual(still);
  await page.emulateMedia({reducedMotion:'no-preference'});
  // Exercise the document visibility event without adding production hooks.
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  await expect.poll(()=>layers.evaluateAll(nodes=>nodes.every(n=>n.classList.contains('seasonal-paused')))).toBe(true);
  expect(await page.locator('.seasonal-shell-decoration .seasonal-ambient').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationPlayState==='paused'))).toBe(true);
  expect(await layers.locator('.seasonal-particle').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationPlayState==='paused'))).toBe(true);
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await expect.poll(()=>layers.evaluateAll(nodes=>nodes.every(n=>!n.classList.contains('seasonal-paused')))).toBe(true);
  // After a reload clears the temporary preview, the calendar supplies Halloween
  // to the existing workspace chrome through real navigation.
  language='ru';await send('/language',{language},'PUT');await send('/appearance',{seasonal:'auto',style:'glass',mode:'light'},'PUT');await page.setViewportSize({width:1440,height:1000});await page.reload();
  for(const [name,selector] of [['Главная','#view-content>.section-heading'],['Заметки','.notes-shell .page-top'],['Напоминания','.tasks-toolbar'],['Файлы','.drive-workspace-header'],['Задачи','.tracker-chrome'],['Сообщения','.chat-nav-head']]){
    await appSection(page,name);if(name==='Заметки')await page.locator('[data-open-page="'+note.id+'"]').click();
    const header=page.locator(selector);await expect(header).toBeVisible();await expect(header.locator(':scope>.seasonal-decoration')).toHaveAttribute('data-seasonal-theme','halloween');
    const buttons=header.locator('button:visible');
    for(const button of await buttons.all())await expect.poll(()=>button.evaluate(b=>{const r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===b||b.contains(hit);}),{timeout:5000}).toBe(true);
    report.headers.push({name,selector});await header.screenshot({path:out+'/section-'+report.headers.length+'.png',animations:'disabled'});
  }
  for(const width of [390,320]){
    const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760;
    await page.setViewportSize({width,height:844});if(crossed)await expect.poll(()=>old.evaluate(node=>node.isConnected)).toBe(false);
    await page.locator('[data-mobile-view=notes]').click();await page.locator('.notes-shell').waitFor();
    if(!await page.locator('.notes-shell.mobile-notes-list').count())await page.locator('#notes-main-menu').click();
    for(const [label,selector] of [['notes-list','.notes-navigation'],['notes-document','.notes-navigation'],['tracker','.tracker-chrome'],['drive','.drive-workspace-header']]){
      if(label==='notes-document')await page.locator('[data-open-page="'+note.id+'"]').click();
      if(label==='tracker'){await page.locator('[data-mobile-view=tracker]').click();await expect.poll(async()=>await page.locator('.tracker-chrome.is-project,[data-tracker-project="'+project.id+'"]').count()).toBeGreaterThan(0);if(await page.locator('[data-tracker-project="'+project.id+'"]').count())await page.locator('[data-tracker-project="'+project.id+'"]').click();await page.locator('.tracker-chrome.is-project').waitFor();await expect(page.locator('.tracker-loading')).toHaveCount(0);}
      if(label==='drive'){await page.locator('[data-mobile-view=settings]').click();await page.locator('[data-mobile-open=files]').click();await page.locator('.drive-panel').waitFor();}
      const header=page.locator(selector);await expect(header).toBeVisible();await expect(header.locator(':scope>.seasonal-decoration')).toBeVisible();
      if(label.startsWith('notes-')){
        const overlaps=()=>page.evaluate(()=>{
          const motif=document.querySelector('.notes-navigation .seasonal-motif').getBoundingClientRect(),failures=[];
          const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
          const intersects=r=>Math.min(motif.right,r.right)-Math.max(motif.left,r.left)>.5&&Math.min(motif.bottom,r.bottom)-Math.max(motif.top,r.top)>.5;
          for(const svg of document.querySelectorAll('.notes-navigation button svg,.pages-sidebar-head button svg,.page-top-actions svg'))if(visible(svg)&&intersects(svg.getBoundingClientRect()))failures.push('icon:'+svg.closest('button')?.id);
          for(const select of document.querySelectorAll('.pages-sidebar-head select'))if(visible(select)){
            const r=select.getBoundingClientRect(),css=getComputedStyle(select),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font=css.font;
            const metrics=ctx.measureText(select.selectedOptions[0].textContent),height=metrics.actualBoundingBoxAscent+metrics.actualBoundingBoxDescent,left=r.left+parseFloat(css.paddingLeft),top=r.top+(r.height-height)/2;
            if(intersects({left,top,right:Math.min(r.right,left+metrics.width),bottom:top+height}))failures.push('text:'+select.id);
          }
          return failures;
        });
        await expect.poll(overlaps,{message:'Holiday motif must clear real note text and glyphs'}).toEqual([]);
      }
      await header.screenshot({path:out+'/mobile-'+label+'-'+width+'-header.png',animations:'disabled'});
      await page.screenshot({path:out+'/mobile-'+label+'-'+width+'-full.png',animations:'disabled'});
      const buttons=header.locator('button:visible');
      for(const button of await buttons.all())await expect.poll(()=>button.evaluate(b=>{const r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===b||b.contains(hit);}),{timeout:5000}).toBe(true);
      report.headers.push({name:label,width,selector});
    }
    await page.locator('#files-main-menu').click();
  }
  // Returning from a conversation only toggles a class: no child-list mutation
  // or resize should be required before the list's holiday bar returns.
  await page.locator('[data-mobile-view=chat]').click();await page.locator('.chat-nav-head').waitFor();
  await expect(page.locator('.chat-sidebar-heading>.seasonal-decoration')).toBeVisible();
  await page.locator('.chat-shell').evaluate(el=>el.classList.add('room-open'));
  await expect(page.locator('.chat-nav-head')).toBeHidden();await expect(page.locator('.chat-sidebar-heading>.seasonal-decoration')).toHaveCount(0);
  await page.locator('.chat-shell').evaluate(el=>el.classList.remove('room-open'));
  await expect(page.locator('.chat-sidebar-heading>.seasonal-decoration')).toBeVisible({timeout:1500});
  report.hiddenNavigationRestored=true;
  expect(errors).toEqual([]);report.passed=true;await writeFile(out+'/report.json',JSON.stringify(report,null,2));console.log('Holiday browser matrix passed:',engine,report.matrix.length,'layouts,',report.headers.length,'real headers.');
  }
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
