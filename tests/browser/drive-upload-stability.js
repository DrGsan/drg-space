import {expect} from '@playwright/test';

export async function driveUploadStability(page){
 await page.setViewportSize({width:1440,height:900});
 let release,started=false;
 const held=new Promise(resolve=>release=resolve);
 await page.route('**/api/files/upload?name=stability-*',async route=>{started=true;await held;await route.continue();},{times:1});
 await page.evaluate(()=>{
  window.originalDrivePicker=window.showDirectoryPicker;window.originalDriveSend=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send=function(...args){window.driveTestRequest=this;return window.originalDriveSend.apply(this,args);};
  window.showDirectoryPicker=async()=>({name:'Длинная очередь',kind:'directory',async *entries(){
   for(let i=0;i<1633;i++){
    const name='stability-'+i+'-'+('Длинное название комикса '.repeat(5))+'.txt';
    yield [name,{kind:'file',getFile:async()=>new File([i===0?new Uint8Array(131072):'next'],name,{type:'text/plain'})}];
   }
  }});
 });
 try{
  await page.locator('[data-upload-folder]').click();await expect.poll(()=>started).toBe(true);
  await expect(page.locator('.drive-transfer-item')).toHaveCount(1633);
  const before=await page.locator('.drive-transfer-list').evaluate(el=>{el.scrollTop=300;return {top:el.getBoundingClientRect().top,height:el.clientHeight,scroll:el.scrollTop,page:document.documentElement.scrollHeight};});
  await page.evaluate(()=>{const upload=window.driveTestRequest.upload;upload.dispatchEvent(new ProgressEvent('progress',{lengthComputable:true,loaded:65536,total:131072}));upload.dispatchEvent(new ProgressEvent('load'));});
  await expect(page.locator('[data-folder-upload-status]')).toContainText('Завершаем');
  const after=await page.locator('.drive-transfer-list').evaluate(el=>({top:el.getBoundingClientRect().top,height:el.clientHeight,scroll:el.scrollTop,page:document.documentElement.scrollHeight}));
  expect(after.top-before.top,'Queue position must not jump when the long name is shown').toBe(0);
  expect(after.height).toBe(before.height);expect(after.scroll).toBe(before.scroll);
  expect(after.page,'Queue must scroll inside its panel').toBeLessThanOrEqual(900);
  await page.screenshot({path:'artifacts/files-drive-long-queue.png',fullPage:true});
  await page.locator('[data-upload-stop]').click();release();
  await expect(page.locator('[data-folder-upload-status]')).toContainText('остановлена');
  await page.locator('[data-upload-close]').click();
 }finally{
  release();await page.evaluate(()=>{window.showDirectoryPicker=window.originalDrivePicker;XMLHttpRequest.prototype.send=window.originalDriveSend;});
 }
}
