import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {appSection} from './notes-ui.js';
const origin='http://127.0.0.1:8048',password='Deletion-grace-test-password-123';
function totp(secret){
 let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
 const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
 const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
 return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
async function scenario(browser){
 const owner=await browser.newContext({locale:'ru-RU',baseURL:origin}),user=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000}});
 async function api(context,path,method='GET',data,csrf=''){
   const result=await context.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
   expect(result.ok(),path+' '+await result.text()).toBe(true);return result.json();
 }
 let csrf=(await api(owner,'/setup','POST',{login:'owner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api(owner,'/2fa/enroll','POST',{password},csrf);
 await api(owner,'/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);
 csrf=(await api(owner,'/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
 await api(owner,'/admin/access/unlock','POST',{password},csrf);
 const invitation=await api(owner,'/invites','POST',{login:'waiting'},csrf);
 let token=(await api(user,'/register','POST',{login:'waiting',password,invite:invitation.url.split('/').at(-1)})).csrf;
 token=(await api(user,'/2fa/skip','POST',undefined,token)).csrf;
 const uid=(await api(user,'/me')).id;
 const note=await api(user,'/notes','POST',{title:'Сохранить до срока',body:'Личные данные остаются до удаления',blocks:[{type:'paragraph',content:'Личные данные остаются до удаления'},{type:'heading',props:{level:2,isToggleable:true},content:'Раздел удалённой заметки',children:[{type:'paragraph',content:'Содержимое блока'}]}]},token);
 await api(user,'/notes/'+note.id,'DELETE',undefined,token);
 const page=await user.newPage(),second=await user.newPage(),errors=[];
 for(const tab of [page,second])tab.on('pageerror',error=>errors.push(error.message));
 await page.goto('/');await appSection(page,'Заметки');await page.locator('#show-trash').click();
 await expect(page.getByRole('dialog')).toContainText('30 дней');
 await page.getByLabel('Действия: Сохранить до срока').click();
 await expect(page.getByRole('menuitem',{name:'Окончательно удалить',exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await page.locator('[data-trash-preview="'+note.id+'"]').click();
 await expect(page.locator('.note-trash-banner')).toContainText('До окончательного удаления: 30 дн.');
 await expect(page.locator('[data-trash-document]')).toContainText('Личные данные остаются до удаления');
 await expect(page.locator('[data-trash-document] [contenteditable=true]')).toHaveCount(0);
 await expect(page.locator('.modal-overlay')).toHaveCount(0);
 await page.locator('[data-trash-history]').click();await expect(page.locator('.note-history')).toBeVisible();await page.locator('.history-close').click();await expect(page.locator('.note-trash-banner')).toBeVisible();await expect(page.locator('[data-trash-document]')).toContainText('Раздел удалённой заметки');await page.screenshot({path:'artifacts/trash-full-document.png'});await page.locator('[data-trash-restore]').click();
 await expect(page.getByLabel('Название заметки',{exact:true})).toHaveValue('Сохранить до срока');
 await second.goto('/');await appSection(page,'Настройки');await page.locator('[data-delete-own-account]').click();
 const form=page.locator('[data-delete-account]');await form.getByLabel('Ваш пароль',{exact:true}).fill(password);await form.getByLabel('Введите свой логин',{exact:true}).fill('waiting');
 await form.getByRole('button',{name:'Запланировать удаление',exact:true}).click();
 for(const tab of [page,second])await expect(tab.getByRole('heading',{name:'Аккаунт ожидает удаления',exact:true})).toBeVisible({timeout:15000});
 await expect(page.getByRole('timer')).toContainText('До удаления:');
 await page.locator('[data-account-notes] summary').click();await page.getByRole('button',{name:'Сохранить до срока',exact:true}).click();await expect(page.locator('[data-account-note-view]')).toContainText('Личные данные остаются до удаления');
 const blocked=await user.request.post('/api/notes',{headers:{Origin:origin,'X-CSRF-Token':token},data:{title:'blocked'}});expect(blocked.status()).toBe(423);
 expect((await api(user,'/notes/'+note.id)).body).toBe('Личные данные остаются до удаления');
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:`artifacts/deletion-account-${width}.png`,fullPage:true});}
 await page.getByRole('button',{name:'Отменить удаление аккаунта',exact:true}).click();await expect(page.locator('.workspace')).toBeVisible();
 await expect(second.locator('.workspace')).toBeVisible({timeout:15000});
 expect((await api(user,'/me')).account_deletion).toBeNull();
 await api(owner,'/admin/users/delete','POST',{user_id:uid,confirmation:'waiting',admin_password:password},csrf);
 await page.reload();await expect(page.getByRole('heading',{name:'Аккаунт ожидает удаления',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Выйти',exact:true}).click();await expect(page.locator('#auth-form')).toBeVisible();
 await page.getByLabel('Логин',{exact:true}).fill('waiting');await page.getByLabel('Пароль',{exact:true}).fill(password);await page.locator('#auth-form button[type=submit]').click();
 await expect(page.getByRole('heading',{name:'Аккаунт ожидает удаления',exact:true})).toBeVisible();
 await api(owner,'/admin/users/restore','POST',{user_id:uid,confirmation:'waiting',admin_password:password},csrf);
 await expect(page.locator('.workspace')).toBeVisible({timeout:15000});
 expect((await api(user,'/notes/'+note.id)).body).toBe('Личные данные остаются до удаления');
 expect(errors).toEqual([]);await user.close();await owner.close();
}
const directory=await mkdtemp(tmpdir()+'/drg-people-groups-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8048','tests.browser_app:app'],{env:{...process.env,GUNICORN_WORKERS:'2',GUNICORN_THREADS:'8',DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-people-groups-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=process.env.DRG_BROWSER_ENGINE==='webkit'
    ?await webkit.launch({headless:true})
    :await chromium.launch({headless:true,channel:process.env.DRG_BROWSER_CHANNEL||undefined});
  await mkdir('artifacts',{recursive:true});await scenario(browser);
  console.log('Deletion grace passed: note trash/restore, own/admin account scheduling, write freeze, two tabs, reload, login/logout, owner cancellation and 320–1440px.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
