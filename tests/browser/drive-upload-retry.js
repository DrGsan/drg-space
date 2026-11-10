import {expect} from '@playwright/test';

export async function driveUploadRetry(page){
 let attempts=0;
 await page.route('**/api/files/upload?name=retry-single.txt*',route=>{
  attempts++;return attempts===1?route.fulfill({status:429,headers:{'Retry-After':'1'},json:{detail:'Слишком много запросов.'}}):route.continue();
 });
 await page.locator('[data-upload-files]').setInputFiles({name:'retry-single.txt',mimeType:'text/plain',buffer:Buffer.from('once')});
 await expect(page.locator('[data-folder-upload-status]')).toContainText('Пауза сервера');
 await expect(page.locator('[data-folder-upload-status]')).toContainText('Готово.');expect(attempts).toBe(2);
 await page.locator('[data-upload-close]').click();
 expect((await (await page.request.get('/api/files?view=recent')).json()).items.filter(x=>x.name==='retry-single.txt')).toHaveLength(1);

 let imports=0,files=0;
 await page.route('**/api/files/folder-import',route=>{
  imports++;return imports===1?route.fulfill({status:429,headers:{'Retry-After':'1'},json:{detail:'Слишком много запросов.'}}):route.continue();
 });
 await page.route('**/api/files/upload?name=retry-nested.txt*',route=>{
  files++;return files===1?route.fulfill({status:429,headers:{'Retry-After':'1'},json:{detail:'Слишком много запросов.'}}):route.continue();
 });
 // Native directory selection is represented by its File System Access handle.
 await page.evaluate(()=>{
  window.driveRetryPicker=window.showDirectoryPicker;
  window.showDirectoryPicker=async()=>({name:'Повтор после паузы',async *entries(){yield ['retry-nested.txt',{kind:'file',getFile:async()=>new File(['nested'],'retry-nested.txt',{type:'text/plain'})}];}});
 });
 try{
  await page.locator('[data-upload-folder]').click();
  await expect(page.locator('[data-folder-upload-status]')).toContainText('Пауза сервера');
  await expect(page.locator('[data-folder-upload-status]')).toContainText('Готово.');
  expect(imports).toBe(2);expect(files).toBe(2);
  await page.locator('[data-upload-close]').click();
 }finally{await page.evaluate(()=>{window.showDirectoryPicker=window.driveRetryPicker;});}
 await page.unroute('**/api/files/folder-import');

 let stopped=0;
 await page.route('**/api/files/upload?name=retry-stop.txt*',route=>{stopped++;return route.fulfill({status:429,headers:{'Retry-After':'60'},json:{detail:'Слишком много запросов.'}});});
 await page.locator('[data-upload-files]').setInputFiles({name:'retry-stop.txt',mimeType:'text/plain',buffer:Buffer.from('stop')});
 await expect(page.locator('[data-folder-upload-status]')).toContainText('Пауза сервера');
 await page.locator('[data-upload-stop]').click();
 await expect(page.locator('[data-folder-upload-status]')).toContainText('остановлена');expect(stopped).toBe(1);
 await page.locator('[data-upload-close]').click();
}
