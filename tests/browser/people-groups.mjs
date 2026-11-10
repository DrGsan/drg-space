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
  const errors=[],adminContext=await browser.newContext({locale:'ru-RU',baseURL:origin}),context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1280,height:1000}});
  const excludedContext=await browser.newContext({locale:'ru-RU',baseURL:origin}),excludedApi=apiFor(excludedContext);
  const admin=await adminContext.newPage(),page=await context.newPage();
  for(const p of [page,admin])p.on('pageerror',e=>errors.push(e.message));
  const adminApi=apiFor(adminContext),api=apiFor(context);
  let adminCsrf=(await adminApi('/setup','POST',{login:'peopleadmin',password,token:'browser-test-setup-key'})).csrf;
  adminCsrf=await activate(adminApi,adminCsrf);await adminApi('/admin/access/unlock','POST',{password},adminCsrf);
  const invitation=await adminApi('/invites','POST',{login:'companyowner'},adminCsrf);
  let csrf=(await api('/register','POST',{login:'companyowner',password,invite:invitation.url.split('/').at(-1)})).csrf;
  csrf=await activate(api,csrf);
  const excludedInvite=await adminApi('/invites','POST',{login:'excludedmember'},adminCsrf);
  let excludedCsrf=(await excludedApi('/register','POST',{login:'excludedmember',password,invite:excludedInvite.url.split('/').at(-1)})).csrf;
  excludedCsrf=await activate(excludedApi,excludedCsrf);
  const legacy=(await api('/social/families','POST',{name:'Существующая семья'},csrf)).id;
  const legacyNote=await api('/notes','POST',{title:'Прежние планы',body:'Семейные данные сохранены',space_id:legacy},csrf);
  await api('/chat/'+legacy+'/messages','POST',{body:'Прежняя переписка',client_id:'legacy-chat-123'},csrf);
  await page.goto('/');await appSection(page,'Люди и группы');
  await expect(page.getByRole('heading',{name:'Люди и группы',exact:true})).toBeVisible();
  for(const [template,name] of [['family','Новая семья'],['group','Шашлыки 2026'],['project','Ремонт квартиры'],['organization','Компания Альфа']]){
    const form=page.locator('[data-preference-key=create-space]');
    if(await form.getAttribute('open')===null)await form.locator(':scope>summary').click();
    await page.getByLabel('Тип пространства',{exact:true}).selectOption(template);
    await page.getByLabel('Название пространства',{exact:true}).fill(name);
    await page.getByRole('button',{name:'Создать пространство',exact:true}).click();
    await expect(page.locator('[data-social-name]').filter({has:page.locator('summary strong',{hasText:name})})).toHaveCount(1);
  }
  for(const name of ['Новая семья','Шашлыки 2026']){
    await (await openRow(page,name)).getByRole('button',{name:'Управление',exact:true}).click();
    const simple=page.getByRole('dialog');
    await simple.getByRole('button',{name:'Настройки',exact:true}).click();
    await expect(simple.getByLabel('Расширенный вид',{exact:true})).toHaveCount(0);
    await expect(simple.getByRole('button',{name:'Команды',exact:true})).toHaveCount(0);
    await simple.getByRole('button',{name:'Закрыть',exact:true}).click();
  }
  let spaces=(await api('/social')).spaces;
  expect(spaces).toHaveLength(5);expect(spaces.every(s=>s.kind==='family')).toBe(true);
  expect((await api('/me')).role).toBe('member');
  await expect((await openRow(page,'Компания Альфа')).locator(':scope>summary')).toContainText('Организация');
  await page.reload();await expect(page.locator('[data-social-name]')).toHaveCount(5);
  for(const width of [1280,390,320]){
    await page.setViewportSize({width,height:1000});
    await openRow(page,'Компания Альфа');
    for(const scheme of ['light','dark']){
      await page.evaluate(scheme=>document.documentElement.dataset.scheme=scheme,scheme);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:`artifacts/people-groups-${width}-${scheme}.png`,fullPage:true});
    }
  }
  await page.setViewportSize({width:1280,height:1000});
  const organization=spaces.find(s=>s.template==='organization');
  await (await openRow(page,'Компания Альфа')).getByRole('button',{name:'＋ Участник',exact:true}).click();
  await page.getByLabel('Логин участника').fill('peopleadmin');
  await page.getByRole('button',{name:'Отправить приглашение',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await admin.goto('/');await appSection(admin,'Люди и группы');
  await expect(admin.locator('.social-request')).toContainText('Пространство «Компания Альфа»');
  await admin.getByRole('button',{name:'Принять',exact:true}).click();
  await expect(admin.locator('[data-social-name="Компания Альфа"]')).toHaveCount(1);
  const excludedSpaceInvite=await api(`/social/spaces/${organization.id}/invites`,'POST',{login:'excludedmember'},csrf);
  await excludedApi(`/social/requests/${excludedSpaceInvite.id}/accept`,'POST',undefined,excludedCsrf);
  const memberNote=await excludedApi('/notes','POST',{title:'Страница участника',space_id:organization.id},excludedCsrf);
  expect(memberNote.manage).toBe(false);
  const memberPage=await excludedContext.newPage();memberPage.on('pageerror',e=>errors.push(e.message));
  await memberPage.goto('/page/'+memberNote.id);await expect(memberPage.locator('#page-title')).toBeEnabled();
  await memberPage.locator('#page-participants').click();
  const memberAccess=memberPage.getByRole('dialog',{name:'Доступ к странице'});
  await expect(memberAccess.locator('.note-access-invite')).toHaveCount(0);
  await expect(memberAccess.locator('[data-access-publish]')).toBeHidden();
  await expect(memberAccess.locator('[data-note-audience]')).toHaveCount(0);
  await memberAccess.getByRole('button',{name:'Закрыть',exact:true}).click();
  await appSection(memberPage,'Люди и группы');
  const memberRow=await openRow(memberPage,'Компания Альфа');
  await expect(memberRow.getByRole('button',{name:'＋ Участник',exact:true})).toHaveCount(0);
  await memberRow.getByRole('button',{name:'Участники и команды',exact:true}).click();
  await expect(memberPage.getByRole('dialog').getByRole('button',{name:'Настройки',exact:true})).toHaveCount(0);
  await expect(memberPage.getByRole('dialog').locator('[data-space-member-role]')).toHaveCount(0);
  await memberPage.close();
  await page.getByRole('button',{name:'Обновить приглашения',exact:true}).click();
  await (await openRow(page,'Компания Альфа')).getByRole('button',{name:'Управление',exact:true}).click();
  const management=page.getByRole('dialog');
  await management.getByRole('button',{name:'Команды',exact:true}).click();
  for(const width of [1440,1280,390,320]){
    await page.setViewportSize({width,height:1000});
    const overlay=await page.locator('.space-management-dialog').boundingBox();
    const modal=await management.boundingBox();
    expect(overlay.x).toBe(0);expect(overlay.y).toBe(0);expect(overlay.width).toBe(width);expect(overlay.height).toBe(1000);
    expect(Math.abs(modal.x+modal.width/2-width/2)).toBeLessThan(2);
    const input=await management.getByLabel('Название команды',{exact:true}).boundingBox();
    const button=await management.getByRole('button',{name:'Создать команду',exact:true}).boundingBox();
    expect(input.width).toBeGreaterThan(150);
    if(width>480)expect(input.x+input.width).toBeLessThanOrEqual(button.x);
    else expect(input.y+input.height).toBeLessThanOrEqual(button.y);
    for(const scheme of ['light','dark']){
      await page.evaluate(value=>document.documentElement.dataset.scheme=value,scheme);
      await page.screenshot({path:`artifacts/team-create-${width}-${scheme}.png`,fullPage:true});
    }
  }
  await page.setViewportSize({width:1280,height:1000});
  await management.getByLabel('Название команды',{exact:true}).fill('Разработка');
  await management.getByRole('button',{name:'Создать команду',exact:true}).click();
  const team=management.locator('[data-edit-team]');
  const adminId=(await adminApi('/me')).id;
  await team.locator(`[value="${adminId}"]`).check();
  await team.getByRole('button',{name:'Сохранить команду',exact:true}).click();
  await expect.poll(async()=>Object.values((await api(`/social/spaces/${organization.id}/management`)).teams)[0]?.members).toEqual([adminId]);
  for(const width of [1280,390,320]){
    await page.setViewportSize({width,height:1000});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`artifacts/space-teams-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:1280,height:1000});
  await management.getByRole('button',{name:'Участники',exact:true}).click();
  await management.getByLabel('Выбрать excludedmember',{exact:true}).check();
  await management.getByRole('button',{name:'Добавить в команду',exact:true}).click();
  const excludedId=(await excludedApi('/me')).id;
  await expect.poll(async()=>Object.values((await api(`/social/spaces/${organization.id}/management`)).teams)[0].members.includes(excludedId)).toBe(true);
  await management.getByRole('button',{name:'Исключить из команды',exact:true}).click();
  await expect.poll(async()=>Object.values((await api(`/social/spaces/${organization.id}/management`)).teams)[0].members.includes(excludedId)).toBe(false);
  await management.getByRole('button',{name:'Закрыть',exact:true}).click();
  await (await openRow(page,'Компания Альфа')).getByRole('button',{name:'Общие заметки',exact:true}).click();
  await expect(page.getByLabel('Пространство заметок')).toHaveValue(organization.id);
  await expect(page.getByLabel('Пространство заметок').locator('option:checked')).toContainText('Организация');
  await page.getByRole('button',{name:'Создать страницу',exact:true}).click();
  await page.getByLabel('Название заметки').fill('План компании');
  await page.getByRole('textbox',{name:'Содержимое страницы'}).fill('Общие рабочие планы');
  await expect(page.locator('#page-save-status')).toContainText('Сохранено');
  await expect.poll(async()=>(await adminApi('/notes')).some(n=>n.title==='План компании')).toBe(true);
  const companyNote=(await api('/notes')).find(n=>n.title==='План компании');
  await page.locator('#page-participants').click();
  const access=page.getByRole('dialog',{name:'Доступ к странице'});
  await access.getByText('Команды и доступ к ветке',{exact:true}).click();
  await access.getByLabel('Доступ к ветке',{exact:true}).selectOption('restricted');
  await access.getByLabel('Доступ команды Разработка',{exact:true}).selectOption('viewer');
  await access.getByRole('button',{name:'Сохранить доступ к ветке',exact:true}).click();
  await expect(access.locator('[data-audience-preview]')).toContainText('Изменения доступа');
  await access.getByRole('button',{name:'Применить изменения',exact:true}).click();
  await expect.poll(async()=>(await adminApi('/notes/'+companyNote.id)).role).toBe('viewer');
  await expect.poll(async()=>(await excludedContext.request.get(origin+'/api/notes/'+companyNote.id)).status()).toBe(404);
  await expect(access.locator('.note-access-source')).toContainText(['Команда «Разработка»']);
  for(const width of [1280,390,320]){
    await page.setViewportSize({width,height:1000});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`artifacts/space-access-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:1280,height:1000});
  await access.getByRole('button',{name:'Закрыть',exact:true}).click();
  await appSection(page,'Люди и группы');
  await (await openRow(page,'Компания Альфа')).getByRole('button',{name:'Управление',exact:true}).click();
  await management.getByLabel('Роль peopleadmin',{exact:true}).selectOption('admin');
  await expect.poll(async()=>(await adminApi('/notes/'+companyNote.id)).manage).toBe(true);
  await expect(management.getByLabel('Роль peopleadmin',{exact:true})).toBeEnabled();
  await management.getByLabel('Роль peopleadmin',{exact:true}).selectOption('member');
  await expect.poll(async()=>(await adminApi('/notes/'+companyNote.id)).role).toBe('viewer');
  await management.getByRole('button',{name:'Настройки',exact:true}).click();
  await management.getByLabel('Расширенный вид',{exact:true}).uncheck();
  await expect(management.getByRole('button',{name:'Команды',exact:true})).toHaveCount(0);
  expect((await excludedContext.request.get(origin+'/api/notes/'+companyNote.id)).status()).toBe(404);
  await management.getByLabel('Расширенный вид',{exact:true}).check();
  await expect(management.getByRole('button',{name:'Команды',exact:true})).toHaveCount(1);
  await management.getByLabel('Новый владелец',{exact:true}).selectOption((await excludedApi('/me')).id);
  page.once('dialog',dialog=>dialog.accept());
  await management.getByRole('button',{name:'Передать владение',exact:true}).click();
  await expect(management.locator('[data-transfer-space]')).toHaveCount(0);
  expect((await api(`/social/spaces/${organization.id}/management`)).role).toBe('admin');
  expect((await excludedApi(`/social/spaces/${organization.id}/management`)).role).toBe('owner');
  await management.getByRole('button',{name:'Закрыть',exact:true}).click();

  await (await openRow(page,'Компания Альфа')).getByRole('button',{name:'Чат',exact:true}).click();
  await page.getByLabel('Сообщение',{exact:true}).fill('Встреча команды завтра');
  await page.getByRole('button',{name:'Отправить',exact:true}).click();
  await appSection(admin,'Сообщения');await admin.locator(`[data-room="${organization.id}"]`).click();
  await expect(admin.locator('.chat-messages')).toContainText('Встреча команды завтра');
  await expect.poll(async()=>(await api('/notes/'+legacyNote.id)).body).toBe('Семейные данные сохранены');
  expect((await api('/chat/'+legacy+'/messages')).items[0].body).toBe('Прежняя переписка');
  await adminApi(`/social/spaces/${organization.id}/members/${adminId}`,'DELETE',undefined,adminCsrf);
  await expect(admin.getByLabel('Сообщение',{exact:true})).toBeDisabled({timeout:10000});
  expect((await api('/social')).spaces.find(s=>s.id===organization.id).members).toHaveLength(2);
  // Lifecycle: real invitation, explicit offboarding, audit and reversible deletion.
  const lifecycleSpace=(await api('/social/spaces','POST',{name:'Проверка жизненного цикла',template:'project'},csrf)).id;
  const lifecycleNote=await api('/notes','POST',{space_id:lifecycleSpace,title:'Сохранить до срока'},csrf);
  await appSection(page,'Люди и группы');await page.reload();
  await (await openRow(page,'Проверка жизненного цикла')).getByRole('button',{name:'＋ Участник',exact:true}).click();
  await page.getByRole('button',{name:'Пригласить по ссылке',exact:true}).click();
  await page.getByRole('button',{name:'Создать одноразовую ссылку',exact:true}).click();
  const joinUrl=await page.getByLabel('Ссылка приглашения',{exact:true}).inputValue();
  const newcomer=await excludedContext.newPage();newcomer.on('pageerror',e=>errors.push(e.message));
  await newcomer.goto(joinUrl);await newcomer.getByRole('button',{name:'Принять приглашение',exact:true}).click();
  await expect(newcomer.getByRole('heading',{name:'Люди и группы',exact:true})).toBeVisible();
  await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.reload();
  await (await openRow(page,'Проверка жизненного цикла')).getByRole('button',{name:'Удалить участника excludedmember',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('Созданные заметки останутся');
  await page.getByRole('button',{name:'Удалить участника',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await (await openRow(page,'Проверка жизненного цикла')).getByRole('button',{name:'Управление',exact:true}).click();
  await page.getByRole('button',{name:'Журнал',exact:true}).click();
  await expect(page.locator('.space-audit-table')).toContainText('Удалён участник');
  await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  page.once('dialog',dialog=>dialog.accept());
  await (await openRow(page,'Проверка жизненного цикла')).getByRole('button',{name:'Удалить пространство',exact:true}).click();
  await expect(page.locator('.space-deletion-screen')).toContainText('Пространство ожидает удаления');
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:1000});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`artifacts/workspace-deletion-${width}.png`,fullPage:true});
  }
  expect((await context.request.get(origin+'/api/notes/'+lifecycleNote.id)).status()).toBe(404);
  await page.getByRole('button',{name:'Отменить удаление',exact:true}).click();
  await expect(page.locator('.space-deletion-screen')).toHaveCount(0);
  expect((await api('/notes/'+lifecycleNote.id)).title).toBe('Сохранить до срока');
  await newcomer.close();
  await api(`/social/spaces/${lifecycleSpace}`,'DELETE',undefined,csrf);
  await page.setViewportSize({width:1280,height:1000});
  await page.reload();await appSection(page,'Заметки');
  await page.getByLabel('Пространство заметок',{exact:true}).selectOption(lifecycleSpace);
  await expect(page.locator('#notion-page .space-deletion-screen')).toContainText('Пространство ожидает удаления');
  await expect(page.getByRole('button',{name:'Создать страницу',exact:true})).toBeDisabled();
  await appSection(page,'Сообщения');await page.locator(`[data-room="${lifecycleSpace}"]`).click();
  await expect(page.locator('#chat-main .space-deletion-screen')).toContainText('Пространство ожидает удаления');
  await page.getByRole('button',{name:'Отменить удаление',exact:true}).click();
  await expect(page.getByLabel('Сообщение',{exact:true})).toBeEnabled();
  await appSection(page,'Люди и группы');
  const registrationLink=await api(`/social/spaces/${lifecycleSpace}/invite-links`,'POST',undefined,csrf);
  const freshContext=await browser.newContext({locale:'ru-RU',baseURL:origin}),freshPage=await freshContext.newPage();
  freshPage.on('pageerror',e=>errors.push(e.message));await freshPage.goto(registrationLink.url);
  await freshPage.locator('input[name=login]').fill('link-new-worker');
  await freshPage.locator('input[name=password]').fill(password);
  await freshPage.locator('#auth-form').getByRole('button',{name:'Принять приглашение',exact:true}).click();
  await freshPage.getByRole('button',{name:'Настроить позже',exact:true}).click();
  await appSection(freshPage,'Люди и группы');
  await expect(freshPage.locator('[data-social-name="Проверка жизненного цикла"]')).toHaveCount(1);
  await freshContext.close();
  // Twenty-five people exercise the same controls without adding real accounts.
  const own=(await api('/me')),fakeMembers=[{...own,display_name:'companyowner',space_role:'owner',joined_at:Math.floor(Date.now()/1000)}];
  for(const [name,count] of [['dev',10],['qa',8],['cs',6]])for(let i=1;i<=count;i++)fakeMembers.push({id:String(fakeMembers.length).padStart(32,'0'),login:`${name}-${i}`,display_name:`${name}-${i}`,space_role:'member',joined_at:Math.floor(Date.now()/1000)});
  let fixture={version:1,revision:100,advanced:true,advanced_available:true,admins:[],teams:{},owner_id:own.id,role:'owner',manage:true,members:fakeMembers};
  for(const [i,name] of ['dev','qa','cs'].entries())fixture.teams[`tablegrp0000000${i}`]={name,members:fakeMembers.filter(u=>u.login.startsWith(name+'-')).map(u=>u.id)};
  fixture.teams['tablegrp00000003']={name:'Dev Team',members:[]};
  const social=await api('/social');
  await page.route('**/api/social',route=>route.fulfill({json:{...social,spaces:social.spaces.map(s=>s.id===organization.id?{...s,members:fixture.members,teams:fixture.teams,advanced:true,advanced_available:true,owner:true,manage:true,role:'owner',owner_user_id:own.id}:s)}}));
  await page.route(`**/api/social/spaces/${organization.id}/management`,async route=>{
    if(route.request().method()==='PUT')fixture={...fixture,...route.request().postDataJSON(),revision:fixture.revision+1};
    await route.fulfill({json:fixture});
  });
  await appSection(page,'Люди и группы');
  await page.getByRole('button',{name:'Обновить приглашения',exact:true}).click();
  const largeRow=await openRow(page,'Компания Альфа'),people=largeRow.locator('[data-space-people]');
  await expect(people.locator('[data-people-row]')).toHaveCount(10);
  await expect(people.locator('[data-people-count]')).toHaveText('1–10 из 25');
  await people.getByLabel('Фильтр по команде',{exact:true}).selectOption(Object.keys(fixture.teams)[1]);
  await expect(people.locator('[data-people-row]')).toHaveCount(8);
  await people.getByLabel('Фильтр по команде',{exact:true}).selectOption('');
  await people.getByLabel('Поиск участников',{exact:true}).fill('qa-8');
  await expect(people.locator('[data-people-row]')).toHaveCount(1);
  await people.getByLabel('Поиск участников',{exact:true}).fill('');
  await people.getByRole('button',{name:'Следующая страница участников',exact:true}).click();
  await expect(people.locator('[data-people-count]')).toHaveText('11–20 из 25');
  await largeRow.getByRole('button',{name:'Управление',exact:true}).click();
  await management.getByLabel('Фильтр по роли',{exact:true}).selectOption('owner');
  await expect(management.locator('[data-people-row]')).toHaveCount(1);
  await management.getByLabel('Фильтр по роли',{exact:true}).selectOption('');
  for(const width of [1440,390,320]){
    await page.setViewportSize({width,height:1000});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    for(const scheme of ['light','dark']){
      await page.evaluate(value=>document.documentElement.dataset.scheme=value,scheme);
      await page.screenshot({path:`artifacts/people-table-${width}-${scheme}.png`,fullPage:true});
    }
  }
  await page.setViewportSize({width:1440,height:1000});
  await management.getByRole('button',{name:'Команды',exact:true}).click();
  await expect(management.locator('[data-team-row]')).toHaveCount(4);
  await expect(management.locator('.space-avatar-more').first()).toBeVisible();
  await management.getByLabel('Поиск команд',{exact:true}).fill('qa');
  await expect(management.locator('[data-team-row]')).toHaveCount(1);
  await management.getByLabel('Поиск команд',{exact:true}).fill('');
  await page.screenshot({path:'artifacts/teams-table-1440.png',fullPage:true});
  await management.getByLabel('Название команды',{exact:true}).fill('UI-проверка');
  await management.getByRole('button',{name:'Создать команду',exact:true}).click();
  await expect(management.locator('[data-edit-team]')).toHaveCount(1);
  await management.getByLabel('Поиск участников',{exact:true}).fill('dev-1');
  await management.getByLabel('Выбрать dev-1',{exact:true}).check();
  await management.getByLabel('Поиск участников',{exact:true}).fill('');
  await management.getByRole('button',{name:'Следующая страница участников',exact:true}).click();
  await management.getByRole('button',{name:'Следующая страница участников',exact:true}).click();
  await management.getByLabel('Выбрать qa-8',{exact:true}).check();
  await management.getByLabel('Поиск участников',{exact:true}).fill('dev-1');
  await expect(management.getByLabel('Выбрать dev-1',{exact:true})).toBeChecked();
  await management.getByRole('button',{name:'Сохранить команду',exact:true}).click();
  await expect.poll(()=>Object.values(fixture.teams).find(t=>t.name==='UI-проверка')?.members.length).toBe(2);
  await management.getByRole('button',{name:'Закрыть',exact:true}).click();
  expect(errors).toEqual([]);await context.close();await adminContext.close();await excludedContext.close();
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
  console.log('People and groups passed: four templates, 25-person tables, search/filters/pagination, team selection across pages, restricted notes, admins, ownership transfer, live chat/revocation, legacy data, 320–1440px light/dark.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
