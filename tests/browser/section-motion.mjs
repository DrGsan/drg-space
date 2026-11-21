import {chromium,webkit,expect,request} from '@playwright/test';
import {createHmac} from 'node:crypto';
import {spawn} from 'node:child_process';

const port=process.env.DRG_MOTION_PORT||'8091';
const origin='http://127.0.0.1:'+port;
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','tests.browser_server'],{
  cwd:process.env.DRG_MOTION_ROOT||process.cwd(),env:{...process.env,DRG_BROWSER_PORT:port},stdio:'inherit',
});
const stopped=new Promise(resolve=>server.once('exit',resolve));
const baseline=process.env.DRG_MOTION_BASELINE==='1';
const password='Synthetic-motion-password-123';
const engines=process.env.DRG_BROWSER_ENGINE ? [process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium] : [chromium,webkit];
const elapsed=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function totp(secret) {
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const bits=[...secret].map(char=>alphabet.indexOf(char).toString(2).padStart(5,'0')).join('');
  const key=Buffer.from(bits.match(/.{8}/g).map(byte=>parseInt(byte,2)));
  const time=Buffer.alloc(8);
  time.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',key).update(time).digest();
  return String((hash.readUInt32BE(hash.at(-1)&15)&0x7fffffff)%1000000).padStart(6,'0');
}

async function fixture() {
  const owner=await request.newContext({baseURL:origin});
  const headers={Origin:origin};
  const api=async(path,data,method='POST')=>{
    for(let attempt=0;;attempt++){
      const res=await owner.fetch('/api'+path,{method,data,headers});
      const result=await res.json();
      if(res.ok())return result;
      if(attempt===12||result.code!=='operationWithDataIsInProgressRetryLater')throw new Error(path+' '+JSON.stringify(result));
      await elapsed(250);
    }
  };
  headers['X-CSRF-Token']=(await api('/setup',{login:'motion-owner',password,token:'browser-test-setup-key'})).csrf;
  await api('/2fa/confirm',{code:totp((await api('/2fa/enroll',{password})).secret)});
  headers['X-CSRF-Token']=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
  const modules=await api('/modules',undefined,'GET');
  await api('/modules',{enabled:{...modules.enabled,tracker:true}},'PUT');
  const note=await api('/notes',{title:'First motion note',body:'First note body'});
  const other=await api('/notes',{title:'Second motion note',body:'Second note body'});
  const share=await api('/notes/'+note.id+'/shares',{mode:'public',permission:'view'});
  const folder=await api('/files/folders',{name:'Motion folder'});
  const fileShare=await api('/files/'+folder.id+'/shares',{mode:'public'});
  const project=await api('/tracker/projects',{name:'Motion project',key:'MOTION'});
  const issue=await api('/tracker/projects/'+project.id+'/issues',{title:'Motion issue'});
  const result={note,other,share,folder,fileShare,project,issue,storageState:await owner.storageState()};
  await owner.dispose();
  return result;
}

async function setupPage(page,mobile) {
  await page.addInitScript(standalone=>{
    Object.defineProperty(navigator,'standalone',{configurable:true,value:standalone});
    window.motionEvents=[];
    const original=Element.prototype.animate;
    Element.prototype.animate=function(frames,options){
      const rect=this.getBoundingClientRect(),animation=original.call(this,frames,options);
      if(this.closest('#view-content')||this.matches('.modal,dialog,.auth-card,#shared-content,.drive-main,.shared-drive-gate')){
        window.motionEvents.push({node:this.id||[...this.classList].join(' '),first:frames[0],rect:[rect.x,rect.y,rect.width,rect.height],animation});
      }
      return animation;
    };
  },mobile);
  await page.route('**/api/widgets/**/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  await page.route('**/api/modules',async route=>{
    const response=await route.fetch(),body=await response.json();
    if(body.enabled){
      body.enabled.family_photos=true;
      if(!body.catalog.some(item=>item.id==='family_photos'))body.catalog.push({id:'family_photos',kind:'module',title:'Family photos'});
    }
    await route.fulfill({response,json:body});
  });
  await page.route('**/api/family-photos/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith('/groups'))return route.fulfill({json:{items:[{id:'-1001',title:'Motion family',history_complete:true}],status:{can_send:false,worker_online:true}}});
    if(path.endsWith('/sync'))return route.fulfill({json:{state:'idle',history_complete:true,pending:0}});
    return route.fulfill({json:{items:[],more:false,unread:0}});
  });
}

async function navigate(page,view,mobile) {
  await page.locator('.workspace').waitFor();
  const current=await page.locator('.workspace').getAttribute('data-current-view');
  if(mobile) {
    if(['home','files','family_photos','social','expenses','admin','security','personal-data'].includes(view)){
      if(current!=='settings')await page.locator('[data-mobile-view=settings]').click();
      if(['security','personal-data'].includes(view)) {
        const row=page.locator('[data-preference-key='+(view==='security'?'security':'data')+']');
        if(await row.getAttribute('open')===null)await row.locator('summary').click();
        await page.locator(view==='security'?'#open-security':'#open-personal-data').click();
      } else await page.locator('[data-mobile-open='+view+']').click();
    } else if(view!==current)await page.locator('[data-mobile-view='+view+']').click();
  } else {
    const menus={notes:'#notes-main-menu',tasks:'#tasks-main-menu',chat:'#chat-main-menu',files:'#files-main-menu'};
    if(current!==view&&menus[current]){
      const button=page.locator(menus[current]);
      await button.waitFor({state:'attached'});
      if(!await button.isVisible())await page.locator('#toggle-pages-sidebar,[data-toggle-tasks-sidebar],[data-chat-back]').first().click();
      await button.click();
    }
    if(['security','personal-data'].includes(view)) {
      await page.locator('[data-view=settings]').click();
      const row=page.locator('[data-preference-key='+(view==='security'?'security':'data')+']');
      if(await row.getAttribute('open')===null)await row.locator('summary').click();
      await page.locator(view==='security'?'#open-security':'#open-personal-data').click();
    } else if(view!==current)await page.locator('[data-view='+view+']').click();
  }
  await expect(page.locator('.workspace')).toHaveAttribute('data-current-view',view);
}

async function checkEntry(page,node,{reduce=false}={}) {
  if(baseline)return;
  const count=()=>page.evaluate(name=>motionEvents.filter(event=>String(event.node).includes(name)&&event.animation.id==='surface-entry').length,node);
  if(reduce)expect(await count()).toBe(0);
  else await expect.poll(count,{message:node}).toBeGreaterThan(0);
}

async function installDelay(page,pattern) {
  let release;
  const gate=new Promise(resolve=>release=resolve);
  await page.route(pattern,async route=>{await gate;await route.fallback();},{times:1});
  return release;
}

async function flow(browser,fixtures,{width,mobile=false,reduce=false,video}) {
  const context=await browser.newContext({baseURL:origin,storageState:fixtures.storageState,
    viewport:{width,height:900},reducedMotion:reduce?'reduce':'no-preference',serviceWorkers:'block',recordVideo:video});
  try {
    const page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.setDefaultTimeout(10000);
    await setupPage(page,mobile);
    await page.goto('/');
    await navigate(page,'home',mobile);
    await expect(page.locator('[data-widget]').first()).toBeVisible();

    await navigate(page,'settings',mobile);
    await expect(page.locator('[data-preference-key=security]')).toBeVisible();
    if (video) await elapsed(450);
    await checkEntry(page,'view-content',{reduce});
    await navigate(page,'security',mobile);
    await expect(page.locator('[data-sessions] .share-row').first()).toBeVisible();
    await page.locator('[data-toggle-2fa]').focus();
    await page.locator('[data-toggle-2fa]').press('Enter');
    const modal=page.locator('.modal-overlay:not([data-closing])');
    await expect(modal.locator('input').first()).toBeEnabled();
    await modal.locator('input').first().fill('typing during entrance');
    if (video) await elapsed(450);
    if(!baseline){
      const motions=await page.evaluate(()=>motionEvents.filter(e=>e.animation.id==='dialog-entry'));
      expect(motions.length).toBe(reduce?0:1);
    }
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-toggle-2fa]')).toBeFocused();
    await expect(page.locator('.modal-overlay')).toHaveCount(0);
    if(!baseline&&!video){
      await page.evaluate(()=>{
        const trigger=document.querySelector('[data-toggle-2fa]');
        trigger.click();
        document.querySelector('.modal-close').click();
        trigger.click();
      });
      await expect(page.locator('.modal-overlay')).toHaveCount(1);
      await expect(page.locator('.modal [name=password]')).toHaveCount(1);
      await page.keyboard.press('Escape');
      await expect(page.locator('.modal-overlay')).toHaveCount(0);
    }
    await navigate(page,'personal-data',mobile);
    await expect(page.locator('[data-transfer-result]')).toBeAttached();
    if(video){await elapsed(500);return;}
    if(reduce)return;
    await navigate(page,'admin',mobile);
    const unlock=page.locator('[data-admin-unlock]');
    await unlock.locator('[name=password]').fill(password);
    await unlock.locator('[type=submit]').click();
    await expect(page.locator('[data-admin-content]')).toBeVisible();

    let release=await installDelay(page,'**/api/social');
    await navigate(page,'social',mobile);
    await expect(page.locator('.social-view')).toHaveCount(0);
    release();
    await expect(page.locator('.social-view')).toBeVisible();
    if(!baseline&&!mobile){
      const count=()=>page.evaluate(()=>motionEvents.filter(e=>e.node==='view-content'&&e.animation.id==='surface-entry').length);
      const before=await count();
      release=await installDelay(page,'**/api/social');
      const response=page.waitForResponse('**/api/social');
      await page.locator('#refresh-social').click();
      await expect(page.locator('.social-view')).toBeVisible();
      release();
      await response;
      expect(await count()).toBe(before);
    }

    await navigate(page,'expenses',mobile);
    await expect(page.locator('.expense-event-list')).toBeVisible();

    release=await installDelay(page,'**/api/family-photos/groups/*/media*');
    await navigate(page,'family_photos',mobile);
    if(!baseline)await expect(page.locator('.fp-empty[role=status]')).toBeVisible();
    release();
    await expect(page.locator('.fp-empty:not([role=status])')).toBeVisible();

    await navigate(page,'chat',mobile);
    await expect(page.locator('[data-room="telegram:-1001"]')).toBeVisible();
    await page.route('**/api/family-photos/**/history*',route=>route.fulfill({json:{items:[],more:false}}));
    await page.locator('[data-room="telegram:-1001"]').click();
    await expect(page.locator('.fp-chat')).toBeVisible();
    if(await page.locator('[data-fp-back]').isVisible())await page.locator('[data-fp-back]').click();

    await navigate(page,'tasks',mobile);
    if(mobile)await page.locator('[data-toggle-tasks-sidebar]').click();
    await expect(page.locator('[data-task-list]').first()).toBeVisible();
    await page.locator('[data-task-list]').first().click();
    await checkEntry(page,'task',{reduce});

    await navigate(page,'tracker',mobile);
    await page.locator('[data-tracker-project="'+fixtures.project.id+'"]').click();
    await expect(page.locator('.tracker-board')).toBeVisible();
    await page.locator('[data-tracker-issue="'+fixtures.issue.id+'"]').filter({visible:true}).first().click();
    await expect(page.locator('.tracker-issue')).toBeVisible();
    await checkEntry(page,'tracker-shell',{reduce});

    await navigate(page,'files',mobile);
    const openFolder=page.locator('[data-open-file="'+fixtures.folder.id+'"]');
    await expect(openFolder).toBeVisible();
    release=await installDelay(page,'**/api/files?**parent_id=*');
    await openFolder.click();
    await expect(page.locator('.drive-panel')).toHaveAttribute('aria-busy','true');
    await expect(openFolder).toBeVisible();
    release();
    await expect(page.locator('.drive-heading h2')).toContainText(fixtures.folder.name);
    await checkEntry(page,'drive-main',{reduce});
    const stable=await page.locator('.drive-main').boundingBox();
    await elapsed(300);
    expect(await page.locator('.drive-main').boundingBox()).toEqual(stable);
    for(const view of ['recent','shared','trash','active']){
      await page.locator('['+(mobile?'data-mobile-files-view':'data-files-view')+'='+view+']').click();
      await expect(page.locator('.drive-panel')).toHaveAttribute('data-drive-view',view);
    }

    await page.goto('/page/'+fixtures.note.id);
    await expect(page.locator('#page-title')).toHaveValue(fixtures.note.title);
    await expect(page.locator('.bn-editor')).toContainText('First note body');
    await checkEntry(page,'notion-page',{reduce});
    if(mobile){await page.locator('#notes-main-menu').click();}
    const other=page.locator('[data-open-page="'+fixtures.other.id+'"]').first();
    if(!mobile&&!await other.isVisible())await page.locator('#toggle-pages-sidebar').click();
    await other.click();
    await expect(page.locator('#page-title')).toHaveValue(fixtures.other.title);
    expect(errors).toEqual([]);
    console.log(browser.browserType().name(),width,mobile?'standalone':'browser','all authenticated sections');
  } finally { await context.close(); }
}

async function chatAfterDialog(browser,{storageState}) {
  const name='motion-peer-'+browser.browserType().name();
  const owner=await browser.newContext({baseURL:origin,storageState,viewport:{width:1280,height:900}});
  const peer=await request.newContext({baseURL:origin});
  try {
    const csrf=(await owner.cookies()).find(cookie=>cookie.name==='drg_csrf').value;
    const send=async(context,path,data,token=csrf)=>{
      const response=await context.post('/api'+path,{headers:{Origin:origin,'X-CSRF-Token':token},data});
      expect(response.ok(),path+' '+await response.text()).toBe(true);
      return response.json();
    };
    await send(owner.request,'/admin/access/unlock',{password});
    const invite=await send(owner.request,'/invites',{});
    await send(owner.request,'/admin/access/lock',{});
    const registration=await send(peer,'/register',{login:name,password,invite:new URL(invite.url).pathname.split('/').at(-1)},'');
    await send(peer,'/2fa/skip',{},registration.csrf);
    const room=await send(owner.request,'/social/families',{name:'Motion read receipts'});
    await send(owner.request,'/social/families/'+room.id+'/invites',{login:name});
    const pending=(await (await peer.get('/api/social')).json()).requests.find(item=>item.space_id===room.id&&item.incoming);
    const peerCsrf=(await peer.storageState()).cookies.find(cookie=>cookie.name==='drg_csrf').value;
    await send(peer,'/social/requests/'+pending.id+'/accept',{},peerCsrf);
    const page=await owner.newPage();
    await setupPage(page,false);
    await page.goto('/');
    await navigate(page,'chat',false);
    await page.locator('[data-room="'+room.id+'"]').click();
    await expect(page.locator('.chat-shell')).toHaveClass(/room-open/);
    await page.bringToFront();
    await page.locator('[data-chat-info]').focus();
    await page.locator('[data-chat-info]').press('Enter');
    const readable=()=>owner.request.get('/api/chat/rooms').then(response=>response.json()).then(data=>data.items.find(item=>item.id===room.id).unread);
    const body='Message received while the dialog is open';
    await send(peer,'/chat/'+room.id+'/messages',{body,client_id:'modal-read-'+room.id},peerCsrf);
    await expect(page.locator('.chat-messages')).toContainText(body);
    await expect.poll(readable).toBe(1);
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-chat-info]')).toBeFocused();
    await expect.poll(readable).toBe(0);
    console.log(browser.browserType().name(),'chat read receipt resumes after closing a dialog');
  } finally {
    await peer.dispose();
    await owner.close();
  }
}

async function publicScreens(browser,{share,fileShare}) {
  const context=await browser.newContext({baseURL:origin,viewport:{width:390,height:844},serviceWorkers:'block'});
  try {
    const page=await context.newPage();
    await setupPage(page,false);
    await page.goto('/');
    await expect(page.locator('#auth-form')).toBeVisible();
    await page.goto('/share/'+share.token);
    await expect(page.locator('#shared-content')).toContainText('First note body');
    await checkEntry(page,'shared-content');
    await page.goto('/f/'+fileShare.token);
    await expect(page.locator('.shared-drive-panel')).toBeVisible();
    for(const [path,selector] of [
      ['/share/missing','#shared-content h1'],
      ['/f/missing','.shared-drive-gate'],
      ['/call/missing','.guest-call-page .auth-card a[href="/"]'],
      ['/expense-share/missing','.enroll h2'],
      ['/reset-password/missing','[data-reset-password]'],
      ['/invite/missing','#auth-form'],
    ]) {
      await page.goto(path);
      await expect(page.locator(selector).first()).toBeVisible();
    }
    expect((await page.goto('/nonexistent-space-route')).status()).toBe(404);
    await page.route('**/api/status',route=>route.fulfill({status:503,json:{error:'connection error'}}));
    await page.goto('/');
    await expect(page.locator('#retry')).toBeVisible();
    console.log(browser.browserType().name(),'sign-in, public links, reset and error routes');
  } finally { await context.close(); }
}

try {
  for(let attempt=0;;attempt++){
    try{if((await fetch(origin+'/api/health')).ok)break;}catch{}
    if(attempt===200||server.exitCode!==null)throw Error('Local server failed to start');
    await elapsed(100);
  }
  const fixtures=await fixture();
  for(const engine of engines){
    const browser=await engine.launch();
    try{
      const videoDir=process.env.DRG_MOTION_VIDEO;
      if(videoDir)await flow(browser,fixtures,{width:1200,video:{dir:videoDir,size:{width:960,height:720}}});
      else {
        await flow(browser,fixtures,{width:1440});
        await flow(browser,fixtures,{width:390,mobile:true});
        if(!baseline)await flow(browser,fixtures,{width:390,mobile:true,reduce:true});
        await publicScreens(browser,fixtures);
        await chatAfterDialog(browser,fixtures);
      }
    }finally{await browser.close();}
  }
}finally{
  server.kill('SIGTERM');
  await stopped;
}
