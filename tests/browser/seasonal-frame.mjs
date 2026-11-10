import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac,createHash} from 'node:crypto';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {appSection} from './notes-ui.js';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium',phase=process.env.DRG_SEASONAL_FRAME_PHASE||'after',focus=process.env.DRG_SEASONAL_FRAME_FOCUS||'',focused=Boolean(focus);
const directory=await mkdtemp(tmpdir()+'/drg-seasonal-preview-'),origin='http://127.0.0.1:8062';
const out=process.env.DRG_SEASONAL_FRAME_OUTPUT||(focus==='contrast'?'artifacts/seasonal-contrast/'+phase:focus==='scroll'?'artifacts/seasonal-scroll/'+phase:focus==='seam'?'artifacts/seasonal-flush-edge':'artifacts/seasonal-frame-review')+(engine==='webkit'?'/webkit':'');await mkdir(out,{recursive:true});
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8062','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-seasonal-preview-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));const exited=new Promise(resolve=>server.once('exit',resolve));
const report={engine,phase,screenshots:[],headers:[]};
try{
 let ready=false;for(let i=0;i<150;i++){try{ready=(await fetch(origin+'/api/health')).ok;}catch{}if(ready)break;if(server.exitCode!==null)throw Error(output);await new Promise(resolve=>setTimeout(resolve,200));}if(!ready)throw Error(output);
 browser=await ({chromium,webkit}[engine]).launch({headless:true});
 const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:950},colorScheme:'light',timezoneId:'Europe/Moscow'});
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{configurable:true,get:()=>true}));
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
 let csrf='';const password='Seasonal-preview-password-123';
 const send=async(path,body,method='POST')=>{const r=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});expect(r.ok(),path+' '+await r.text()).toBe(true);return r.json();};
 csrf=(await send('/setup',{login:'alexander',password,token:'browser-test-setup-key'})).csrf;
 const {secret}=await send('/2fa/enroll',{password}),alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join(''),counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
 const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
 const recovery=(await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')})).recovery_codes;
 csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
 await send('/modules',{enabled:{tracker:true}},'PUT');
 const note=await send('/notes',{title:'Планы на выходные',body:'Выбрать маршрут и подготовить всё для прогулки.'});
 const project=await send('/tracker/projects',{name:'DrG Space',key:'SITE',description:'Работы над приложением'});
 const room=await send('/social/families',{name:'Планы на выходные'});
 await send('/tracker/projects/'+project.id+'/issues',{title:'Проверить праздничное оформление',kind:'task',priority:2});
 await send('/appearance',{wallpaper:'mountains',mode:'light',style:'glass',seasonal:'off'},'PUT');
 const stored=(await send('/appearance',undefined,'GET')).preferences;
 await page.clock.setFixedTime(new Date('2026-08-20T09:00:00Z'));await page.goto('/');
 async function settings(){
  await page.locator('.workspace').waitFor();await page.locator('[data-mobile-view=settings]').waitFor({state:'attached'});
  if(page.viewportSize().width<=760&&await page.locator('#files-main-menu').isVisible())await page.locator('#files-main-menu').click();
  else if(await page.locator('[data-mobile-view=settings]').isVisible())await page.locator('[data-mobile-view=settings]').click();else await appSection(page,'Настройки');
  await page.locator('.preference-row').first().waitFor();
  for(const row of await page.locator('.preference-row').all()){const open=await row.getAttribute('data-preference-key')==='appearance';if((await row.getAttribute('open')!==null)!==open)await row.locator(':scope>summary').click();}
  await page.locator('[data-seasonal-preview]').waitFor();
 }
 async function home(){if(await page.locator('[data-mobile-view=settings]').isVisible()){await settings();await page.locator('[data-mobile-open=home]').click();}else await appSection(page,'Главная');await page.locator('#customize-dashboard').waitFor();await expect(page.locator('.widget:not(.widget-ready)')).toHaveCount(0,{timeout:20000});}
 async function tracker(){if(await page.locator('[data-mobile-view=tracker]').isVisible())await page.locator('[data-mobile-view=tracker]').click();else await appSection(page,'Задачи');await expect.poll(async()=>await page.locator('.tracker-chrome.is-project,[data-tracker-project="'+project.id+'"]').count()).toBeGreaterThan(0);if(await page.locator('[data-tracker-project="'+project.id+'"]').count())await page.locator('[data-tracker-project="'+project.id+'"]').click();await page.locator('.tracker-chrome.is-project').waitFor();await expect(page.locator('.tracker-loading')).toHaveCount(0);}

 // Rounded card corners belong to the exterior wallpaper, where decorations
 // should remain visible. Compare the painted reading area, not those corners.
 async function protectedPaint(surface){
  await surface.scrollIntoViewIfNeeded();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const clip=await surface.evaluate(e=>{const b=e.getBoundingClientRect(),c=getComputedStyle(e),radius=Math.min(b.width/2,...['TopLeft','TopRight','BottomRight','BottomLeft'].map(k=>parseFloat(c['border'+k+'Radius'])||0))+1,x=Math.max(0,Math.ceil(b.left+radius)),y=Math.max(0,Math.ceil(b.top)+1);return {x,y,width:Math.min(innerWidth,Math.floor(b.right-radius))-x,height:Math.min(innerHeight,Math.floor(b.bottom)-1)-y};});
  return page.screenshot({clip,animations:'disabled'});
 }

 async function capture(header,theme,section,width,mode='light'){
  await expect(header.locator(':scope>.seasonal-decoration')).toHaveAttribute('data-seasonal-theme',theme);await page.evaluate(()=>document.fonts.ready);
  const metrics=await header.evaluate((h,{theme,section,width,mode})=>{
   const r=h.getBoundingClientRect(),m=h.querySelector('.seasonal-motif'),q=m.getBoundingClientRect(),elements=[...h.querySelectorAll('.seasonal-motif,.seasonal-particle,.seasonal-frieze,.seasonal-lights>i')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'),obstacles=[];
   const inClosedDetails=e=>{const d=e.closest('details:not([open])');return d&&!d.querySelector(':scope>summary')?.contains(e);};
   const read=root=>{
    for(const control of root.querySelectorAll('button,select,input,a,svg,img')){if(control.closest('.seasonal-decoration')||inClosedDetails(control))continue;const css=getComputedStyle(control),transparent=c=>c==='transparent'||/rgba\([^)]*,\s*0\s*\)/.test(c);if(!control.getClientRects().length||css.visibility==='hidden')continue;if(control.matches('button,a')&&transparent(css.backgroundColor)&&css.backgroundImage==='none'&&css.boxShadow==='none'&&['Top','Right','Bottom','Left'].every(s=>parseFloat(css['border'+s+'Width'])===0||css['border'+s+'Style']==='none'||transparent(css['border'+s+'Color'])))continue;obstacles.push({label:control.id||control.getAttribute('aria-label')||control.textContent||control.tagName,rect:control.getBoundingClientRect()});}
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);while(walker.nextNode()){const n=walker.currentNode,p=n.parentElement;if(!n.textContent.trim()||p.closest('.seasonal-decoration,select,svg')||inClosedDetails(p)||getComputedStyle(p).visibility==='hidden')continue;const range=document.createRange();range.selectNodeContents(n);for(const rect of range.getClientRects()){const box={left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};let visible=true;for(let a=p;a&&a!==root;a=a.parentElement){const style=getComputedStyle(a);if(style.opacity==='0'||style.clipPath==='inset(50%)'){visible=false;break;}const b=a.getBoundingClientRect();if(/hidden|clip|scroll|auto/.test(style.overflowX)){box.left=Math.max(box.left,b.left);box.right=Math.min(box.right,b.right);}if(/hidden|clip|scroll|auto/.test(style.overflowY)){box.top=Math.max(box.top,b.top);box.bottom=Math.min(box.bottom,b.bottom);}}if(visible&&box.right>box.left&&box.bottom>box.top)obstacles.push({label:n.textContent,rect:{...box,x:box.left,y:box.top,width:box.right-box.left,height:box.bottom-box.top}});}}
   };
   read(h);if(h.matches('.chat-sidebar-heading')){const bell=h.closest('.chat-sidebar').querySelector('.chat-nav-head');if(bell)read(bell);}if(h.matches('.notes-navigation')){const scope=h.closest('.notes-shell')?.querySelector('.pages-sidebar-head');if(scope)read(scope);}
   const collisions=[];for(const e of elements){const b=e.getBoundingClientRect(),visible={left:Math.max(b.left,r.left),right:Math.min(b.right,r.right),top:Math.max(b.top,r.top),bottom:Math.min(b.bottom,r.bottom)};for(const o of obstacles)if(Math.min(visible.right,o.rect.right)-Math.max(visible.left,o.rect.left)>1&&Math.min(visible.bottom,o.rect.bottom)-Math.max(visible.top,o.rect.top)>1)collisions.push({decoration:e.className||'bulb',control:o.label});}
   const svg=[...h.querySelectorAll('.seasonal-motif>svg,.seasonal-particle>svg,.seasonal-frieze>i>svg')].filter(s=>s.getClientRects().length).map(s=>({width:parseFloat(getComputedStyle(s).width),height:parseFloat(getComputedStyle(s).height),parentWidth:parseFloat(getComputedStyle(s.parentElement).width),parentHeight:parseFloat(getComputedStyle(s.parentElement).height)}));
   return {theme,section,width,mode,height:r.height,motif:{x:q.x-r.x,y:q.y-r.y,width:q.width,height:q.height,display:getComputedStyle(m).display},svg,collisions,protectedBounds:obstacles.map(o=>({label:o.label,x:o.rect.x-r.x,y:o.rect.y-r.y,width:o.rect.width,height:o.rect.height})),overflow:document.documentElement.scrollWidth-innerWidth};
  },{theme,section,width,mode});
  if(phase==='after'){expect(metrics.motif.display,section+' must keep a visible main symbol').toBe('block');expect(metrics.motif.width).toBeGreaterThanOrEqual(28);expect(metrics.collisions,section+' '+width+' '+theme+' decorations must clear controls and text').toEqual([]);expect(metrics.overflow).toBeLessThanOrEqual(1);for(const svg of metrics.svg){expect(svg.width).toBeCloseTo(svg.parentWidth,0);expect(svg.height).toBeCloseTo(svg.parentHeight,0);}}
  report.headers.push(metrics);
  const path=out+'/'+phase+'-'+section+'-'+width+'-'+theme+(mode==='dark'?'-dark':'');await header.screenshot({path:path+'-header.png',animations:'disabled'});report.screenshots.push(path+'-header.png');
  if(mode==='light'&&width!==320){await page.screenshot({path:path+'-full.png',animations:'disabled'});report.screenshots.push(path+'-full.png');}
  if(phase==='after'&&mode==='light'&&theme==='newyear'&&['home','tracker','settings'].includes(section)){
   const surface=page.locator(section==='home'?'.widget':section==='tracker'?'.tracker-column:visible':'.preference-row').first();
   const first=await protectedPaint(surface);await page.locator('.seasonal-shell-decoration').evaluate(e=>e.style.visibility='hidden');const second=await protectedPaint(surface);await page.locator('.seasonal-shell-decoration').evaluate(e=>e.style.visibility='');
   const equal=createHash('sha256').update(first).digest('hex')===createHash('sha256').update(second).digest('hex');report.paint??=[];report.paint.push({section,width,identical:equal});expect(equal,'Masked glass content must not change when decorations are hidden: '+section+' '+width).toBe(true);
  }
  if(phase==='after'&&mode==='light'&&theme==='newyear'&&(width===1440&&section==='home'||width===390&&section==='tracker')){
   const read=()=>page.locator('.seasonal-decoration,.seasonal-shell-decoration,.seasonal-sidebar-scene').evaluateAll(nodes=>nodes.flatMap(n=>n.getAnimations({subtree:true})).filter(a=>a.effect?.target?.getClientRects().length).map(a=>({name:a.animationName,time:Number(a.currentTime)||0,state:a.playState,transform:getComputedStyle(a.effect.target).transform,translate:getComputedStyle(a.effect.target).translate,opacity:getComputedStyle(a.effect.target).opacity})));
   const first=await read();await page.screenshot({path:path+'-motion-0.png',animations:'allow'});await page.waitForTimeout(1600);const second=await read();await page.screenshot({path:path+'-motion-2.png',animations:'allow'});
   const advancing=second.filter((a,i)=>a.state==='running'&&a.time-first[i].time>1200).length,changing=second.filter((a,i)=>a.transform!==first[i].transform||a.translate!==first[i].translate||a.opacity!==first[i].opacity).length;expect(advancing).toBeGreaterThan(20);expect(changing).toBeGreaterThan(10);report.motion??=[];report.motion.push({section,width,advancing,changing});
  }
 }
 if(focus==='contrast'){
  await settings();
  for(const style of ['glass','classic'])for(const mode of ['dark','light']){
   await page.evaluate(({style,mode})=>{document.documentElement.dataset.interface=style;document.documentElement.dataset.scheme=mode;},{style,mode});
   for(const theme of ['newyear','halloween','valentine','spring','space']){
    await page.locator('[data-seasonal-preview]').selectOption(theme);await expect(page.locator('.workspace')).toHaveAttribute('data-seasonal-theme',theme);
    for(const hover of [false,true]){
     const button=page.locator('.workspace>.sidebar .nav-btn.active');if(hover)await button.hover();else await page.mouse.move(900,10);
     await button.evaluate(async e=>{await new Promise(requestAnimationFrame);await Promise.all(e.getAnimations().map(a=>a.finished.catch(()=>{})));});
     const colors=await button.evaluate(e=>{const c=getComputedStyle(e),rgb=value=>value.match(/[\d.]+/g).slice(0,3).map(Number),luminance=values=>values.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);const a=luminance(rgb(c.color)),b=luminance(rgb(c.backgroundColor));return {foreground:c.color,background:c.backgroundColor,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),icon:getComputedStyle(e.querySelector('svg')).color};});
     report.contrast??=[];report.contrast.push({style,mode,theme,hover,...colors});
     if(phase==='after'){expect(colors.ratio).toBeGreaterThanOrEqual(4.5);expect(colors.icon).toBe(colors.foreground);}
     if(style==='glass'&&mode==='dark'&&theme==='newyear'&&!hover)await page.locator('.workspace>.sidebar').screenshot({path:out+'/sidebar-newyear-dark.png',animations:'disabled'});
    }
   }
  }
 }else if(focus==='scroll'){
  for(const width of [1440,390]){
   const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760&&width<=760;await page.setViewportSize({width,height:width===1440?950:844});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
   await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');
   const frame=page.locator('.seasonal-shell-decoration');await expect(frame).toHaveAttribute('data-seasonal-theme','newyear');
   await page.waitForTimeout(100);
   const metrics=await page.evaluate(async()=>{
    const frame=document.querySelector('.seasonal-shell-decoration'),mask=frame.querySelector('mask')||frame.style.maskImage,rect=Element.prototype.getBoundingClientRect,query=Element.prototype.querySelectorAll;
    let queries=0,reads=0,maskWrites=0;const deltas=[];
    Element.prototype.getBoundingClientRect=function(...args){reads++;return rect.apply(this,args)};Element.prototype.querySelectorAll=function(...args){queries++;return query.apply(this,args)};
    const observer=new MutationObserver(records=>{for(const r of records)if(r.target===frame&&r.attributeName==='style'&&r.oldValue?.includes('data:image/svg+xml'))maskWrites++;});observer.observe(frame,{attributes:true,attributeOldValue:true});
    const scrollers=[...document.querySelectorAll('.workspace,.content,#view-content')].filter(e=>e.scrollHeight>e.clientHeight+100&&/auto|scroll/.test(getComputedStyle(e).overflowY));
    const target=scrollers[0]||document.scrollingElement,max=target.scrollHeight-target.clientHeight;
    const animations=frame.getAnimations({subtree:true}),first=animations.map(a=>Number(a.currentTime));let previous=performance.now();
    try{for(let i=0;i<90;i++){target.scrollTop=(1-Math.cos(i/89*Math.PI*2))/2*Math.min(max,900);await new Promise(requestAnimationFrame);const now=performance.now();deltas.push(now-previous);previous=now;}}
    finally{Element.prototype.getBoundingClientRect=rect;Element.prototype.querySelectorAll=query;observer.disconnect();}
    return {max,queries,reads,maskWrites,maskUnchanged:(frame.querySelector('mask')||frame.style.maskImage)===mask,frameConnected:frame.isConnected,animated:animations.some((a,i)=>Number(a.currentTime)>first[i]+500),medianFrame:deltas.sort((a,b)=>a-b)[45],worstFrame:Math.max(...deltas)};
   });
   expect(metrics.max).toBeGreaterThan(100);expect(metrics.frameConnected).toBe(true);expect(metrics.animated).toBe(true);
   if(phase==='after'){expect(metrics.maskUnchanged).toBe(true);expect(metrics.maskWrites).toBe(0);expect(metrics.queries).toBeLessThan(20);}
   report.scroll??=[];report.scroll.push({width,...metrics});
   await page.screenshot({path:out+'/scroll-'+width+'.png',animations:'disabled'});
   const surfaces=page.locator('.preference-row');for(const surface of await surfaces.all()){if(!await surface.isVisible())continue;await surface.scrollIntoViewIfNeeded();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));const clip=await surface.evaluate(e=>{const b=e.getBoundingClientRect(),c=getComputedStyle(e),radius=Math.min(b.width/2,...['TopLeft','TopRight','BottomRight','BottomLeft'].map(k=>parseFloat(c['border'+k+'Radius'])||0))+1,x=Math.max(0,Math.ceil(b.left+radius)),y=Math.max(0,Math.ceil(b.top)+1);return {x,y,width:Math.min(innerWidth,Math.floor(b.right-radius))-x,height:Math.min(innerHeight,Math.floor(b.bottom)-1)-y}});const a=await page.screenshot({clip,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='hidden');const b=await page.screenshot({clip,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='');if(createHash('sha256').update(a).digest('hex')!==createHash('sha256').update(b).digest('hex')){await writeFile(out+'/paint-visible.png',a);await writeFile(out+'/paint-hidden.png',b);report.failedSurface=await surface.getAttribute('data-preference-key');await writeFile(out+'/paint-failure.json',JSON.stringify({width,surface:report.failedSurface,geometry:await surface.evaluate(e=>({surface:e.getBoundingClientRect().toJSON(),mask:[...document.querySelectorAll('.seasonal-shell-decoration mask rect')].map(r=>({x:r.getAttribute('x'),y:r.getAttribute('y'),w:r.getAttribute('width'),h:r.getAttribute('height')}))}))}));}expect(createHash('sha256').update(a).digest('hex')).toBe(createHash('sha256').update(b).digest('hex'));}
   await page.emulateMedia({reducedMotion:'reduce'});expect(await frame.locator('*').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationName==='none'))).toBe(true);await page.emulateMedia({reducedMotion:'no-preference'});
   await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});await expect(frame).toHaveClass(/seasonal-paused/);expect(await frame.locator('*').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationPlayState==='paused'))).toBe(true);await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
   await page.locator('[data-seasonal-end-preview]').click();await expect(page.locator('.seasonal-shell-decoration,.seasonal-mask-definitions')).toHaveCount(0);
  }
 }else if(focus==='seam'){
  async function seam(section,width,theme){
   await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const frame=page.locator('.seasonal-shell-decoration');await expect(frame).toHaveAttribute('data-seasonal-theme',theme);
   for(const phase of [.001,.999]){
    await frame.evaluate((e,phase)=>{for(const a of e.getAnimations({subtree:true})){a.pause();const timing=a.effect.getTiming();a.currentTime=timing.delay+Number(timing.duration)*phase;}},phase);
    const metrics=await frame.evaluate(e=>{
     const holes=[...e.querySelectorAll('mask rect[fill="black"]')].map(r=>({left:+r.getAttribute('x'),top:+r.getAttribute('y'),right:+r.getAttribute('x')+(+r.getAttribute('width')),bottom:+r.getAttribute('y')+(+r.getAttribute('height'))}));
     const sidebar=document.querySelector('.workspace>.sidebar'),side=sidebar?.getClientRects().length?sidebar.getBoundingClientRect():null;
     const first=e.querySelector('.seasonal-frame-left>.seasonal-frame-piece'),piece=first.getBoundingClientRect();
     const boundary=side?holes.find(h=>Math.abs(h.left-side.left)<.5&&Math.abs(h.top-side.top)<.5&&Math.abs(h.bottom-side.bottom)<.5):null;
     return {sidebar:side?{left:side.left,right:side.right,top:side.top,bottom:side.bottom}:null,boundary,piece:{left:piece.left,right:piece.right,top:piece.top,bottom:piece.bottom},overflow:document.documentElement.scrollWidth-innerWidth};
    });
    expect(metrics.overflow).toBeLessThanOrEqual(1);
    if(metrics.sidebar){
     expect(metrics.boundary,'Sidebar must have its exact mask rectangle').toBeTruthy();expect(metrics.boundary.right).toBeCloseTo(metrics.sidebar.right,1);
     expect(metrics.piece.left).toBeLessThan(metrics.sidebar.right);expect(metrics.piece.right).toBeGreaterThan(metrics.sidebar.right);
     // Actual paint, not just pointer-events or DOM bounds: decoration reaches
     // the first two pixels immediately outside the menu, in both swing phases.
     const clip={x:metrics.sidebar.right,y:Math.max(0,Math.floor(metrics.piece.top)),width:2,height:Math.floor(Math.min(950,metrics.piece.bottom))-Math.max(0,Math.floor(metrics.piece.top))};
     const a=await page.screenshot({clip,animations:'allow'});await frame.evaluate(e=>e.style.visibility='hidden');const b=await page.screenshot({clip,animations:'allow'});await frame.evaluate(e=>e.style.visibility='');metrics.paintTouchesMenu=createHash('sha256').update(a).digest('hex')!==createHash('sha256').update(b).digest('hex');expect(metrics.paintTouchesMenu,theme+' must touch the menu edge').toBe(true);
    }
    report.seam??=[];report.seam.push({section,width,theme,phase,...metrics});
   }
   const file=out+'/after-'+section+'-'+width+'-'+theme;
   await page.screenshot({path:file+'-full.png',animations:'allow'});report.screenshots.push(file+'-full.png');
   if(section==='settings'&&width===1440&&theme==='space'){await page.screenshot({path:file+'-seam.png',clip:{x:0,y:70,width:360,height:830},animations:'allow'});report.screenshots.push(file+'-seam.png');}
   const surface=page.locator(section==='home'?'.widget':section==='tracker'?'.tracker-column:visible':'.preference-row').first();
   const a=await protectedPaint(surface);await frame.evaluate(e=>e.style.visibility='hidden');const b=await protectedPaint(surface);await frame.evaluate(e=>e.style.visibility='');expect(createHash('sha256').update(a).digest('hex')).toBe(createHash('sha256').update(b).digest('hex'));report.paint??=[];report.paint.push({section,width,theme,identical:true});
  }
  for(const width of [1440,390,320]){
   const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760&&width<=760;await page.setViewportSize({width,height:width===1440?950:844});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
   for(const theme of ['space','newyear','halloween','valentine','spring']){
    await settings();await page.locator('[data-seasonal-preview]').selectOption(theme);for(const row of await page.locator('.preference-row[open]').all())await row.locator(':scope>summary').click();await page.evaluate(()=>{window.scrollTo(0,0);for(const e of document.querySelectorAll('.content,.workspace'))e.scrollTop=0;});await seam('settings',width,theme);
   }
   await settings();await page.locator('[data-seasonal-preview]').selectOption('space');await home();await seam('home',width,'space');await tracker();await seam('tracker',width,'space');
  }
 }else if(focus==='details'){
  for(const width of [1440,390]){
   const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760&&width<=760;await page.setViewportSize({width,height:950});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
   await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');for(const row of await page.locator('.preference-row[open]').all())await row.locator(':scope>summary').click();
   const row=page.locator('.preference-row').first(),frame=page.locator('.seasonal-shell-decoration');await row.scrollIntoViewIfNeeded();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   for(const expanded of [true,false]){
    const mask=await frame.evaluate(e=>e.querySelector('mask').innerHTML);await row.locator(':scope>summary').click();await expect.poll(()=>row.getAttribute('open')).toBe(expanded?'':null);await expect.poll(()=>frame.evaluate(e=>e.querySelector('mask').innerHTML)).not.toBe(mask);
    const clip=await row.evaluate(e=>{const r=e.getBoundingClientRect(),limit=document.querySelector('.seasonal-preview-control').getBoundingClientRect().top;return {x:r.x+10,y:Math.max(0,r.y)+10,width:r.width-20,height:Math.min(r.bottom,limit)-Math.max(0,r.y)-20};});expect(clip.height).toBeGreaterThan(10);
    const a=await page.screenshot({clip,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='hidden');const b=await page.screenshot({clip,animations:'disabled'});await frame.evaluate(e=>e.style.visibility='');const identical=createHash('sha256').update(a).digest('hex')===createHash('sha256').update(b).digest('hex');expect(identical).toBe(true);report.details??=[];report.details.push({width,expanded,maskUpdatedWithoutScroll:true,identical});
   }
  }
 }else if(focus==='chat'){
  for(const width of [390,320]){
   const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760;await page.setViewportSize({width,height:844});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
   await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');await page.locator('[data-mobile-view=chat]').click();await page.locator('.chat-sidebar-heading').waitFor();
   await capture(page.locator('.chat-sidebar-heading'),'newyear','chat-list',width);await expect(page.locator('.chat-nav-head>.seasonal-decoration')).toHaveCount(0);
   const buttons=page.locator('.chat-sidebar-heading button:visible,.chat-nav-head button:visible');for(const button of await buttons.all())await expect.poll(()=>button.evaluate(b=>{const r=b.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===b||b.contains(hit);})).toBe(true);
   await page.locator('[data-room="'+room.id+'"]').click();await page.locator('.chat-header').waitFor();await expect(page.locator('.chat-sidebar-heading>.seasonal-decoration')).toHaveCount(0);await page.locator('[data-chat-back]').click();await expect(page.locator('.chat-sidebar-heading>.seasonal-decoration')).toBeVisible({timeout:1500});await expect(page.locator('.chat-nav-head>.seasonal-decoration')).toHaveCount(0);
   await capture(page.locator('.chat-sidebar-heading'),'newyear','chat-list-returned',width);
  }
 }else{
 for(const width of focused?(focus==='surfaces'?[1440,390]:[]):['before','candidate'].includes(phase)?[1440,390]:[1440,390,320]){
  const old=await page.locator('.workspace').elementHandle(),crossed=(page.viewportSize().width<=760)!==(width<=760);await page.setViewportSize({width,height:width===1440?950:844});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
  for(const theme of ['halloween','newyear','valentine','spring','space']){
   await settings();await page.locator('[data-seasonal-preview]').selectOption(theme);
   for(const section of ['home','tracker','settings']){
    if(section==='settings'){await settings();for(const row of await page.locator('.preference-row[open]').all())await row.locator(':scope>summary').click();await page.evaluate(()=>{window.scrollTo(0,0);for(const e of document.querySelectorAll('.content,.workspace'))e.scrollTop=0;});}else await (section==='home'?home():tracker());
    const selector=section==='tracker'?'.tracker-chrome':width>760?'#view-content>.section-heading':'.content>.topbar';await capture(page.locator(selector),theme,section,width);
   }
  }
  console.log('Seasonal scale images ready',phase,width);
 }
 if(phase==='after'&&focus!=='surfaces'){
  for(const width of focused?[]:[320,390,1440]){
   const old=await page.locator('.workspace').elementHandle(),crossed=(page.viewportSize().width<=760)!==(width<=760);await page.setViewportSize({width,height:width===1440?950:844});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
   await settings();await page.locator('[name=mode]').selectOption('dark');await page.locator('[data-appearance] [type=submit]').click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');
   for(const theme of ['newyear','space']){await settings();await page.locator('[data-seasonal-preview]').selectOption(theme);await home();await capture(page.locator(width>760?'#view-content>.section-heading':'.content>.topbar'),theme,'home',width,'dark');await tracker();await capture(page.locator('.tracker-chrome'),theme,'tracker',width,'dark');}
  }
  if(focused){await settings();await page.locator('[name=mode]').selectOption('dark');await page.locator('[data-appearance] [type=submit]').click();await expect(page.locator('[data-appearance-status]')).toHaveText('Оформление сохранено');}
  for(const width of [390,320]){
   const old=await page.locator('.workspace').elementHandle(),crossed=page.viewportSize().width>760;await page.setViewportSize({width,height:844});if(crossed)await expect.poll(()=>old.evaluate(e=>e.isConnected)).toBe(false);
   await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');await page.locator('[data-mobile-view=notes]').click();await page.locator('.notes-navigation').waitFor();if(!await page.locator('.notes-shell.mobile-notes-list').count())await page.locator('#notes-main-menu').click();await capture(page.locator('.notes-navigation'),'newyear','notes-list',width,'dark');
   const geometry=()=>page.evaluate(()=>{const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};return {header:rect(document.querySelector('.notes-navigation')),select:rect(document.querySelector('#notes-space')),name:document.querySelector('#notes-space').selectedOptions[0].textContent,add:rect(document.querySelector('#add-root-page')),bell:rect(document.querySelector('.notes-navigation .inbox-button'))};});
   const active=await geometry();await page.locator('[data-seasonal-end-preview]').click();await expect(page.locator('.notes-navigation')).not.toHaveClass(/seasonal-host/);const inactive=await geometry();expect(active.header).toEqual(inactive.header);expect(active.add).toEqual(inactive.add);expect(active.bell).toEqual(inactive.bell);expect(active.name).toBe(inactive.name);expect(inactive.select.width-active.select.width).toBeCloseTo(40,1);report.notesGeometry??=[];report.notesGeometry.push({width,active,inactive});
   await settings();await page.locator('[data-seasonal-preview]').selectOption('newyear');await page.locator('[data-mobile-view=notes]').click();await page.locator('.notes-navigation').waitFor();if(!await page.locator('.notes-shell.mobile-notes-list').count())await page.locator('#notes-main-menu').click();
   await page.locator('[data-open-page="'+note.id+'"]').click();await capture(page.locator('.notes-navigation'),'newyear','notes-document',width,'dark');
   await page.locator('[data-mobile-view=chat]').click();await page.locator('[data-room="'+room.id+'"]').click();await page.locator('.chat-header').waitFor();await capture(page.locator('.chat-header'),'newyear','chat-room',width,'dark');await page.locator('[data-chat-back]').click();await expect(page.locator('.chat-sidebar-heading>.seasonal-decoration')).toBeVisible();
  }
  const current=await send('/tracker/projects/'+project.id,undefined,'GET');await send('/tracker/projects/'+project.id,{name:'DrG Space — разработка приложения и семейные проекты',version:current.version},'PATCH');await tracker();await capture(page.locator('.tracker-chrome'),'newyear','tracker-long-title',320,'dark');
  await page.emulateMedia({reducedMotion:'reduce'});expect(await page.locator('.seasonal-decoration *,.seasonal-shell-decoration *,.seasonal-sidebar-scene *').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationName==='none'))).toBe(true);const still=await page.locator('.seasonal-motif').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).transform));await page.waitForTimeout(150);expect(await page.locator('.seasonal-motif').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).transform))).toEqual(still);
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));});
  await expect.poll(()=>page.locator('.seasonal-decoration,.seasonal-shell-decoration,.seasonal-sidebar-scene').evaluateAll(nodes=>nodes.every(n=>n.classList.contains('seasonal-paused')))).toBe(true);
  expect(await page.locator('.seasonal-decoration *,.seasonal-shell-decoration *,.seasonal-sidebar-scene *').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).animationPlayState==='paused'))).toBe(true);
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await page.locator('[data-seasonal-end-preview]').click();await expect(page.locator('.seasonal-decoration,.seasonal-shell-decoration,.seasonal-sidebar-scene')).toHaveCount(0);await expect(page.locator('.workspace')).not.toHaveClass(/seasonal-framed/);
  expect((await send('/appearance',undefined,'GET')).preferences.wallpaper).toBe(stored.wallpaper);report.hiddenPause=true;report.cleanupOnEnd=true;
  const heights=new Map();for(const item of report.headers){const key=item.width+'-'+item.section;if(heights.has(key))expect(item.height).toBeCloseTo(heights.get(key),1);else heights.set(key,item.height);}report.reducedMotion=true;
 }
 }
 expect(errors).toEqual([]);report.passed=true;await writeFile(out+'/'+(focus?focus+'-focused':phase)+'-report.json',JSON.stringify(report,null,2));console.log('Seasonal scale review passed',engine,phase);
}finally{await browser?.close();server.kill('SIGTERM');await exited;await rm(directory,{recursive:true,force:true});}
