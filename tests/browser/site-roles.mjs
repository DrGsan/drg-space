import {readAppStyles} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundle=await build({entryPoints:['web/modules/admin/index.js'],bundle:true,format:'iife',globalName:'AdminWorkspace',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles();
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://site-roles.test/**',r=>r.fulfill({body:'<div id="app" class="workspace"><main class="content"><div id="view-content"><div id="fixture"></div></div></main></div><div id="toast"></div>',contentType:'text/html'}));
  await page.goto('http://site-roles.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  const mount=async viewer=>page.evaluate(viewer=>{
    window.disposeAdmin?.();document.querySelector('#fixture').innerHTML='';
    const users=[['1','owner','Владелец'],['2','admin','Помощник'],['3','member','Пользователь']].map(([id,role,name])=>({id:id.repeat(32),login:role,role,role_label:name,profile:{id:id.repeat(32),login:role,display_name:name},two_factor:role!=='member',current:role===viewer,can_manage:viewer==='owner'||role==='member',total_bytes:0,data_bytes:0,file_bytes:0,media_bytes:0,note_content_bytes:0,quota:{used_bytes:0,limit_bytes:1073741824}}));
    window.roleChanges=[];window.rolesApiCalls=[];
    const status=()=>({unlocked:true,expires_at:Math.floor(Date.now()/1000)+900,server_time:Math.floor(Date.now()/1000)});
    const ctx={state:{me:users.find(u=>u.role===viewer)},navigate(){},toast(){},modal(html){const el=document.createElement('div');el.className='modal-overlay';el.innerHTML='<section class="modal" role="dialog"><button class="modal-close">×</button>'+html+'</section>';document.body.append(el);el.querySelector('.modal-close').onclick=()=>el.remove();return el;},async api(path,method='GET',data){
      rolesApiCalls.push(path);
      if(path==='/admin/access'||path==='/admin/access/activity')return status();
      if(path==='/admin/users')return {users,can_change_roles:viewer==='owner',families:[],workspaces:[],database_bytes:0,media_bytes:0,file_bytes:0,site_total_bytes:0,note_content_bytes:0};
      if(path==='/admin/registration')return {enabled:false};
      if(path==='/invites')return {invitations:[]};
      if(path.endsWith('/role')){
        roleChanges.push(data);const user=users.find(u=>path.includes(u.id));
        if(data.role==='admin'&&!user.two_factor)throw Error('Перед назначением администратором пользователь должен включить 2FA.');
        user.role=data.role;return {};
      }
      if(viewer==='owner'&&path==='/family-photos/admin/storage')return {configured:false};
      throw Error('Unexpected request: '+path);
    }};
    window.disposeAdmin=AdminWorkspace.mountAdmin(document.querySelector('#fixture'),ctx);
  },viewer);
  await mount('owner');await page.locator('[data-preference-key=accounts]>summary').click();
  await expect(page.locator('[data-site-role]')).toHaveCount(2);
  await expect(page.locator('[data-admin-user="'+'1'.repeat(32)+'"] .admin-role-badge')).toHaveText('Владелец');
  const adminRole=page.locator('[data-site-role="'+'2'.repeat(32)+'"]');
  await adminRole.selectOption('member');await expect(page.locator('[data-role-form]')).toBeVisible();
  assert.equal(await adminRole.inputValue(),'admin','selection waits for confirmation');
  await page.locator('.modal-close').click();assert.deepEqual(await page.evaluate(()=>roleChanges),[]);
  await adminRole.selectOption('member');await page.locator('[data-role-form] [type=submit]').click();
  await expect(page.locator('[data-role-form]')).toHaveCount(0);
  await expect(adminRole).toHaveValue('member');
  assert.deepEqual(await page.evaluate(()=>roleChanges),[{role:'member',previous_role:'admin'}]);
  const memberRole=page.locator('[data-site-role="'+'3'.repeat(32)+'"]');
  await memberRole.selectOption('admin');await page.locator('[data-role-form] [type=submit]').click();
  await expect(page.locator('#toast')).toContainText('включить 2FA');
  await expect(memberRole).toHaveValue('member');await expect(page.locator('[data-role-form]')).toBeVisible();
  await page.locator('.modal-close').click();await page.locator('#toast').evaluate(el=>el.classList.remove('show'));
  await page.getByLabel('Роль сайта',{exact:true}).selectOption('member');await expect(page.locator('[data-admin-user]')).toHaveCount(2);
  await page.getByLabel('Роль сайта',{exact:true}).selectOption('');
  for(const width of [1280,390,320]){
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert(await page.locator('.admin-accounts-scroll').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
    await page.screenshot({path:`artifacts/site-roles-owner-${process.env.DRG_BROWSER_ENGINE||'chromium'}-${width}.png`,fullPage:true});
  }
  await mount('admin');await page.locator('[data-preference-key=accounts]>summary').click();
  await expect(page.locator('[data-site-role]')).toHaveCount(0);
  await expect(page.locator('[data-preference-key=security],[data-preference-key=backups],[data-preference-key=family-photos]')).toHaveCount(0);
  await page.locator('[data-preference-key=registration]>summary').click();
  await expect(page.locator('[data-registration-toggle]')).toBeHidden();await expect(page.locator('[data-invite-member]')).toBeVisible();
  for(const id of ['1','2']){
    await page.locator('[data-open-admin-user="'+id.repeat(32)+'"]').click();
    await expect(page.locator('.admin-account-details [data-admin-profile],.admin-account-details [data-admin-quota],.admin-account-details [data-admin-2fa]')).toHaveCount(0);
    await page.locator('.modal-close').click();
  }
  await page.locator('[data-open-admin-user="'+'3'.repeat(32)+'"]').click();
  await expect(page.locator('[data-admin-profile]')).toBeVisible();await expect(page.locator('[data-admin-friends]')).toHaveCount(0);
  await page.locator('.modal-close').click();
  assert.deepEqual(await page.evaluate(()=>[...new Set(rolesApiCalls)].sort()),['/admin/access','/admin/registration','/admin/users','/invites']);
  await page.screenshot({path:`artifacts/site-roles-admin-${process.env.DRG_BROWSER_ENGINE||'chromium'}.png`,fullPage:true});
  assert.deepEqual(errors,[]);console.log('Site roles passed: owner confirmation/cancel, filters, 320–1280px, restricted administrator sections and actions.');
}finally{await browser.close();}
