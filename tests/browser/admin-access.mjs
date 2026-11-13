import {readAppStyles} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({entryPoints:['web/modules/admin/index.js'],bundle:true,format:'iife',globalName:'AdminWorkspace',write:false,loader:{'.css':'empty'}});
const css=await readAppStyles();
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://admin-access.test/**',route=>route.fulfill({body:'<div id="app"><main id="fixture"></main><div id="toast"></div></div>',contentType:'text/html'}));
  await page.goto('http://admin-access.test/');await page.clock.install();await page.addStyleTag({content:css});
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
    const users=[['1','owner','Александр'],['2','alena','Алёна'],['3','nikolay','Николай'],['4','lubov','Любовь'],['5','shura','Саша']].map(([n,login,name])=>({id:n.repeat(32),login,role:n==='1'?'owner':'member',role_label:n==='1'?'Администратор':'Участник',profile:{id:n.repeat(32),login,display_name:name},total_bytes:0,data_bytes:0,file_bytes:0,media_bytes:0,note_content_bytes:0,notes:0,tasks:0,images:0,quota:{used_bytes:0,limit_bytes:1073741824}}));
    const state=window.adminState={users,lastActivity:0,unlocked:false,reads:0,saves:0,activity:0,selected:['3'.repeat(32)],hold:false};
    const stamp=()=>Math.floor(Date.now()/1000),status=()=>({unlocked:state.unlocked&&stamp()-state.lastActivity<900,expires_at:state.lastActivity+900,server_time:stamp(),idle_seconds:900});
    const locked=()=>Object.assign(Error('Для управления введите пароль администратора.'),{status:423,adminUnlock:true});
    const ctx={state:{me:users[0]},navigate(){},toast(text){document.querySelector('#toast').textContent=text;},modal(html){
      const el=document.createElement('div');el.className='modal-overlay';el.innerHTML='<section class="modal" role="dialog" aria-modal="true"><button class="modal-close icon-btn" aria-label="Закрыть">×</button>'+html+'</section>';document.body.append(el);el.querySelector('.modal-close').onclick=()=>el.remove();return el;
    },async api(path,method='GET',data){
      if(path==='/admin/access'){
        if(state.holdStatus)return new Promise(resolve=>state.releaseStatus=()=>resolve(status()));
        return status();
      }
      if(path==='/admin/access/unlock'){
        if(data.password!=='Admin-test-password-123')throw Error('Неверный пароль администратора.');
        state.lastActivity=stamp();state.unlocked=true;return status();
      }
      if(path==='/admin/access/activity'){
        if(!status().unlocked)throw locked();state.lastActivity=stamp();state.activity++;return status();
      }
      if(!status().unlocked)throw locked();
      if(path==='/admin/users'){state.reads++;return {users,families:[],database_bytes:0,media_bytes:0,file_bytes:0,site_total_bytes:0,note_content_bytes:0,backup_limit_bytes:1073741824};}
      if(path==='/admin/registration')return {enabled:false};
      if(path==='/invites')return {invitations:[]};
      if(path==='/family-photos/admin')return {users:[],groups:state.exportRequested?[{id:'-1001',title:'Семейный архив из уведомления',enabled:true,photos:10,videos:0}]:[],status:{available:state.exportRequested,group_management:true,photo_exports:true}};
      if(path==='/family-photos/admin/exports'){state.exportReads=(state.exportReads||0)+1;return {items:[],server_time:stamp()};}
      if(path.endsWith('/friends')){
        if(method==='GET')return {users:users.filter(u=>u.id!=='2'.repeat(32)),friend_ids:[...state.selected],version:'a'.repeat(64)};
        state.saves++;const old=state.selected;state.selected=data.friend_ids;
        return {added:state.selected.filter(id=>!old.includes(id)).length,removed:old.filter(id=>!state.selected.includes(id)).length};
      }
      if(path.endsWith('/profile')){state.users.find(u=>path.includes(u.id)).profile.display_name=data.display_name;return {};}
      throw Error('Unexpected '+path);
    }};
    window.adminCtx=ctx;window.disposeAdmin=AdminWorkspace.mountAdmin(document.querySelector('#fixture'),ctx);
  });
  const unlock=async()=>{
    await page.locator('[data-admin-unlock]').waitFor();
    await page.locator('[data-admin-unlock] [name=password]').fill('Admin-test-password-123');
    await page.locator('[data-admin-unlock] [type=submit]').click();
    await page.locator('[data-admin-unlock]').waitFor({state:'detached'});
  };
  await page.locator('[data-admin-unlock]').waitFor();assert.equal(await page.evaluate(()=>adminState.reads),0,'no privileged data before unlock');
  await page.locator('[data-admin-unlock] [name=password]').fill('wrong');await page.locator('[data-admin-unlock] [type=submit]').click();
  assert.match(await page.locator('[data-unlock-error]').innerText(),/Неверный пароль/);
  assert.equal(await page.locator('[data-admin-unlock] [name=password]').inputValue(),'');
  await unlock();
  await page.locator('[data-preference-key=accounts] > summary').click();
  const row=page.locator('[data-admin-user="'+'2'.repeat(32)+'"]');
  await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-friends]').click();await page.locator('[data-friend-search]').waitFor();
  assert.equal(await page.locator('[name=admin_password]').count(),0);
  const search=page.locator('[data-friend-search]');await search.fill('любо');
  assert.equal(await page.locator('.admin-friend-option:visible').count(),1);
  await page.locator('.admin-friend-option:visible input').check();await search.fill('shura');
  await page.locator('.admin-friend-option:visible input').check();await search.fill('никол');
  assert.equal(await page.locator('.admin-friend-option:visible input').isChecked(),true,'filtering preserves previous choices');
  for(const scheme of ['light','dark'])for(const width of [1280,390,320]){
    await page.evaluate(value=>document.documentElement.dataset.scheme=value,scheme);await page.setViewportSize({width,height:900});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:`artifacts/admin-friends-${process.env.DRG_BROWSER_ENGINE||'chromium'}-${scheme}-${width}.png`});
  }
  await search.fill('');await page.locator('[data-admin-friends] [type=submit]').click();
  assert.deepEqual(await page.evaluate(()=>adminState.selected.slice().sort()),['3'.repeat(32),'4'.repeat(32),'5'.repeat(32)]);
  await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-profile]').click();await page.locator('[data-managed-account] [name=display_name]').fill('Несохранённое имя');
  await page.clock.runFor(6000);const touches=await page.evaluate(()=>adminState.activity);
  await page.evaluate(()=>adminState.holdStatus=true);
  await page.clock.runFor(901000);
  assert.equal(await page.locator('[data-admin-content]').isVisible(),false,'local expiry hides privileged content even while server status is delayed');
  await page.evaluate(()=>{adminState.holdStatus=false;adminState.releaseStatus();});
  await page.locator('[data-admin-unlock]').waitFor();assert.equal(await page.evaluate(()=>adminState.activity),touches,'background clock/status reads cannot keep access alive');
  assert.equal(await page.locator('[data-managed-account] [name=display_name]').inputValue(),'Несохранённое имя','expiry keeps unsaved form');
  await unlock();
  await page.locator('[data-managed-account] [type=submit]').click();
  assert.equal(await page.evaluate(()=>adminState.users[1].profile.display_name),'Несохранённое имя');
  await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-friends]').click();await page.locator('[data-friend-search]').waitFor();
  await page.locator('[data-friend-id="'+'3'.repeat(32)+'"] input').uncheck();
  assert.match(await page.locator('[data-friend-remove-names]').innerText(),/Николай.*переписка и общие заметки будут удалены/);
  assert.equal(await page.locator('[name=confirm_removals]').getAttribute('required'),'');
  await page.locator('[name=confirm_removals]').check();await page.locator('[data-admin-friends] [type=submit]').click();
  assert.deepEqual(await page.evaluate(()=>adminState.selected.slice().sort()),['4'.repeat(32),'5'.repeat(32)]);
  // A server-side lock between status polls prompts once and safely retries.
  await row.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-profile]').click();await page.locator('[data-managed-account] [name=display_name]').fill('После подтверждения');
  await page.evaluate(()=>adminState.unlocked=false);await page.locator('[data-managed-account] [type=submit]').click();
  await unlock();await page.locator('[data-managed-account]').waitFor({state:'detached'});
  assert.equal(await page.evaluate(()=>adminState.users[1].profile.display_name),'После подтверждения');
  await page.evaluate(()=>{disposeAdmin();document.querySelector('#fixture').innerHTML='Другой раздел';});
  await page.clock.runFor(901000);assert.equal(await page.locator('[data-admin-unlock]').count(),0,'disposed view cannot reopen unlock UI');
  // A readiness notification opens its group only after the normal management unlock.
  await page.evaluate(()=>{adminState.unlocked=false;adminState.exportRequested=true;adminCtx.state.adminFamilyExportRequested='-1001';window.disposeAdmin=AdminWorkspace.mountAdmin(document.querySelector('#fixture'),adminCtx);});
  await page.locator('[data-admin-unlock]').waitFor();assert.equal(await page.evaluate(()=>adminState.exportReads||0),0);
  await unlock();await page.locator('.fp-export-dialog').waitFor();
  assert.match(await page.locator('.fp-export-dialog').innerText(),/Семейный архив из уведомления/);
  assert.equal(await page.locator('.fp-export-dialog [name=groups]').count(),0);
  assert.equal(await page.locator('[data-preference-key=family-photos]').evaluate(row=>row.open),true);
  assert.equal(await page.evaluate(()=>adminCtx.state.adminFamilyExportRequested),undefined);
  await page.evaluate(()=>disposeAdmin());
  assert.deepEqual(errors,[]);console.log('Admin access/friends passed: entry, idle expiry, preserved form, server-lock retry, search/checkboxes, removal confirmation, themes and 320–1280px, teardown.');
}finally{await browser.close();}
