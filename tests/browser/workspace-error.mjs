import {expect,chromium,webkit} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
const origin='http://127.0.0.1:8049',folder='artifacts/contact-error-review',baseline=process.env.DRG_ERROR_BASELINE==='1';
const directory=await mkdtemp(tmpdir()+'/drg-workspace-error-');
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8049','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'workspace-error-isolated-test-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
 for(let n=0;n<100;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}
 const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
 browser=await engine.launch({headless:true,...(process.env.DRG_BROWSER_CHANNEL?{channel:process.env.DRG_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({baseURL:origin,locale:'ru-RU',viewport:{width:1440,height:1000},reducedMotion:'reduce'});let csrf='';
 const api=async(path,method='GET',data)=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Workspace-error-test-password-123';csrf=(await api('/setup','POST',{login:'errorowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await mkdir(folder,{recursive:true});const errors=[],checks=[];
 for(const [style,mode,width,lang='ru',standalone=false,failurePath='/api/notes'] of [['classic','light',1440],['classic','light',390],['classic','dark',320],['glass','light',390],['glass','dark',1440],['classic','light',390,'en',true],['glass','dark',320,'en',true],['classic','light',390,'ru',false,'/api/modules']]){
  await api('/language','PUT',{language:lang});const me=await api('/me');await api('/appearance','PUT',{...me.settings.appearance,style,mode,seasonal:'off'});
  const page=await context.newPage();await page.addInitScript(({lang,standalone})=>{localStorage.setItem('drg-language',lang);Object.defineProperty(navigator,'standalone',{get:()=>standalone});},{lang,standalone});page.on('pageerror',e=>errors.push(e.message));let failure=true;
  await page.route('**'+failurePath,route=>failure?route.fulfill({status:503,json:{detail:lang==='en'?'The server did not respond in time. Please try again.':'Сервер не ответил вовремя. Повторите попытку.'}}):route.continue());
  let heavyRequests=0;await page.route('**/chunk-blocks-*.js',route=>{heavyRequests++;return route.abort();});await page.setViewportSize({width,height:1000});await page.goto('/');
  await expect(page.getByRole('heading',{name:lang==='en'?'Could not load workspace':'Не удалось загрузить пространство',exact:true})).toBeVisible();
  const controls=page.locator('[data-workspace-retry],[data-workspace-chat],a[href="/"]').filter({visible:true});await expect(controls).toHaveCount(failurePath==='/api/modules'?2:3);expect(heavyRequests).toBe(0);
  const boxes=await controls.evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}}));
  if(!baseline){
   for(const b of boxes){expect(b.x).toBeGreaterThanOrEqual(0);expect(b.x+b.width).toBeLessThanOrEqual(width+1);expect(b.height).toBeGreaterThanOrEqual(40);}
   for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],b=boxes[j];const x=Math.max(b.x-a.x-a.width,a.x-b.x-b.width),y=Math.max(b.y-a.y-a.height,a.y-b.y-b.height);expect(Math.max(x,y)).toBeGreaterThanOrEqual(7);}
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  await page.screenshot({path:`${folder}/error-${baseline?'before':'after'}-${engine.name()}-${style}-${mode}-${width}${lang==='ru'?'':'-'+lang}${standalone?'-webapp':''}${failurePath==='/api/modules'?'-partial':''}.png`});checks.push({style,mode,width,lang,standalone,failurePath,boxes});
  if(!baseline){await expect(page.locator('#workspace-error-title')).toBeFocused();await page.keyboard.press(engine.name()==='webkit'?'Alt+Tab':'Tab');await expect(page.locator('[data-workspace-retry]')).toBeFocused();await expect(page.getByRole('link',{name:lang==='en'?'Go to home':'На главную',exact:true})).toHaveAttribute('href','/');}
  await page.locator('[data-workspace-retry]').click();await expect(page.locator('[data-workspace-retry]')).toBeEnabled();await expect(page.getByRole('heading',{name:lang==='en'?'Could not load workspace':'Не удалось загрузить пространство',exact:true})).toBeVisible();
  failure=false;await page.locator('[data-workspace-retry]').click();await expect(page.locator('.workspace')).toBeVisible();await page.close();
 }
 // The same layout must handle an actual API deadline, not just an HTTP 503.
 if(!baseline){
  await api('/language','PUT',{language:'ru'});const page=await context.newPage();let release;
  await page.addInitScript(()=>localStorage.setItem('drg-language','ru'));
  await page.route('**/api/notes',route=>new Promise(resolve=>{release=()=>{route.abort().catch(()=>{}).finally(resolve);};}));
  await page.goto('/');await expect(page.getByRole('heading',{name:'Не удалось загрузить пространство',exact:true})).toBeVisible({timeout:20000});
  await expect(page.locator('.workspace-error-message')).toContainText('не ответил вовремя');release();await page.close();
  const chat=await context.newPage();await chat.route('**/api/notes',route=>route.fulfill({status:503,json:{detail:'Unavailable'}}));await chat.goto('/');
  await expect(chat.locator('[data-workspace-chat]')).toBeVisible();await chat.locator('[data-workspace-chat]').click();await expect(chat.locator('.workspace')).toHaveAttribute('data-current-view','chat');await chat.close();
 }
 expect(errors).toEqual([]);await writeFile(`${folder}/error-${baseline?'before':'after'}-${engine.name()}.json`,JSON.stringify({checks,errors},null,2));console.log('Workspace recovery UI checked in '+engine.name());
}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});}
