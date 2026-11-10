import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const baseline=process.env.DRG_NOTION_BASELINE_REF;
const source='web/modules/portability/notion-sync.js';
const plugins=baseline?[{name:'baseline',setup(api){api.onLoad({filter:/web\/modules\/portability\/notion-sync\.js$/},()=>({contents:execFileSync('git',['show',baseline+':'+source],{encoding:'utf8'}),loader:'js'}));}}]:[];
const bundle=await build({stdin:{contents:"import {notionSyncDialog} from './web/modules/portability/notion-sync.js';import {modal} from './web/core/modal.js';window.openNotion=notionSyncDialog;window.makeModal=modal;",resolveDir:process.cwd()},bundle:true,format:'iife',write:false,loader:{'.css':'empty'},plugins});
const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));
const css=await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8');
const wallpaper=await readFile('web/public/assets/wallpapers/mountains.webp');
const output='artifacts/notion-assets';await mkdir(output,{recursive:true});
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:1100,height:1000}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://notion.test/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html data-scheme="light" data-theme="ocean" data-interface="glass"><body><main><p>Настройки</p></main></body></html>'}));
 await page.goto('http://notion.test/');await page.evaluate(()=>localStorage.setItem('drg-language','ru'));
 await page.addStyleTag({content:css+`body{background:url(data:image/webp;base64,${wallpaper.toString('base64')}) center/cover fixed;min-height:100vh}`});
 await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(async()=>{
  window.reads=0;window.mutations=[];
  window.state={configured:true,write_configured:true,connections:[],roots:[{id:'fixture',title:'IT',status:'importing',mode:'pull',version:7,note_id:'local-fixture',copy_available:true,summary:{pages:58,files:80,databases:0,issues:[],issue_count:0,issue_groups:[]},progress:{copied_pages:56,total_pages:58},error:'notionAssetUnavailable',has_asset_errors:true,asset_failures:[{page:'Правильное монтирование USB HDD и флешек',file:'mount-usb.png',host:'files.example',reason:'notionAssetTimeout',attempts:5,blocked:true},{page:'Mac: инструкции',file:'<img src=x onerror=alert(1)>.png',host:'images.example',reason:'notionAssetMissing',attempts:2,blocked:false}]}]};
  window.dialog=await openNotion({state:{me:{role:'owner'}},modal:makeModal,async api(path,method,body){
   if(!method){reads++;return structuredClone(state);}
   mutations.push({path,method,body});state.roots[0]={...state.roots[0],status:'active',error:null,has_asset_errors:false,asset_failures:[],progress:null,version:8};return {ok:true};
  }});
 });
 const scheme=process.env.DRG_NOTION_SCHEME||'light';await page.evaluate(value=>document.documentElement.dataset.scheme=value,scheme);
 const details=page.locator('[data-asset-failures]');
 if(!baseline){
  assert.equal(await details.count(),1);await details.locator('summary').click();
  assert.equal(await details.locator('li').count(),2);
  assert.equal(await details.locator('img').count(),0,'file names are escaped');
  assert.match(await details.innerText(),/не ответил вовремя/);
  assert.match(await details.innerText(),/пяти неудач/);
  const reads=await page.evaluate(()=>reads);await page.waitForFunction(value=>reads>value,reads);
  assert(await details.evaluate(node=>node.open),'polling preserves expansion');
 }
 await page.evaluate(()=>{state.roots[0].asset_failures[1].file='mac-help.png';document.dispatchEvent(new Event('visibilitychange'));});
 if(!baseline)await page.waitForFunction(()=>document.querySelector('[data-asset-failures]').textContent.includes('mac-help.png'));
 for(const width of [1100,390,320]){
  await page.setViewportSize({width,height:1000});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),JSON.stringify(await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,client:document.documentElement.clientWidth,body:document.body.getBoundingClientRect().toJSON(),html:document.documentElement.getBoundingClientRect().toJSON(),main:document.querySelector('main').getBoundingClientRect().toJSON(),wide:[...document.querySelectorAll('*')].filter(n=>n.getBoundingClientRect().right>innerWidth+1).map(n=>({tag:n.tagName,cls:n.className,w:n.getBoundingClientRect().width,right:n.getBoundingClientRect().right})).slice(0,8)}))));
  await page.locator('[data-sync-root]').screenshot({path:`${output}/${baseline?'before':'after'}-${process.env.DRG_BROWSER_ENGINE||'chromium'}-${width}${scheme==='dark'?'-dark':''}.png`});
 }
 if(!baseline){
  await page.locator('[data-sync-action=retry_assets]').click();
  await page.waitForFunction(()=>state.roots[0].status==='active'&&document.querySelector('[data-asset-failures]').hidden);
  const actions=await page.evaluate(()=>mutations);
  assert.deepEqual(actions,[{path:'/account/notion-sync/roots/fixture/action',method:'POST',body:{action:'retry_assets',version:7}}]);
  assert.equal(await page.locator('[data-sync-action=retry_assets]').count(),0);
 }
 await page.locator('.modal-close').click();assert.equal(await page.locator('[role=dialog]').count(),0);
 assert.deepEqual(errors,[]);console.log(baseline?'Captured stable baseline':'Notion attachment UI passed: detailed errors, no HTML injection, stable disclosure, one explicit retry, narrow screens and cleanup.');
}finally{await browser.close();}
