import {expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

export async function driveSharingFlow(page,csrf,origin){
 const headers={Origin:origin,'X-CSRF-Token':csrf};
 const folder=await (await page.request.post('/api/files/folders',{headers,data:{name:'3D Печать'}})).json();
 const nested=await (await page.request.post('/api/files/folders',{headers,data:{name:'Внутри',parent_id:folder.id}})).json();
 const file=await (await page.request.post('/api/files/upload?name=public.txt&parent_id='+nested.id,{headers,data:Buffer.from('shared content')})).json();
 for(const name of ['FreeCAD','Nixie','Лицензия','BambuLab'])await page.request.post('/api/files/folders',{headers,data:{name,parent_id:folder.id}});
 for(const [name,size] of [['Air Vent Tab Toyota Corolla 2016.stl',1048576],['Air Vent Tab Toyota Corolla 2016.3mf',262144],['iQOS v2.3mf',139264],['illidan-stormrage-parts.3mf',524288]])await page.request.post('/api/files/upload?name='+encodeURIComponent(name)+'&parent_id='+folder.id,{headers,data:Buffer.alloc(size,65)});
 const image=await (await page.request.post('/api/files/upload?name=Логотип.png&parent_id='+folder.id,{headers,data:await readFile('web/public/assets/icons/drg-d8-512.png')})).json();
 await page.locator('[data-files-view=active]').click();
 await page.locator('[data-file-menu="'+folder.id+'"]').click();await page.locator('[data-file-share]').click();
 const links=[];
 for(let i=0;i<2;i++){
  await page.locator('[data-file-share-form] button[type=submit]').click();
  await expect(page.locator('[data-file-share-list] .share-row')).toHaveCount(i+1);
  links.push(await page.locator('[data-file-share-result] input').inputValue());
 }
 await page.locator('.modal-close').click();expect(links[0]).not.toBe(links[1]);
 const context=await page.context().browser().newContext(),guest=await context.newPage(),errors=[];guest.on('pageerror',e=>errors.push(e.message));
 try{
  for(const link of links){
   await guest.goto(link);await expect(guest.locator('.shared-drive-page .drive-heading h2')).toHaveText('3D Печать');
   await guest.locator('[data-shared-open="'+nested.id+'"]').click();await expect(guest.locator('.file-list')).toContainText('public.txt');
   const token=new URL(link).pathname.split('/').at(-1);
   const downloaded=await context.request.get(origin+'/api/shared-files/'+token+'/'+file.id+'/content');expect(downloaded.ok()).toBe(true);expect(await downloaded.text()).toBe('shared content');
   const zip=await context.request.get(origin+'/api/shared-files/'+token+'/download.zip');expect(zip.ok()).toBe(true);expect(zip.headers()['content-type']).toContain('application/zip');
   await guest.locator('[data-shared-root]').click();await expect(guest.locator('.file-list')).toContainText('Внутри');
  }
  await guest.setViewportSize({width:1440,height:900});
  await expect(guest.locator('.drive-thumbnail')).toBeVisible();
  await expect.poll(()=>guest.locator('.drive-thumbnail').evaluate(img=>img.naturalWidth)).toBeGreaterThan(0);
  await guest.screenshot({path:'artifacts/shared-drive-desktop-list.png',fullPage:true});
  await guest.locator('[data-drive-layout]').click();await guest.locator('[data-layout-option=grid]').click();
  await guest.screenshot({path:'artifacts/shared-drive-desktop-grid.png',fullPage:true});
  await guest.locator('[data-file-menu="'+image.id+'"]').click();await expect(guest.locator('.drive-popover')).not.toContainText('Удалить');
  await guest.locator('[data-shared-preview]').click();await expect(guest.locator('.drive-preview-content img')).toBeVisible();await guest.locator('[data-preview-close]').click();
  await guest.locator('[data-drive-layout]').click();await guest.locator('[data-layout-option=list]').click();
  for(const width of [390,320]){
   await guest.setViewportSize({width,height:844});expect(await guest.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   await guest.screenshot({path:'artifacts/shared-drive-phone-'+width+'.png',fullPage:true});
  }
  expect((await context.request.get(origin+'/api/files/'+file.id+'/content')).status()).toBe(401);
  expect(errors).toEqual([]);
 }finally{await context.close();}
}
