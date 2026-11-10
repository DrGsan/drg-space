import {appSection} from './notes-ui.js';
import {expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

export async function personalDataFlow(page){
  const password='Updated-owner-password-456',archivePassword='Личный-архив-пароль-123';
  const dialog=()=>page.getByRole('dialog').last();
  await appSection(page, 'Настройки');
  await page.getByRole('button',{name:'Экспорт и импорт',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Личный архив',exact:true})).toBeVisible();
  await expect(page.locator('#toast')).toHaveCSS('opacity','0',{timeout:7000});
  await page.screenshot({path:'artifacts/personal-data-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'artifacts/personal-data-mobile.png',fullPage:true});
  await page.setViewportSize({width:1280,height:900});
  const before=await (await page.request.get('/api/notes')).json();
  const original=(await (await page.request.get('/api/me')).json());
  await page.getByRole('button',{name:'Экспортировать мои данные',exact:true}).click();
  await dialog().getByLabel('Текущий пароль аккаунта').fill(password);
  await dialog().getByLabel('Пароль архива',{exact:true}).fill(archivePassword);
  await dialog().getByLabel('Повторите пароль архива').fill(archivePassword);
  const [download]=await Promise.all([page.waitForEvent('download'),dialog().getByRole('button',{name:'Скачать личный архив',exact:true}).click()]);
  const file=await download.path();expect(download.suggestedFilename()).toMatch(/\.drgpersonal$/);
  const bytes=await readFile(file);expect(bytes.subarray(0,16).toString()).toContain('DRG-PERSONAL-02');
  expect(bytes.toString()).not.toContain('Северный ветер');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button',{name:'Импортировать мои данные',exact:true}).click();
  await dialog().getByLabel('Личный архив',{exact:true}).setInputFiles({name:download.suggestedFilename(),mimeType:'application/octet-stream',buffer:bytes});
  await dialog().getByLabel('Текущий пароль аккаунта').fill(password);
  await dialog().getByLabel('Пароль архива',{exact:true}).fill('Неверный-пароль-архива');
  await dialog().getByRole('button',{name:'Проверить архив',exact:true}).click();
  await expect(page.locator('#toast')).toContainText('Неверный пароль архива');
  await expect(dialog().getByRole('button',{name:'Добавить данные в мой аккаунт',exact:true})).toHaveCount(0);
  await dialog().getByLabel('Пароль архива',{exact:true}).fill(archivePassword);
  await dialog().getByLabel('Перенести оформление, модули и виджеты').uncheck();
  await dialog().getByRole('button',{name:'Проверить архив',exact:true}).click();
  await expect(dialog().getByRole('heading',{name:'Архив проверен',exact:true})).toBeVisible();
  const confirm=dialog().getByRole('button',{name:'Добавить данные в мой аккаунт',exact:true});
  await expect(confirm).toBeDisabled();
  expect((await (await page.request.get('/api/notes')).json()).length).toBe(before.length);
  await expect(page.locator('#toast')).toHaveCSS('opacity','0',{timeout:7000});
  await page.setViewportSize({width:1280,height:1400});
  await dialog().screenshot({path:'artifacts/personal-import-preview.png'});
  await page.setViewportSize({width:1280,height:900});
  await dialog().getByLabel('Я понимаю, что импорт добавит копии данных').check();
  await confirm.click();await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Импорт завершён',exact:true})).toBeVisible();
  const after=await (await page.request.get('/api/notes')).json();
  expect(after.length).toBe(before.length*2);
  const me=await (await page.request.get('/api/me')).json();
  expect(me.id).toBe(original.id);expect(me.role).toBe(original.role);expect(me.two_factor).toBe(original.two_factor);
  expect(me.settings).toEqual(original.settings);
  const stableNote=note=>{
    const value=structuredClone(note);
    // Live presence changes while export/import runs; it is not note content.
    for(const person of [value.owner,...(value.participants||[])])if(person){delete person.last_seen;delete person.online;}
    return value;
  };
  for(const n of before)expect(stableNote(after.find(next=>next.id===n.id))).toEqual(stableNote(n));
}
