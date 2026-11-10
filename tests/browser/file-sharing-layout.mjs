import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const baseline=process.env.DRG_SHARING_BASELINE==='1',engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const plugins=baseline?[{name:'old-sharing',setup(api){api.onLoad({filter:/web\/modules\/files\/sharing\.js$/},()=>{
 const source=execFileSync('git',['show','v4.17.3:web/modules/files/index.js'],{encoding:'utf8'});
 return {contents:`import {tText,tHtml} from '../../core/i18n.js';import {$,esc,date,formBind} from '../../core/ui.js';\n`+source.slice(source.indexOf('export async function fileShare('),source.indexOf("export {mountSharedFiles}")),loader:'js'};
});}}]:[];
const bundle=await build({stdin:{contents:"import {fileShare} from './web/modules/files/sharing.js';import {modal} from './web/core/modal.js';window.openFileShare=fileShare;window.makeModal=modal;",resolveDir:process.cwd()},bundle:true,format:'iife',write:false,loader:{'.css':'empty'},plugins});
const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8')),css=await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8');
const out='artifacts/file-share-limits/layout/'+engine;await mkdir(out,{recursive:true});
const browser=await ({chromium,webkit}[engine]).launch();
try{
 for(const lang of ['ru','en']){
  const page=await browser.newPage({locale:lang==='ru'?'ru-RU':'en-GB',viewport:{width:1100,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://sharing.test/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html data-theme="ocean" data-scheme="light" data-interface="classic"><body><main class=workspace></main></body></html>'}));
  await page.goto('http://sharing.test/');await page.evaluate(lang=>localStorage.setItem('drg-language',lang),lang);await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async()=>{
   window.mutations=[];window.open=async()=>openFileShare({id:'folder',kind:'folder',name:'Документы для передачи'},{modal:makeModal,toast:()=>{},async api(path,method,body){
    if(method){mutations.push({path,method,body});return {auto_delete:body.auto_delete,requires_confirmation:true,confirmation:'fixture',files:[{id:'a',name:'Инструкция.pdf',delete_at:1794484800},{id:'b',name:'Описание.txt',delete_at:1794571200}]};}
    if(path.endsWith('/retention'))return {files:[{id:'a',name:'Инструкция.pdf',delete_at:1794484800,version:1}]};
    return [{token:'fixture',url:'http://sharing.test/f/fixture',mode:'public',permission:'view',expires:1794484800,used:2,download_limit:5,version:1}];
   }});await window.open();
  });
  await page.locator('[name=duration]').fill('2');if(!baseline)await page.locator('[name=download_limit]').fill('3');
  for(const scheme of ['light','dark'])for(const width of [1100,390,320]){
   await page.setViewportSize({width,height:width===1100?1100:844});await page.evaluate(scheme=>document.documentElement.dataset.scheme=scheme,scheme);
   await page.locator('[name=duration]').scrollIntoViewIfNeeded();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   const modal=page.locator('.modal').last();assert(await modal.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   if(lang==='ru'&&scheme==='light')await page.screenshot({path:out+'/'+(baseline?'before':'after')+'-'+width+'.png'});
  }
  if(!baseline){
   await page.locator('[name=auto_delete]').check();await page.locator('[data-retention-confirm]').waitFor();
   assert.equal(await page.locator('.file-retention-confirm li').count(),2);
   await page.screenshot({path:out+'/confirmation-'+lang+'.png'});await page.locator('[data-retention-cancel]').click();assert(!await page.locator('[name=auto_delete]').isChecked());
  }
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log('File sharing layouts '+(baseline?'before':'after')+' '+engine+' passed');
}finally{await browser.close();}
