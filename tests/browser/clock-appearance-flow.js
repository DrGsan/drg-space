import {appSection} from './notes-ui.js';
import {expect} from '@playwright/test';
async function api(page,path,method='GET',data){
 const result=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:r.status,data:await r.json()};},{path,method,data});expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}
export async function clockAppearanceFlow(page){
 const layout=await api(page,'/dashboard/layout'),modules=(await api(page,'/modules')).enabled,appearance=(await api(page,'/me')).settings.appearance;
 await api(page,'/modules','PUT',{enabled:{files:true,expenses:true,chat:true}});
 await api(page,'/appearance','PUT',{...appearance,mode:'light'});
 const instances=[
  ['classic','analog','Europe/Moscow'],['minimal','analog','Europe/Moscow'],['station','analog','Europe/Moscow'],
  ['station','analog','Asia/Vladivostok'],['station','digital','Europe/Moscow'],['minimal','digital','Asia/Vladivostok'],
 ].map(([theme,mode,timezone],i)=>({id:'appearance_clock_'+i,module_id:'clock',title:'Проверка часов '+i,size:'1x1',config:{theme,mode,timezone,show_seconds:false}}));
 await api(page,'/dashboard/layout','PUT',{...layout,instances});
 await page.clock.setSystemTime(new Date('2026-10-11T12:00:00Z'));await page.goto('/');await appSection(page,'Главная');
 await expect(page.locator('html')).toHaveAttribute('data-scheme','light');
 await expect(page.locator('.sidebar .nav-btn')).toHaveCount(9);
 const nav=await page.locator('.sidebar .nav-btn').evaluateAll(buttons=>buttons.map(b=>({icon:b.querySelector('.nav-icon').getBoundingClientRect(),text:b.querySelector('.nav-text').getBoundingClientRect()})).map(({icon,text})=>({center:icon.x+icon.width/2,text:text.x,width:icon.width})));
  expect(nav.length).toBe(9);expect(Math.max(...nav.map(x=>x.center))-Math.min(...nav.map(x=>x.center))).toBeLessThan(.1);expect(new Set(nav.map(x=>x.text)).size).toBe(1);expect(nav.every(x=>x.width===20)).toBe(true);
 const clock=i=>page.locator('[data-widget-id="appearance_clock_'+i+'"]');
 // The same instant is daytime in Moscow and nighttime in Vladivostok.
 for(const i of [0,1,2,4])await expect(clock(i).locator('.clock-widget')).toHaveAttribute('data-clock-period','day');
 for(const i of [3,5])await expect(clock(i).locator('.clock-widget')).toHaveAttribute('data-clock-period','night');
 const surface=el=>el.evaluate(e=>getComputedStyle(e).backgroundColor),dial=el=>el.locator('.clock-dial').evaluate(e=>getComputedStyle(e).fill);
 for(const i of [1,2,3,4,5])expect(await surface(clock(i))).toBe(await surface(clock(0)));
 expect(await dial(clock(0))).toBe(await dial(clock(2)));expect(await dial(clock(3))).toBe('rgb(29, 41, 61)');
 await expect(clock(4).locator('.clock-digits')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');await expect(clock(5).locator('.clock-digits')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
 await page.screenshot({path:'artifacts/clock-regional-night.png',fullPage:true});
 await appSection(page, 'Настройки');await page.getByLabel('Режим оформления').selectOption('dark');await page.getByRole('button',{name:'Сохранить оформление'}).click();await appSection(page, 'Главная');
 await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');for(const i of [1,2,3,4,5])expect(await surface(clock(i))).toBe(await surface(clock(0)));
 for(const theme of ['ocean','sage','amber','lilac','rose','slate']){
  await page.locator('html').evaluate((el,theme)=>el.dataset.theme=theme,theme);
  for(const i of [0,1,2])expect(await dial(clock(i))).toBe('rgb(255, 255, 255)');
  expect(await dial(clock(3))).toBe('rgb(29, 41, 61)');
  await expect(clock(0).locator('.clock-hour-hand')).toHaveCSS('stroke','rgb(37, 43, 54)');
  await expect(clock(3).locator('.clock-hour-hand')).toHaveCSS('stroke','rgb(243, 246, 253)');
  expect(await clock(4).locator('.clock-digits').evaluate(el=>getComputedStyle(el).color)).toBe(await clock(4).evaluate(el=>getComputedStyle(el).color));
  await expect(clock(4).locator('.clock-digits')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
  await expect(clock(5).locator('.clock-digits')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
 }
 await page.locator('html').evaluate((el,theme)=>el.dataset.theme=theme,appearance.theme);
 await page.screenshot({path:'artifacts/clock-regional-night-dark.png',fullPage:true});
 // A minute boundary switches the face without a reload, including seconds off.
 await page.clock.setSystemTime(new Date('2026-10-11T04:59:59Z'));await appSection(page, 'Главная');await expect(clock(0).locator('.clock-widget')).toHaveAttribute('data-clock-period','night');await page.clock.runFor(1200);await expect(clock(0).locator('.clock-widget')).toHaveAttribute('data-clock-period','day');
 // Regions switch independently without changing the outer card.
 await page.clock.setSystemTime(new Date('2026-10-11T22:00:00Z'));await appSection(page, 'Главная');await expect(clock(0).locator('.clock-widget')).toHaveAttribute('data-clock-period','night');await expect(clock(3).locator('.clock-widget')).toHaveAttribute('data-clock-period','day');
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.setViewportSize({width:1280,height:900});
 await api(page,'/modules','PUT',{enabled:modules});const current=await api(page,'/dashboard/layout');await api(page,'/dashboard/layout','PUT',{...layout,version:current.version});await api(page,'/appearance','PUT',appearance);await page.clock.setSystemTime(new Date());await page.goto('/');await appSection(page,'Главная');
}
