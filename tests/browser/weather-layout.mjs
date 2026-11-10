import {expect,chromium,webkit} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
const origin='http://127.0.0.1:8064',folder='artifacts/reminders-weather/weather-checks/'+(process.env.DRG_BROWSER_ENGINE||'chromium');
const directory=await mkdtemp(tmpdir()+'/drg-weather-layout-');
function totp(secret){let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');}
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8064','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'weather-layout-isolated-test-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
 for(let n=0;n<100;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,100));}
 const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium;
 browser=await engine.launch({headless:true,...(process.env.DRG_BROWSER_CHANNEL?{channel:process.env.DRG_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({baseURL:origin,locale:process.env.DRG_WEATHER_LANGUAGE==='en'?'en-US':'ru-RU',viewport:{width:390,height:844}});
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));let csrf='';
 const api=async(path,method='GET',data)=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Weather-layout-test-password-123';csrf=(await api('/setup','POST',{login:'weatherowner',password,token:'browser-test-setup-key'})).csrf;
 const enrollment=await api('/2fa/enroll','POST',{password});await api('/2fa/confirm','POST',{code:totp(enrollment.secret)});csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true})).csrf;
 await api('/modules','PUT',{enabled:{tracker:true}});await mkdir(folder,{recursive:true});const errors=[];


 await api('/language','PUT',{language:process.env.DRG_WEATHER_LANGUAGE||'ru'});
 await api('/appearance','PUT',{theme:'ocean',style:'glass',mode:process.env.DRG_WEATHER_MODE||'light',wallpaper:'mountains',seasonal:'off'});
 const layout=await api('/dashboard/layout');layout.instances=['2x2','1x1','2x1','1x2'].map(size=>({id:'weather_'+size,module_id:'weather',title:'',size,config:{city:'Москва',latitude:55.75,longitude:37.62}}));
 await api('/dashboard/layout','PUT',layout);
 const forecast=JSON.parse(await readFile(new URL('../fixtures/weather-layout.json',import.meta.url),'utf8'));
 const report=[];
 for(const code of [3,0])for(const width of [1440,430,390,320]){
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.setViewportSize({width,height:950});
  await page.route('**/api/widgets/*/weather',route=>route.fulfill({json:{...forecast,current:{...forecast.current,weather_code:code,temperature_2m:code===0?20:6}}}));
  await page.goto('/');await page.locator('.workspace').waitFor();
  if(width<760){await page.locator('[data-mobile-view=settings]').click();await page.locator('[data-mobile-open=home]').click();}
  await page.locator('.weather-current').first().waitFor();await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(600);
  for(const size of ['1x1','2x1','1x2','2x2']){
   const widget=page.locator('[data-widget-id="weather_'+size+'"]');
   await widget.scrollIntoViewIfNeeded();
   await widget.screenshot({path:folder+'/weather-'+size+'-'+width+'-'+code+'.png',animations:'disabled'});
   report.push(await widget.evaluate((e,info)=>{
    const r=n=>n.getBoundingClientRect().toJSON(),icon=e.querySelector('.weather-current .weather-symbol');
    return {...info,widget:r(e),icon:r(icon),temperature:r(e.querySelector('.weather-temperature')),condition:r(e.querySelector('.weather-condition')),metrics:r(e.querySelector('.weather-metrics')),source:r(e.querySelector('.weather-source')),forecast:[...e.querySelectorAll('.weather-period-icon,.weather-hour strong>span')].map(r),body:{w:e.querySelector('[data-widget-body]').clientWidth,h:e.querySelector('[data-widget-body]').clientHeight,scrollHeight:e.querySelector('[data-widget-body]').scrollHeight}};
   },{size,width,code}));
  }
  await page.close();
 }
 for(const row of report){
  expect(row.body.scrollHeight,'Clipped weather content '+JSON.stringify({size:row.size,width:row.width,code:row.code})).toBeLessThanOrEqual(row.body.h+1);
  expect(row.metrics.bottom,'Metrics must clear forecast source').toBeLessThanOrEqual(row.source.top+1);
  expect(row.icon.width).toBeGreaterThan(0);
  expect(row.icon.left).toBeGreaterThanOrEqual(row.widget.left-1);expect(row.icon.right).toBeLessThanOrEqual(row.widget.right+1);
  expect(row.icon.top).toBeGreaterThanOrEqual(row.widget.top-1);expect(row.icon.bottom).toBeLessThanOrEqual(row.widget.bottom+1);
  for(const text of [row.temperature,row.condition]){
   const overlapX=Math.min(row.icon.right,text.right)-Math.max(row.icon.left,text.left),overlapY=Math.min(row.icon.bottom,text.bottom)-Math.max(row.icon.top,text.top);
   expect(overlapX>1&&overlapY>1,'Icon must clear temperature and condition').toBe(false);
  }
 }
 expect(errors).toEqual([]);await writeFile(folder+'/weather-metrics.json',JSON.stringify(report,null,2));console.log('Weather captures ready',folder);
}finally{await browser?.close();server.kill('SIGTERM');await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));await rm(directory,{recursive:true,force:true});}
