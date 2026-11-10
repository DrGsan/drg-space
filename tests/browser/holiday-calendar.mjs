// Real application and isolated SQLCipher account; no production data is used.
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdir,mkdtemp,rm,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {appSection} from './notes-ui.js';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const directory=await mkdtemp(tmpdir()+'/drg-seasonal-'),origin='http://127.0.0.1:8062';
const out='artifacts/drg-82-41/browser/'+engine;await mkdir(out,{recursive:true});
const themes=JSON.parse(await readFile('app/seasonal_themes.json','utf8'));
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8062','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-seasonal-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));const exited=new Promise(resolve=>server.once('exit',resolve));
const report={engine,screenshots:[],matrix:[],headers:[]};
try{
  let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,200));}
  if(!ready)throw Error(output);
  browser=await ({chromium,webkit}[engine]).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1000},colorScheme:'light',timezoneId:'Europe/Moscow'});
  await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,get:()=>true}));
  const page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  let csrf='',language='ru';const password='Seasonal-test-password-123';
  const send=async(path,body,method='POST')=>{const r=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
  csrf=(await send('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
  const {secret}=await send('/2fa/enroll',{password}),alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
  await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
  await send('/modules',{enabled:{tracker:true,chat:false}},'PUT');
  const phase=process.env.DRG_CALENDAR_PHASE||'after';
  const source={year:2026,events:[{start:'2026-11-03',end:'2026-11-04',title:'* Сокращённый день',name:'shortDay',kind:'short_day'},{start:'2026-11-04',end:'2026-11-05',title:'День народного единства',name:'unityDay',kind:'holiday'}],has_data:true,status:'ready',next_check_at:Math.floor(Date.now()/1000)+86400};
  let requests=0,subscription=source;
  await page.route('**/api/calendar/holidays?*',async route=>{requests++;await new Promise(r=>setTimeout(r,120));await route.fulfill({json:subscription});});
  await send('/appearance',{wallpaper:'mountains',mode:'light',style:'glass',seasonal:'off'},'PUT');
  const oldLayout=await send('/dashboard/layout',undefined,'GET');
  await send('/dashboard/layout',{version:oldLayout.version,instances:['1x1','2x1','1x2','2x2'].map((size,i)=>({id:'calendar_qa_'+i,module_id:'calendar',title:'Календарь · '+size,size,config:{production:true,thematic:true}}))},'PUT');
  await page.clock.setFixedTime(new Date('2026-11-04T09:00:00Z'));await page.goto('/');
  await page.locator('.calendar-panel').first().waitFor();await expect(page.locator('.calendar-panel')).toHaveCount(4);
  await expect(page.locator('.calendar-panel [inert]')).toHaveCount(0);
  await page.screenshot({path:out+'/'+phase+'-home-1440.png',fullPage:true});
  if(phase==='after'){
    await expect(page.locator('.calendar-off[data-day="2026-11-04"]')).toHaveCount(4);expect(requests).toBe(1);
    for(const size of ['1x1','2x1','1x2','2x2']){
      const widget=page.locator(`.widget-${size}.calendar-panel`);
      await widget.locator('[data-day="2026-11-03"]').click();await expect(widget.locator('.calendar-agenda')).toContainText('Сокращённый день');
      await expect(page.locator('#widget-grid')).toBeVisible();
      await widget.screenshot({path:out+`/after-${size}.png`});
    }
    for(const size of ['1x2','2x2']){
      const widget=page.locator(`.widget-${size}.calendar-panel`);
      await widget.locator('[data-shift="1"]').click();await widget.locator('[data-day="2026-12-29"]').click();
      await expect(widget.locator('.calendar-agenda')).toContainText('День рождения создателя');
      const rows=await widget.locator('.calendar-upcoming time').evaluateAll(nodes=>nodes.map(node=>{const range=document.createRange();range.selectNodeContents(node);return {text:node.textContent,lines:range.getClientRects().length};}));
      expect(rows.every(row=>row.lines===1)).toBe(true);
      await widget.screenshot({path:out+`/after-${size}-birthday.png`});
    }
    expect(requests).toBe(1);
    // A different year must fetch exactly once for concurrent widgets.
    for(const size of ['1x1','2x1']){const w=page.locator(`.widget-${size}.calendar-panel`);await w.locator('[data-shift="1"]').click();await w.locator('[data-shift="1"]').click();}
    await expect.poll(()=>requests).toBe(2);
    await page.setViewportSize({width:390,height:844});await page.locator('.calendar-panel').first().waitFor();await expect(page.locator('.calendar-panel [inert]')).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:out+'/after-home-390.png',fullPage:true});
    await page.setViewportSize({width:320,height:740});await page.locator('.calendar-panel').first().waitFor();await expect(page.locator('.calendar-panel [inert]')).toHaveCount(0);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
    await page.screenshot({path:out+'/after-home-320.png',fullPage:true});
    // Birthday text belongs to the date, even when seasonal effects are off.
    await page.setViewportSize({width:1440,height:1000});await page.clock.setFixedTime(new Date('2026-12-29T09:00:00Z'));await page.reload();await appSection(page,'Главная');
    await expect(page.locator('[data-day-greeting]')).toContainText('день рождения создателя');
    await expect(page.locator('.workspace[data-seasonal-theme]')).toHaveCount(0);
    await page.screenshot({path:out+'/after-birthday-off.png',fullPage:true});
    await send('/language',{language:'en'},'PUT');subscription={...source,status:'stale'};await page.reload();
    await expect(page.locator('[data-day-greeting]')).toContainText('birthday');
    await expect(page.locator('.calendar-source').first()).toContainText('saved version');
    await page.screenshot({path:out+'/after-en-stale.png',fullPage:true});
    subscription={...source,events:[],has_data:false,status:'unavailable'};await page.clock.setFixedTime(new Date('2041-12-29T09:00:00Z'));await page.reload();
    await expect(page.locator('.calendar-source').first()).toContainText('No source data for this year');
    await expect(page.locator('.widget-1x2 .calendar-agenda')).toContainText('Creator’s birthday');
    await page.locator('#dashboard-lock').click();
    const square=page.locator('.widget-1x1.calendar-panel');await square.locator('[data-configure]').click();
    await page.locator('[name=production]').uncheck();await page.locator('[data-widget-form] button[type=submit]').click();
    await expect(page.locator('[data-widget-form]')).toHaveCount(0);
    const savedLayout=await send('/dashboard/layout',undefined,'GET');
    expect(savedLayout.instances[0].config).toEqual({production:false,thematic:true});
    expect(requests).toBeLessThanOrEqual(6);

  }
  expect(errors).toEqual([]);report.requests=requests;report.passed=true;await writeFile(out+'/'+phase+'-report.json',JSON.stringify(report,null,2));console.log('Calendar browser passed',engine,phase,requests);
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
