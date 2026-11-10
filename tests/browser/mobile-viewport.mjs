// Model keyboard resize/pan explicitly: desktop WebKit has no iOS keyboard.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/core/mobile.js'],bundle:true,format:'iife',globalName:'MobileApp',write:false});
const css=await readFile('app/static/assets/app.css','utf8');
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
try{
  for(const tasks of [false,true]){
    const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:844},hasTouch:true});
    await page.setContent(`<!doctype html><style>${css}</style><div id="app">${tasks?'<div class="workspace tasks-open"><main class="content"><div id="view-content"><div class="tasks-shell"><main class="tasks-center"><div class="tasks-scroll-area">Дела</div><div class="tasks-add-area"><input aria-label="Новое дело"></div></main></div></div></main><nav class="mobile-app-tabs"><button>Сообщения</button><button>Дела</button></nav></div>':'<div class="auth-layout"><main class="auth-main"><form class="auth-card"><label>Логин<input autocomplete="username"></label></form></main></div>'}</div>`);
    await page.addScriptTag({content:bundle.outputFiles[0].text});
    await page.evaluate(()=>{
      document.documentElement.classList.add('app-standalone');Object.defineProperty(navigator,'standalone',{value:true});
      document.documentElement.style.setProperty('--app-safe-top','59px');document.documentElement.style.setProperty('--app-safe-bottom','34px');
      window.testViewport=new EventTarget();Object.assign(testViewport,{height:844,offsetTop:0,scale:1});Object.defineProperty(window,'visualViewport',{configurable:true,value:testViewport});
      window.unbindMobile=MobileApp.bindMobileViewport();window.kept=document.querySelector('input');kept.value='Черновик';kept.focus();
    });
    const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const bounds=()=>page.locator('#app').evaluate(el=>({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom}));
    const footer=async()=>{
      const geometry=await page.locator('.mobile-app-tabs').evaluate(el=>{const r=el.getBoundingClientRect();return {bottom:r.bottom,height:r.height,left:r.left,right:r.right,edge:el.contains(document.elementFromPoint(innerWidth/2,innerHeight-2))};});
      const height=await page.evaluate(()=>testViewport.height);
      assert.deepEqual(geometry,{bottom:height-24,height:64,left:12,right:378,edge:false});
    };
    await settle();assert.deepEqual(await bounds(),{top:59,bottom:tasks?844:810});
    if(tasks){assert(await page.locator('.mobile-app-tabs').isVisible());await footer();}
    for(const [height,offset] of [[780,0],[432,0],[380,120],[844,120]]){
      await page.evaluate(({height,offset})=>{Object.assign(testViewport,{height,offsetTop:offset});testViewport.dispatchEvent(new Event('resize'));},{height,offset});await settle();
      const keyboard=height<744;assert.deepEqual(await bounds(),{top:59+(keyboard?offset:0),bottom:height+(keyboard?offset:tasks?0:-34)});
      if(tasks){assert.equal(await page.locator('.mobile-app-tabs').isVisible(),!keyboard);assert((await page.locator('.tasks-add-area').boundingBox()).y<(await bounds()).bottom);if(!keyboard)await footer();}
      assert(await page.evaluate(()=>kept===document.activeElement&&kept.value==='Черновик'));
    }
    await page.evaluate(()=>{Object.assign(testViewport,{height:250,offsetTop:200,scale:1.5});testViewport.dispatchEvent(new Event('resize'));});await settle();assert.deepEqual(await bounds(),{top:59,bottom:tasks?844:810});
    await page.evaluate(()=>unbindMobile());assert.equal(await page.locator('html').evaluate(el=>el.classList.contains('mobile-keyboard')),false);
    await page.close();
  }
  console.log('Phone viewport passed: login/tasks safe areas, keyboard pan/dismiss, tabs, focus, draft, zoom and cleanup.');
}finally{await browser.close();}
