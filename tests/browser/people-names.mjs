// Real UI, SQLCipher storage, three accounts and preserved legacy family data.
import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
import {appSection} from './notes-ui.js';

const origin='http://127.0.0.1:8043',password='People-groups-test-password-123';
function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}
function apiFor(context){return async(path,method='GET',data,csrf='')=>{
  const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
  expect(response.ok(),path+' '+await response.text()).toBe(true);return response.json();
};}
async function activate(api,csrf){
  const enrollment=await api('/2fa/enroll','POST',{password},csrf);
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);
  return (await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
}
async function openRow(page,name){
  const row=page.locator('[data-social-name]').filter({has:page.locator('summary strong',{hasText:name})});
  await expect(row).toHaveCount(1);
  if(await row.getAttribute('open')===null)await row.locator(':scope>summary').click();
  return row;
}
async function scenario(browser){
  const errors=[],contexts=[];
  const create=async()=>{const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000}});contexts.push(context);return context;};
  const ownerContext=await create(),targetContext=await create(),otherContext=await create();
  const api=apiFor(ownerContext),targetApi=apiFor(targetContext),otherApi=apiFor(otherContext);
  let csrf=(await api('/setup','POST',{login:'viewer',password,token:'browser-test-setup-key'})).csrf;csrf=await activate(api,csrf);
  const register=async(apiForUser,login)=>{const invitation=await api('/invites','POST',{login},csrf);let token=(await apiForUser('/register','POST',{login,password,invite:invitation.url.split('/').at(-1)})).csrf;return activate(apiForUser,token);};
  await api('/admin/access/unlock','POST',{password},csrf);
  const targetCsrf=await register(targetApi,'alena'),otherCsrf=await register(otherApi,'another');
  await targetApi('/account/profile','PUT',{display_name:'Алёна'},targetCsrf);
  const target=(await targetApi('/me')),other=(await otherApi('/me'));
  const space=(await api('/social/spaces','POST',{name:'Общая компания',template:'organization'},csrf)).id;
  for(const [client,token,login] of [[targetApi,targetCsrf,'alena'],[otherApi,otherCsrf,'another']]){const invite=await api(`/social/spaces/${space}/invites`,'POST',{login},csrf);await client('/social/requests/'+invite.id+'/accept','POST',undefined,token);}
  const friendInvite=await api('/social/friends','POST',{login:'alena'},csrf),direct=(await targetApi('/social/requests/'+friendInvite.id+'/accept','POST',undefined,targetCsrf)).space_id;
  const incoming=await targetApi(`/chat/${direct}/messages`,'POST',{body:'Привет @alena',client_id:'nickname-source-123'},targetCsrf);
  await api(`/chat/${direct}/messages`,'POST',{body:'Ответ',reply_to:incoming.id,client_id:'nickname-reply-123'},csrf);
  const note=await targetApi('/notes','POST',{title:'Общая заметка',space_id:space},targetCsrf);
  await targetApi(`/notes/${note.id}/comments`,'POST',{body:'Обсуждение @alena'},targetCsrf);
  const page=await ownerContext.newPage(),otherPage=await otherContext.newPage();page.on('pageerror',e=>errors.push(e.message));otherPage.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');await appSection(page,'Люди и группы');await openRow(page,'Общая компания');
  await page.getByRole('button',{name:'Личное имя для alena',exact:true}).first().click();
  await page.getByLabel('Как называть этого человека',{exact:true}).fill('Мама');await page.getByRole('button',{name:'Сохранить имя',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.reload();const row=await openRow(page,'Общая компания');const table=row.locator('[data-space-people]');
  for(const query of ['Мама','Алёна','alena']){await table.getByLabel('Поиск участников',{exact:true}).fill(query);await expect(table.locator('[data-people-row]')).toHaveCount(1);await expect(table.locator('[data-people-row]')).toContainText('Мама');}
  await table.getByLabel('Поиск участников',{exact:true}).fill('');
  await otherPage.goto('/');await appSection(otherPage,'Люди и группы');const otherRow=await openRow(otherPage,'Общая компания');
  await expect(otherRow.locator(`[data-people-row="${target.id}"]`)).toContainText('Алёна');await expect(otherRow).not.toContainText('Мама');
  const secondPage=await ownerContext.newPage();await secondPage.goto('/');await appSection(secondPage,'Люди и группы');await openRow(secondPage,'Общая компания');
  await appSection(page,'Сообщения');await page.locator(`[data-room="${direct}"]`).click();
  await expect(page.locator('.chat-header h2')).toHaveText('Мама');await expect(page.locator('.chat-reply-quote strong')).toHaveText('Мама');
  await expect(page.locator('.chat-mention').first()).toHaveText('@Мама');
  await page.getByLabel('Сообщение',{exact:true}).fill('Несохранённый черновик');
  await page.locator('[data-chat-info]').click();await page.getByRole('dialog').getByRole('button',{name:'Личное имя',exact:true}).click();
  await page.getByLabel('Как называть этого человека',{exact:true}).fill('Мамуля');await page.getByRole('button',{name:'Сохранить имя',exact:true}).click();
  await expect(page.locator('.chat-header h2')).toHaveText('Мамуля');await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue('Несохранённый черновик');
  await expect(secondPage.locator('[data-social-name="Общая компания"]').getByRole('button',{name:'Личное имя для alena',exact:true})).toHaveText('Мамуля',{timeout:15000});
  await page.getByLabel('Сообщение',{exact:true}).fill('@Ма');await expect(page.locator('[data-mention-index]').first()).toContainText('Мамуля');
  await page.locator('[data-mention-index]').first().click();await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue('@alena ');
  await page.goto('/page/'+note.id);await page.locator('#discussions-panel>summary').click();
  await expect(page.locator('.comment-author')).toContainText('Мамуля');await expect(page.locator('.comment-text .chat-mention')).toHaveText('@Мамуля');
  await expect(page.getByLabel('Упомянуть участника',{exact:true})).toContainText('Мамуля');
  await page.locator('#page-participants').click();await expect(page.getByRole('dialog',{name:'Доступ к странице'})).toContainText('Мамуля');
  await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  await appSection(page,'Люди и группы');await openRow(page,'Общая компания');await page.getByRole('button',{name:'Личное имя для alena',exact:true}).first().click();
  await page.getByRole('button',{name:'Убрать прозвище',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Личное имя для alena',exact:true}).first()).toHaveText('Алёна');
  expect((await targetApi('/me')).settings.profile.display_name).toBe('Алёна');expect((await otherApi('/me')).people_names).toEqual({});
  expect((await targetApi(`/chat/${direct}/messages`)).items.find(m=>m.id===incoming.id).body).toBe('Привет @alena');
  for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:`artifacts/people-names-${width}.png`,fullPage:true});}
  expect(errors).toEqual([]);for(const context of contexts)await context.close();
}

const directory=await mkdtemp(tmpdir()+'/drg-people-groups-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8043','tests.browser_app:app'],{env:{...process.env,GUNICORN_WORKERS:'2',GUNICORN_THREADS:'8',DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-people-groups-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  console.log('Private names passed: viewer isolation, reload, canonical/nickname search, replies, mentions, comments, note access, two tabs, reset, draft preservation and 320–1440px.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
