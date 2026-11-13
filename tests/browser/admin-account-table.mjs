import {readAppStyles,openTestOrigin} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundle=await build({entryPoints:['web/modules/admin/account-table.js'],bundle:true,format:'iife',globalName:'AccountTable',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles();
const browser=process.env.DRG_BROWSER_ENGINE==='webkit'?await webkit.launch({headless:true}):await chromium.launch({headless:true,channel:process.env.DRG_BROWSER_CHANNEL||undefined});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await openTestOrigin(page);await page.setContent('<main class="panel"><h2>Аккаунты и данные</h2><div id="fixture"></div></main>');
  await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
    const stamp=Math.floor(Date.now()/1000),workspaces=[{id:'company',name:'Компания'},{id:'project',name:'Проект'}];
    const users=Array.from({length:32},(_,i)=>({id:String(i).padStart(32,'0'),login:i?'user-'+i:'owner',profile:{id:String(i).padStart(32,'0'),login:i?'user-'+i:'owner',display_name:i?'Пользователь '+i:'Владелец',last_seen:stamp-i*3600},role:i?'member':'owner',created:stamp-i*86400,two_factor:i%2===0,passkeys:i%3,sessions:i%4,current:i===0,notes:i*3,tasks:i,images:i*2,file_count:i,data_bytes:i*100,total_bytes:i*1024*1024,file_bytes:i*1024*512,media_bytes:i*1024*256,note_content_bytes:i*30,quota:{used_bytes:i*1024*1024,limit_bytes:i%5?1073741824:null},families:[i%2?'company':'project'],workspaces:[{...workspaces[i%2?0:1],role:i===0?'owner':i===1?'admin':'member',teams:[i%2?'dev':'qa']}]}));
    AccountTable.accountsTable(document.querySelector('#fixture'),{users,workspaces},{},u=>{
      const overlay=document.createElement('div');overlay.className='modal-overlay admin-account-details';overlay.innerHTML='<section class="modal" role="dialog"><button class="modal-close" aria-label="Закрыть">×</button>'+AccountTable.accountDetails(u)+'</section>';document.body.append(overlay);overlay.querySelector('.modal-close').onclick=()=>overlay.remove();
    });
  });
  await expect(page.locator('[data-admin-user]')).toHaveCount(10);await expect(page.locator('[data-account-count]')).toHaveText('1–10 из 32');
  await page.getByLabel('Роль сайта',{exact:true}).selectOption('owner');await expect(page.locator('[data-admin-user]')).toHaveCount(1);
  await page.getByLabel('Роль сайта',{exact:true}).selectOption('');await page.getByLabel('Защита аккаунта',{exact:true}).selectOption('yes');await expect(page.locator('[data-account-count]')).toHaveText('1–10 из 16');
  await page.getByLabel('Защита аккаунта',{exact:true}).selectOption('');await page.getByLabel('Пространство аккаунта',{exact:true}).selectOption('company');await expect(page.locator('[data-account-count]')).toHaveText('1–10 из 16');
  await page.getByLabel('Пространство аккаунта',{exact:true}).selectOption('');
  await page.getByRole('button',{name:'Следующая страница аккаунтов',exact:true}).click();await expect(page.locator('[data-account-count]')).toHaveText('11–20 из 32');
  await page.getByLabel('Поиск аккаунтов',{exact:true}).fill('user-31');await expect(page.locator('[data-admin-user]')).toHaveCount(1);
  await page.getByLabel('Поиск аккаунтов',{exact:true}).fill('');await page.getByLabel('Сортировка аккаунтов',{exact:true}).selectOption('size');await page.getByRole('button',{name:'Изменить направление сортировки',exact:true}).click();await expect(page.locator('[data-admin-user]').first()).toHaveAttribute('data-admin-user','31'.padStart(32,'0'));
  for(const width of [1440,1024,768,700,390,320])for(const scheme of ['light','dark']){
    await page.setViewportSize({width,height:1000});await page.evaluate(s=>document.documentElement.dataset.scheme=s,scheme);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert(await page.locator('.admin-accounts-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:`artifacts/admin-accounts-table-${width}-${scheme}.png`,fullPage:true});
  }
  await page.setViewportSize({width:1440,height:1000});await page.getByLabel('Поиск аккаунтов',{exact:true}).fill('user-1');await page.getByRole('button',{name:'Следующая страница аккаунтов',exact:true}).click();
  await page.locator('[data-admin-user="'+String(1).padStart(32,'0')+'"] [data-open-admin-user]').click();
  const detail=page.getByRole('dialog');await expect(detail).toContainText('Пользователь');await expect(detail).toContainText('Администратор · dev');await expect(detail).toContainText('Активных сеансов');await expect(detail).toContainText('Passkey: 1');
  for(const width of [1440,1024,768,700,390,320]){await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert(await page.locator('.admin-accounts-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:`artifacts/admin-account-details-${width}.png`,fullPage:true});}
  assert.deepEqual(errors,[]);console.log('Account table passed: 32 users, search, role/2FA/workspace filters, size sort, pagination, expanded details, 320–1440px in both themes');
}finally{await browser.close();}
