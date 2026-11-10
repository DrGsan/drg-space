import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/security/index.js'],bundle:true,format:'iife',globalName:'SessionSecurity',write:false,loader:{'.css':'empty'}});
const css=await readFile('app/static/assets/app.css','utf8');
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
 const page=await browser.newPage({locale:'ru-RU',viewport:{width:390,height:1100}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://session-devices.test/**',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><main id="fixture" style="padding:12px"></main><div id="toast"></div>'}));
 await page.goto('http://session-devices.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
 await page.evaluate(async()=>{
  const stamp=Math.floor(Date.now()/1000),state=window.sessionFixture={calls:[],rows:[{id:'one',device:'iOS · Safari · Web App',name:'',current:true,online:true,ip:'2001:db8:1234:5678:1234:5678:1234:5678',created:stamp-500,expires:stamp+3600,last_seen:stamp,can_revoke:true},{id:'two',device:'Windows · Chrome · Web',name:'Рабочий компьютер',current:false,online:false,ip:'203.0.113.7',created:stamp-5000,expires:stamp+3600,last_seen:stamp-300,can_revoke:true}]};
  const ctx={state:{me:{id:'owner',role:'owner',two_factor:true}},navigate(){},toast(t){document.querySelector('#toast').textContent=t;},modal(html){const el=document.createElement('div');el.className='modal-overlay';el.innerHTML='<section class="modal" role="dialog"><button class="modal-close">Закрыть</button>'+html+'</section>';document.body.append(el);el.querySelector('.modal-close').onclick=()=>el.remove();return el;},async api(path,method='GET',body){
   state.calls.push({path,method,body});
   if(path==='/security/sessions')return state.rows;
   if(path.startsWith('/security/sessions/')&&method==='PATCH'){state.rows.find(r=>r.id===path.split('/').pop()).name=body.name.trim();return {ok:true};}
   if(path==='/security/passkeys')return {items:[]};
   if(path==='/secret-vault')return {enabled:false};
   return {};
  }};
  await window.SessionSecurity.mountSecurity(document.querySelector('#fixture'),ctx);
 });
 const sessions=page.locator('[data-sessions]');
 await sessions.getByText('iOS · Safari · Web App',{exact:true}).waitFor();
 assert.match(await sessions.textContent(),/Последняя активность/);assert.match(await sessions.textContent(),/2001:db8:/);
 await page.locator('[data-name-session="one"]').click();await page.getByLabel('Имя',{exact:true}).fill('<img src=x onerror="window.injected=true">');await page.locator('[data-session-name] button[type=submit]').click();await page.getByText('Имя сеанса сохранено.',{exact:true}).waitFor();
 assert.equal(await sessions.locator('img').count(),0);assert.equal(await page.evaluate(()=>window.injected),undefined);
 await page.locator('[data-name-session="one"]').click();await page.getByLabel('Имя',{exact:true}).fill('Семейный iPhone');await page.locator('[data-session-name] button[type=submit]').click();await sessions.getByText('Семейный iPhone',{exact:true}).waitFor();
 for(const width of [320,390,462,1280]){await page.setViewportSize({width,height:1100});await sessions.screenshot({path:`artifacts/session-devices-${width}.png`});
 const bounds=await page.locator('[data-name-session="two"]').evaluate(button=>{const next=button.nextElementSibling,a=button.getBoundingClientRect(),b=next.getBoundingClientRect();return {gap:b.left-a.right,separate:b.top>=a.bottom,inside:b.right<=innerWidth};});
 assert.ok(bounds.gap>=12||bounds.separate,`Session actions overlap at ${width}`);assert.ok(bounds.inside,`Session action overflows at ${width}`);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Horizontal overflow at ${width}`);}
 await page.locator('[data-name-session="one"]').click();await page.getByLabel('Имя',{exact:true}).fill('');await page.locator('[data-session-name] button[type=submit]').click();await sessions.getByText('iOS · Safari · Web App',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('Session names, device/last activity/IP, escaping and mobile widths passed.');
}finally{await browser.close();}
