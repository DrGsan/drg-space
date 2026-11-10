import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const bundle=await build({entryPoints:['web/modules/admin/security.js'],bundle:true,format:'iife',globalName:'AdminSecurity',write:false,loader:{'.css':'empty'}});
const css=(await Promise.all(['app/static/assets/app.css','web/modules/admin/security.css'].map(path=>readFile(path,'utf8')))).join('\n');
const engine=process.env.DRG_BROWSER_ENGINE||'chromium';
const browser=await (engine==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  const page=await browser.newPage({locale:'ru-RU',viewport:{width:1280,height:1100},acceptDownloads:true}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://admin-security.test/**',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><div class="workspace" style="display:block;padding:20px"><main id="view-content" class="preferences-surface"><div class="preference-rows"><details class="preference-row" data-preference-key="security"><summary><span class="preference-label"><strong>Безопасность портала</strong></span><span class="preference-status" data-preference-status></span></summary><section class="panel admin-security" id="fixture"></section></details></div></main></div><div id="toast"></div>'}));
  await page.goto('http://admin-security.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
    const stamp=Math.floor(Date.now()/1000),attack='<img src=x onerror="window.securityInjected=true">';
    const state=window.securityFixture={calls:[],mode:{mode:'normal',until:0,reason:''},bans:[],reviewed:false,revoke:[],holdEvents:false,failEvents:false};
    const events=[{id:'event-a',event:'auth_failure',action:'2fa',outcome:'failure',severity:'high',ip:'2001:db8:1234:ffff:1234:ffff:1234:abcd',subject:attack,agent:attack,route:'/api/2fa/verify',details:{reason:attack},first_seen:stamp-300,last_seen:stamp-30,count:8},{id:'event-b',event:'auth_success',action:'login',outcome:'success',severity:'info',ip:'203.0.113.9',subject:'owner',details:{},first_seen:stamp-60,last_seen:stamp-50,count:1},{id:'event-c',event:'login_success',action:'login',outcome:'success',severity:'info',ip:'203.0.113.9',subject:'owner',details:{},first_seen:stamp-40,last_seen:stamp-40,count:1}];
    const incident={id:'incident-a',title:'Пароль принят, повторные ошибки второго фактора',severity:'high',status:'open',ip:'203.0.113.9',subject:'owner',first_seen:stamp-300,last_seen:stamp-30,event_count:8,ip_count:1};
    const ctx=window.securityCtx={state:{me:{role:'owner',id:'owner',two_factor:true}},toast(text){document.querySelector('#toast').textContent=text;},navigate(target){state.navigation=target;},modal(html){
      const el=document.createElement('div');el.className='modal-overlay';el.innerHTML='<section class="modal" role="dialog" aria-modal="true"><button class="modal-close icon-btn" aria-label="Закрыть">×</button>'+html+'</section>';document.body.append(el);el.querySelector('.modal-close').onclick=()=>el.remove();return el;
    },async api(path,method='GET',data){
      state.calls.push({path,method,data});const url=new URL(path,'http://admin-security.test');
      if(method!=='GET'&&!path.endsWith('/review')&&data.password!=='Valid-test-password-123')throw Error('Неверное подтверждение.');
      if(url.pathname==='/admin/security')return {days:Number(url.searchParams.get('days')),mode:state.mode,counts:{failed:8,successful:1,events:10,blocked:3,open_incidents:state.reviewed?0:1,active_bans:state.bans.length},health:{ok:true},policy:{event_days:365,incident_days:365,max_events:50000,max_incidents:5000}};
      if(url.pathname==='/admin/security/events'){
        if(state.failEvents)throw Error('Временная ошибка загрузки.');
        const result={events:url.searchParams.get('offset')==='50'?[events[2]]:events,total:51,limit:50};
        if(state.holdEvents)return new Promise(resolve=>state.releaseEvents=()=>resolve(result));return result;
      }
      if(url.pathname==='/admin/security/events/export')return {events,total:3,truncated:false};
      if(url.pathname==='/admin/security/incidents')return {incidents:state.reviewed?[]:[incident],total:state.reviewed?0:1,limit:50};
      if(url.pathname==='/admin/security/incidents/incident-a')return {incident,events};
      if(url.pathname==='/admin/security/incidents/incident-a/review'){state.reviewed=data.status==='reviewed';return {ok:true};}
      if(url.pathname==='/admin/security/bans'){
        if(method==='POST'){state.bans.push({id:'ban-a',ip:data.ip,until:stamp+data.duration,kind:'ip',action:'manual'});return {ok:true};}
        return {bans:state.bans,total:state.bans.length};
      }
      if(url.pathname==='/admin/security/bans/ban-a'){state.bans=[];return {ok:true};}
      if(url.pathname==='/admin/security/sessions')return {sessions:[{id:'current',login:'owner',current:true,stage:'full',created:stamp-300,expires:stamp+9999,ip:'203.0.113.2',last_seen:stamp},{id:'other',login:attack,current:false,stage:'pending',created:stamp-500,expires:stamp+9999,agent:attack,ip:'203.0.113.9'}].filter(s=>!state.revoke.includes(s.id))};
      if(url.pathname==='/admin/security/sessions/other/revoke'){state.revoke.push('other');return {ok:true};}
      if(url.pathname==='/admin/security/mode'){state.mode={mode:data.mode,until:data.duration?stamp+data.duration:0,reason:data.reason};return {ok:true,mode:state.mode};}
      throw Error('Unexpected '+path);
    }};
    AdminSecurity.mountAdminSecurity(document.querySelector('#fixture'),ctx);
  });
  assert.equal(await page.evaluate(()=>securityFixture.calls.length),0,'security history loads only after opening its section');
  await page.locator('.preference-row>summary').click();await page.locator('.as-event').first().waitFor();
  assert.equal(await page.locator('[data-as-view] img').count(),0,'untrusted names cannot create HTML');
  assert.equal(await page.evaluate(()=>window.securityInjected),undefined);
  await page.locator('.as-event').first().locator('summary').click();
  assert.match(await page.locator('.as-event').first().innerText(),/Ошибка второго фактора/);
  assert.match(await page.locator('.as-event').nth(1).innerText(),/Пароль принят, проверка входа продолжается/);
  assert.match(await page.locator('.as-event').nth(2).innerText(),/Вход выполнен/);
  await page.locator('[data-as-days]').selectOption('365');await page.waitForFunction(()=>securityFixture.calls.some(c=>c.path.includes('/events?days=365')));
  await page.locator('[data-as-days]').selectOption('7');await page.locator('[data-as-next]').waitFor();
  await page.locator('[name=ip]').fill('203.0.113.9');await page.locator('[name=severity]').selectOption('high');await page.locator('[data-as-filters] [type=submit]').click();
  await page.waitForFunction(()=>securityFixture.calls.some(c=>c.path.includes('/events?')&&c.path.includes('days=7')&&c.path.includes('ip=203.0.113.9')&&c.path.includes('severity=high')));
  await page.locator('[data-as-next]').click();await page.waitForFunction(()=>securityFixture.calls.at(-1).path.includes('offset=50'));
  assert.match(await page.locator('.as-pagination').innerText(),/51–51 из 51/);
  const downloaded=page.waitForEvent('download');await page.locator('[data-as-export]').click();const download=await downloaded;
  assert.match(download.suggestedFilename(),/^drg-security-.*\.json$/);assert(await download.path());
  assert(await page.evaluate(()=>securityFixture.calls.some(c=>c.path.includes('/events/export?')&&c.path.includes('severity=high')&&!c.path.includes('offset=50'))));
  await page.locator('[data-as-tab=incidents]').click();await page.locator('[data-as-incident]').click();
  assert.match(await page.locator('[data-as-incident-content]').innerText(),/Связанные события/);
  await page.locator('[data-as-review]').click();await page.locator('[data-as-incident-content]').waitFor({state:'detached'});await page.waitForFunction(()=>securityFixture.reviewed);
  assert.match(await page.locator('[data-as-view]').innerText(),/Открытых инцидентов нет/);
  await page.locator('[data-as-tab=bans]').click();await page.locator('[data-as-add-ban]').click();
  const form=()=>page.locator('[data-as-confirm]');
  await form().locator('[name=ip]').fill('203.0.113.9');await form().locator('[name=reason]').fill('Проверка серии неудачных входов');await form().locator('[name=password]').fill('wrong');await form().locator('[name=code]').fill('123456');await form().locator('[type=submit]').click();
  await page.locator('[data-as-form-status]').filter({hasText:'Неверное подтверждение'}).waitFor();
  assert.equal(await form().locator('[name=password]').inputValue(),'');assert.equal(await form().locator('[name=code]').inputValue(),'');
  assert.equal(await page.evaluate(()=>securityFixture.bans.length),0,'failed proof cannot apply a ban');
  const prove=async()=>{await form().locator('[name=password]').fill('Valid-test-password-123');await form().locator('[name=code]').fill('654321');await form().locator('[type=submit]').click();await form().waitFor({state:'detached'});};
  await prove();await page.locator('[data-as-unban]').waitFor();
  await page.locator('[data-as-unban]').click();await form().locator('[name=reason]').fill('Проверка завершена');await prove();assert.equal(await page.evaluate(()=>securityFixture.bans.length),0);
  await page.locator('[data-as-tab=sessions]').click();await page.locator('[data-as-revoke]').waitFor();
  assert.equal(await page.locator('[data-as-revoke]').count(),1,'no revoke action for current owner session');
  await page.locator('[data-as-revoke]').click();assert.equal(await page.locator('[role=dialog] img').count(),0);await form().locator('[name=reason]').fill('Неизвестное устройство');await prove();await page.locator('[data-as-revoke]').waitFor({state:'detached'});
  await page.locator('[data-as-mode]').click();await form().locator('[name=mode]').selectOption('logins_closed');assert.match(await page.locator('[data-as-mode-explanation]').innerText(),/действующими сеансами продолжают/);
  await form().locator('[name=mode]').selectOption('lockdown');assert.match(await page.locator('[data-as-mode-explanation]').innerText(),/публикации, файлы и активные соединения/);assert.equal(await form().locator('[name=duration]').inputValue(),'0','lockdown defaults to manual reopening');
  await form().locator('[name=duration]').selectOption('0');await form().locator('[name=reason]').fill('Проверяем подозрительные входы');await prove();assert.equal(await page.evaluate(()=>securityFixture.mode.mode),'lockdown');
  assert.match(await page.locator('.as-mode').innerText(),/До ручного отключения/);
  await page.locator('[data-as-tab=events]').click();await page.locator('[data-as-list][aria-busy=false]').waitFor();await page.locator('[data-as-reset]').click();await page.locator('[data-as-list][aria-busy=false]').waitFor();
  await page.locator('.as-event').first().locator('summary').click();
  for(const scheme of ['light','dark'])for(const width of [1280,390,320]){
    await page.evaluate(value=>document.documentElement.dataset.scheme=value,scheme);await page.setViewportSize({width,height:1100});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`no overflow in ${scheme} at ${width}`);
    await page.screenshot({path:`artifacts/admin-security-${engine}-${scheme}-${width}.png`,fullPage:true});
  }
  await page.locator('[data-as-mode]').click();await form().locator('[name=mode]').selectOption('lockdown');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'modal has no mobile overflow');
  await page.screenshot({path:`artifacts/admin-security-mode-${engine}-320.png`,fullPage:true});await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.evaluate(()=>securityFixture.holdEvents=true);await page.locator('[data-as-refresh]').click();await page.waitForFunction(()=>!!securityFixture.releaseEvents);
  await page.locator('[data-as-tab=sessions]').click();await page.locator('.as-current').waitFor();await page.evaluate(()=>{securityFixture.holdEvents=false;securityFixture.releaseEvents();});
  assert.equal(await page.locator('.as-event').count(),0,'late journal response cannot overwrite another tab');
  await page.evaluate(()=>securityFixture.failEvents=true);await page.locator('[data-as-tab=events]').click();await page.locator('[data-as-retry]').waitFor();
  assert.match(await page.locator('[data-as-view]').innerText(),/Временная ошибка/);
  await page.evaluate(()=>securityFixture.failEvents=false);await page.locator('[data-as-retry]').click();await page.locator('.as-event').first().waitFor();
  await page.evaluate(()=>securityCtx.state.me.two_factor=false);await page.locator('[data-as-mode]').click();await page.locator('[data-as-enable-factor]').waitFor();assert.equal(await form().count(),0,'dangerous actions explain required second factor');await page.locator('[data-as-enable-factor]').click();assert.equal(await page.evaluate(()=>securityFixture.navigation),'security');
  await page.evaluate(()=>{const host=document.createElement('div');host.id='non-owner';document.body.append(host);AdminSecurity.mountAdminSecurity(host,{state:{me:{role:'member'}},api(){throw Error('Member must not fetch privileged data');}});});
  assert.match(await page.locator('#non-owner').innerText(),/только администратору/);
  assert.deepEqual(errors,[]);
  console.log('Admin security passed: owner gate, lazy history, safe attacker strings, filters/export/pagination, stage labels, incident review, proof errors/secret clearing, bans, current-session protection, lockdown, responsive themes, stale response and retry.');
}finally{await browser.close();}
