import {appSection} from './notes-ui.js';
import {expect} from '@playwright/test';

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);
  return result.data;
}

export async function filesTrashFlow(page){
  await appSection(page, 'Файлы');
  await page.locator('[data-files-view=active]').click();
  await page.locator('.file-crumbs [data-folder=""]').click();
  await page.locator('[data-new-folder]').click();
  await page.getByLabel('Название папки',{exact:true}).fill('Корзина browser');
  await page.getByLabel('Название папки',{exact:true}).press('Enter');
  await page.locator('.file-open').filter({hasText:'Корзина browser'}).click();
  await page.locator('[data-upload-files]').setInputFiles({name:'trash.txt',mimeType:'text/plain',buffer:Buffer.from('Recoverable file contents')});
  await expect(page.locator('.file-list')).toContainText('trash.txt');
  const active=await api(page,'/files'),folder=active.items.find(item=>item.name==='Корзина browser');
  expect(folder.kind).toBe('folder');
  const original=await api(page,'/files?parent_id='+folder.id);
  const content=original.items.find(item=>item.name==='trash.txt');
  const share=await api(page,'/files/'+content.id+'/shares','POST',{mode:'public',permission:'view',duration:null,unit:'days',allowed:[],password:null});
  const sharedApi='/api/shared-files/'+share.url.split('/').at(-1);
  await page.locator('.file-crumbs [data-folder=""]').click();
  const moveToTrash=async()=>{
    await page.locator('[data-file-row="'+folder.id+'"]').locator('[data-file-menu]').click();
    let confirmationShown=false;
    const unexpected=async dialog=>{confirmationShown=true;await dialog.dismiss();};page.on('dialog',unexpected);
    await page.getByRole('menuitem',{name:'В корзину',exact:true}).click();
    await expect(page.locator('[data-file-row="'+folder.id+'"]')).toHaveCount(0);
    page.off('dialog',unexpected);expect(confirmationShown).toBe(false);
  };
  await moveToTrash();
  await expect(page.locator('[data-files-view=trash]')).toContainText('1');
  await page.locator('[data-files-view=trash]').click();
  const row=page.locator('[data-file-row="'+folder.id+'"]');
  await expect(row).toContainText('Корзина browser');
  await expect(row).toContainText('Удалится');
  await expect(page.locator('.drive-hint')).toContainText('7 дней');
  await expect(page.locator('[data-upload-files], [data-new-folder], [data-file-menu], [data-open-file]')).toHaveCount(0);
  expect((await page.request.get(sharedApi)).status()).toBe(404);
  await page.screenshot({path:'artifacts/files-trash-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(row.locator('[data-file-restore]')).toBeVisible();
  await expect(row.locator('[data-file-permanent]')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'artifacts/files-trash-mobile.png',fullPage:true});
  await page.setViewportSize({width:1280,height:900});

  // A held request proves the controls cannot submit the same action twice.
  let releaseRestore,restoreStarted;
  const held=new Promise(resolve=>releaseRestore=resolve),started=new Promise(resolve=>restoreStarted=resolve);
  const restorePath='**/api/files/'+folder.id+'/restore';
  await page.route(restorePath,async route=>{restoreStarted();await held;await route.continue();});
  await row.locator('[data-file-restore]').click();await started;
  await expect(row.locator('[data-file-restore]')).toBeDisabled();
  await expect(row.locator('[data-file-permanent]')).toBeDisabled();
  releaseRestore();
  await expect(page.locator('.file-empty strong')).toHaveText('Корзина пуста.');
  await page.unroute(restorePath);
  await page.locator('[data-files-view=active]').click();
  await page.locator('[data-file-row="'+folder.id+'"]').locator('[data-open-file]').click();
  await page.locator('[data-open-file="'+content.id+'"]').click();
  await expect(page.getByRole('dialog').getByLabel('Содержимое файла')).toHaveValue('Recoverable file contents');
  await page.getByRole('dialog').locator('.modal-close').click();
  expect((await page.request.get(sharedApi)).status()).toBe(404);
  await page.locator('.file-crumbs [data-folder=""]').click();
  await moveToTrash();await page.locator('[data-files-view=trash]').click();

  const canceled=page.waitForEvent('dialog'),cancelClick=row.locator('[data-file-permanent]').click();
  const cancelDialog=await canceled;expect(cancelDialog.message()).toContain('Восстановить папку будет невозможно');
  await cancelDialog.dismiss();await cancelClick;await expect(row).toBeVisible();
  const confirmed=page.waitForEvent('dialog'),permanentClick=row.locator('[data-file-permanent]').click();
  const permanentDialog=await confirmed;await permanentDialog.accept();await permanentClick;
  await expect(page.locator('.file-empty strong')).toHaveText('Корзина пуста.');
  expect((await api(page,'/files/trash')).items).toEqual([]);
  await page.locator('[data-files-view=active]').click();
  await expect(page.locator('[data-file-row="'+folder.id+'"]')).toHaveCount(0);
}
