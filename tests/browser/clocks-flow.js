import {appSection} from './notes-ui.js';
import {expect} from '@playwright/test';
import {unlockDashboard} from './widget-edit-flow.js';
export async function clocksFlow(page){
  const dialog=()=>page.getByRole('dialog').last();
  const overview=async()=>appSection(page, 'Главная');
  const settings=async()=>appSection(page, 'Настройки');
  const moscow=()=>page.getByRole('region',{name:'Часы · Москва',exact:true});
  const vlad=()=>page.getByRole('region',{name:'Часы · Владивосток',exact:true});
  async function configure(region){await unlockDashboard(page);await region().getByRole('button',{name:/Настроить виджет/}).click();await dialog().locator('.clock-advanced summary').click();}
  async function save(){await dialog().getByRole('button',{name:'Сохранить виджет',exact:true}).click();}
  async function geometry(region){
    const bounds=await region().evaluate(el=>{
      const card=el.getBoundingClientRect(),title=el.querySelector('.widget-toolbar h2').getBoundingClientRect();
      const body=el.querySelector('[data-widget-body]'),area=el.querySelector('.clock-face-area').getBoundingClientRect(),dial=el.querySelector('.clock-dial').getBoundingClientRect();
      const details=el.querySelector('.clock-details').getBoundingClientRect(),content=body.getBoundingClientRect();
      return {diameter:dial.width,expected:.95*Math.min(area.width,area.height),titleTop:title.top-card.top,scroll:body.scrollHeight,height:body.clientHeight,fits:dial.top>=content.top-1&&dial.bottom<=content.bottom+1&&details.bottom<=content.bottom+1&&dial.bottom<=details.top+1};
    });
    expect(bounds.fits,JSON.stringify(bounds)).toBe(true);
    expect(bounds.diameter).toBeGreaterThan(100);
    expect(Math.abs(bounds.diameter-bounds.expected),JSON.stringify(bounds)).toBeLessThanOrEqual(2);
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.height+1);
    expect(bounds.titleTop).toBeGreaterThanOrEqual(0);expect(bounds.titleTop).toBeLessThanOrEqual(20);
    return bounds.diameter;
  }
  async function settleGrid(){
    await expect.poll(async()=>{
      // ResizeObserver delivery can follow the first advance; keep the paused
      // test clock moving until its queued animation frame applies geometry.
      await page.clock.runFor(100);
      return page.locator('#widget-grid').evaluate(el=>{
      const width=el.getBoundingClientRect().width,cols=width>=840?4:width>=440?2:1;
      return Math.abs(Number.parseFloat(el.style.getPropertyValue('--grid-cell'))-(width-(cols-1)*16)/cols)<1;
      });
    }).toBe(true);
  }
  async function capturePair(path){
    await page.evaluate(()=>scrollTo(0,0));
    const [a,b]=await Promise.all([moscow().boundingBox(),vlad().boundingBox()]);
    await page.screenshot({path,fullPage:true,animations:'disabled',clip:{x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),width:Math.max(a.x+a.width,b.x+b.width)-Math.min(a.x,b.x),height:Math.max(a.y+a.height,b.y+b.height)-Math.min(a.y,b.y)}});
  }
  await settings();await expect(page.getByRole('checkbox',{name:'Модуль: Часы',exact:true})).toHaveCount(0);
  await overview();await expect(page.locator('[data-widget=clock]')).toHaveCount(0);
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await expect(dialog().getByLabel('Новый виджет').locator('option[value="clock"]')).toBeEnabled();
  for(const [title,zone,label,mode,seconds] of [['Часы · Москва','Europe/Moscow','MSK','analog',false],['Часы · Владивосток','Asia/Vladivostok','VL','digital',true]]){
    await dialog().getByLabel('Новый виджет').selectOption('clock');await dialog().getByRole('button',{name:'Добавить виджет',exact:true}).click();await dialog().locator('[data-configure]').last().click();
    await dialog().getByLabel('Название виджета').fill(title);await dialog().locator(`[data-clock-mode=${mode}]`).click();await dialog().locator('.clock-advanced summary').click();await dialog().getByLabel('Регион часов').selectOption(zone);await dialog().getByLabel('Подпись часов').fill(label);await dialog().getByLabel('Показывать секунды', {exact:false}).setChecked(seconds);
    if(mode==='digital')await dialog().getByRole('combobox',{name:'Формат даты',exact:true}).selectOption('yyyy-mm-dd');await save();
  }
  await dialog().getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await unlockDashboard(page);
  const upMoscow=moscow().locator('[data-move-widget]'),upVlad=vlad().locator('[data-move-widget]');
  while(await page.locator('[data-widget]').first().getAttribute('aria-label')!=='Часы · Москва')await upMoscow.press('ArrowUp');
  while(await page.locator('[data-widget]').nth(1).getAttribute('aria-label')!=='Часы · Владивосток')await upVlad.press('ArrowUp');
  await expect(page.locator('[data-layout-status]')).toHaveText('Порядок сохранён');
  await expect(page.locator('[data-widget=clock]')).toHaveCount(2);await expect(moscow().locator('.clock-label')).toHaveText('MSK');await expect(vlad().locator('.clock-label')).toHaveText('VL');
  await expect(moscow().locator('[data-hand=second]')).toHaveCount(0);
  // Date and hand positions come from the selected region, even across midnight.
  await page.clock.setSystemTime(new Date('2026-10-11T20:59:57Z'));await page.clock.pauseAt(new Date('2026-10-11T20:59:58Z'));
  await overview();await expect(moscow().locator('.clock-face')).toHaveAttribute('aria-label','MSK · 23:59');await expect(vlad().locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 06:59:58$'));await expect(vlad().locator('.clock-date')).toHaveText('2026-10-12');
  await page.clock.runFor(3100);await expect(moscow().locator('.clock-face')).toHaveAttribute('aria-label','MSK · 00:00');await expect(vlad().locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 07:00:01$'));
  await expect(moscow().locator('.clock-zone-offset')).toHaveText('UTC+03:00');await expect(vlad().locator('.clock-zone-offset')).toHaveText('UTC+10:00');
  await expect(moscow().locator('.clock-date')).toHaveText('12.10.2026');
  const fullDial=await geometry(moscow);
  // Both clock modes can hide the automatic city/UTC line without hiding their
  // personal label or date; their configured region still controls local time.
  for(const region of [moscow,vlad]){
    await configure(region);await dialog().getByLabel('Показывать часовой пояс',{exact:true}).uncheck();
    await expect(dialog().locator('[data-clock-preview] .clock-region')).toHaveCount(0);
    await expect(dialog().locator('[data-clock-preview] .clock-date')).toHaveCount(1);await save();
    await expect(region().locator('.clock-region')).toHaveCount(0);await expect(region().locator('.clock-label')).toHaveCount(1);await expect(region().locator('.clock-date')).toHaveCount(1);
  }
  await page.reload();await expect(moscow().locator('.clock-region')).toHaveCount(0);await expect(vlad().locator('.clock-region')).toHaveCount(0);
  await configure(moscow);await dialog().getByLabel('Показывать текущую дату',{exact:true}).uncheck();await save();
  await expect(moscow().locator('.clock-date')).toHaveCount(0);await expect(moscow().locator('.clock-region')).toHaveCount(0);
  expect(await geometry(moscow)).toBeGreaterThan(fullDial+5);
  await configure(moscow);await dialog().getByLabel('Показывать текущую дату',{exact:true}).check();await dialog().getByLabel('Показывать часовой пояс',{exact:true}).check();await save();
  await expect(moscow().locator('.clock-date')).toHaveText('12.10.2026');await expect(moscow().locator('.clock-zone-offset')).toHaveText('UTC+03:00');
  for(const theme of ['minimal','station','classic']){
    await configure(moscow);await dialog().locator(`[data-clock-theme=${theme}]`).click();await save();await expect(moscow().locator('.clock-widget')).toHaveClass(new RegExp('clock-theme-'+theme));
  }
  await configure(moscow);await dialog().getByLabel('Показывать секунды',{exact:false}).check();await save();await expect(moscow().locator('[data-hand=second]')).toHaveCount(1);
  await configure(vlad);await dialog().getByLabel('Формат времени').selectOption('12h');await save();await expect(vlad().locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 07:00:01 AM$'));
  await configure(vlad);await dialog().getByLabel('Формат времени').selectOption('24h');await dialog().getByLabel('Показывать секунды',{exact:false}).uncheck();await dialog().getByLabel('Показывать текущую дату').uncheck();await save();
  await expect(vlad().locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 07:00$'));await expect(vlad().locator('.clock-date')).toHaveCount(0);
  await expect(vlad().locator('.clock-region')).toHaveCount(0);
  await configure(vlad);await dialog().getByLabel('Показывать текущую дату').check();await dialog().getByRole('combobox',{name:'Формат даты',exact:true}).selectOption('custom');await dialog().getByLabel('Свой формат даты',{exact:true}).fill('ddd · DD/MM/YYYY');await save();await expect(vlad().locator('.clock-date')).toHaveText('пн · 12/10/2026');
  await configure(moscow);await dialog().getByLabel('Регион часов').selectOption('custom');await dialog().getByLabel('Другой часовой пояс').fill('Asia/Kolkata');await save();await expect(moscow().locator('.clock-zone-offset')).toHaveText('UTC+05:30');
  await configure(moscow);await dialog().getByLabel('Регион часов').selectOption('Europe/Moscow');await dialog().getByLabel('Показывать секунды',{exact:false}).uncheck();await dialog().locator('[data-clock-theme=minimal]').click();await save();
  await page.reload();await expect(moscow().locator('.clock-label')).toHaveText('MSK');await expect(vlad().locator('.clock-date')).toHaveText('пн · 12/10/2026');await expect(vlad().locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 07:00$'));await expect(vlad().locator('.clock-region')).toHaveCount(0);
  await capturePair('artifacts/clocks-desktop.png');
  await configure(vlad);await dialog().evaluate(el=>el.scrollTop=0);await dialog().screenshot({path:'artifacts/clock-settings.png',animations:'disabled'});await dialog().getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.setViewportSize({width:390,height:844});await settleGrid();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const mobileFullDial=await geometry(moscow);
  const mobileArea=await moscow().locator('.clock-face-area').evaluate(el=>{const {width,height}=el.getBoundingClientRect();return {width,height};});
  await configure(moscow);await dialog().getByLabel('Показывать текущую дату',{exact:true}).uncheck();await dialog().getByLabel('Показывать часовой пояс',{exact:true}).uncheck();await save();
  await expect(moscow().locator('.clock-date')).toHaveCount(0);await expect(moscow().locator('.clock-region')).toHaveCount(0);
  const mobileWithoutDetails=await geometry(moscow);
  // A face already bounded by the card width cannot grow after removing rows.
  if(mobileArea.height<mobileArea.width-5)expect(mobileWithoutDetails).toBeGreaterThan(mobileFullDial+5);
  else expect(mobileWithoutDetails).toBeGreaterThanOrEqual(mobileFullDial-.1);
  await expect(moscow().locator('.clock-label')).toHaveText('MSK');
  await configure(moscow);await dialog().getByLabel('Показывать текущую дату',{exact:true}).check();await dialog().getByLabel('Показывать часовой пояс',{exact:true}).check();await save();
  await expect(moscow().locator('.clock-date')).toHaveCount(1);await expect(moscow().locator('.clock-region')).toHaveCount(1);await geometry(moscow);
  await capturePair('artifacts/clocks-mobile.png');await page.setViewportSize({width:1280,height:900});await settleGrid();
  // Visibility belongs to one saved instance; hiding Moscow cannot stop the
  // Vladivostok clock or change either clock's region, date, mode, or label.
  const beforeVisibility=(await (await page.request.get('/api/dashboard/layout')).json()).instances;
  const moscowId=beforeVisibility.find(item=>item.title==='Часы · Москва').id;
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await dialog().getByRole('checkbox',{name:'Часы · Москва',exact:true}).uncheck();
  await expect(dialog().getByRole('checkbox',{name:'Часы · Владивосток',exact:true})).toBeChecked();
  await dialog().getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();await expect(moscow()).toHaveCount(0);await expect(vlad()).toHaveCount(1);
  await expect(vlad().locator('.clock-label')).toHaveText('VL');await expect(vlad().locator('.clock-date')).toHaveText('пн · 12/10/2026');
  const hidden=(await (await page.request.get('/api/dashboard/layout')).json()).instances;
  expect(hidden).toEqual(beforeVisibility.map(item=>item.id===moscowId?{...item,hidden:true}:item));
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await dialog().getByRole('checkbox',{name:'Часы · Москва',exact:true}).check();
  await dialog().getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();await expect(page.locator('[data-widget=clock]')).toHaveCount(2);
  await expect(moscow().locator('.clock-label')).toHaveText('MSK');await expect(moscow().locator('.clock-zone-offset')).toHaveText('UTC+03:00');
  await expect(vlad().locator('.clock-digits')).toHaveAttribute('aria-label',new RegExp(' · 07:00$'));await expect(vlad().locator('.clock-region')).toHaveCount(0);
  expect((await (await page.request.get('/api/dashboard/layout')).json()).instances).toEqual(beforeVisibility);
  await page.clock.setSystemTime(new Date());await page.clock.resume();
}
