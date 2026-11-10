import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {expect} from '@playwright/test';
import {appSection} from './notes-ui.js';

export async function glassAppearanceFlow(page,{engine='chromium',compareOriginal=false,origin='http://127.0.0.1:8001'}={}){
  const out='artifacts/glass-appearance';await mkdir(out,{recursive:true});
  const catalog=JSON.parse(await readFile('app/wallpaper_catalog.json','utf8'));
  const presetIds=catalog.flatMap(category=>category.items.map(item=>item.id));
  const chooseWallpaper=async id=>{
    const category=catalog.find(category=>category.items.some(item=>item.id===id));
    if(category)await page.locator('[data-wallpaper-category="'+category.id+'"]').click();
    await page.locator('button[data-wallpaper="'+id+'"]').click();
  };
  const errors=[],webkitResourceErrors=[];
  const captureError=(error,localErrors=errors)=>{
    // WebKit also reports cancelled same-origin fetches as resource-policy
    // errors during reload, even when the caller catches the rejection. Keep
    // these separate from JS exceptions and verify browser fetches below.
    if(engine==='webkit'&&error.name==='Fetch API cannot load http'&&error.stack?.includes(origin+'/api/')&&error.message.endsWith('due to access control checks.'))webkitResourceErrors.push(error.message);
    else localErrors.push(error.message);
  };
  const onPageError=error=>captureError(error);page.on('pageerror',onPageError);
  await page.route('**/api/widgets/*/weather',r=>r.fulfill({json:{configured:true,city:'Москва',provider:'met-no',forecast_range:'next_24h',forecast_time:1793048400,checked_at:1793048400,current:{temperature_2m:12,weather_code:0,wind_speed_10m:5},daily:{temperature_2m_min:[8],temperature_2m_max:[19]},hourly:[]}}));
  const api=async(path,method='GET',body)=>{
    const csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||''));
    const response=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});
    assert(response.ok(),path+' '+response.status()+' '+await response.text());return response.json();
  };
  const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const bounds=selector=>page.locator(selector).evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {id:el.dataset.widgetId||el.classList[0],x:r.x,y:r.y,width:r.width,height:r.height};}));
  const within=async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Horizontal viewport overflow');
  const same=(before,after,label)=>{
    assert.equal(after.length,before.length,label+' count');
    for(let i=0;i<before.length;i++)for(const key of ['x','y','width','height'])assert(Math.abs(before[i][key]-after[i][key])<=.6,`${label} ${i} ${key}: ${before[i][key]} -> ${after[i][key]}`);
  };
  const headingFits=async()=>{
    const fit=await page.locator('.dashboard-heading').evaluate(heading=>{
      const subtitle=heading.querySelector('.section-heading-copy>p'),title=heading.querySelector('h1');
      const box=heading.getBoundingClientRect(),text=subtitle.getBoundingClientRect(),top=title.getBoundingClientRect();
      return getComputedStyle(subtitle).position==='static'&&text.top>=top.bottom+3&&text.bottom<=box.bottom-6;
    });
    assert(fit,'The subtitle must sit below the greeting, inside the glass heading with bottom padding');
  };
  async function section(name){
    await appSection(page,name);
    if(name==='Люди и группы')await page.locator('.federation-social-host button').first().waitFor();
    if(name==='Раздельный счёт')await page.locator('.expense-list-heading').waitFor();
    if(name==='Настройки')for(const row of await page.locator('.preference-row').all())if((await row.getAttribute('data-preference-key'))!=='appearance'&&await row.getAttribute('open')!==null)await row.locator(':scope>summary').click();
    await page.evaluate(()=>scrollTo(0,0));await settle();
  }
  async function home(){await section('Главная');await expect(page.locator('[data-widget=tasks] .task-widget-heading')).toBeVisible();await expect(page.locator('[data-widget=expenses] .expense-widget')).toBeVisible();await expect(page.locator('[data-widget=weather] .weather-temperature')).toBeVisible();if(page.viewportSize().width>=1000)await page.waitForFunction(()=>[...document.querySelectorAll('[data-widget=bookmarks] img')].every(image=>image.complete));}
  await page.goto('/');await page.locator('.workspace').waitFor();
  await expect(page.locator('html')).toHaveAttribute('data-interface','glass');
  const effective=(await api('/me')).settings.appearance;
  assert.equal(effective.style,'glass');assert(presetIds.includes(effective.wallpaper));
  await expect(page.locator('html')).toHaveAttribute('data-wallpaper',effective.wallpaper);

  await api('/appearance','PUT',{theme:'amber',mode:'light',style:'classic',wallpaper:'warm'});await page.reload();
  const layout=await api('/dashboard/layout'),notes=await api('/notes'),tasks=await api('/tasks');
  const sizes=[[1440,1000],[1280,900],[768,1024],[390,844],[320,740]],classic={};
  const original=compareOriginal?JSON.parse(await readFile(out+'/before-geometry.json','utf8')):null;
  for(const [width,height] of sizes){
    await page.setViewportSize({width,height});await home();await within();classic[width]=await bounds('[data-widget]');
    if(original)same(original[width],classic[width],'Original main '+width);
    await page.screenshot({path:`${out}/before-home-${width}-${engine}.png`});
  }
  const sections=[['Заметки','.pages-sidebar,.notion-page'],['Напоминания','.tasks-sidebar,.tasks-center'],['Сообщения','.chat-shell'],['Файлы','.drive-panel'],['Раздельный счёт','.expense-layout'],['Люди и группы','.social-view']];
  const classicSections={};
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    for(const [name,selector] of sections){await section(name);await page.locator(selector).first().waitFor({state:'attached'});await within();classicSections[name+width]=await bounds(selector);if(width===1440)await page.screenshot({path:`${out}/before-section-${sections.findIndex(x=>x[0]===name)}-${engine}.png`});}
  }
  await page.setViewportSize({width:1440,height:1000});await section('Настройки');
  // Preview changes neither persisted preferences nor the dashboard layout.
  await page.getByRole('radio',{name:'Стекло',exact:true}).check();await expect(page.locator('html')).toHaveAttribute('data-interface','glass');assert.equal((await api('/appearance')).preferences.style,'classic');
  await page.getByRole('button',{name:'Туман',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-wallpaper','mist');
  await section('Главная');await expect(page.locator('html')).toHaveAttribute('data-interface','classic');await expect(page.locator('html')).toHaveAttribute('data-wallpaper','warm');
  await section('Настройки');await page.getByRole('radio',{name:'Стекло',exact:true}).check();await page.getByRole('button',{name:'Сохранить оформление',exact:true}).click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');
  await page.reload();await expect(page.locator('html')).toHaveAttribute('data-interface','glass');
  await section('Настройки');await page.evaluate(()=>scrollTo(0,270));await page.screenshot({path:`${out}/after-settings-${engine}.png`});
  await page.locator('.wallpaper-choices:not([hidden])').screenshot({path:`${out}/wallpaper-choices-${engine}.png`});
  const glass={};
  for(const [width,height] of sizes){
    await page.setViewportSize({width,height});await home();await within();await headingFits();glass[width]=await bounds('[data-widget]');same(classic[width],glass[width],'Home '+width);
    await page.locator('#toast').evaluate(el=>el.classList.remove('show'));
    await page.screenshot({path:`${out}/after-home-${width}-${engine}.png`});
    if(width===1440)await page.locator('.dashboard-heading').screenshot({path:`${out}/subtitle-after-fix-${engine}.png`});
  }
  // Each of the six original presets is still a local photograph. Exercise preview, Save,
  // reload and the actual full-screen image, including its heading geometry.
  await page.setViewportSize({width:1440,height:1000});
  for(const wallpaper of ['warm','forest','coast','lavender','mountains','dunes']){
    await section('Настройки');await expect(page.locator('.wallpaper-choices [data-wallpaper]')).toHaveCount(presetIds.length);
    await chooseWallpaper(wallpaper);await expect(page.locator('html')).toHaveAttribute('data-wallpaper',wallpaper);
    await page.getByRole('button',{name:'Сохранить оформление',exact:true}).click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');
    await page.reload();await expect(page.locator('html')).toHaveAttribute('data-wallpaper',wallpaper);await home();await headingFits();
    assert.deepEqual(await page.evaluate(async wallpaper=>{const image=new Image();image.src='/assets/wallpapers/'+wallpaper+'.webp';await image.decode();return [image.naturalWidth>0,image.naturalHeight>0];},wallpaper),[true,true]);
    await page.locator('#toast').evaluate(el=>el.classList.remove('show'));await page.screenshot({path:`${out}/preset-${wallpaper}-${engine}.png`});
  }
  await api('/appearance','PUT',{wallpaper:'warm'});await page.reload();await home();
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:width===1440?1000:844});
    for(const [name,selector] of sections){await section(name);await page.locator(selector).first().waitFor({state:'attached'});await within();same(classicSections[name+width],await bounds(selector),name+' '+width);await page.screenshot({path:`${out}/after-section-${sections.findIndex(x=>x[0]===name)}-${width}-${engine}.png`});}
  }
  // Custom photos stay local until Save; cancellation revokes the preview.
  await page.setViewportSize({width:390,height:844});await section('Настройки');await within();
  const mediaBefore=await api('/media');
  await page.getByLabel('Изображение для обоев').setInputFiles('tests/fixtures/blue.png');await expect(page.locator('[data-wallpaper-status]')).toContainText('Изображение готово');
  assert.equal((await api('/media')).length,mediaBefore.length);assert.match(await page.locator('html').evaluate(el=>el.style.getPropertyValue('--app-wallpaper')),/blob:/);
  await page.getByRole('button',{name:'Отменить изменения',exact:true}).click();await expect(page.locator('[data-custom-wallpaper]')).toBeHidden();assert.doesNotMatch(await page.locator('html').evaluate(el=>el.style.getPropertyValue('--app-wallpaper')),/blob:/);
  // A rejected settings write cleans its staged upload, keeps the draft and
  // can be retried without losing the file or leaving the save button stuck.
  await page.getByLabel('Изображение для обоев').setInputFiles('tests/fixtures/blue.png');await expect(page.locator('[data-wallpaper-status]')).toContainText('Изображение готово');
  const fail=route=>route.request().method()==='PUT'?route.fulfill({status:503,json:{detail:'Проверка ошибки сохранения'}}):route.continue();
  await page.route('**/api/appearance',fail);await page.getByRole('button',{name:'Сохранить оформление',exact:true}).click();await expect(page.locator('#toast')).toContainText('Проверка ошибки сохранения');assert.equal((await api('/media')).length,mediaBefore.length);await page.unroute('**/api/appearance',fail);
  await page.getByRole('button',{name:'Сохранить оформление',exact:true}).click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');const custom=(await api('/appearance')).preferences.wallpaper;assert.match(custom,/^[a-f0-9]{32}$/);
  await page.reload();await expect(page.locator('html')).toHaveAttribute('data-wallpaper',custom);const privateImage=await page.request.get('/api/media/'+custom);assert(privateImage.ok());
  // All six palettes and both schemes keep their controls within a phone.
  await section('Настройки');
  for(const theme of ['Океан','Лес','Песок','Лаванда','Роза','Графит'])for(const mode of ['light','dark']){
    await page.getByRole('radio',{name:theme,exact:true}).check();await page.getByLabel('Режим оформления').selectOption(mode);await within();
  }
  await page.getByRole('button',{name:'Отменить изменения',exact:true}).click();await chooseWallpaper('warm');await page.getByRole('radio',{name:'Песок',exact:true}).check();await page.getByLabel('Режим оформления').selectOption('light');await page.getByRole('button',{name:'Сохранить оформление',exact:true}).click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');
  assert.deepEqual(errors,[],'Main workspace runtime errors');
  // This page's UI scenario is complete. Observe each following context only
  // while it is being exercised, before WebKit tears down its network realm.
  page.off('pageerror',onPageError);
  const uid=(await api('/me')).id;
  const installedContext=await page.context().browser().newContext({baseURL:origin,storageState:await page.context().storageState(),hasTouch:true,viewport:{width:390,height:844},colorScheme:'light'});
  try{
    await installedContext.addInitScript(uid=>{Object.defineProperty(navigator,'standalone',{value:true});sessionStorage.setItem('drg-mobile-view:'+uid,'home');},uid);
    await installedContext.route('**/api/widgets/*/weather',r=>r.fulfill({json:{configured:true,city:'Москва',provider:'met-no',current:{temperature_2m:12,weather_code:0},daily:{temperature_2m_min:[8],temperature_2m_max:[19]},hourly:[]}}));
    const installedBefore={};
    for(const style of ['classic','glass']){
      await api('/appearance','PUT',{style});
      for(const [width,height,top,bottom,side] of [[1440,1000,0,0,0],[390,844,59,34,0],[320,740,0,0,0],[740,390,0,21,44]]){
        const installed=await installedContext.newPage(),installedErrors=[];
        const onError=error=>captureError(error,installedErrors);installed.on('pageerror',onError);
        await installed.setViewportSize({width,height});await installed.goto('/');await expect(installed.locator('[data-widget=tasks] .task-widget-heading')).toBeVisible();
        await installed.evaluate(({top,bottom,side})=>{for(const [key,value] of Object.entries({top,bottom,left:side,right:side}))document.documentElement.style.setProperty('--app-safe-'+key,value+'px');},{top,bottom,side});
        await installed.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
        const nav=installed.locator('.workspace>.mobile-app-tabs');
        if(width<=760){const rect=await nav.boundingBox();assert(rect);assert.equal(Math.round(rect.x),side+12);assert.equal(Math.round(rect.y+rect.height),height-Math.max(12,bottom-10));}else await expect(nav).toBeHidden();
        const geometry=await installed.locator('[data-widget]').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return{id:el.dataset.widgetId,x:r.x,y:r.y,width:r.width,height:r.height};}));
        if(style==='classic')installedBefore[width]=geometry;else same(installedBefore[width],geometry,'Installed '+width);
        assert(await installed.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        if(style==='glass')assert.deepEqual(await installed.locator('.workspace,.workspace>.content').evaluateAll(els=>els.map(el=>getComputedStyle(el).backgroundColor)),['rgba(0, 0, 0, 0)','rgba(0, 0, 0, 0)'],'Installed canvas must reveal the wallpaper');
        // A visible workspace can still be covered by the entry View
        // Transition snapshot. Wait for actual hit testing, not an arbitrary
        // number of frames, before checking every navigation button.
        if(width<=760)for(const button of await nav.locator('button').all())await expect.poll(()=>button.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),{message:'Bottom navigation hit target',timeout:5000}).toBe(true);
        await installed.screenshot({path:`${out}/${style==='classic'?'before':'after'}-installed-${width}-${engine}.png`});
        if(width===390){await installed.locator('[data-mobile-view=settings]').click();await expect(installed.locator('[data-preference-key=appearance]')).toBeAttached();}
        assert.deepEqual(installedErrors,[],'Installed runtime errors');installed.off('pageerror',onError);await installed.close();
      }
    }
  }finally{await installedContext.close();}
  const anonymous=await page.context().browser().newContext({baseURL:origin,viewport:{width:390,height:844}});
  try{
    await anonymous.addInitScript(custom=>localStorage.setItem('drg-appearance',JSON.stringify({style:'glass',wallpaper:custom,theme:'amber',mode:'light'})),custom);
    const login=await anonymous.newPage(),loginErrors=[];let privateRequests=0;login.on('pageerror',error=>loginErrors.push(error.message));login.on('request',r=>{if(r.url().includes('/api/media/'))privateRequests++;});
    await login.goto('/');await expect(login.locator('.auth-layout')).toBeVisible();await expect(login.locator('#app-wallpaper')).toBeHidden();assert.equal(privateRequests,0,'Login must not fetch the previous account wallpaper');
    assert.deepEqual(loginErrors,[],'Login runtime errors');
  }finally{await anonymous.close();}
  if(engine==='webkit')assert.deepEqual(await page.evaluate(async()=>Promise.all(['/api/presence','/api/chat/rooms'].map(async url=>(await fetch(url,{credentials:'same-origin'})).status))),[200,200],'WebKit must still read both background endpoints in the browser');
  assert.deepEqual(await api('/dashboard/layout'),layout,'Appearance must not rewrite widget configuration');assert.deepEqual(await api('/notes'),notes,'Appearance must preserve notes');assert.deepEqual(await api('/tasks'),tasks,'Appearance must preserve reminders');assert.deepEqual(errors,[]);
  await writeFile(`${out}/geometry-${engine}.json`,JSON.stringify({classic,glass,sections:classicSections,errors,webkitResourceErrors},null,2));
  console.log(engine+': unchanged geometry at five sizes, six workspaces, all palettes/schemes, preview/cancel/save/reload, private custom upload, failed-save cleanup and preserved data passed.');
}
