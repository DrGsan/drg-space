import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

async function scenario(page){
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const ctx=page.context(),password='Section-header-password-123';
  async function post(path,data,csrf=''){
    const r=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});expect(r.ok(),path+' '+await r.text()).toBeTruthy();return r.json();
  }
  let csrf=(await post('/setup',{login:'headerowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await post('/2fa/enroll',{password},csrf);await post('/2fa/confirm',{code:totp(enrolled.secret)},csrf);csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;await post('/admin/access/unlock',{password},csrf);
  await page.route('**/api/widgets/**/rss',r=>r.fulfill({json:{items:[],errors:[]}}));
  await page.route('**/api/modules',async route=>{
    if(route.request().method()!=='GET')return route.continue();const response=await route.fetch(),body=await response.json();
    body.enabled.family_photos=true;body.catalog.push({id:'family_photos',kind:'module',title:'Семейные фото'});await route.fulfill({response,json:body});
  });
  await page.route('**/api/family-photos/**',route=>route.fulfill({json:route.request().url().endsWith('/groups')?{items:[],status:{can_send:false,worker_online:false}}:route.request().url().endsWith('/admin')?{users:[],groups:[]}:{items:[],more:false}}));
  const views=['settings','admin','social','expenses','family_photos','security','personal-data','home'];
  await page.setViewportSize({width:1440,height:1000});await page.goto('/');
  const heading=page.locator('#view-content>.section-heading');
  async function section(view){
    if(['security','personal-data'].includes(view)){
      await page.locator('.sidebar [data-view=settings]').click();const key=view==='security'?'security':'data',row=page.locator('[data-preference-key='+key+']');
      if(await row.getAttribute('open')===null)await row.locator(':scope>summary').click();await page.locator(view==='security'?'#open-security':'#open-personal-data').click();
    }else await page.locator('.sidebar [data-view='+view+']').click();
    await expect(heading).toBeVisible();await expect(page.locator('.topbar')).toBeHidden();
    if(view==='social')await expect(page.locator('.social-sections')).toBeVisible();
  }
  for(const width of [1440,1920,390,320]){
    let anchor;
    await page.setViewportSize({width,height:1000});
    for(const view of views){
      await section(view);const box=await heading.boundingBox();expect(box.height,view+' height').toBe(64);
      expect(await heading.locator('h1').evaluate(el=>getComputedStyle(el).fontSize)).toBe(width>760?'24px':'20px');
      expect((await heading.locator('h1').boundingBox()).width,view+' title width').toBeGreaterThan(45);
      await expect(heading.locator('.eyebrow,.hello-dot')).toHaveCount(0);await expect(page.locator('#open-inbox')).toHaveCount(1);await expect(heading.locator('#open-inbox')).toBeVisible();
      expect(await page.locator('#view-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),view+' overflow').toBe(true);
      await expect(heading.locator('.preferences-identity,[data-profile-avatar],[data-profile-display-name]')).toHaveCount(0);
      const title=await heading.locator('h1').boundingBox(),bell=await heading.locator('#open-inbox').boundingBox(),surface=await page.locator('#view-content').boundingBox();
      expect(box.x,view+' header left edge').toBeCloseTo(surface.x,1);
      expect(box.x+box.width,view+' header right edge').toBeCloseTo(surface.x+surface.width,1);
      if(!anchor)anchor={titleX:title.x,titleY:title.y,bellX:bell.x,bellY:bell.y};
      expect(title.x,view+' title alignment').toBeCloseTo(anchor.titleX,1);
      expect(title.y,view+' title baseline').toBeCloseTo(anchor.titleY,1);
      expect(bell.x,view+' bell alignment').toBeCloseTo(anchor.bellX,1);
      expect(bell.y,view+' bell baseline').toBeCloseTo(anchor.bellY,1);
      const actions=heading.locator('.section-heading-actions');
      if(await actions.isVisible()){const actionBox=await actions.boundingBox();expect(actionBox.x+actionBox.width,view+' actions before bell').toBeLessThanOrEqual(bell.x);}
      if(width>760){
        await expect(page.locator('.sidebar .user')).toBeVisible();
        if(['settings','admin','social'].includes(view)){
          const body=await page.locator(view==='settings'?'.settings-grid':view==='admin'?'.admin-preference-groups':'.social-view').boundingBox();
          expect(body.width,view+' body width').toBe(920);
          expect(body.x+body.width/2,view+' body centered').toBeCloseTo(surface.x+surface.width/2,1);
          expect(body.y-box.y-box.height,view+' body gap').toBe(12);
        }
      }
      if(width===1440)await page.screenshot({path:'artifacts/section-header-'+view+'.png',animations:'disabled'});
      if(view==='social'){await page.locator('#refresh-social').click();await expect(page.locator('.social-sections')).toBeVisible();await expect(page.locator('#open-inbox')).toHaveCount(1);}
      if(view==='expenses'){await page.locator('[data-start-night]').click();await expect(page.locator('[data-start-event]')).toBeVisible();await page.locator('.modal-close').click();}
    }
  }
  await page.setViewportSize({width:1440,height:1000});await section('settings');
  await page.locator('#open-inbox').click();await expect(page.locator('.modal-overlay')).toContainText('Уведомления');await page.locator('.modal-close').click();
  // The focused workspaces retain their own headers and never use this component.
  for(const view of ['notes','tasks','chat','files']){
    await page.goto('/');await page.locator('.sidebar [data-view='+view+']').click();await expect(heading).toHaveCount(0);
    await page.locator('#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu').click();
  }
  expect(errors).toEqual([]);
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8030',directory=await mkdtemp(tmpdir()+'/drg-section-headers-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8030','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-4000));
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{if((await fetch(origin+'/api/health')).ok){ready=true;break;}}catch{}
    if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  if(!ready)throw Error(output||'Browser server did not start');
  browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:false,isMobile:false}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Section headers passed: ordinary sections, full-width aligned titles/bells, no duplicate profile, centered forms, consistent typography/height, actions/bell, social refresh, desktop/mobile and excluded workspaces.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
