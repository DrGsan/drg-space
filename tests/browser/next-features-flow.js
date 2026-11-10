import {appSection,editorMode,newNote,pageAction} from './notes-ui.js';
import {expect} from '@playwright/test';
export async function nextFeaturesFlow(page,browser,totp){
  await expect(page.locator('.security-badge')).toHaveCount(0);
  await newNote(page);await page.getByLabel('Название заметки').fill('Главная памятка');await editorMode(page, 'Markdown');await page.getByLabel('Текст заметки в Markdown').fill('Проверенная финальная версия.');await expect(page.locator('#page-save-status')).toContainText('Сохранено');
  await page.getByRole('button',{name:'Добавить в избранное',exact:true}).click();await expect(page.getByRole('button',{name:'Убрать из избранного',exact:true})).toBeVisible();
  await pageAction(page,'lock');await expect(page.getByLabel('Название заметки')).toBeDisabled();
  await page.getByRole('button',{name:'Разблокировать заметку'}).click();await page.getByRole('button',{name:'Разблокировать временно',exact:true}).click();await expect(page.getByLabel('Название заметки')).toBeEnabled();await page.getByLabel('Название заметки').fill('Главная памятка · исправлена');await expect(page.locator('#page-save-status')).toContainText('Сохранено');
  await appSection(page, 'Главная');await appSection(page, 'Заметки');await page.locator('[data-open-page]').filter({hasText:'Главная памятка'}).first().click();await expect(page.getByLabel('Название заметки')).toBeDisabled();
  await appSection(page, 'Настройки');
  for(const title of ['Главные заметки','Коды 2FA'])await expect(page.getByRole('checkbox',{name:'Модуль: '+title,exact:true})).toHaveCount(0);
  await page.getByRole('checkbox',{name:'Модуль: Раздельный счёт',exact:true}).check();await page.getByRole('button',{name:'Сохранить модули',exact:true}).click();
  await appSection(page, 'Главная');await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  for(const id of ['favorites','authenticator','expenses']){
    await expect(page.getByLabel('Новый виджет').locator(`option[value="${id}"]`)).toBeEnabled();
    await page.getByLabel('Новый виджет').selectOption(id);await page.getByRole('button',{name:'Добавить виджет',exact:true}).click();
  }
  await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  const favorite=page.locator('[data-widget=favorites]');await expect(favorite).toContainText('Главная памятка · исправлена');
  const vault=page.locator('[data-widget=authenticator]');await vault.getByRole('button',{name:'Добавить сервис'}).click();const modal=page.getByRole('dialog').last();await modal.getByLabel('Название',{exact:true}).fill('Gitea');await modal.getByLabel('Сервис или аккаунт').fill('Мой сервер');const seed='JBSWY3DPEHPK3PXP';await modal.getByLabel('Секретный ключ или otpauth-ссылка').fill(seed);await modal.getByRole('button',{name:'Добавить сервис',exact:true}).click();
  const code=vault.locator('[data-code]');await expect(code).toHaveText('••••••');await code.click();await expect(code).toHaveText(totp(seed));await code.click();await expect(code).toHaveText('••••••');
  await expect(page.locator('#toast')).toHaveCSS('opacity','0',{timeout:7000});
  await page.screenshot({path:'artifacts/next-dashboard.png',fullPage:true});
  await appSection(page, 'Настройки');
  await expect(page.getByRole('heading',{name:'Виджеты главной',exact:true})).toHaveCount(0);
  await page.getByLabel('Отображаемое имя').fill('DrG');await page.getByRole('button',{name:'Сохранить имя'}).click();
  await expect(page.locator('[data-profile-status]')).toHaveText('Имя сохранено');await page.screenshot({path:'artifacts/profile-settings.png',fullPage:true});
  await appSection(page, 'Главная');await expect(page.getByRole('heading',{name:'Привет, DrG.'})).toBeVisible();
  // Exercise the profile default here. Billing entries, shares and photo
  // galleries use the current end-to-end flow in expense-entry.mjs.
  await page.locator('[data-widget=expenses]').getByRole('button',{name:'Начать гулянку'}).click();
  await expect(page.getByLabel('Моё имя')).toHaveValue('DrG');
  await expect(page.getByLabel('Участники, каждый с новой строки')).toHaveValue('');
  await page.getByRole('dialog').last().getByRole('button',{name:'Закрыть',exact:true}).click();
  // New invite: choose a login, skip 2FA, show only the warning badge.
  await appSection(page, 'Управление');
  const csrf=await page.evaluate(()=>decodeURIComponent(document.cookie.split('; ').find(v=>v.startsWith('drg_csrf=')).slice(9)));
  const response=await page.request.post('/api/invites',{headers:{Origin:'http://127.0.0.1:8001','X-CSRF-Token':csrf},data:{}});expect(response.ok(),await response.text()).toBe(true);const invite=(await response.json()).url;
  const context=await browser.newContext(),guest=await context.newPage();await guest.goto(invite);await guest.getByLabel('Логин',{exact:true}).fill('newperson');await guest.getByLabel('Пароль',{exact:true}).fill('Guest-password-123');await guest.getByLabel('Подсказка пароля, необязательно').fill('Моя подсказка');await guest.getByRole('button',{name:'Принять приглашение'}).click();await guest.getByRole('button',{name:'Настроить позже'}).click();await expect(guest.locator('.security-badge')).toHaveText('⚠ Аккаунт не защищён 2FA');await guest.getByRole('button',{name:'Выйти',exact:true}).click();for(let i=0;i<3;i++){await guest.getByLabel('Логин',{exact:true}).fill('newperson');await guest.getByLabel('Пароль',{exact:true}).fill('Wrong-password-123');await guest.locator('#auth-form button[type=submit]').click();await expect(guest.locator('#toast')).toContainText('Неверный логин');}await guest.getByRole('button',{name:'Показать подсказку пароля'}).click();await expect(guest.locator('[data-hint-result]')).toHaveText('Моя подсказка');await context.close();
}
