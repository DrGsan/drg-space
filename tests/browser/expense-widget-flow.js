import {expect} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
import {appSection} from './notes-ui.js';

const money=value=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:2}).format(value/100);
const sizes=['1x1','2x1','1x2','2x2'];
const visibleAccounts=(size,bounds)=>{
  if(size==='1x1'&&bounds.physicalHeight<175||size==='1x2'&&bounds.physicalHeight<380)return 1;
  if(size==='2x2'&&(bounds.width<380||bounds.physicalHeight<380))return 2;
  return size==='2x2'?4:2;
};

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);
  return result.data;
}

function fixtures(){
  const stamp=Math.floor(Date.now()/1000),longTitle='ОченьДлинноеНазваниеСчёта'.repeat(8).slice(0,150),longName='ОченьДлинноеИмяУчастника'.repeat(5).slice(0,80);
  const participants=[{id:'me',name:'Саша'},{id:'unknown',name:'Неизвестный человек'},{id:'friend',name:longName},{id:'guest',name:'Гость'}];
  const zeros=()=>Object.fromEntries(participants.map(person=>[person.id,0]));
  const categories=()=>({food:0,beer:0,whisky:0,tinctures:0,cocktails:0,mixed:0,taxi:0,shop:0});
  const event=(id,title,created,active)=>{
    const ends=active?created+20*3600:stamp-3600;
    return {id,title,created,active,ends,purge_at:ends+30*86400,version:1,retention_days:30,participants,entries:[],paid:zeros(),cost:zeros(),balance:zeros(),debts:[],categories:categories(),currency:'RUB',photos:[]};
  };
  const entry=(id,created,kind,payer,amount,allocations,category='mixed')=>({id,created,kind,payer,amount,allocations,category,note:''});

  // Each of 2,000 actual purchases reaches the API's maximum of 1,000,000 ₽.
  // This exercises a valid 2-billion-ruble report without invented balances.
  const large=event('widget_large_closed',longTitle,stamp-7200,false);
  large.entries=Array.from({length:2000},(_,i)=>entry('large_'+String(i).padStart(4,'0'),large.created+i,'expense','me',100000000,{me:100000000},'food'));
  large.version=2001;large.paid.me=large.cost.me=large.categories.food=200000000000;

  const both=event('widget_two_directions','Два направления расчётов',stamp-10800,true);
  both.entries=[
    entry('both_1',stamp-120,'expense','me',60000,{me:30000,friend:30000},'beer'),
    entry('both_2',stamp-100,'debt','guest',40000,{me:40000}),
    entry('both_3',stamp-80,'expense','friend',15000,{me:5000,friend:10000},'taxi'),
    entry('both_4',stamp-60,'debt','me',10000,{guest:10000}),
  ];
  both.version=5;both.paid.me=60000;both.paid.friend=15000;both.cost.me=35000;both.cost.friend=40000;
  both.balance.me=-5000;both.balance.friend=-25000;both.balance.guest=30000;
  both.debts=[{from:'friend',to:'me',amount:25000},{from:'me',to:'guest',amount:30000}];
  both.categories.beer=30000;both.categories.taxi=5000;

  // Equal debts in different events stay separate, even for the same name/ID.
  const outgoing=event('widget_direct_out','Прямой долг участнику',stamp-14400,false);
  outgoing.entries=[entry('out_1',stamp-7200,'debt','friend',50000,{me:50000})];outgoing.version=2;
  outgoing.balance.me=-50000;outgoing.balance.friend=50000;outgoing.debts=[{from:'me',to:'friend',amount:50000}];
  const incoming=event('widget_direct_in','Прямой долг от участника',stamp-16200,true);
  incoming.entries=[entry('in_1',stamp-30,'debt','me',50000,{friend:50000})];incoming.version=2;
  incoming.balance.me=50000;incoming.balance.friend=-50000;incoming.debts=[{from:'friend',to:'me',amount:50000}];
  const empty=event('widget_no_entries','Встреча без платежей',stamp-18000,true);
  return {items:[large,both,outgoing,incoming,empty],empty,longName,longTitle};
}

async function geometry(widget){
  return widget.locator('[data-widget-body]').evaluate(body=>{
    const root=body.getBoundingClientRect(),view=body.querySelector('.expense-widget').getBoundingClientRect();
    const inside=(inner,outer)=>inner.top>=outer.top-1&&inner.bottom<=outer.bottom+1&&inner.left>=outer.left-1&&inner.right<=outer.right+1;
    const visible=element=>{const rect=element.getBoundingClientRect();return rect.width>0&&rect.height>0&&getComputedStyle(element).visibility!=='hidden';};
    const groups=[...body.querySelector('.expense-widget').children].filter(visible),parts=groups.map(element=>element.getBoundingClientRect());
    const leaves=body.querySelectorAll('.expense-widget-summary>div,.expense-widget-money,.expense-widget-account-title,.expense-widget-account-meta,.expense-widget-status,.expense-widget-account-meta time,.expense-widget-details h3,.expense-widget-entry>span:first-child,.expense-widget-entry small,.expense-widget-category>span:first-child,.expense-widget-bar,.expense-widget-footer button,.expense-widget-empty h3,.expense-widget-empty p,.expense-widget-empty>span,.expense-widget-empty-detail');
    const badLeaves=[...leaves].filter(visible).flatMap(element=>{
      const rect=element.getBoundingClientRect(),parent=element.parentElement.getBoundingClientRect();
      return inside(rect,parent)&&inside(rect,root)?[]:[{class:element.className,text:element.textContent.slice(0,45),height:rect.height,top:rect.top-parent.top,bottom:rect.bottom-parent.bottom,left:rect.left-parent.left,right:rect.right-parent.right}];
    });
    const overlaps=parts.some((left,i)=>parts.slice(i+1).some(right=>Math.min(left.right,right.right)-Math.max(left.left,right.left)>1&&Math.min(left.bottom,right.bottom)-Math.max(left.top,right.top)>1));
    const lineHeight=element=>{const style=getComputedStyle(element);return Number.parseFloat(style.lineHeight)||Number.parseFloat(style.fontSize)*1.2;};
    const compressedRows=[...body.querySelectorAll('.expense-widget-account,.expense-widget-entry,.expense-widget-category')].filter(element=>element.getBoundingClientRect().width>0).flatMap(element=>{
      const style=getComputedStyle(element),rect=element.getBoundingClientRect();
      const first=element.querySelector('.expense-widget-account-title')||element.firstElementChild,amount=element.querySelector('.expense-widget-money');
      const second=element.querySelector('.expense-widget-account-meta,small,.expense-widget-bar');
      const secondHeight=second.classList.contains('expense-widget-bar')?second.getBoundingClientRect().height:lineHeight(second);
      const minimum=Math.max(lineHeight(first),lineHeight(amount))+secondHeight+(Number.parseFloat(style.rowGap)||0)+Number.parseFloat(style.paddingTop)+Number.parseFloat(style.paddingBottom)+Number.parseFloat(style.borderTopWidth)+Number.parseFloat(style.borderBottomWidth);
      return rect.height+1>=minimum?[]:[{class:element.className,height:rect.height,minimum}];
    });
    const overlappingRows=[...body.querySelectorAll('.widget-expense-list,.expense-widget-details,.expense-widget-details section>div')].flatMap(parent=>{
      const rows=[...parent.children].filter(visible).map(element=>({class:element.className,rect:element.getBoundingClientRect()}));
      return rows.flatMap((left,i)=>rows.slice(i+1).filter(right=>Math.min(left.rect.right,right.rect.right)-Math.max(left.rect.left,right.rect.left)>1&&Math.min(left.rect.bottom,right.rect.bottom)-Math.max(left.rect.top,right.rect.top)>1).map(right=>({parent:parent.className,first:left.class,second:right.class})));
    });
    const clippedMoney=[...body.querySelectorAll('.expense-widget-money')].filter(visible).flatMap(element=>{
      const range=document.createRange();range.selectNodeContents(element);
      const text=range.getBoundingClientRect(),parent=element.parentElement.getBoundingClientRect();
      return inside(text,parent)&&inside(text,root)?[]:[element.textContent];
    });
    const footer=body.querySelector('.expense-widget-footer').getBoundingClientRect();
    return {width:root.width,physicalHeight:root.height,height:body.clientHeight,scroll:body.scrollHeight,overflow:getComputedStyle(body).overflowY,fits:inside(view,root)&&parts.every(part=>inside(part,root)),badLeaves,overlaps,compressedRows,overlappingRows,clippedMoney,footerGap:root.bottom-footer.bottom};
  });
}

export async function expenseWidgetFlow(page){
  const originalLayout=await api(page,'/dashboard/layout'),originalModules=(await api(page,'/modules')).enabled,viewport=page.viewportSize();
  const fixture=fixtures();let phase={items:fixture.items};
  const handler=route=>{
    if(route.request().method()!=='GET')return route.continue();
    const path=new URL(route.request().url()).pathname;
    if(path!=='/api/expenses')return route.continue();
    const response=phase; // Snapshot this request's phase before fulfilment.
    return response.error?route.fulfill({status:503,json:{detail:response.error}}):route.fulfill({json:response.items});
  };
  const widget=i=>page.locator('[data-widget-id="expense_sizing_'+i+'"]');
  const summary=async(box,label,value)=>{
    const amount=box.locator('.expense-widget-summary').getByText(label,{exact:true}).locator('..').locator('.expense-widget-money');
    await expect(amount).toHaveAttribute('aria-label',money(value));await expect(amount).toHaveAttribute('title',money(value));
  };
  const settle=async()=>{
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect.poll(()=>page.locator('#widget-grid').evaluate(element=>{
      const width=element.getBoundingClientRect().width,columns=width>=840?4:width>=440?2:1;
      return Math.abs(Number.parseFloat(element.style.getPropertyValue('--grid-cell'))-(width-(columns-1)*16)/columns)<1;
    })).toBe(true);
  };
  try{
    await api(page,'/modules','PUT',{enabled:{expenses:true}});
    await api(page,'/dashboard/layout','PUT',{...originalLayout,instances:sizes.map((size,i)=>({id:'expense_sizing_'+i,module_id:'expenses',title:'Раздельный счёт '+size,size,config:{participants:['Володя'],retention_days:30}}))});
    await page.route('**/api/expenses',handler);await page.goto('/');await appSection(page,'Главная');
    for(const [i,size] of sizes.entries()){
      const box=widget(i);
      await expect(box.locator('.expense-widget')).toBeVisible();
      await summary(box,'Моя доля',200000035000);await summary(box,'Мне должны',75000);await summary(box,'Я должен',80000);
      await expect(box.getByRole('button',{name:'Открыть все счета',exact:true})).toContainText('5');
      await expect(box.locator('.expense-widget-account')).toHaveCount(size==='2x2'?4:2);
      await expect(box.locator('.expense-widget-account').first()).toContainText('Завершён');
      await expect(box.locator('.expense-widget-account-title').first()).toHaveAttribute('title',fixture.longTitle);
      if(size!=='1x1')await summary(box,'Я оплатил',200000060000);
      if(size==='1x2'||size==='2x2'){
        await expect(box.locator('.expense-widget-categories .expense-widget-category')).toHaveCount(size==='1x2'?2:3);
        const beer=box.locator('.expense-widget-category').filter({hasText:'Пиво'}),taxi=box.locator('.expense-widget-category').filter({hasText:'Такси'});
        await expect(beer.locator('.expense-widget-money')).toHaveAttribute('aria-label',money(30000));
        if(size==='2x2')await expect(taxi.locator('.expense-widget-money')).toHaveAttribute('aria-label',money(5000));
        await expect(box.locator('.expense-widget-recent .expense-widget-entry').first()).toContainText('Прямой долг');
        await expect(box.locator('.expense-widget-recent .expense-widget-entry').first()).toHaveAttribute('data-expense-event','widget_direct_in');
        await expect(box.locator('.expense-widget-recent .expense-widget-entry').first().locator('.expense-widget-money')).toHaveAttribute('aria-label',money(50000));
      }else await expect(box.locator('.expense-widget-details')).toHaveCount(0);
    }
    const debtRows=widget(3).locator('.expense-widget-debts .expense-widget-entry');
    await expect(debtRows).toHaveCount(2);
    for(const id of ['widget_direct_in','widget_direct_out']){
      const row=debtRows.filter({has:page.locator('[title="'+(id==='widget_direct_in'?'Прямой долг от участника':'Прямой долг участнику')+'"]')});
      await expect(row).toHaveAttribute('data-expense-event',id);
      await expect(row.locator('span').first()).toHaveAttribute('title',fixture.longName);
      await expect(row.locator('.expense-widget-money')).toHaveAttribute('aria-label',money(50000));
    }
    const matrix=[];
    for(const width of [1280,1100,390,320]){
      await page.setViewportSize({width,height:900});await settle();
      for(const [i,size] of sizes.entries()){
        const bounds=await geometry(widget(i));
        const roomy=bounds.width>=380;
        await expect(widget(i).locator('.expense-widget-account:visible')).toHaveCount(visibleAccounts(size,bounds));
        await expect(widget(i).locator('.expense-widget-recent .expense-widget-entry:visible')).toHaveCount(size==='1x2'?2:size==='2x2'?(roomy?4:2):0);
        await expect(widget(i).locator('.expense-widget-debts .expense-widget-entry:visible')).toHaveCount(size==='2x2'?2:0);
        matrix.push({size,width,bounds});
      }
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:'artifacts/expense-widget-sizes-'+width+'.png',fullPage:true});
    }
    await writeFile('artifacts/expense-widget-geometry.json',JSON.stringify(matrix,null,2));
    for(const {size,width,bounds} of matrix){
      expect(bounds,`${size} at ${width}px: ${JSON.stringify(bounds)}`).toMatchObject({overflow:'clip',fits:true,badLeaves:[],overlaps:false,compressedRows:[],overlappingRows:[],clippedMoney:[]});
      expect(bounds.scroll,`${size} at ${width}px`).toBeLessThanOrEqual(bounds.height+1);
      expect(bounds.footerGap,`${size} uses its full height at ${width}px`).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({width:1280,height:900});await settle();
    // Fractional container widths must not fall between the narrow/wide rules.
    const boundaryBody=widget(3).locator('[data-widget-body]'),bodyStyle=await boundaryBody.getAttribute('style'),boundary=[];
    try{
      for(const width of [379.5,380]){
        await boundaryBody.evaluate((element,width)=>{element.style.flex='0 0 auto';element.style.width=width+'px';},width);await settle();
        const actual=await boundaryBody.evaluate(element=>element.getBoundingClientRect().width);
        expect(Math.abs(actual-width)).toBeLessThan(.1);
        const count=width<380?2:4;
        await expect(widget(3).locator('.expense-widget-account:visible')).toHaveCount(count);
        await expect(widget(3).locator('.expense-widget-recent .expense-widget-entry:visible')).toHaveCount(count);
        boundary.push({requested:width,actual,accounts:count,recent:count});
      }
      await writeFile('artifacts/expense-widget-boundary.json',JSON.stringify(boundary,null,2));
    }finally{
      await boundaryBody.evaluate((element,style)=>{if(style===null)element.removeAttribute('style');else element.setAttribute('style',style);},bodyStyle);await settle();
    }
    // Editing reveals the toolbar controls and must still leave room for data.
    await page.setViewportSize({width:1100,height:900});await settle();
    const gridWidth=await page.locator('#widget-grid').evaluate(element=>element.getBoundingClientRect().width),minimumFourColumns=Math.ceil(1100+842-gridWidth);
    const beforeEdit=await api(page,'/dashboard/layout'),editMatrix=[];
    await page.locator('#dashboard-lock').click();await expect(page.locator('#dashboard-lock')).toHaveAttribute('aria-pressed','true');
    try{
      for(const width of [1100,320,minimumFourColumns]){
        await page.setViewportSize({width,height:900});await settle();
        if(width===minimumFourColumns){
          const actual=await page.locator('#widget-grid').evaluate(element=>({width:element.getBoundingClientRect().width,columns:Number(element.style.getPropertyValue('--grid-columns'))}));
          expect(actual.columns).toBe(4);expect(Math.abs(actual.width-842)).toBeLessThanOrEqual(1);
        }
        for(const [i,size] of sizes.entries()){
          await expect(widget(i).locator('[data-configure]')).toBeVisible();await expect(widget(i).locator('[data-remove-widget]')).toBeVisible();
          const bounds=await geometry(widget(i));
          await expect(widget(i).locator('.expense-widget-account:visible')).toHaveCount(visibleAccounts(size,bounds));
          editMatrix.push({size,width,bounds});
        }
        await page.screenshot({path:'artifacts/expense-widget-edit-'+width+'.png',fullPage:true});
      }
      await writeFile('artifacts/expense-widget-edit-geometry.json',JSON.stringify(editMatrix,null,2));
      for(const {size,width,bounds} of editMatrix){
        expect(bounds,`Editing ${size} at ${width}px: ${JSON.stringify(bounds)}`).toMatchObject({overflow:'clip',fits:true,badLeaves:[],overlaps:false,compressedRows:[],overlappingRows:[],clippedMoney:[]});
        expect(bounds.scroll,`Editing ${size} at ${width}px`).toBeLessThanOrEqual(bounds.height+1);
      }
    }finally{
      await page.locator('#dashboard-lock').click();await expect(page.locator('#dashboard-lock')).toHaveAttribute('aria-pressed','false');
      expect(await api(page,'/dashboard/layout')).toEqual(beforeEdit);
    }
    await page.setViewportSize({width:1280,height:900});await settle();
    await widget(0).locator('.expense-widget-account').first().click();
    await expect(page.getByRole('heading',{name:fixture.longTitle,exact:true})).toBeVisible();
    await expect(page.locator('[data-event-id="widget_large_closed"]')).toHaveAttribute('aria-current','true');
    await expect(page.locator('[data-entry-form]')).toHaveCount(0);
    await appSection(page,'Главная');
    await widget(3).locator('.expense-widget-debts [data-expense-event="widget_direct_in"]').click();
    await expect(page.getByRole('heading',{name:'Прямой долг от участника',exact:true})).toBeVisible();
    await expect(page.locator('[data-event-id="widget_direct_in"]')).toHaveAttribute('aria-current','true');
    await appSection(page,'Главная');await widget(0).getByRole('button',{name:'Открыть все счета',exact:true}).click();
    await expect(page.locator('[data-events] [data-event-id]')).toHaveCount(5);await appSection(page,'Главная');

    phase={items:[fixture.empty]};await page.reload();
    for(const i of [2,3]){
      const box=widget(i);
      await summary(box,'Моя доля',0);await summary(box,'Мне должны',0);await summary(box,'Я должен',0);await summary(box,'Я оплатил',0);
      await expect(box.locator('.expense-widget-categories')).toContainText('Личных расходов пока нет.');
      await expect(box.locator('.expense-widget-recent')).toContainText('Добавьте первый платёж в счёт.');
    }
    await expect(widget(3).locator('.expense-widget-debts')).toContainText('Все в расчёте.');
    phase={items:[]};await page.reload();
    for(const i of [0,1,2,3]){
      await expect(widget(i).getByRole('heading',{name:'Разделите расходы',exact:true})).toBeVisible();
      await expect(widget(i).locator('.expense-widget-summary,.expense-widget-details')).toHaveCount(0);
      await expect(widget(i).getByRole('button',{name:'Открыть все счета',exact:true})).toBeVisible();
    }
    await page.setViewportSize({width:320,height:900});await settle();
    for(const [i,size] of sizes.entries())expect(await geometry(widget(i)),`Empty ${size} at 320px`).toMatchObject({fits:true,badLeaves:[],overlaps:false,compressedRows:[],overlappingRows:[],clippedMoney:[]});
    await page.setViewportSize({width:minimumFourColumns,height:900});await settle();
    await page.locator('#dashboard-lock').click();await expect(page.locator('#dashboard-lock')).toHaveAttribute('aria-pressed','true');
    try{
      const emptyMatrix=[];
      for(const [i,size] of sizes.entries())emptyMatrix.push({size,width:minimumFourColumns,bounds:await geometry(widget(i))});
      await page.screenshot({path:'artifacts/expense-widget-empty-edit.png',fullPage:true});
      await writeFile('artifacts/expense-widget-empty-edit-geometry.json',JSON.stringify(emptyMatrix,null,2));
      for(const {size,bounds} of emptyMatrix){
        expect(bounds,`Empty editing ${size} at four-column boundary: ${JSON.stringify(bounds)}`).toMatchObject({fits:true,badLeaves:[],overlaps:false,compressedRows:[],overlappingRows:[],clippedMoney:[]});
        expect(bounds.scroll).toBeLessThanOrEqual(bounds.height+1);
      }
    }finally{
      await page.locator('#dashboard-lock').click();await expect(page.locator('#dashboard-lock')).toHaveAttribute('aria-pressed','false');
    }
    await widget(0).getByRole('button',{name:'Начать гулянку'}).click();
    await expect(page.getByRole('dialog').getByLabel('Участники, каждый с новой строки')).toHaveValue('');
    await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();

    phase={error:'Временно не удалось загрузить счета.'};await page.reload();
    for(const i of [0,1,2,3])await expect(widget(i).locator('[data-widget-body]').getByRole('status')).toContainText(phase.error);
    phase={items:fixture.items};await widget(0).getByRole('button',{name:'Повторить загрузку',exact:true}).click();
    await summary(widget(0),'Моя доля',200000035000);
    await expect(widget(0).getByRole('button',{name:'Повторить загрузку',exact:true})).toHaveCount(0);
    // Retrying one instance must not silently replace the other error states.
    for(const i of [1,2,3])await expect(widget(i).getByRole('button',{name:'Повторить загрузку',exact:true})).toBeVisible();
  }finally{
    await page.unroute('**/api/expenses',handler);
    await api(page,'/modules','PUT',{enabled:originalModules});
    const current=await api(page,'/dashboard/layout');
    await api(page,'/dashboard/layout','PUT',{...originalLayout,version:current.version});
    await page.setViewportSize(viewport);await page.goto('/');await appSection(page,'Главная');
  }
}
