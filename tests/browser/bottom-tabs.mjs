import {expect,chromium,webkit} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
const origin='http://127.0.0.1:8059',folder=process.env.DRG_TABS_OUTPUT||'artifacts/bottom-tab-motion',baseline=process.env.DRG_TABS_BASELINE==='1',capture=process.env.DRG_CAPTURE==='1';
const directory=await mkdtemp(tmpdir()+'/drg-bottom-tabs-');
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
async function captionsFit(nav){
 const captions=await nav.locator('button').evaluateAll(buttons=>buttons.map(button=>{const span=button.querySelector(':scope>span');const range=document.createRange();range.selectNodeContents(span);const text=range.getBoundingClientRect(),bounds=button.getBoundingClientRect();return {title:span.textContent,left:text.left-bounds.left,right:bounds.right-text.right};}));
 for(const caption of captions){expect(caption.left,caption.title+' left edge').toBeGreaterThanOrEqual(-0.5);expect(caption.right,caption.title+' right edge').toBeGreaterThanOrEqual(-0.5);}
}
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8059','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'bottom-tabs-isolated-test-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
 for(let n=0;n<100;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}
 const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
 browser=await engine.launch({headless:true,...(process.env.DRG_BROWSER_CHANNEL?{channel:process.env.DRG_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:390,height:844},...(capture?{recordVideo:{dir:folder+'/raw-web',size:{width:390,height:844}}}:{})});
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));let csrf='';
 const api=async(path,method='GET',data)=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Bottom-tabs-test-password-123';csrf=(await api('/setup','POST',{login:'tabsowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await api('/modules','PUT',{enabled:{tracker:true}});await mkdir(folder,{recursive:true});const errors=[],checks=[];
 for(const [style,mode,lang] of (baseline?[['classic','light','ru']]:[['classic','light','ru'],['glass','light','ru'],['classic','dark','en']])){
  await api('/language','PUT',{language:lang});const me=await api('/me');await api('/appearance','PUT',{...me.settings.appearance,style,mode,seasonal:'off'});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(lang=>localStorage.setItem('drg-language',lang),lang);await page.goto('/');
  const nav=page.locator('.workspace>.mobile-app-tabs:not(.drive-mobile-tabs)');await expect(nav).toBeVisible();await expect(nav.locator('button')).toHaveCount(5);
  for(const tab of ['chat','settings','notes','tracker','tasks']){
   if(!baseline){await nav.evaluate(el=>{window.savedBottomBar=el;window.tabFrames=[];const sample=()=>{const slider=el.querySelector('.mobile-tab-indicator'),button=el.querySelector('[aria-current=page]');if(slider&&button){window.tabFrames.push({x:slider.getBoundingClientRect().left,target:button.getBoundingClientRect().left,view:document.querySelector('.workspace').dataset.currentView});}if(window.tabFrames.length<35)requestAnimationFrame(sample);};sample();});}
   await nav.locator(`[data-mobile-view=${tab}]`).click();await expect(page.locator('.workspace')).toHaveAttribute('data-current-view',tab);
   if(!baseline){
    await page.waitForFunction(()=>window.tabFrames.length===35);expect(await nav.evaluate(el=>el===window.savedBottomBar)).toBe(true);
    const frames=await page.evaluate(()=>window.tabFrames);if(tab!=='chat')expect(frames.some(f=>Math.abs(f.x-f.target)>1)).toBe(true);
    const aligned=await nav.evaluate(el=>Math.abs(el.querySelector('.mobile-tab-indicator').getBoundingClientRect().left-el.querySelector('[aria-current=page]').getBoundingClientRect().left)<1);expect(aligned).toBe(true);
   }
   await page.screenshot({path:`${folder}/${baseline?'before':'after'}-${engine.name()}-${style}-${mode}-${lang}-${tab}.png`});
  }
  if(!baseline){
   // Rapid taps interrupt the one shared capsule; the last committed view wins.
   await page.evaluate(()=>{const nav=document.querySelector('.workspace>.mobile-app-tabs');nav.querySelector('[data-mobile-view=chat]').click();setTimeout(()=>nav.querySelector('[data-mobile-view=settings]').click(),50);setTimeout(()=>nav.querySelector('[data-mobile-view=tracker]').click(),90);});
   await expect(page.locator('.workspace')).toHaveAttribute('data-current-view','tracker');await page.waitForTimeout(450);
   expect(await nav.evaluate(el=>Math.abs(el.querySelector('.mobile-tab-indicator').getBoundingClientRect().left-el.querySelector('[aria-current=page]').getBoundingClientRect().left)<1)).toBe(true);
   for(const width of [320,430]){await page.setViewportSize({width,height:844});await page.waitForTimeout(100);await captionsFit(nav);expect(await nav.evaluate(el=>{const r=el.getBoundingClientRect();const a=el.querySelector('.mobile-tab-indicator').getBoundingClientRect(),b=el.querySelector('[aria-current=page]').getBoundingClientRect();return r.right<=innerWidth&&Math.abs(a.left-b.left)<1&&Math.abs(a.width-b.width)<1;})).toBe(true);}
   await page.emulateMedia({reducedMotion:'reduce'});await nav.locator('[data-mobile-view=settings]').click();await expect(page.locator('.workspace')).toHaveAttribute('data-current-view','settings');expect(await nav.locator('.mobile-tab-indicator').evaluate(el=>getComputedStyle(el).transitionDuration)).toBe('0s');await page.emulateMedia({reducedMotion:'no-preference'});
   // Secondary file navigation keeps its own bar and never inherits the main capsule.
   await page.locator('[data-mobile-open=files]').click();await expect(page.locator('.drive-mobile-tabs')).toBeVisible();await expect(nav).toBeHidden();await expect(page.locator('.drive-mobile-tabs .mobile-tab-indicator')).toHaveCount(0);await page.locator('#files-main-menu').click();await expect(nav).toBeVisible();
   await page.evaluate(()=>document.documentElement.classList.add('mobile-keyboard'));await expect(nav).toBeHidden();await page.evaluate(()=>document.documentElement.classList.remove('mobile-keyboard'));await expect(nav).toBeVisible();
   await page.setViewportSize({width:1100,height:844});await expect(nav).toBeHidden();await page.setViewportSize({width:390,height:844});await expect(nav).toBeVisible();
   await page.locator('[data-preference-key=appearance]>summary').click();await page.locator('[data-seasonal-preview]').selectOption('newyear');await expect(page.locator('.workspace')).toHaveClass(/seasonal-framed/);
   await nav.locator('[data-mobile-view=tracker]').click();await page.waitForTimeout(450);
   expect(await nav.locator('.mobile-tab-indicator').evaluate(el=>getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
   expect(await nav.locator('[aria-current=page]').evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
   await page.screenshot({path:`${folder}/after-${engine.name()}-${style}-${mode}-${lang}-holiday.png`});
  }
  checks.push({style,mode,lang});await page.close();if(capture&&style==='classic'&&mode==='light')await page.video().saveAs(`${folder}/web-${engine.name()}.webm`);
 }
 if(!baseline){
  await api('/modules','PUT',{enabled:{tracker:false,tasks:false}});const page=await context.newPage();await page.goto('/');const nav=page.locator('.workspace>.mobile-app-tabs');await expect(nav.locator('button')).toHaveCount(3);await nav.locator('[data-mobile-view=settings]').click();await page.waitForTimeout(450);expect(await nav.evaluate(el=>Math.abs(el.querySelector('.mobile-tab-indicator').getBoundingClientRect().width-el.querySelector('[aria-current=page]').getBoundingClientRect().width)<1)).toBe(true);await page.close();
  await api('/modules','PUT',{enabled:{chat:false,notes:false,tracker:false,tasks:false}});const single=await context.newPage();await single.goto('/');const one=single.locator('.workspace>.mobile-app-tabs');await expect(one.locator('button')).toHaveCount(1);await expect(one.locator('[data-mobile-view=settings]')).toHaveAttribute('aria-current','page');expect(await one.evaluate(el=>Math.abs(el.querySelector('.mobile-tab-indicator').getBoundingClientRect().width-el.querySelector('button').getBoundingClientRect().width)<1)).toBe(true);await single.close();
 }
 expect(errors).toEqual([]);await writeFile(`${folder}/web-${baseline?'before':'after'}-${engine.name()}.json`,JSON.stringify({checks,errors},null,2));console.log('Bottom tabs checked in '+engine.name());
}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});}
