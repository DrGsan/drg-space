import {appSection} from './notes-ui.js';
import { expect } from '@playwright/test';
import {unlockDashboard} from './widget-edit-flow.js';

export async function widgetsFlow(page){
  const dialog=()=>page.getByRole('dialog').last();
  const settings=async()=>appSection(page, 'Настройки');
  const overview=async()=>appSection(page, 'Главная');
  const saveWidget=async()=>{await dialog().getByRole('button',{name:'Сохранить виджет',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(1);};
  const add=async(type)=>{
    await expect(dialog().getByLabel('Новый виджет').locator(`option[value="${type}"]`)).toBeEnabled();
    await dialog().getByLabel('Новый виджет').selectOption(type);
    await dialog().getByRole('button',{name:'Добавить виджет',exact:true}).click();
    await dialog().locator('[data-configure]').last().click();
  };
  await settings();
  for(const title of ['Погода','Новости RSS','Ярлыки сайтов','Галерея и GIF']){
    await expect(page.getByRole('checkbox',{name:'Модуль: '+title,exact:true})).toHaveCount(0);
  }
  // External service fixtures exercise configuration/display; server parsing and
  // destination checks are separately covered by tests/test_rss_fetch.py.
  const searches=[],releaseSearches=new Map();let activeSearches=0,maxActiveSearches=0;
  await page.route('**/api/weather/search?*',async(route)=>{
    const query=new URL(route.request().url()).searchParams.get('q');searches.push(query);
    maxActiveSearches=Math.max(maxActiveSearches,++activeSearches);
    try{
      if(['Старый город','Закрытый город'].includes(query))await new Promise(resolve=>releaseSearches.set(query,resolve));
      if(query==='Несуществующий')return await route.fulfill({json:[]});
      if(query==='Ошибка')return await route.fulfill({status:503,json:{detail:'Поиск временно недоступен'}});
      const moscow=query==='Москва',name=query==='Старый город'?'Устаревший город':query==='Закрытый город'?'Закрытый город':moscow?'Москва':'Владивосток';
      await route.fulfill({json:[{name,country:'Россия',latitude:moscow?55.75:43.11,longitude:moscow?37.6:131.88}]});
    }finally{activeSearches--;}
  });
  let weatherFailure=false;
  await page.route('**/api/widgets/*/weather',async(route)=>{
    const failed=weatherFailure;
    const id=new URL(route.request().url()).pathname.split('/')[3];
    const response=await page.request.get('/api/dashboard/layout',{maxRetries:2});
    const item=(await response.json()).instances.find(w=>w.id===id);
    if(failed)return route.fulfill({status:503,json:{detail:'Сервис погоды временно недоступен'}});
    if(!item.config.city)return route.fulfill({json:{configured:false}});
    const time=1791878400,sizing=id.startsWith('sizing_weather_');
    await route.fulfill({json:{configured:true,city:item.config.city,provider:'met-no',forecast_range:'next_24h',forecast_time:time,checked_at:time,current:{temperature_2m:sizing?-35:item.config.city==='Москва'?12:18,weather_code:sizing?69:0,apparent_temperature:null,wind_speed_10m:sizing?110:5,relative_humidity:72,pressure_hpa:1017,wind_direction:315,precipitation_mm:0.3,precipitation_hours:6},daily:{temperature_2m_min:[8],temperature_2m_max:[19]},hourly:Array.from({length:6},(_,i)=>({time:time+i*10800,temperature_2m:12+i,weather_code:i%2?63:0,wind_speed_10m:8,precipitation_mm:0.3,precipitation_hours:1})),forecast_periods:Array.from({length:5},(_,i)=>({start:time+i*86400,end:time+(i+1)*86400,temperature_2m_min:8+i,temperature_2m_max:19+i,weather_code:63,wind_speed_10m_max:10+i}))}});
  });
  await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[{title:'Новые идеи для личного пространства',url:'https://example.com/news',source:'Мои новости',published:1791712800}],errors:[],updated:1791712800}}));
  await overview();await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  for(const [city,title] of [['Москва','Погода · Москва'],['Владивосток','Погода · Владивосток']]){
    await add('weather');await dialog().getByLabel('Название виджета').fill(title);
    const query=dialog().getByLabel('Поиск города');
    await expect(query).toHaveAttribute('placeholder','Введите город');
    await expect(dialog().getByRole('button',{name:'Найти город',exact:true})).toHaveCount(0);
    if(city==='Москва'){
      await query.fill('М');await page.waitForTimeout(450);expect(searches).toEqual([]);
      await query.fill('Старый город');await expect.poll(()=>searches).toEqual(['Старый город']);
      await expect(dialog().getByRole('status')).toHaveText('Ищем города…');
      await query.fill('Москва');await query.press('Enter');
      await expect(page.getByRole('dialog')).toHaveCount(2);
      await page.waitForTimeout(450);expect(searches).toEqual(['Старый город']);
      releaseSearches.get('Старый город')();
    }else await query.fill(city);
    await dialog().getByRole('button',{name:new RegExp(city+' Россия')}).click();await saveWidget();
  }
  // Typing never changes an already selected city; results and pending work
  // belong only to their current dialog/query, including IME composition.
  const configureMoscow=()=>dialog().getByRole('button',{name:'Настройки: Погода · Москва',exact:true}).click();
  await configureMoscow();
  let query=dialog().getByLabel('Поиск города');
  await query.fill('Несуществующий');await expect(dialog().getByRole('status')).toContainText('Ничего не найдено');
  await query.fill('Ошибка');await expect(dialog().getByRole('status')).toHaveText('Поиск временно недоступен');
  await expect(dialog().locator('[data-selected-city]')).toHaveText('Москва');
  await saveWidget();
  await expect(dialog().locator('.instance-row').filter({hasText:'Погода · Москва'})).toContainText('Москва');
  await configureMoscow();query=dialog().getByLabel('Поиск города');
  // Wait for the entrance animation before timing an immediate close.
  await query.click();
  const beforeClosed=searches.length;
  await query.fill('Отменённый город');await dialog().getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.waitForTimeout(450);expect(searches).toHaveLength(beforeClosed);
  await configureMoscow();query=dialog().getByLabel('Поиск города');
  await query.fill('Закрытый город');await expect.poll(()=>searches.at(-1)).toBe('Закрытый город');
  await dialog().getByRole('button',{name:'Закрыть',exact:true}).click();await configureMoscow();
  query=dialog().getByLabel('Поиск города');
  await query.evaluate(input=>{
    input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
    input.value='Москва';input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
  });
  expect(await query.evaluate(input=>input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true})))).toBe(true);
  await page.waitForTimeout(450);expect(searches.at(-1)).toBe('Закрытый город');
  await query.evaluate(input=>input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));
  await page.waitForTimeout(450);expect(searches.at(-1)).toBe('Закрытый город');
  releaseSearches.get('Закрытый город')();
  await expect(dialog().getByRole('button',{name:'Москва Россия',exact:true})).toBeVisible();
  await expect(dialog().getByRole('button',{name:'Закрытый город Россия',exact:true})).toHaveCount(0);
  expect(maxActiveSearches).toBe(1);
  await dialog().getByRole('button',{name:'Закрыть',exact:true}).click();
  await add('rss');await dialog().getByLabel('Название виджета').fill('Мои новости');
  await dialog().getByRole('button',{name:'Добавить источник'}).click();
  await dialog().getByLabel('Название источника').fill('Мои новости');await dialog().getByLabel('Адрес источника').fill('https://example.com/feed.xml');await saveWidget();
  await add('bookmarks');await dialog().getByRole('button',{name:'Добавить ярлык'}).click();
  await dialog().getByLabel('Название сайта').fill('GitHub');await dialog().getByLabel('Адрес сайта').fill('https://github.com/DrGsan');await dialog().getByLabel('Значок').fill('💻');await dialog().getByLabel('Цвет').selectOption('lilac');await saveWidget();
  await add('media');await dialog().getByLabel('Название виджета').fill('Настроение');
  await dialog().getByLabel('Загрузить изображения или GIF').setInputFiles(['tests/fixtures/blue.png','tests/fixtures/sand.png','tests/fixtures/animated.gif']);
  await expect(dialog().locator('.media-config-row')).toHaveCount(3);
  await expect(dialog().getByLabel('Загрузить изображения или GIF')).toBeEnabled();
  await dialog().getByLabel('Показывать изображение 3').uncheck();
  await dialog().getByLabel('Режим показа').selectOption('slideshow');await dialog().getByLabel('Смена каждые, сек.').fill('2');await saveWidget();
  await dialog().getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('[data-widget=weather]')).toHaveCount(2);
  await expect(page.getByRole('region',{name:'Погода · Москва',exact:true})).toContainText('12');
  await expect(page.getByRole('region',{name:'Погода · Москва',exact:true})).toContainText('Ближайшие 24 часа');
  await expect(page.getByRole('region',{name:'Погода · Москва',exact:true})).not.toContainText('ощущается');
  await expect(page.getByRole('region',{name:'Погода · Москва',exact:true}).getByRole('link',{name:'MET Norway'})).toHaveAttribute('href','https://api.met.no/weatherapi/locationforecast/2.0/documentation');
  await expect(page.getByRole('region',{name:'Погода · Владивосток',exact:true})).toContainText('18');
  await expect(page.getByRole('link',{name:/GitHub.*github.com/})).toHaveAttribute('href','https://github.com/DrGsan');
  await expect(page.locator('[data-widget=rss]')).toContainText('Новые идеи');
  weatherFailure=true;await page.reload();
  const weather=page.getByRole('region',{name:'Погода · Москва',exact:true});
  const vladivostok=page.getByRole('region',{name:'Погода · Владивосток',exact:true});
  await expect(weather).toContainText('Сервис погоды временно недоступен');
  await expect(vladivostok).toContainText('Сервис погоды временно недоступен');
  await expect(weather.getByRole('button',{name:'Повторить загрузку',exact:true})).toBeVisible();
  await expect(vladivostok.getByRole('button',{name:'Повторить загрузку',exact:true})).toBeVisible();
  weatherFailure=false;await weather.getByRole('button',{name:'Повторить загрузку',exact:true}).click();
  await expect(weather).toContainText('12');
  await vladivostok.getByRole('button',{name:'Повторить загрузку',exact:true}).click();
  await expect(vladivostok).toContainText('18');
  const gallery=page.locator('[data-widget=media]');
  await expect.poll(()=>gallery.locator('img').evaluate(img=>img.complete && img.naturalWidth)).toBe(640);
  const firstSrc=await gallery.locator('img').getAttribute('src');
  await expect.poll(()=>gallery.locator('img').getAttribute('src')).not.toBe(firstSrc);
  // A static selection of the third item keeps the original animated GIF.
  await unlockDashboard(page);
  await gallery.getByRole('button',{name:'Настроить виджет: Настроение'}).click();
  await dialog().getByLabel('Режим показа').selectOption('static');
  await dialog().getByLabel('Показывать изображение 1').uncheck();await dialog().getByLabel('Показывать изображение 2').uncheck();await dialog().getByLabel('Показывать изображение 3').check();
  await dialog().getByRole('button',{name:'Сохранить виджет'}).click();
  await expect(gallery.locator('figcaption')).toContainText('animated.gif');
  await expect.poll(()=>gallery.locator('img').evaluate(img=>img.naturalWidth)).toBe(80);
  const gifSrc=await gallery.locator('img').getAttribute('src');
  expect((await page.request.get(gifSrc)).headers()['content-type']).toBe('image/gif');
  await page.reload();await expect(gallery.locator('img')).toHaveAttribute('src',gifSrc);
  // Each day/night palette persists, and scheduling uses the browser clock.
  await settings();
  for(const [theme,title] of [['ocean','Океан'],['sage','Лес'],['amber','Песок'],['lilac','Лаванда'],['rose','Роза'],['slate','Графит']]){
    await page.getByRole('radio',{name:title,exact:true}).check();
    for(const mode of ['dark','light']){
      await page.getByLabel('Режим оформления').selectOption(mode);
      await expect(page.locator('html')).toHaveAttribute('data-theme',theme);await expect(page.locator('html')).toHaveAttribute('data-scheme',mode);
      await page.getByRole('button',{name:'Сохранить оформление'}).click();
      await expect(page.locator('html')).toHaveAttribute('data-theme',theme);await expect(page.locator('html')).toHaveAttribute('data-scheme',mode);
    }
  }
  await page.getByRole('radio',{name:'Лаванда',exact:true}).check();
  await page.getByLabel('Режим оформления').selectOption('schedule');await page.getByLabel('День начинается').fill('07:00');await page.getByLabel('Ночь начинается').fill('22:00');
  await page.clock.install({time:new Date('2026-10-11T12:00:00Z')});
  await page.getByRole('button',{name:'Сохранить оформление'}).click();await expect(page.locator('html')).toHaveAttribute('data-scheme','light');
  await page.clock.setSystemTime(new Date('2026-10-11T22:00:00Z'));await page.clock.runFor(61000);
  await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');
  await page.bringToFront();await overview();await page.clock.runFor(5000);
  await expect(page.getByRole('region',{name:'Погода · Москва',exact:true})).toContainText('Ближайшие 24 часа');
  await page.screenshot({animations:'disabled',path:'artifacts/widgets-night-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({animations:'disabled',path:'artifacts/widgets-night-mobile.png',fullPage:true});
  await page.setViewportSize({width:1280,height:900});
  await page.clock.setSystemTime(new Date('2026-10-12T08:00:00Z'));await page.clock.runFor(61000);
  await expect(page.locator('html')).toHaveAttribute('data-scheme','light');
  await page.clock.runFor(5000);await page.screenshot({animations:'disabled',path:'artifacts/widgets-day-desktop.png',fullPage:true});
  // Hide just Moscow in the manager. Both saved city configurations and the
  // other weather instance survive hiding, restoring, and page reloads.
  const beforeVisibility=(await (await page.request.get('/api/dashboard/layout')).json()).instances;
  const moscowId=beforeVisibility.find(item=>item.title==='Погода · Москва').id;
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await dialog().getByRole('checkbox',{name:'Погода · Москва',exact:true}).uncheck();
  await expect(dialog().getByRole('checkbox',{name:'Погода · Владивосток',exact:true})).toBeChecked();
  await dialog().getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();await expect(page.getByRole('region',{name:'Погода · Москва',exact:true})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Погода · Владивосток',exact:true})).toContainText('18');
  const hidden=(await (await page.request.get('/api/dashboard/layout')).json()).instances;
  expect(hidden).toEqual(beforeVisibility.map(item=>item.id===moscowId?{...item,hidden:true}:item));
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await dialog().getByRole('checkbox',{name:'Погода · Москва',exact:true}).check();
  await dialog().getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();await expect(page.locator('[data-widget=weather]')).toHaveCount(2);
  await expect(page.getByRole('region',{name:'Погода · Москва',exact:true})).toContainText('12');
  await expect(page.getByRole('region',{name:'Погода · Владивосток',exact:true})).toContainText('18');
  expect((await (await page.request.get('/api/dashboard/layout')).json()).instances).toEqual(beforeVisibility);
  // Return to real time before later auth expiry tests.
  await page.clock.setSystemTime(new Date());
}

export async function weatherSizingFlow(page){
  const original=await (await page.request.get('/api/dashboard/layout')).json();
  const configured=original.instances.find(item=>item.module_id==='weather'&&item.config.city);
  const sizes=['1x1','2x1','1x2','2x2'];
  const save=async instances=>{
    const ok=await page.evaluate(async instances=>{
      const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');
      const current=await (await fetch('/api/dashboard/layout')).json();
      return (await fetch('/api/dashboard/layout',{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({...current,instances})})).ok;
    },instances);expect(ok).toBe(true);
  };
  await save(sizes.map((size,i)=>({...configured,id:'sizing_weather_'+i,size,config:{...configured.config,city:'Петропавловск-Камчатский'}})));
  await page.reload();
  for(const width of [1280,1100,390,320]){
    await page.setViewportSize({width,height:900});
    // The preceding theme flow installs a controlled animation clock. Let the
    // grid ResizeObserver settle before measuring the new viewport.
    await page.clock.runFor(100);
    await expect.poll(()=>page.locator('#widget-grid').evaluate(el=>{
      const width=el.getBoundingClientRect().width,cols=width>=840?4:width>=440?2:1;
      return Math.abs(Number.parseFloat(el.style.getPropertyValue('--grid-cell'))-(width-(cols-1)*16)/cols)<1;
    })).toBe(true);
    if(width===390||width===320)await page.screenshot({path:'artifacts/weather-sizes-'+width+'.png',fullPage:true});
    for(const [i,size] of sizes.entries()){
      const widget=page.locator('[data-widget-id="sizing_weather_'+i+'"]');
      await expect(widget.locator('.weather-view')).toBeVisible();
      await expect(widget.locator('.weather-hour')).toHaveCount(size.startsWith('2x')?6:0);
      await expect(widget.locator('.weather-period')).toHaveCount(size==='2x2'?5:size==='1x2'?3:0);
      const bounds=await widget.locator('[data-widget-body]').evaluate(el=>{
        const body=el.getBoundingClientRect(),view=el.firstElementChild.getBoundingClientRect();
        const groups=[...el.querySelectorAll('.weather-now,.weather-hourly,.weather-periods,.weather-source')];
        const parts=groups.map(p=>p.getBoundingClientRect());
        const inside=(r,p)=>r.top>=p.top-1&&r.bottom<=p.bottom+1&&r.left>=p.left-1&&r.right<=p.right+1;
        const badLeaves=[];
        for(const group of groups)for(const leaf of group.querySelectorAll('.weather-city,.weather-current,.weather-range,.weather-metrics,.weather-trend,.weather-trend svg,.weather-period,.weather-hour')){
          const l=leaf.getBoundingClientRect(),g=group.getBoundingClientRect();
          if(!inside(l,g))badLeaves.push({class:leaf.className.baseVal??leaf.className,top:l.top-g.top,bottom:l.bottom-g.bottom,left:l.left-g.left,right:l.right-g.right});
        }
        for(const row of el.querySelectorAll('.weather-period,.weather-hour'))for(const child of row.children){
          if(!inside(child.getBoundingClientRect(),row.getBoundingClientRect()))badLeaves.push({class:child.className,parent:row.className});
        }
        const periods=el.querySelector('.weather-periods'),list=el.querySelector('.weather-period-list'),trend=el.querySelector('.weather-trend');
        const listBottomGap=list?periods.getBoundingClientRect().bottom-list.getBoundingClientRect().bottom:0;
        const graphBottomGap=trend?el.querySelector('.weather-now').getBoundingClientRect().bottom-trend.getBoundingClientRect().bottom:0;
        const current=el.querySelector('.weather-current');
        const currentWidthRatio=current?current.getBoundingClientRect().width/el.querySelector('.weather-now').getBoundingClientRect().width:0;
        const overlaps=parts.some((a,i)=>parts.slice(i+1).some(b=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1));
        return {height:el.clientHeight,scroll:el.scrollHeight,overflow:getComputedStyle(el).overflowY,view:Math.ceil(view.height),badLeaves,overlaps,listBottomGap,graphBottomGap,currentWidthRatio,fits:parts.every(p=>inside(p,body))};
      });
      expect(bounds,`${size} at ${width}px: ${JSON.stringify(bounds)}`).toMatchObject({overflow:'clip',fits:true,badLeaves:[],overlaps:false});
      expect(bounds.scroll,`${size} at ${width}px`).toBeLessThanOrEqual(bounds.height+1);
      expect(bounds.listBottomGap,`${size} forecast fills its area at ${width}px`).toBeLessThanOrEqual(1);
      expect(bounds.graphBottomGap,`${size} graph fills its area at ${width}px`).toBeLessThanOrEqual(1);
      if(size==='1x1')expect(bounds.currentWidthRatio).toBeGreaterThan(0.98);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(width===1280||width===390)await page.screenshot({path:'artifacts/weather-sizes-'+width+'.png',fullPage:true});
  }
  await save(original.instances);await page.setViewportSize({width:1280,height:900});await page.reload();
}
