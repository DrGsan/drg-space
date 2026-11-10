import {editorMode,newNote} from './notes-ui.js';
import {expect} from '@playwright/test';

export async function shareEditFlow(page,browser){
  await newNote(page);
  await page.getByLabel('Название заметки').fill('Страница для совместных правок');
  await editorMode(page, 'Markdown');
  await page.getByLabel('Текст заметки в Markdown').fill('# Планы\n\nЭту страницу можно редактировать по ссылке.');
  await expect(page.locator('#page-save-status')).toContainText('Сохранено');
  await page.waitForURL(/\/page\/[a-zA-Z0-9_-]{16}$/);
  const nid=new URL(page.url()).pathname.split('/')[2];
  await page.getByRole('button',{name:'Поделиться'}).click();
  const modal=()=>page.getByRole('dialog').last();
  await expect(modal().getByRole('combobox',{name:'Права по ссылке',exact:true})).toHaveValue('view');
  await modal().getByRole('combobox',{name:'Права по ссылке',exact:true}).selectOption('edit');
  await modal().getByLabel('Срок',{exact:true}).fill('13');await modal().getByLabel('Единица').selectOption('minutes');
  await modal().getByRole('button',{name:'Создать ссылку',exact:false}).click();
  const url=await page.locator('#share-url').inputValue(),token=new URL(url).pathname.split('/')[2];
  await page.setViewportSize({width:1280,height:1400});await modal().screenshot({path:'artifacts/share-permissions.png'});await page.setViewportSize({width:1280,height:900});
  const context=await browser.newContext(),reader=await context.newPage();const errors=[];reader.on('pageerror',e=>errors.push(e.message));
  await reader.goto(url);await expect(reader.locator('[data-save-status]')).toContainText('автоматически');
  await editorMode(reader, 'Markdown');
  await reader.getByLabel('Текст страницы в Markdown').fill('## Обновлённые планы\n\n**Друг дополнил заметку.**');
  await expect(reader.locator('[data-save-status]')).toHaveText('Изменения сохранены.');
  await editorMode(reader, 'Блоки');await expect(reader.locator('.bn-editor strong')).toHaveText('Друг дополнил заметку.');
  await reader.screenshot({path:'artifacts/shared-page-editor.png',fullPage:true});
  const updated=await (await page.request.get('/api/notes/'+nid)).json();expect(updated.body).toContain('Друг дополнил заметку.');
  // Two independent publication editors cannot silently overwrite each other.
  const second=await context.newPage();await second.goto(url);
  await editorMode(reader, 'Markdown');await reader.getByLabel('Текст страницы в Markdown').fill('Первая правка');await expect(reader.locator('[data-save-status]')).toHaveText('Изменения сохранены.');
  await editorMode(second, 'Markdown');await second.getByLabel('Текст страницы в Markdown').fill('Несохранённый текст второй вкладки');await expect(second.locator('[data-edit-error]')).toContainText('Страница изменилась');await expect(second.getByLabel('Текст страницы в Markdown')).toHaveValue('Несохранённый текст второй вкладки');
  await modal().locator('[data-share-permission="'+token+'"]').selectOption('view');
  await expect(page.locator('#toast')).toContainText('Права ссылки изменены');
  await second.getByRole('button',{name:'Повторить сохранение',exact:true}).click();await expect(second.locator('[data-edit-error]')).toContainText('Право редактирования отозвано');
  await reader.reload();await expect(reader.getByRole('button',{name:'Сохранить изменения',exact:true})).toHaveCount(0);await expect(reader.getByRole('heading',{name:'Страница для совместных правок',exact:true})).toBeVisible();
  expect(errors).toEqual([]);await context.close();await modal().getByRole('button',{name:'Закрыть',exact:true}).click();
}
