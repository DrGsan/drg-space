import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
const dir=await mkdtemp(tmpdir()+'/drg34-before-'),origin='http://127.0.0.1:8084';
const server=spawn('.venv/bin/python',['-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:8084','tests.browser_app:app'],{env:{...process.env,DATA_DIR:dir,DATABASE_URL:'',FILE_STORAGE:'local',REDIS_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'drg34-only-isolated-capture-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,FAMILY_PHOTO_URL:'',CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',x=>logs=(logs+x).slice(-3000));const exited=new Promise(r=>server.once('exit',r));
try{
 for(let n=0;n<150;n++){try{if((await fetch(origin+'/api/health')).ok)break;}catch{}if(server.exitCode!==null)throw Error(logs);await new Promise(r=>setTimeout(r,100));}
 browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();const context=await browser.newContext({baseURL:origin,viewport:{width:1280,height:900},locale:'ru-RU'});let csrf='';
 const api=async(path,data,method='POST')=>{const r=await context.request.fetch('/api'+path,{method,data,headers:{Origin:origin,'X-CSRF-Token':csrf}});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 const password='Local-clock-review-123';csrf=(await api('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await api('/2fa/enroll',{password}),bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 await api('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await api('/appearance',{theme:'ocean',style:'glass',mode:'light',wallpaper:'mountains',seasonal:'off'},'PUT');
 const layout=await api('/dashboard/layout',undefined,'GET');await api('/dashboard/layout',{version:layout.version,instances:[{id:'review_clock',module_id:'clock',size:'1x1',config:{timezone:'Europe/Moscow',label:'Москва'}}]},'PUT');
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('response',async r=>{if(r.url().includes('/api/dashboard/layout')&&r.status()>=400)console.log('layout error',r.status(),await r.text());});await page.goto('/');await expect(page.locator('#widget-grid>.widget-ready')).toHaveCount(1);
 const widget=page.locator('[data-widget-id=review_clock]');await page.locator('#customize-dashboard').click();await page.locator('[data-configure="0"]').click();const dialog=page.getByRole('dialog').last();
 const preview=dialog.locator('[data-clock-preview]');await expect(preview).toBeVisible();
 await expect(dialog.locator('[data-clock-theme]')).toHaveCount(6);
 const before=await api('/dashboard/layout',undefined,'GET');
 for(const mode of ['analog','digital']){await dialog.locator(`[data-clock-mode=${mode}]`).click();await expect(dialog.locator('[data-clock-theme]')).toHaveCount(6);for(const theme of await dialog.locator('[data-clock-theme]').evaluateAll(els=>els.map(e=>e.dataset.clockTheme))){await dialog.locator(`[data-clock-theme=${theme}]`).click();await expect(preview.locator('.clock-widget')).toHaveClass(new RegExp('clock-theme-'+theme));}}
 expect(await api('/dashboard/layout',undefined,'GET')).toEqual(before);
 await dialog.locator('[data-clock-theme=classic]').click();
 console.log('preview geometry',await preview.evaluate(el=>{const b=el.getBoundingClientRect(),m=el.closest('.modal').getBoundingClientRect();return {previewRight:b.right,dialogRight:m.right};}));await dialog.screenshot({path:'artifacts/drg-34/after-web-settings.png'});
 await dialog.locator('[name=clock_world]').check();await dialog.locator('[data-clock-add]').click();await dialog.locator('.clock-advanced summary').click();await dialog.locator('[name=clock_region]').selectOption('Asia/Vladivostok');await dialog.locator('[name=clock_label]').fill('Владивосток');
 await dialog.locator('[data-clock-add]').click();await dialog.locator('[name=clock_region]').selectOption('Europe/Paris');await dialog.locator('[name=clock_label]').fill('Париж');
 await dialog.locator('[data-clock-add]').click();await dialog.locator('[name=clock_region]').selectOption('Asia/Tokyo');await dialog.locator('[name=clock_label]').fill('Токио');await dialog.locator('.clock-advanced summary').click();
 await dialog.locator('[name=size]').selectOption('2x2');await expect(preview.locator('.clock-widget')).toHaveCount(4);
 await dialog.locator('[name=size]').selectOption('1x1');await expect(preview.locator('.clock-widget')).toHaveCount(1);await expect(dialog.locator('[data-clock-capacity]')).toContainText('1 из 4');
 await dialog.locator('[name=size]').selectOption('2x2');await expect(preview.locator('.clock-widget')).toHaveCount(4);
 await dialog.getByRole('button',{name:'Сохранить виджет',exact:true}).click();await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(widget.locator('.clock-widget')).toHaveCount(4);
 await page.reload();await expect(widget.locator('.clock-widget')).toHaveCount(4);const saved=await api('/dashboard/layout',undefined,'GET');expect(saved.instances[0].config.cities.map(c=>c.timezone)).toEqual(['Europe/Moscow','Asia/Vladivostok','Europe/Paris','Asia/Tokyo']);
 await expect(widget.locator('[data-widget-body]')).toHaveCSS('opacity','1');await widget.screenshot({path:'artifacts/drg-34/after-web-world.png'});
 await page.locator('#customize-dashboard').click();await page.locator('[data-configure="0"]').click();const next=page.getByRole('dialog').last();await next.locator('[data-clock-mode=analog]').click();await next.locator('[data-clock-theme=roman]').click();await next.getByRole('button',{name:'Закрыть',exact:true}).click();expect(await api('/dashboard/layout',undefined,'GET')).toEqual(saved);await page.getByRole('dialog').last().getByRole('button',{name:'Закрыть',exact:true}).click();
 for(const width of [390,320]){await page.setViewportSize({width,height:844});await page.locator('#customize-dashboard').click();await page.locator('[data-configure="0"]').click();const d=page.getByRole('dialog').last();await expect(d.locator('[data-clock-preview]')).toBeVisible();await d.locator('[name=clock_world]').uncheck();await d.locator('[name=size]').selectOption('1x1');await d.locator('[data-clock-theme=classic]').click();await d.evaluate(el=>el.scrollTop=0);await d.screenshot({path:`artifacts/drg-34/after-webapp-${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await d.getByRole('button',{name:'Закрыть',exact:true}).click();await page.getByRole('dialog').last().getByRole('button',{name:'Закрыть',exact:true}).click();}
 expect(errors).toEqual([]);console.log('Clock gallery passed: both modes/six faces, visible live preview, no writes before save, four cities/save/reload, shrink retains cities, cancel, 390/320 layouts.');
}finally{await browser?.close();server.kill('SIGTERM');await Promise.race([exited,new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null){server.kill('SIGKILL');await exited;}await rm(dir,{recursive:true,force:true});}
