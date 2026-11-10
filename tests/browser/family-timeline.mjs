// Real application shell, disposable encrypted account, deterministic archive responses.
import {chromium,webkit,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';
const phase=process.env.DRG_FAMILY_PHASE||'after',engine=process.env.DRG_BROWSER_ENGINE||'chromium',monthReview=process.env.DRG_FAMILY_MONTH_REVIEW==='1',ordinary=process.env.DRG_FAMILY_NORMAL_BROWSER==='1';
const dir=await mkdtemp(tmpdir()+'/drg-family-timeline-'),origin='http://127.0.0.1:8044';
const server=spawn('.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8044','tests.browser_app:app'],{env:{...process.env,DATA_DIR:dir,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-family-review-'.repeat(4),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FAMILY_PHOTO_URL:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
const exited=new Promise(resolve=>server.once('exit',resolve));let output='',browser;server.stderr.on('data',d=>output=(output+d).slice(-3000));
try{
 let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(r=>setTimeout(r,200));}if(!ready)throw Error(output);
 browser=await({chromium,webkit}[engine]).launch();const context=await browser.newContext({baseURL:origin,viewport:{width:1440,height:960},locale:'ru-RU'});
 if(!ordinary)await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let csrf='';const password='Gallery-review-password-123';const api=async(path,data,method='POST')=>{const r=await context.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});if(!r.ok())throw Error(path+' '+await r.text());return r.json();};
 csrf=(await api('/setup',{login:'preview',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await api('/2fa/enroll',{password}),bits=[...secret].map(c=>'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 await api('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await api('/appearance',{theme:'ocean',style:'glass',mode:'light',wallpaper:'mountains',seasonal:'off'},'PUT');
 await page.route('**/api/modules',async route=>{const response=await route.fetch(),data=await response.json();data.enabled.family_photos=true;data.catalog.push({id:'family_photos',kind:'module',title:'Семейные фото',available:true,default_enabled:true});await route.fulfill({response,json:data});});
 const arts=['forest','coast','nature-waterfall','mountains','lavender','dunes','cozy-terrace','cozy-winter','warm','cozy-rain','cozy-library','cozy-evening'];
 const groups=[{id:'family',title:'Семья',history_complete:true,media_pending:0},{id:'parents',title:'Родители',history_complete:true,media_pending:0}];
 const files=arts.map((art,i)=>({id:'photo-'+i,group_id:'family',message_id:100-i,created:Date.parse(i<3?'2026-10-25T12:00:00Z':i<6?'2026-10-18T12:00:00Z':'2026-09-11T12:00:00Z')/1000,kind:'photo',state:'ready',name:art+'.webp',url:'/assets/wallpapers/'+art+'.webp',thumbnail_url:'/assets/wallpapers/'+art+'.webp',sender:i%2?'Папа':'Мама'}));
 files[2].kind='video';
 if(monthReview){
  files.splice(0,files.length,...Array.from({length:120},(_,month)=>[28,8].map((day,index)=>({id:'month-'+month+'-'+day,group_id:'family',message_id:1000-month*2-index,created:Date.UTC(2018,5-month,day,12)/1000,kind:'photo',state:'ready',name:'archive.webp',url:'/assets/wallpapers/'+arts[month%arts.length]+'.webp',thumbnail_url:'/assets/wallpapers/'+arts[month%arts.length]+'.webp',sender:'Семья'}))).flat());
  await api('/appearance',{theme:'ocean',style:'glass',mode:'dark',wallpaper:'mountains',seasonal:'off'},'PUT');
 }
 let version=1;const requests=[];
 await page.route('**/api/family-photos/**',route=>{const u=new URL(route.request().url()),path=u.pathname;requests.push(path+u.search);let data;
 if(path==='/api/family-photos/groups')data={items:groups,status:{gallery_versions:true,gallery_index:true}};
 else if(path.endsWith('/media-version'))data={version:String(version)};
 else if(path.endsWith('/sync'))data={state:'idle',history_complete:true,pending:0};
 else if(path.endsWith('/media-index')){
  const buckets=new Map();for(const file of files){const date=new Date(file.created*1000),key=date.toISOString().slice(0,7);if(!buckets.has(key))buckets.set(key,{key,count:0,start:Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),1)/1000,end:Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,1)/1000});buckets.get(key).count++;}
  data={items:[...buckets.values()].sort((a,b)=>b.key.localeCompare(a.key)),version:String(version)};
 }
 else if(path.endsWith('/media')){
  const available=files.filter(f=>(!u.searchParams.has('until')||f.created<Number(u.searchParams.get('until')))&&(!u.searchParams.has('before')||f.message_id<Number(u.searchParams.get('before'))));const limit=monthReview?60:6;
  data={items:available.slice(0,limit),more:available.length>limit};
 }
 else if(path.endsWith('/unread'))data={unread:0};
 else data={items:[],more:false};
 if(path.includes('/groups/parents/media')&&!path.endsWith('media-version'))data={items:files.slice(0,1).map(f=>({...f,group_id:'parents',sender:'Родители'})),more:false};
 return route.fulfill({json:data});});
 const out='artifacts/family-timeline/'+(monthReview?'index-':'')+phase+'/'+engine+(ordinary?'-browser':'');await mkdir(out,{recursive:true});
 if(monthReview){
  await page.setViewportSize({width:1200,height:540});await page.goto('/');await page.locator('[data-view=family_photos]').click();await expect(page.locator('[data-fp-jump]')).toHaveCount(phase==='before'?30:120);await page.locator('[data-fp-day]').nth(14).waitFor();
  {await page.locator('.fp-date-rail').evaluate(el=>{el.scrollTop=el.scrollHeight;});await page.screenshot({path:out+'/complete-index-on-open.png',animations:'disabled'});await writeFile(out+'/initial-index.json',JSON.stringify({months:await page.locator('[data-fp-jump]').count(),loadedPhotos:await page.locator('.fp-tile').count(),indexRequests:requests.filter(p=>p.includes('/media-index')).length},null,2));}
  await page.evaluate(ordinary=>{const root=ordinary?document.scrollingElement:document.querySelector('#app'),target=document.querySelectorAll('[data-fp-day]')[14];root.scrollTop+=target.getBoundingClientRect().top-(ordinary?0:root.getBoundingClientRect().top)-80;},ordinary);
  await page.waitForTimeout(150);await page.screenshot({path:out+'/long-archive.png',animations:'disabled'});
  const metrics=await page.locator('.fp-date-rail').evaluate(el=>({height:el.clientHeight,scrollHeight:el.scrollHeight,top:el.getBoundingClientRect().top,viewport:innerHeight}));
  await writeFile(out+'/metrics.json',JSON.stringify(metrics,null,2));
  if(phase==='after'){
   expect(metrics.height).toBeLessThanOrEqual(492);expect(metrics.top).toBeGreaterThanOrEqual(20);expect(metrics.scrollHeight).toBeGreaterThan(metrics.height);expect(await page.locator('.fp-tile').count()).toBeLessThanOrEqual(60);expect(requests.filter(p=>p.includes('/media-index')).length).toBe(1);
   const position=await page.evaluate(ordinary=>(ordinary?document.scrollingElement:document.querySelector('#app')).scrollTop,ordinary);await page.locator('.fp-date-rail').hover();await page.mouse.wheel(0,500);await expect.poll(()=>page.locator('.fp-date-rail').evaluate(el=>el.scrollTop)).toBeGreaterThan(0);expect(await page.evaluate(ordinary=>(ordinary?document.scrollingElement:document.querySelector('#app')).scrollTop,ordinary)).toBe(position);
   const buttons=page.locator('[data-fp-jump]');expect(await buttons.evaluateAll(es=>es.every(e=>/^\d{4}-\d{2}$/.test(e.dataset.fpJump)))).toBe(true);
   for(const index of [119,6,0]){
    const button=buttons.nth(index),key=await button.getAttribute('data-fp-jump'),priorPages=requests.filter(p=>/\/media(?:\?|$)/.test(p)).length;await button.click();
    await expect(button).toHaveAttribute('aria-current','location');if(index===119){expect(requests.filter(p=>/\/media(?:\?|$)/.test(p)).length).toBe(priorPages+1);expect(await page.locator('.fp-tile').count()).toBeLessThanOrEqual(60);}
    const top=await page.locator('[data-fp-day^="'+key+'-"]').first().evaluate(el=>el.getBoundingClientRect().top);expect(top).toBeGreaterThanOrEqual(15);expect(top).toBeLessThan(150);
    if(!ordinary)expect(await page.evaluate(()=>document.documentElement.scrollTop)).toBe(0);
   }
   await page.evaluate(ordinary=>{const root=ordinary?document.scrollingElement:document.querySelector('#app'),target=document.querySelectorAll('[data-fp-day]')[16];root.scrollTop+=target.getBoundingClientRect().top-(ordinary?0:root.getBoundingClientRect().top)-24;},ordinary);
   await expect(buttons.nth(8)).toHaveAttribute('aria-current','location');
   const active=await page.locator('[aria-current=location]').boundingBox(),rail=await page.locator('.fp-date-rail').boundingBox();expect(active.y).toBeGreaterThanOrEqual(rail.y);expect(active.y+active.height).toBeLessThanOrEqual(rail.y+rail.height+1);
   const beforePoll=await page.evaluate(ordinary=>({page:(ordinary?document.scrollingElement:document.querySelector('#app')).scrollTop,menu:document.querySelector('.fp-date-rail').scrollTop}),ordinary);await Promise.all([page.waitForResponse(r=>r.url().includes('/media-version')),page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))]);await page.waitForTimeout(100);expect(await page.evaluate(ordinary=>({page:(ordinary?document.scrollingElement:document.querySelector('#app')).scrollTop,menu:document.querySelector('.fp-date-rail').scrollTop}),ordinary)).toEqual(beforePoll);
   await buttons.nth(7).focus();await page.keyboard.press('Enter');await expect(buttons.nth(7)).toHaveAttribute('aria-current','location');
   const focused=await page.evaluate(()=>document.activeElement.id);files.unshift({...files[0],id:'new-month',message_id:2000,created:Date.UTC(2019,3,28,12)/1000});version++;await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));await expect(buttons).toHaveCount(121);expect(await page.evaluate(()=>document.activeElement.id)).toBe(focused);
   await page.setViewportSize({width:1200,height:360});await expect.poll(()=>page.locator('.fp-date-rail').evaluate(el=>el.clientHeight)).toBeLessThanOrEqual(312);
   await page.screenshot({path:out+'/short-window.png',animations:'disabled'});
   await page.setViewportSize({width:1200,height:540});await page.locator('[data-fp-jump="2017-11"]').click();await page.waitForTimeout(100);await page.screenshot({path:out+'/long-archive.png',animations:'disabled'});
  }
 }else{
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:width<500?844:960});await page.goto('/');await page.locator('.workspace').waitFor();
  if(width<500){await page.locator('[data-mobile-view=settings]').click();await page.locator('[data-mobile-open=family_photos]').click();}else await page.locator('[data-view=family_photos]').click();
  await page.locator('.fp-tile img').first().waitFor();await page.locator('.fp-tile img').first().evaluate(async im=>{if(!im.complete)await new Promise(r=>im.onload=r);});
  await page.locator('.fp-tile img').evaluateAll(async images=>{await Promise.all(images.filter(im=>{const r=im.getBoundingClientRect();return r.top<innerHeight&&r.bottom>0;}).map(im=>im.complete?Promise.resolve():new Promise((resolve,reject)=>{im.addEventListener('load',resolve,{once:true});im.addEventListener('error',reject,{once:true});})));});
  await page.screenshot({path:out+'/'+width+'.png',animations:'disabled'});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(phase==='before')continue;
  expect(await page.locator('[data-fp-day]').count()).toBeGreaterThanOrEqual(2);
  await expect(page.locator('.fp-day-heading').first()).toContainText('2 фото · 1 видео');
  await page.locator('[data-fp-search]').fill('Папа');expect(await page.locator('.fp-tile').count()).toBeGreaterThanOrEqual(3);
  if(await page.locator('[data-fp-more]').isVisible())await page.locator('[data-fp-more]').click();await expect(page.locator('.fp-tile')).toHaveCount(6);await expect(page.locator('[data-fp-day]')).toHaveCount(3);
  await page.locator('[data-fp-search]').fill('no-such-photo');await expect(page.locator('.fp-empty')).toBeVisible();await page.locator('[data-fp-search]').fill('');
  await page.locator('[data-fp-sync-details]>summary').click();await expect(page.locator('.fp-sync-popover')).toBeVisible();await page.locator('[data-fp-sync-details]>summary').click();
  await page.locator('[data-fp-group-select]').selectOption('parents');await expect(page.locator('.fp-tile')).toHaveCount(1);await expect(page.locator('.fp-tile')).toHaveAttribute('aria-label',/Родители/);await page.locator('[data-fp-group-select]').selectOption('family');
 }
 if(phase==='after'){
  await page.locator('[data-fp-file]').first().click();await expect(page.locator('[data-fp-viewer]')).toBeVisible();await page.keyboard.press('ArrowRight');await expect(page.locator('.fp-viewer-controls')).toContainText('2 /');await page.screenshot({path:out+'/viewer.png'});await page.keyboard.press('Escape');
  await page.setViewportSize({width:1440,height:960});await page.locator('[data-fp-search]').fill('');await page.evaluate(()=>document.querySelector('[data-page-edge]')?.scrollIntoView({block:'end'}));await expect(page.locator('[data-fp-day]')).toHaveCount(3);await expect(page.locator('[data-fp-jump]')).toHaveCount(2);await page.locator('[data-fp-jump]').last().click();
  await api('/appearance',{theme:'ocean',style:'classic',mode:'dark',wallpaper:'mountains',seasonal:'off'},'PUT');await api('/language',{language:'en'},'PUT');
  await page.reload();await page.locator('[data-view=family_photos]').click();await page.locator('.fp-tile').first().waitFor();await expect(page.locator('.fp-day-heading').first()).toContainText('2 photos · 1 video');await page.screenshot({path:out+'/dark.png'});
 }
 }
 expect(errors).toEqual([]);await writeFile(out+'/report.json',JSON.stringify({errors,requests:requests.length,phase,engine},null,2));console.log('Family timeline '+phase+' '+engine+': real shell, widths, search, pagination, groups, disclosure, viewer and month navigation passed.');
}finally{await browser?.close();server.kill('SIGTERM');await Promise.race([exited,new Promise(r=>setTimeout(r,5000))]);if(server.exitCode===null){server.kill('SIGKILL');await exited;}await rm(dir,{recursive:true,force:true});}
