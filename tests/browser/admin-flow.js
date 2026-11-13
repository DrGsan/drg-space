import {appSection,editorMode,newNote} from './notes-ui.js';
import { expect } from '@playwright/test';
export async function adminFlow(page,browser,totp,ownerRecovery){
  const adminPassword='Test-long-password-123',newPassword='Managed-new-password-123',copyPassword='пароль-копии-12345';
  await page.setViewportSize({width:1280,height:900});
  await appSection(page, 'Управление');
  await page.getByRole('button',{name:'Пригласить человека',exact:true}).click();
  const invite=await page.getByLabel('Ссылка приглашения').inputValue();
  await page.getByRole('dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  const guestContext=await browser.newContext(),guest=await guestContext.newPage();
  await guest.goto(invite);await guest.getByLabel('Логин',{exact:true}).fill('managed');await guest.getByLabel('Пароль',{exact:true}).fill('Managed-old-password-123');await guest.getByRole('button',{name:'Принять приглашение'}).click();
  const enroll=async(password)=>{
    await guest.getByLabel('Подтвердите пароль').fill(password);await guest.getByRole('button',{name:'Настроить аутентификатор'}).click();
    const secret=await guest.locator('.secret').textContent();await guest.getByLabel('Код из приложения').fill(totp(secret));await guest.getByRole('button',{name:'Проверить код'}).click();
    const codes=(await guest.locator('.recovery').textContent()).trim().split('\n');await guest.getByLabel('Я сохранил резервные коды').check();await guest.getByRole('button',{name:'Открыть пространство'}).click();return codes;
  };
  const guestCodes=await enroll('Managed-old-password-123');
  try{await expect(guest.getByRole('heading',{name:'Привет, managed.'})).toBeVisible();}catch(error){await guest.screenshot({path:'artifacts/admin-guest-enrollment-error.png',fullPage:true});throw error;}
  await newNote(guest);await guest.getByLabel('Название заметки').fill('Данные управляемого аккаунта');await editorMode(guest, 'Markdown');await guest.getByLabel('Текст заметки в Markdown').fill('Сохранить эти данные при восстановлении. '.repeat(30));await expect(guest.locator('#page-save-status')).toContainText('Сохранено');
  await appSection(guest, 'Настройки');await expect(guest.getByRole('button',{name:'Управление',exact:true})).toHaveCount(0);
  await appSection(page, 'Управление');
  const account=()=>page.locator('[data-admin-user]').filter({has:page.locator('.admin-account-copy strong').filter({hasText:/^managed$/})});
  await page.locator('[data-account-search]').fill('managed');await expect(account()).toHaveCount(1);await expect(page.locator('.user')).toContainText('Владелец');
  await expect(page.locator('#toast')).not.toHaveClass(/show/);
  await page.screenshot({path:'artifacts/admin-desktop.png',fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'artifacts/admin-mobile.png',fullPage:true,animations:'disabled'});await page.setViewportSize({width:1280,height:900});
  const dialog=()=>page.getByRole('dialog').last();
  const noteStats=()=>dialog().locator('.admin-account-facts>div').filter({has:page.getByText('Заметки / напоминания / изображения',{exact:true})}).locator('dd');
  await account().locator('[data-open-admin-user]').click();await expect(noteStats()).toHaveText(/^1 \/ /);await dialog().getByRole('button',{name:'Ссылка сброса пароля',exact:true}).click();await dialog().getByRole('button',{name:'Создать ссылку сброса',exact:true}).click();
  const resetUrl=await dialog().getByLabel('Ссылка сброса пароля').inputValue();await dialog().getByRole('button',{name:'Закрыть',exact:true}).click();
  await guest.goto(resetUrl);await guest.getByLabel('Новый пароль',{exact:true}).fill(newPassword);await guest.getByLabel('Повторите пароль').fill(newPassword);await guest.getByRole('button',{name:'Сменить пароль',exact:true}).click();await enroll(newPassword);await expect(guest.getByRole('heading',{name:'Привет, managed.'})).toBeVisible();
  await account().locator('[data-open-admin-user]').click();await dialog().getByRole('button',{name:'Сбросить 2FA',exact:true}).click();await dialog().getByRole('button',{name:'Сбросить 2FA',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  await guest.reload();await guest.getByLabel('Логин',{exact:true}).fill('managed');await guest.getByLabel('Пароль',{exact:true}).fill(newPassword);await guest.locator('#auth-form button[type=submit]').click();await expect(guest.getByRole('heading',{name:'Настроим 2FA'})).toBeVisible();
  const newCodes=await enroll(newPassword);await appSection(guest, 'Заметки');await expect(guest.getByText('Данные управляемого аккаунта',{exact:true}).first()).toBeVisible();
  await page.getByRole('button',{name:'Создать резервную копию',exact:true}).click();await dialog().getByLabel('Пароль резервной копии',{exact:true}).fill(copyPassword);await dialog().getByLabel('Повторите пароль копии').fill(copyPassword);
  // A whole-site encrypted archive is a long operation, not a UI action.
  const [download]=await Promise.all([page.waitForEvent('download',{timeout:60000}),dialog().getByRole('button',{name:'Скачать копию'}).click()]);await download.saveAs('artifacts/admin-full.drgbackup');await expect(page.getByRole('dialog')).toHaveCount(0);
  await account().locator('[data-open-admin-user]').click();await dialog().getByRole('button',{name:'Удалить аккаунт'}).click();await dialog().getByLabel('Введите логин для удаления').fill('managed');await dialog().getByRole('button',{name:'Удалить аккаунт',exact:true}).click();await expect(account()).toContainText('Удаление:');await guest.reload();await expect(guest.getByRole('heading',{name:'Аккаунт ожидает удаления',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Восстановить из копии'}).click();await dialog().getByLabel('Файл резервной копии').setInputFiles('artifacts/admin-full.drgbackup');await dialog().getByLabel('Пароль резервной копии',{exact:true}).fill('wrong-backup-password');await dialog().getByLabel('Введите RESTORE для замены данных').fill('RESTORE');await dialog().getByRole('button',{name:'Заменить данные из копии'}).click();await expect(page.locator('#toast')).toContainText('Неверный пароль архива');await expect(page.getByRole('dialog')).toHaveCount(1);
  await dialog().getByLabel('Пароль резервной копии',{exact:true}).fill(copyPassword);await dialog().getByRole('button',{name:'Заменить данные из копии'}).click();await expect(page.getByRole('heading',{name:'Восстановление завершено'})).toBeVisible();await expect(page.locator('.workspace')).toHaveCount(0);
  await page.getByRole('link',{name:'Войти заново'}).click();await page.getByLabel('Логин',{exact:true}).fill('demoowner');await page.getByLabel('Пароль',{exact:true}).fill(adminPassword);await page.locator('#auth-form button[type=submit]').click();await page.getByLabel('Код 2FA или резервный код').fill(ownerRecovery[1]);await page.locator('#auth-form button[type=submit]').click();await expect(page.getByRole('heading',{name:'Привет, demoowner.'})).toBeVisible();
  await expect.poll(()=>page.locator('[data-widget=media] img').evaluate(img=>img.complete&&img.naturalWidth)).toBe(80);
  await appSection(page, 'Настройки');await appSection(page, 'Управление');await page.locator('[data-account-search]').fill('managed');await expect(account()).toContainText('2FA вкл.');await expect(account()).toHaveCount(1);
  await account().locator('[data-open-admin-user]').click();await expect(noteStats()).toHaveText(/^1 \/ /);await dialog().getByRole('button',{name:'Закрыть',exact:true}).click();
  await guest.getByLabel('Логин',{exact:true}).fill('managed');await guest.getByLabel('Пароль',{exact:true}).fill(newPassword);await guest.locator('#auth-form button[type=submit]').click();await guest.getByLabel('Код 2FA или резервный код').fill(newCodes[0]);await guest.locator('#auth-form button[type=submit]').click();await appSection(guest, 'Заметки');await expect(guest.getByText('Данные управляемого аккаунта',{exact:true}).first()).toBeVisible();await guestContext.close();
}
