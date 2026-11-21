import {chromium,webkit,expect,request} from '@playwright/test';
import {createHmac} from 'node:crypto';
import {spawn} from 'node:child_process';

const origin='http://127.0.0.1:8094';
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','tests.browser_server'],{
  env:{...process.env,DRG_BROWSER_PORT:'8094'},stdio:'inherit',
});
const stopped=new Promise(resolve=>server.once('exit',resolve));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function totp(secret) {
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join('');
  const counter=Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(b=>parseInt(b,2)))).update(counter).digest();
  return String((hash.readUInt32BE(hash.at(-1)&15)&0x7fffffff)%1000000).padStart(6,'0');
}

async function fixture() {
  const session=await request.newContext({baseURL:origin});
  const headers={Origin:origin},password='Section-navigation-test-123';
  async function api(path,data,method='POST') {
    for(let attempt=0;;attempt++){
      const response=await session.fetch('/api'+path,{method,data,headers});
      const result=await response.json();
      if(response.ok())return result;
      if(attempt===12||result.code!=='operationWithDataIsInProgressRetryLater')throw Error(path+' '+JSON.stringify(result));
      await pause(250);
    }
  }
  headers['X-CSRF-Token']=(await api('/setup',{login:'navigation-owner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll',{password});
  await api('/2fa/confirm',{code:totp(enrollment.secret)});
  headers['X-CSRF-Token']=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
  const modules=await api('/modules',undefined,'GET');
  await api('/modules',{enabled:{...modules.enabled,tracker:true}},'PUT');
  await api('/admin/access/unlock',{password});
  const state=await session.storageState();
  await session.dispose();
  return state;
}

async function openPage(browser,storageState,{mobile=false,reduce=false,unsupported=false}={}) {
  const context=await browser.newContext({baseURL:origin,storageState,serviceWorkers:'block',
    viewport:mobile?{width:390,height:844}:{width:1366,height:900},reducedMotion:reduce?'reduce':'no-preference'});
  const page=await context.newPage();
  await page.addInitScript(({mobile,unsupported})=>{
    Object.defineProperty(navigator,'standalone',{configurable:true,value:mobile});
    const start=document.startViewTransition?.bind(document);
    window.sectionTransitions=[];
    window.sectionTransitionSupport=Boolean(start)&&!unsupported;
    if(unsupported){document.startViewTransition=undefined;return;}
    if(!start)return;
    document.startViewTransition=update=>{
      if(!document.documentElement.classList.contains('section-switching'))return start(update);
      const item={from:document.querySelector('.workspace')?.dataset.currentView};
      sectionTransitions.push(item);
      const transition=start(update);
      transition.ready.then(()=>{
        const root=document.documentElement;
        item.to=document.querySelector('.workspace')?.dataset.currentView;
        const group=root.classList.contains('section-switching-layout')?'root':'drg-section';
        item.duration=getComputedStyle(root,`::view-transition-old(${group})`).animationDuration;
        item.pointerEvents=getComputedStyle(root,'::view-transition').pointerEvents;
      },()=>{item.skipped=true;});
      transition.finished.then(()=>{item.finished=true;},()=>{});
      return transition;
    };
  },{mobile,unsupported});
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  await page.route('**/api/modules',async route=>{
    const response=await route.fetch(),body=await response.json();
    if(!body.catalog.some(m=>m.id==='family_photos'))body.catalog.push({id:'family_photos',kind:'module',title:'Media'});
    body.enabled.family_photos=true;
    await route.fulfill({response,json:body});
  });
  await page.route('**/api/family-photos/**',route=>route.fulfill({json:{items:[],status:{can_send:false,worker_online:true},unread:0}}));
  await page.goto('/');
  await expect(page.locator('.workspace')).toBeVisible();
  return {context,page};
}

const view=(page,name)=>expect(page.locator('.workspace')).toHaveAttribute('data-current-view',name);
const sidebar=(page,name)=>page.locator('.sidebar [data-view="'+name+'"]');
async function withinFlow(browser,state,options,run) {
  const {context,page}=await openPage(browser,state,options);
  try {await run(page);} finally {await context.close();}
}

async function testDesktop(page) {
  await view(page,'home');
  const sidebarBox=await page.locator('.sidebar').boundingBox();
  expect(await page.evaluate(()=>sectionTransitionSupport)).toBe(true);
  await sidebar(page,'settings').press('Enter');
  await view(page,'settings');
  await expect(sidebar(page,'settings')).toBeFocused();
  await expect.poll(()=>page.evaluate(()=>sectionTransitions.find(item=>item.to==='settings')?.duration)).toBe('0.21s');
  expect(await page.evaluate(()=>sectionTransitions.at(-1).pointerEvents)).toBe('none');
  expect(await page.locator('.sidebar').boundingBox()).toEqual(sidebarBox);
  await expect.poll(()=>page.evaluate(()=>sectionTransitions.at(-1).finished)).toBe(true);

  let release;
  const gate=new Promise(resolve=>release=resolve);
  const delayed='**/api/social';
  await page.route(delayed,async route=>{await gate;await route.fallback();},{times:1});
  await sidebar(page,'social').click();
  await view(page,'social');
  await expect(page.locator('#view-content .social-view')).toHaveCount(0);
  await expect.poll(()=>page.evaluate(()=>sectionTransitions.some(t=>t.to==='social'&&!t.finished))).toBe(true);
  // Click the real sidebar during the active crossfade, then release the
  // pending data. The obsolete response cannot take over the current view.
  const next=await sidebar(page,'expenses').boundingBox();
  await page.mouse.click(next.x+next.width/2,next.y+next.height/2);
  release();
  await view(page,'expenses');
  await expect(page.locator('.expense-event-list')).toBeVisible();
  await expect(page.locator('.social-view')).toHaveCount(0);

  await page.evaluate(()=>{
    document.querySelector('.sidebar [data-view=tracker]').click();
    document.querySelector('.sidebar [data-view=family_photos]').click();
  });
  await view(page,'family_photos');
  const sections={home:'[data-widget]',tracker:'.tracker-shell',admin:'[data-admin-content]',
    notes:'#notes-main-menu',tasks:'#tasks-main-menu',chat:'#chat-main-menu',files:'#files-main-menu'};
  for(const [section,selector] of Object.entries(sections)){
    await sidebar(page,section).click();
    await view(page,section);
    await expect(page.locator(selector).first()).toBeAttached();
    if(['notes','tasks','chat','files'].includes(section)){
      await page.locator(selector).click();
      await view(page,'home');
    }
  }
  await expect.poll(()=>page.evaluate(()=>sectionTransitions.at(-1).finished)).toBe(true);
  expect(await page.evaluate(()=>document.documentElement.classList.contains('section-switching'))).toBe(false);
}

async function testMobile(page) {
  for(const section of ['settings','tasks','tracker','notes','chat','settings']){
    await page.locator('[data-mobile-view='+section+']').click();
    await view(page,section);
  }
  await expect.poll(()=>page.evaluate(()=>sectionTransitions.some(t=>t.to==='chat'))).toBe(true);
}

try {
  for(let attempt=0;;attempt++){
    try{if((await fetch(origin+'/api/health')).ok)break;}catch{}
    if(attempt===180||server.exitCode!==null)throw Error('Local server failed to start');
    await pause(100);
  }
  const state=await fixture();
  for(const engine of [chromium,webkit]){
    const browser=await engine.launch();
    try{
      await withinFlow(browser,state,{},testDesktop);
      await withinFlow(browser,state,{mobile:true},testMobile);
      for(const mode of [{reduce:true},{unsupported:true}]){
        await withinFlow(browser,state,mode,async page=>{
          await sidebar(page,'settings').click();
          await view(page,'settings');
          expect(await page.evaluate(()=>sectionTransitions.length)).toBe(0);
        });
      }
      console.log(engine.name(),'desktop sidebar, mobile tabs, rapid switch, slow response, keyboard, reduced motion and unsupported browser passed');
    }finally{await browser.close();}
  }
}finally{
  server.kill('SIGTERM');
  await stopped;
}
