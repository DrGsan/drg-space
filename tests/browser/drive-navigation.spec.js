import {test,expect} from './installed-test.mjs';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest();
  return String((hash.readUInt32BE(hash[19]&15)&0x7fffffff)%1000000).padStart(6,'0');
}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
async function installation(page){
  const origin='http://127.0.0.1:8001',password='Drive-navigation-isolated-123';
  let csrf='';
  async function post(path,data){
    const response=await page.request.post('/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  csrf=(await post('/setup',{login:'driveowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await post('/2fa/enroll',{password});await post('/2fa/confirm',{code:totp(enrolled.secret)});
  csrf=(await post('/2fa/activate',{saved_recovery_codes:true})).csrf;
  const a=await post('/files/folders',{name:'Documents'}),b=await post('/files/folders',{name:'Pictures'});
  const nested=await post('/files/folders',{name:'Empty',parent_id:a.id});
  await page.goto('/');await page.locator('.sidebar [data-view=files]').click();
  await expect(page.locator('.drive-panel')).toBeVisible();
  return {a,b,nested};
}
for(const width of [1280,390])for(const theme of ['light','dark']){
  test(`Drive navigation keeps its shell, survives errors and ignores late responses (${width}, ${theme})`,async({page},info)=>{
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const {a,b,nested}=await installation(page);
    await page.setViewportSize({width,height:900});await page.evaluate(theme=>document.documentElement.dataset.scheme=theme,theme);
    const open=id=>page.locator(`[data-open-file="${id}"]`),root=()=>page.locator('.file-crumbs [data-folder=""]');
    const title=page.locator('.drive-heading h2');
    await page.locator('[data-drive-layout]').click();await page.locator('[data-layout-option=grid]').click();
    await page.locator('[data-files-sort]').click();await page.locator('[data-sort-option=updated]').click();
    const parts=await page.locator('.drive-workspace-header,.drive-panel,.drive-sidebar,.drive-main,.drive-toolbar,.drive-footer,.drive-mobile-tabs').elementHandles();
    const before=await page.locator('.drive-panel').boundingBox();
    await page.evaluate(()=>{
      window.driveGaps=0;
      window.driveObserver=new MutationObserver(()=>{if(!document.querySelector('.drive-panel')||!document.querySelector('.drive-toolbar'))driveGaps++;});
      driveObserver.observe(document.querySelector('#view-content'),{childList:true,subtree:true});
    });
    const gate=deferred(),started=deferred();let requests=0;
    const routeA=async route=>{
      if(new URL(route.request().url()).searchParams.get('parent_id')!==a.id)return route.continue();
      requests++;const response=await route.fetch();started.resolve();await gate.promise;await route.fulfill({response});
    };
    await page.route('**/api/files?**',routeA);
    try{
      await open(a.id).click();await started.promise;
      await expect(page.locator('.drive-panel')).toHaveAttribute('aria-busy','true');
      await expect(page.locator('[data-file-status]')).toContainText('Загружаем');
      await expect(page.locator('[data-new-folder]')).toBeDisabled();
      await expect(open(b.id)).toBeEnabled();await expect(root()).toBeEnabled();
      expect(await page.locator('.drive-panel').boundingBox()).toEqual(before);
      for(const part of parts)expect(await part.evaluate(el=>el.isConnected)).toBe(true);
      await open(a.id).click();
      await page.screenshot({path:info.outputPath('pending.png'),fullPage:true});
    }finally{gate.resolve();}
    await expect(title).toHaveText('Documents');expect(requests).toBe(1);
    for(const part of parts)expect(await part.evaluate(el=>el.isConnected)).toBe(true);
    await expect(page.locator('.drive-panel')).toHaveAttribute('data-layout','grid');
    await expect(page.locator('[data-files-sort]')).toHaveText('по дате');
    await page.unroute('**/api/files?**',routeA);
    await open(nested.id).click();await expect(title).toHaveText('Empty');await expect(page.locator('.drive-empty')).toBeVisible();
    await page.locator('.drive-back').click();await expect(title).toHaveText('Documents');
    await root().click();await expect(title).toHaveText('DrG Drive');

    // The previous list remains navigable while an older request is pending.
    const late=deferred(),lateStarted=deferred();
    const lateRoute=async route=>{
      if(new URL(route.request().url()).searchParams.get('parent_id')!==a.id)return route.continue();
      const response=await route.fetch();lateStarted.resolve();await late.promise;await route.fulfill({response});
    };
    await page.route('**/api/files?**',lateRoute);
    try{
      await open(a.id).click();await lateStarted.promise;await open(b.id).click();await expect(title).toHaveText('Pictures');
      const response=page.waitForResponse(r=>new URL(r.url()).searchParams.get('parent_id')===a.id);late.resolve();await response;
      await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
      await expect(title).toHaveText('Pictures');await expect(page.locator('.file-crumbs')).not.toContainText('Documents');
    }finally{late.resolve();await page.unroute('**/api/files?**',lateRoute);}
    await root().click();await expect(title).toHaveText('DrG Drive');

    const fail=async route=>new URL(route.request().url()).searchParams.get('parent_id')===a.id
      ?route.fulfill({status:503,json:{detail:'Navigation temporarily unavailable'}}):route.continue();
    await page.route('**/api/files?**',fail);await open(a.id).click();
    await expect(page.locator('[data-file-status]')).toContainText('Navigation temporarily unavailable');
    await expect(title).toHaveText('DrG Drive');await expect(page.locator('[data-new-folder]')).toBeEnabled();
    await expect(page.locator('[data-delete-selected]')).toBeDisabled();
    await page.unroute('**/api/files?**',fail);await open(a.id).click();await expect(title).toHaveText('Documents');
    await page.screenshot({path:info.outputPath('loaded.png'),fullPage:true});
    await page.locator('[data-files-view=trash]').click();
    await expect(page.locator('.drive-panel')).toHaveAttribute('data-drive-view','trash');
    const failRoot=route=>route.fulfill({status:503,json:{detail:'Retry folder navigation'}});
    await page.route('**/api/files?**',failRoot);await page.locator('[data-files-view=active]').click();
    await expect(page.locator('[data-file-status]')).toContainText('Retry folder navigation');
    await expect(page.locator('.drive-panel')).toHaveAttribute('data-drive-view','trash');
    await expect(page.locator('[data-drive-layout]')).toBeDisabled();
    await page.unroute('**/api/files?**',failRoot);await page.locator('[data-files-view=active]').click();
    await expect(title).toHaveText('DrG Drive');await expect(page.locator('[data-drive-layout]')).toBeEnabled();
    expect(await page.evaluate(()=>{driveObserver.disconnect();return driveGaps;})).toBe(0);
    expect(errors).toEqual([]);
  });
}
