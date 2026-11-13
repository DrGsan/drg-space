import {readAppStyles,openTestOrigin} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundle=await build({entryPoints:['web/modules/admin/family-groups.js'],bundle:true,format:'iife',globalName:'FamilyGroups',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles();
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1440,height:1000}}),errors=[];
  await page.clock.install();
  page.on('pageerror',e=>errors.push(e.message));
  await openTestOrigin(page);await page.setContent('<main class="workspace"><div class="preferences-surface"><details class="preference-row" open><summary>Семейные фото</summary><section class="panel admin-family-photos"><div id="fixture"></div></section></details></div></main>');
  await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
    const stamp=Math.floor(Date.now()/1000);
    window.data={server_time:stamp,status:{group_management:true},groups:[
      {id:'export:abcdef0123456789',telegram_id:'-1001234567890',title:'Первая семейная группа',enabled:true,photos:120,videos:12,media_bytes:12345678,state:'online'},
      {id:'export:1234567890abcdef',telegram_id:'-1002345678901',title:'Вторая семейная группа',enabled:false,photos:450,videos:20,media_bytes:23456789,state:'online'}]};
    window.calls=[];window.stop=()=>{};window.discovery={pending:false,hold:false,items:[{id:'-1003456789012',title:'Новая семья',kind:'supergroup'}]};
    const ctx={modal(html){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.innerHTML='<section class="modal" role="dialog"><button type="button" class="modal-close" aria-label="Закрыть">×</button>'+html+'</section>';document.body.append(overlay);overlay.querySelector('.modal-close').onclick=()=>overlay.remove();return overlay;},async api(path,method='GET',body){
      calls.push({path,method,body});
      if(path.endsWith('/discovery')){const result=()=>({items:structuredClone(discovery.items),requested:stamp+1,checked:discovery.pending?stamp:stamp+1});if(discovery.hold)return new Promise(resolve=>window.resolveDiscovery=()=>{discovery.hold=false;resolve(result());});return result();}
      if(path==='/family-photos/admin')return data;
      if(path.endsWith('/groups')){data.groups.push({id:body.telegram_id,telegram_id:body.telegram_id,title:'Новая семья',enabled:true,photos:0,videos:0,media_bytes:0});return {ok:true};}
      const [,id,action]=path.match(/groups\/([^/]+)\/([^/]+)$/)||[];const group=data.groups.find(g=>g.id===decodeURIComponent(id));
      if(action==='disable')group.enabled=false;
      if(action==='enable')group.enabled=true;
      if(action==='delete'){group.previous=group.enabled;group.enabled=false;group.delete_at=stamp+7*86400;}
      if(action==='cancel'){group.enabled=group.previous;group.delete_at=null;}
      return {ok:true};
    }};
    window.mount=()=>{stop();stop=FamilyGroups.mountFamilyGroups(document.querySelector('#fixture'),ctx,data,async()=>mount());};mount();
  });
  const first=page.locator('[data-fp-managed-group="export:abcdef0123456789"]');
  await expect(page.locator('[data-fp-managed-group]')).toHaveCount(2);
  await expect(first).toContainText('Telegram ID: -1001234567890');
  assert.equal(await first.locator('input').count(),0);
  await first.getByRole('button',{name:'Выключить',exact:true}).click();await expect(first).toContainText('Выключена');
  await first.getByRole('button',{name:'Включить',exact:true}).click();await expect(first).toContainText('Включена');
  await first.getByRole('button',{name:'Удалить',exact:true}).click();
  const dialog=page.getByRole('dialog');await expect(dialog).toContainText('7 суток');
  await dialog.locator('input').fill('wrong');await dialog.getByRole('button',{name:'Назначить удаление'}).click();await expect(dialog).toContainText('ID не совпадает');
  await dialog.locator('input').fill('export:abcdef0123456789');await dialog.getByRole('button',{name:'Назначить удаление'}).click();await expect(first).toContainText('Ожидает удаления');
  assert(await first.getByRole('button',{name:'Отменить удаление'}).isVisible());
  for(const width of [1440,1024,768,700,390,320])for(const scheme of ['light','dark']){
    await page.setViewportSize({width,height:1000});await page.evaluate(s=>document.documentElement.dataset.scheme=s,scheme);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert(await page.locator('.admin-accounts-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.screenshot({path:`artifacts/family-groups-${width}-${scheme}.png`,fullPage:true});
  }
  await first.getByRole('button',{name:'Отменить удаление'}).click();await expect(first).toContainText('Включена');
  await page.getByLabel('Поиск группы',{exact:true}).fill('Вторая');await expect(first).toBeHidden();await page.getByLabel('Поиск группы',{exact:true}).fill('');
  await page.getByRole('button',{name:'Добавить группу',exact:true}).click();
  const source=page.getByRole('dialog'),select=source.locator('select');await select.selectOption('-1003456789012');
  const option=await select.locator('option[value="-1003456789012"]').elementHandle();
  await page.evaluate(()=>{discovery.pending=true;discovery.hold=true;});
  const postsBefore=await page.evaluate(()=>calls.filter(c=>c.path.endsWith('/discovery')&&c.method==='POST').length);
  await source.locator('[data-fp-discover]').click();
  for(let i=0;i<3;i++)await source.locator('[data-fp-discover]').dispatchEvent('click');
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.path.endsWith('/discovery')&&c.method==='POST').length),postsBefore+1);
  await page.evaluate(()=>resolveDiscovery());await expect(source.locator('[data-fp-discover]')).toBeDisabled();
  await select.focus();await page.clock.runFor(2200);
  assert(await option.evaluate(el=>el.isConnected),'unchanged polling keeps native picker options intact');
  await expect(select).toHaveValue('-1003456789012');
  await page.evaluate(()=>discovery.items.push({id:'-1009999999999',title:'Ещё одна группа',kind:'supergroup'}));await page.clock.runFor(2200);
  await expect(select.locator('option')).toHaveCount(2);
  await select.evaluate(el=>el.blur());await expect(select.locator('option')).toHaveCount(3);await expect(select).toHaveValue('-1003456789012');
  await page.evaluate(()=>discovery.pending=false);await page.clock.runFor(2200);await expect(source.locator('[data-fp-discover]')).toBeEnabled();
  await source.getByRole('button',{name:'Добавить группу',exact:true}).click();await expect(page.locator('[data-fp-managed-group]')).toHaveCount(3);
  assert.deepEqual(errors,[]);await page.evaluate(()=>stop());
  console.log('Family group management passed: immutable IDs, disable/enable, confirmed seven-day deletion/cancel, discovery/add, search, 320–1440px, light/dark');
}finally{await browser.close();}
