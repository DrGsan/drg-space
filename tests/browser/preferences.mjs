import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

const releaseVersion=(await readFile(new URL('../../VERSION',import.meta.url),'utf8')).trim();

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page,browser){
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.setViewportSize({width:1440,height:1000});
 await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
 const password='Preferences-password-123',context=page.context();
 async function post(ctx,path,data,csrf=''){
  const response=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
 }
 async function activate(ctx,csrf){const enrollment=await post(ctx,'/2fa/enroll',{password},csrf);await post(ctx,'/2fa/confirm',{code:totp(enrollment.secret)},csrf);return (await post(ctx,'/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;}
 let csrf=(await post(context,'/setup',{login:'preferencesowner',password,token:'browser-test-setup-key'})).csrf;csrf=await activate(context,csrf);
 await post(context,'/admin/access/unlock',{password},csrf);
 const invite=await post(context,'/invites',{login:'preferencesmember'},csrf),peer=await browser.newContext({locale:'ru-RU',baseURL:origin});
 try{
  let peerCsrf=(await post(peer,'/register',{login:'preferencesmember',password,invite:invite.url.split('/').at(-1)})).csrf;peerCsrf=await activate(peer,peerCsrf);
  const peerMe=await (await peer.request.get(origin+'/api/me')).json();
  await post(context,'/admin/access/lock',{},csrf);
  const section=async view=>page.locator('.sidebar [data-view='+view+']').click();
  const row=id=>page.locator('[data-preference-key="'+id+'"]');
  const open=async id=>{if(await row(id).getAttribute('open')===null)await row(id).locator(':scope > summary').click();};
  const close=async id=>{if(await row(id).getAttribute('open')!==null)await row(id).locator(':scope > summary').click();};
  const overflow=async()=>expect(await page.locator('#view-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
  await page.goto('/');await section('settings');await expect(page.locator('.preference-row')).toHaveCount(7);
  await expect(page.locator('.preference-row[open]')).toHaveCount(0);await expect(page.locator('.preference-group-title')).toHaveText(['Личное','Аккаунт','Приложение']);
  await open('about');await expect(page.locator('[data-app-version]')).toHaveText(releaseVersion);
  const release=(await (await context.request.get(origin+'/api/me')).json()).app_release;expect(release.version).toBe(releaseVersion);expect(release.changes.length).toBeGreaterThan(0);
  await expect(page.locator('[data-release-changes] li')).toHaveText(release.changes);await expect(page.locator('[data-release-date]')).toContainText(release.date.slice(0,4));await expect(page.locator('[data-app-release]')).not.toContainText('База:');
  const corner=page.locator('.sidebar-bottom [data-sidebar-version]');await expect(corner).toHaveText('v'+releaseVersion);await expect(corner).toBeVisible();
  const profileGeometry=async()=>{const boxes=await page.evaluate(()=>{const version=document.querySelector('[data-sidebar-version]');const rects=()=>[...document.querySelectorAll('.sidebar .user,.sidebar .user .avatar,.sidebar .user strong,.sidebar .user small,.sidebar #logout')].map(el=>el.getBoundingClientRect().toJSON());version.style.display='none';const before=rects();version.style.removeProperty('display');return {before,after:rects()};});expect(boxes.after).toEqual(boxes.before);};
  await profileGeometry();
  const cornerBox=await corner.boundingBox(),userBox=await page.locator('.sidebar .user').boundingBox();expect(cornerBox.y).toBeGreaterThanOrEqual(userBox.y+userBox.height);expect(cornerBox.y+cornerBox.height).toBeLessThanOrEqual(1000);
  await page.setViewportSize({width:1440,height:600});await expect(corner).toBeVisible();await profileGeometry();expect((await corner.boundingBox()).y+(await corner.boundingBox()).height).toBeLessThanOrEqual(600);await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'artifacts/release-settings-desktop.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});await overflow();await expect(page.locator('[data-app-version]')).toBeVisible();await expect(corner).toBeHidden();await expect(page.locator('[data-app-release]')).toBeVisible();
  await page.screenshot({path:'artifacts/release-settings-mobile.png',animations:'disabled'});
  await page.setViewportSize({width:1440,height:1000});await close('about');
  expect((await row('profile').boundingBox()).y).toBeLessThanOrEqual(110);
  expect((await page.locator('.preferences-heading').boundingBox()).height).toBeLessThanOrEqual(64);
  await expect(page.locator('.topbar')).toBeHidden();await expect(page.locator('.preferences-heading #open-inbox')).toBeVisible();
  await page.locator('#open-inbox').click();await expect(page.locator('.modal-overlay')).toContainText('Уведомления');await page.locator('.modal-close').click();
  await page.screenshot({path:'artifacts/desktop-settings-menu.png',animations:'disabled'});await overflow();
  await page.setViewportSize({width:390,height:844});await overflow();await expect(page.locator('.preferences-heading #open-inbox')).toBeVisible();
  await page.setViewportSize({width:1440,height:1000});
  await row('profile').locator('summary').focus();await page.keyboard.press('Enter');await expect(page.locator('[data-profile-form]')).toBeVisible();
  await page.locator('[data-profile-form] [name=display_name]').fill('Александр QA');await page.locator('[data-profile-form] button[type=submit]').click();
  await expect(page.locator('[data-profile-status]')).toHaveText('Имя сохранено');await expect(page.locator('.sidebar .user strong')).toHaveText('Александр QA');
  await page.screenshot({path:'artifacts/desktop-settings-profile.png',animations:'disabled'});
  await close('profile');await open('modules');await page.locator('#modules-form [name=expenses]').uncheck();await page.locator('#modules-form button[type=submit]').click();
  await expect(row('modules')).toHaveAttribute('open','');await expect(page.locator('.sidebar [data-view=expenses]')).toHaveCount(0);
  await open('security');await page.locator('#open-security').click();await expect(page.locator('[data-change-password]')).toBeVisible();
  await page.locator('[data-back-settings]').click();await expect(row('security')).toHaveAttribute('open','');await expect(row('profile')).not.toHaveAttribute('open','');
  await close('modules');await close('security');await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');
  await page.screenshot({path:'artifacts/desktop-settings-menu-dark.png',animations:'disabled'});await page.emulateMedia({colorScheme:'light'});
  let archiveFailure=true;
  const archiveUsage={configured:true,available:true,total_bytes:3*1073741824,objects_bytes:3*1073741824-1024,database_bytes:1024,media_bytes:2*1073741824,thumbnail_bytes:1024,message_bytes:1073741824-2048,avatar_bytes:0,other_bytes:0,objects:12,checked_at:123};
  await page.route('**/api/family-photos/admin/storage',route=>archiveFailure?route.fulfill({status:503,json:{detail:'Фотоархив недоступен'}}):route.fulfill({json:archiveUsage}));
  let photoReads=0,photoFailure=false,photoSave;
  const photoUser={id:peerMe.id,login:'preferencesmember',profile:{display_name:'Николай',login:'preferencesmember'},role:'member',enabled:false,groups:[]};
  await page.route('**/api/family-photos/admin',route=>{
    photoReads++;
    return photoFailure?route.fulfill({status:503,json:{detail:'Фотоархив временно недоступен'}}):route.fulfill({json:{users:[photoUser,{id:'other',login:'other-relative',role:'member',enabled:false,groups:[]}],groups:[{id:'-1001',title:'Семейный архив'}]}});
  });
  await page.route('**/api/family-photos/admin/users/*',route=>{
    photoSave=route.request().postDataJSON();photoUser.enabled=photoSave.enabled;photoUser.groups=photoSave.enabled?photoSave.groups:[];
    return route.fulfill({json:{ok:true,enabled:photoUser.enabled,groups:photoUser.groups}});
  });
  await section('admin');await page.locator('[data-admin-unlock] [name=password]').fill(password);await page.locator('[data-admin-unlock] [type=submit]').click();await expect(page.locator('[data-admin-unlock]')).toHaveCount(0);await expect(page.locator('.preference-row')).toHaveCount(5);await expect(page.locator('.preference-row[open]')).toHaveCount(0);
  await expect(page.locator('#view-content > .section-heading')).toHaveCount(1);
  expect(await page.locator('#view-content > .section-heading h1').evaluate(el=>getComputedStyle(el).fontSize)).toBe('24px');
  await expect(row('accounts').locator('[data-preference-status]')).toHaveText('Аккаунтов: 2');await expect(row('registration').locator('[data-preference-status]')).toHaveText('Закрыта');
  await expect(page.locator('#toast')).not.toHaveClass(/show/);
  await page.screenshot({path:'artifacts/desktop-admin-menu.png',animations:'disabled'});
  expect(photoReads).toBe(0);await expect(page.locator('.sidebar [data-view=family_photos]')).toHaveCount(0);
  await open('family-photos');const photoRow=page.locator('[data-fp-user="'+peerMe.id+'"]'),photoForm=photoRow.locator('[data-fp-admin-form]');
  await expect(page.locator('[data-fp-user]')).toHaveCount(2);await expect(photoRow).not.toHaveAttribute('open','');expect(photoReads).toBe(1);
  await expect(photoRow.locator('summary strong')).toHaveText('Николай');await expect(photoRow.locator('[data-fp-user-state]')).toHaveText('Нет доступа');
  await photoRow.locator('summary').focus();await page.keyboard.press('Enter');await expect(photoForm).toBeVisible();
  await expect(photoForm.locator('[name=group]')).toBeDisabled();
  await photoForm.locator('[name=enabled]').check();await photoForm.locator('button').click();
  await expect(photoForm.locator('[data-fp-grant-status]')).toHaveText('Выберите хотя бы одну группу.');expect(photoSave).toBeUndefined();
  await photoForm.locator('[name=group]').check();
  await close('family-photos');await open('family-photos');expect(photoReads).toBe(1);await expect(photoForm.locator('[name=password]')).toHaveCount(0);
  await photoForm.locator('button').click();await expect(photoForm.locator('[data-fp-grant-status]')).toHaveText('Доступ сохранён. Модуль включён.');
  expect(photoSave).toEqual({enabled:true,groups:['-1001']});await expect(row('family-photos').locator('[data-preference-status]')).toHaveText('Доступ: 1');
  await expect(photoRow.locator('[data-fp-user-state]')).toHaveText('Включён');await expect(photoRow.locator('[data-fp-user-groups]')).toContainText('Семейный архив');
  const otherRow=page.locator('[data-fp-user=other]');await otherRow.locator('summary').click();
  await expect(otherRow.locator('[name=enabled]')).not.toBeChecked();await expect(otherRow.locator('[data-fp-grant-status]')).toBeEmpty();await otherRow.locator('summary').click();
  await expect(photoForm.locator('[name=enabled]')).toBeChecked();
  for(const scheme of ['light','dark']){
    await page.emulateMedia({colorScheme:scheme});
    for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});await overflow();await photoRow.scrollIntoViewIfNeeded();await page.screenshot({path:'artifacts/admin-family-photos-'+scheme+'-'+width+'.png',animations:'disabled'});}
  }
  await page.emulateMedia({colorScheme:'light'});
  await photoForm.locator('[name=enabled]').uncheck();await photoForm.locator('button').click();
  await expect(photoForm.locator('[data-fp-grant-status]')).toHaveText('Доступ отозван. Модуль отключён.');await expect(photoForm.locator('[name=group]')).not.toBeChecked();
  await expect(photoRow.locator('[data-fp-user-state]')).toHaveText('Нет доступа');await expect(otherRow.locator('[name=enabled]')).not.toBeChecked();
  await expect(photoForm.locator('[name=password]')).toHaveCount(0);await expect(row('family-photos').locator('[data-preference-status]')).toHaveText('Доступ: 0');
  await page.setViewportSize({width:1440,height:1000});await section('settings');photoFailure=true;await section('admin');
  await expect(page.locator('[data-fp-admin-retry]')).toBeVisible();await expect(row('family-photos').locator('[data-preference-status]')).toHaveText('Недоступно');
  photoFailure=false;await page.locator('[data-fp-admin-retry]').click();await expect(photoRow).toBeVisible();await expect(photoRow).not.toHaveAttribute('open','');await close('family-photos');
  await open('accounts');await expect(page.locator('[data-admin-user]')).toHaveCount(2);await expect(page.locator('[data-admin-user][open]')).toHaveCount(0);
  await expect(page.locator('[data-archive-storage-value]')).toHaveText('Недоступно');
  await expect(page.locator('[data-storage-total-label]')).toHaveText('на сайте · без фотоархива');
  archiveFailure=false;await page.locator('[data-archive-storage-retry]').click();
  await expect(page.locator('[data-archive-storage-value]')).toHaveText('3.00 Гб');
  await expect(page.locator('[data-storage-total-label]')).toHaveText('всего учтено · сайт и фотоархив');
  await expect(page.locator('[data-archive-storage-detail]')).toContainText('превью');
  await expect(page.locator('.admin-storage-subset')).toContainText('Уже входят в базу');
  expect(await page.locator('[data-storage-total]').textContent()).not.toContain('NaN');
  await page.screenshot({path:'artifacts/admin-storage-summary.png',animations:'disabled'});
  for(const width of [390,320]){await page.setViewportSize({width,height:844});await overflow();expect(await page.locator('.admin-storage-breakdown').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();}
  await page.screenshot({path:'artifacts/admin-storage-narrow.png',animations:'disabled',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  const account=page.locator('[data-admin-user="'+peerMe.id+'"]'),lastSeen=account.locator('[data-person-presence]');
  await expect(lastSeen).toHaveText('Нет данных об активности');
  const accountNode=await account.elementHandle();await post(peer,'/presence',{},peerCsrf);
  await expect(lastSeen).toHaveText('В сети',{timeout:12000});expect(await accountNode.evaluate(el=>el.isConnected)).toBe(true);
  await account.locator('[data-open-admin-user]').click();
  await page.locator('.admin-account-details [data-admin-profile]').click();const dialog=()=>page.locator('.modal-overlay').last();
  await dialog().locator('[name=display_name]').fill('Бабушка Нина');await dialog().locator('button[type=submit]').click();await expect(dialog()).toHaveCount(0);
  await expect(account).toBeVisible();await expect(account.locator('.admin-account-copy strong')).toHaveText('Бабушка Нина');
  await account.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-quota]').click();await dialog().locator('[name=gb]').fill('2');await dialog().getByRole('button',{name:'Сохранить квоту'}).click();await expect(dialog()).toHaveCount(0);
  const users=await (await context.request.get(origin+'/api/admin/users')).json();expect(users.users.find(u=>u.id===peerMe.id).quota.limit_bytes).toBe(2*1073741824);await expect(account).toBeVisible();
  await page.locator('[data-create-account]').click();await dialog().locator('[name=login]').fill('grandma');await dialog().locator('[name=password]').fill(password);await dialog().locator('[name=repeat_password]').fill(password);await dialog().locator('[name=display_name]').fill('Нина');await dialog().locator('button[type=submit]').click();await expect(dialog()).toHaveCount(0);
  await expect(row('accounts').locator('[data-preference-status]')).toHaveText('Аккаунтов: 3');await expect(page.locator('[data-admin-user]')).toHaveCount(3);
  await account.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-friends]').click();await dialog().locator('[data-friend-search]').fill('grandma');
  await dialog().locator('.admin-friend-option:visible input').check();await dialog().getByRole('button',{name:'Сохранить друзей'}).click();await expect(dialog()).toHaveCount(0);
  const relations=await (await peer.request.get(origin+'/api/social')).json();expect(relations.spaces.filter(space=>space.kind==='friends')).toHaveLength(1);
  await account.locator('[data-open-admin-user]').click();await page.locator('.admin-account-details [data-admin-friends]').click();await dialog().locator('[data-friend-search]').fill('grandma');await expect(dialog().locator('.admin-friend-option:visible input')).toBeChecked();await dialog().locator('.modal-close').click();

  await expect(page.locator('#toast')).not.toHaveClass(/show/);
  await page.screenshot({path:'artifacts/desktop-admin-accounts.png',animations:'disabled'});await overflow();
  await close('accounts');await open('registration');await page.locator('[data-registration-toggle]').click();await expect(dialog().locator('[name=admin_password]')).toHaveCount(0);await dialog().locator('.modal-close').click();await close('registration');
  await open('backups');await page.locator('[data-export-backup]').click();await expect(dialog().locator('[name=backup_password]')).toBeVisible();await dialog().locator('.modal-close').click();
  await page.locator('[data-restore-backup]').click();await expect(dialog().locator('[name=confirmation]')).toHaveAttribute('pattern','RESTORE');await expect(dialog().locator('[name=admin_password]')).toHaveCount(0);await dialog().locator('.modal-close').click();await close('backups');
  await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');await page.screenshot({path:'artifacts/desktop-admin-menu-dark.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});await open('accounts');await overflow();await page.screenshot({path:'artifacts/admin-settings-narrow.png',animations:'disabled',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});await section('social');
  await expect(page.locator('.social-heading')).toBeVisible();await expect(page.locator('.topbar')).toBeHidden();
  expect((await page.locator('.social-heading').boundingBox()).height).toBeLessThanOrEqual(64);
  expect((await page.locator('.social-sections .preference-row').first().boundingBox()).y).toBeLessThanOrEqual(110);
  await open('create-space');await page.getByLabel('Тип пространства',{exact:true}).selectOption('family');await page.getByLabel('Название пространства',{exact:true}).fill('Семейный круг');await page.locator('#create-space button').click();
  let family=page.locator('[data-social-name="Семейный круг"]');await expect(family).toBeVisible();await expect(page.locator('#open-inbox')).toHaveCount(1);
  await family.locator(':scope > summary').click();await family.locator('[data-rename]').click();await page.locator('#rename-space input').fill('Близкие');await page.locator('#rename-space button').click();
  family=page.locator('[data-social-name="Близкие"]');await expect(family).toHaveAttribute('open','');
  await family.locator('[data-invite]').click();await page.locator('#invite-space input').fill('preferencesmember');await page.locator('#invite-space button').click();await expect(page.locator('#invite-space')).toHaveCount(0);
  const requests=await (await peer.request.get(origin+'/api/social')).json();const familyInvite=requests.requests.find(r=>r.kind==='family'&&r.space_name==='Близкие');
  expect(familyInvite).toBeTruthy();await post(peer,'/social/requests/'+familyInvite.id+'/accept',{},peerCsrf);
  await page.locator('#refresh-social').click();await expect(page.locator('[data-social-name="Близкие"] [data-people-row]')).toHaveCount(2);await expect(page.locator('#open-inbox')).toHaveCount(1);
  await open('add-friend');await page.getByLabel('Логин друга').fill('preferencesmember');await page.locator('#add-friend button').click();
  await expect(page.locator('.social-request')).toContainText('Приглашение отправлено');
  const friends=await (await peer.request.get(origin+'/api/social')).json();await post(peer,'/social/requests/'+friends.requests.find(r=>r.kind==='friends').id+'/accept',{},peerCsrf);
  await page.locator('#refresh-social').click();await expect(page.locator('.social-person-icon')).toHaveCount(2);
  while(await page.locator('.social-sections .preference-row[open]').count())await page.locator('.social-sections .preference-row[open]').first().locator(':scope > summary').click();
  await page.screenshot({path:'artifacts/desktop-social-menu-dark.png',animations:'disabled'});await overflow();
  family=page.locator('[data-social-name="Близкие"]');await family.locator(':scope > summary').focus();await page.keyboard.press('Enter');await expect(family).toHaveAttribute('open','');
  await family.locator('[data-rename]').click();await expect(page.locator('#rename-space')).toBeVisible();await page.locator('.modal-close').click();
  await section('settings');await section('social');await expect(page.locator('[data-social-name="Близкие"]')).toHaveAttribute('open','');
  await page.locator('#open-inbox').click();await expect(page.locator('.modal-overlay')).toContainText('Уведомления');await page.locator('.modal-close').click();
  await page.emulateMedia({colorScheme:'light'});await page.setViewportSize({width:390,height:844});await overflow();
  await page.screenshot({path:'artifacts/social-menu-narrow.png',animations:'disabled'});
  const guest=await peer.newPage();await guest.goto('/');await guest.locator('.sidebar [data-view=settings]').click();await expect(guest.locator('.preference-row')).toHaveCount(7);await expect(guest.locator('.sidebar [data-view=admin]')).toHaveCount(0);
  expect((await peer.request.get(origin+'/api/admin/users')).status()).toBe(403);
  await guest.evaluate(uid=>sessionStorage.setItem('drg-workspace-view:'+uid,'admin'),peerMe.id);await guest.reload();await expect(guest.locator('.sidebar [data-view=settings]')).toHaveClass(/active/);await expect(guest.locator('[data-admin-user]')).toHaveCount(0);
  await page.bringToFront();await page.setViewportSize({width:1440,height:1000});await section('settings');
  await expect(page.locator('.sidebar [data-view=notes] small')).toHaveCount(0);
  const unread=page.locator('.sidebar [data-chat-unread]');await expect(unread).toBeHidden();
  const rooms=(await (await context.request.get(origin+'/api/chat/rooms')).json()).items;
  const direct=rooms.find(room=>room.kind==='friends'),group=rooms.find(room=>room.name==='Близкие');expect(direct&&group).toBeTruthy();
  await post(peer,'/chat/'+direct.id+'/messages',{body:'Первое непрочитанное',client_id:'nav-unread-1'},peerCsrf);
  const lastDirect=await post(peer,'/chat/'+direct.id+'/messages',{body:'Второе непрочитанное',client_id:'nav-unread-2'},peerCsrf);
  const lastGroup=await post(peer,'/chat/'+group.id+'/messages',{body:'Непрочитанное в семье',client_id:'nav-unread-3'},peerCsrf);
  await post(context,'/chat/'+direct.id+'/messages',{body:'Своё сообщение не считается',client_id:'nav-own-message'},csrf);
  await expect(unread).toBeVisible();await expect(unread).toHaveText('3',{timeout:12000});
  await expect(unread.locator('..')).toHaveAttribute('aria-description','Непрочитанных сообщений: 3');
  await post(context,'/notifications/read-all',{},csrf);await section('home');await expect(unread).toHaveText('3');
  await page.screenshot({path:'artifacts/sidebar-chat-unread.png',animations:'disabled'});
  await post(context,'/chat/'+direct.id+'/read',{message_id:lastDirect.id},csrf);await expect(unread).toHaveText('1',{timeout:12000});
  await section('chat');await page.locator('[data-room="'+group.id+'"]').click();await expect(page.locator('[data-message-id="'+lastGroup.id+'"]')).toBeVisible();
  await expect.poll(async()=> (await (await context.request.get(origin+'/api/chat/rooms')).json()).unread).toBe(0);
  await page.locator('#chat-main-menu').click();await expect(unread).toBeHidden({timeout:12000});
  await page.route('**/api/chat/unread',route=>route.fulfill({json:{unread:120}}));
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(unread).toHaveText('99+');
  await page.unroute('**/api/chat/unread');await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(unread).toBeHidden();
  await guest.locator('#logout').click();await expect(guest.locator('#auth-form')).toBeVisible();
  await page.bringToFront();await section('admin');await open('accounts');await expect(lastSeen).toHaveText(/^Был в сети /,{timeout:12000});
  const oldActivity=Math.floor(Date.now()/1000)-10*86400;
  await page.route('**/api/presence?scope=admin',async route=>{
    const response=await route.fetch(),data=await response.json();
    for(const person of data.users)if(person.id===peerMe.id)Object.assign(person,{online:false,last_seen:oldActivity});
    await route.fulfill({response,json:data});
  });
  const oldText=await page.evaluate(stamp=>'Был в сети '+new Date(stamp*1000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}),oldActivity);
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect(lastSeen).toHaveText(oldText,{timeout:12000});
  await expect(page.locator('[data-admin-user="'+peerMe.id+'"] [data-person-online]')).toBeHidden();
  for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});await overflow();await lastSeen.scrollIntoViewIfNeeded();await page.screenshot({path:'artifacts/admin-last-activity-'+width+'.png',animations:'disabled',style:'#notification-toasts,#toast{visibility:hidden!important}'});}
  expect(errors).toEqual([]);
 }finally{await peer.close();}
}

// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8023',directory=await mkdtemp(tmpdir()+'/drg-preferences-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8023','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page,browser);
  console.log('Preferences passed: compact settings/admin, keyboard disclosure, saved expansion, profile/modules/quota/accounts, existing dialogs, owner rights, themes and responsive layouts.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
