import {test,expect} from './installed-test.mjs';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest();
  return String((hash.readUInt32BE(hash[19]&15)&0x7fffffff)%1000000).padStart(6,'0');
}

for(const width of [1280,390])for(const language of ['ru','en']){
  test(`owner manages existing share links before creating another (${width}, ${language})`,async({page},info)=>{
    test.setTimeout(90000);
    const origin='http://127.0.0.1:8001',password='Sharing-dialog-isolated-123';let csrf='';
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    async function api(path,data,method='POST'){
      const response=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data});
      expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
    }
    csrf=(await api('/setup',{login:'shareowner',password,token:'browser-test-setup-key'})).csrf;
    const enrolled=await api('/2fa/enroll',{password});await api('/2fa/confirm',{code:totp(enrolled.secret)});
    csrf=(await api('/2fa/activate',{saved_recovery_codes:true})).csrf;
    await api('/language',{language},'PUT');
    const folder=await api('/files/folders',{name:'Shared documents'}),empty=await api('/files/folders',{name:'No links'});
    const first=await api('/files/'+folder.id+'/shares',{mode:'public',duration:2,unit:'days',download_limit:10});
    const second=await api('/files/'+folder.id+'/shares',{mode:'password',password:'Shared-secret-123',duration:1,unit:'days'});
    await page.setViewportSize({width,height:900});await page.goto('/');await page.locator('.sidebar [data-view=files]').click();
    const dialog=page.locator('.file-share-dialog'),links=()=>dialog.locator('[data-share-tab=links]'),create=()=>dialog.locator('[data-share-tab=create]');
    async function open(id){await page.locator('[data-file-menu="'+id+'"]').click();await page.locator('[data-file-share]').click();await expect(dialog).toBeVisible();}
    async function close(){await dialog.locator('.modal-close').click();await expect(dialog).toHaveCount(0);}
    await open(folder.id);await expect(dialog.locator('[data-share-id]')).toHaveCount(2);
    await expect(links()).toHaveAttribute('aria-selected','true');await expect(dialog.locator('[data-file-share-form]')).toBeHidden();
    const row=dialog.locator('[data-share-id="'+first.token+'"]'),url=row.locator('[data-share-url]');
    await expect(url).toHaveValue(first.url);await expect(row.locator('strong')).not.toHaveText('public');
    expect(await links().evaluate(el=>el.getBoundingClientRect().bottom<=el.closest('.modal').getBoundingClientRect().bottom)).toBe(true);
    for(const scheme of ['light','dark']){
      await page.evaluate(scheme=>document.documentElement.dataset.scheme=scheme,scheme);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      expect(await dialog.locator('.modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBe(true);
      await page.screenshot({path:info.outputPath('links-'+scheme+'.png'),fullPage:true});
    }
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>window.copiedShare=value}}));
    await row.locator('[data-copy-file-share]').click();expect(await page.evaluate(()=>window.copiedShare)).toBe(first.url);
    await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('Clipboard denied');}}}));
    await row.locator('[data-copy-file-share]').click();await expect(url).toBeFocused();
    expect(await url.evaluate(el=>el.selectionStart===0&&el.selectionEnd===el.value.length)).toBe(true);

    // The focus trap skips controls in hidden tabs and respects roving tabs.
    const last=dialog.locator('[data-limit]').last();await last.focus();await page.keyboard.press('Tab');await expect(dialog.locator('.modal-close')).toBeFocused();
    await page.keyboard.press('Shift+Tab');await expect(last).toBeFocused();
    await links().focus();await page.keyboard.press('ArrowRight');await expect(create()).toBeFocused();
    const form=dialog.locator('[data-file-share-form]');await expect(form).toBeVisible();
    await form.locator('[name=duration]').fill('3');await form.locator('[name=download_limit]').fill('7');
    await links().click();await create().click();await expect(form.locator('[name=duration]')).toHaveValue('3');
    await expect(form.locator('[name=download_limit]')).toHaveValue('7');await expect(form.locator('[name=auto_delete]')).not.toBeChecked();
    await form.locator('[type=submit]').click();await expect(dialog.locator('[data-share-id]')).toHaveCount(3);
    await expect(links()).toHaveAttribute('aria-selected','true');await expect(dialog.locator('[data-file-share-result]')).not.toBeEmpty();
    expect((await api('/files/'+folder.id+'/retention',undefined,'GET')).files).toEqual([]);
    await row.locator('[data-limit]').click();const limit=page.locator('[data-limit-form]');await limit.locator('[name=limit]').fill('12');await limit.locator('button').click();
    await expect(row).toContainText('12');
    await dialog.locator('[data-revoke-file-share="'+second.token+'"]').click();await expect(dialog.locator('[data-share-id]')).toHaveCount(2);
    await close();

    const fail=route=>route.fulfill({status:503,json:{detail:'Links temporarily unavailable'}});
    const endpoint='**/api/files/'+folder.id+'/shares';
    await page.route(endpoint,route=>route.request().method()==='GET'?fail(route):route.continue());
    await open(folder.id);await expect(dialog.locator('[data-file-share-list] [role=alert]')).toHaveText('Links temporarily unavailable');
    await page.unroute(endpoint);await dialog.locator('[data-share-retry]').click();await expect(dialog.locator('[data-share-id]')).toHaveCount(2);

    // A successful POST followed by a failed GET must not invite a duplicate POST.
    let writes=0;const count=request=>{if(request.method()==='POST'&&request.url().endsWith('/files/'+folder.id+'/shares'))writes++;};page.on('request',count);
    await create().click();await page.route(endpoint,route=>route.request().method()==='GET'?fail(route):route.continue());
    await form.locator('[type=submit]').click();await expect(dialog.locator('[data-share-retry]')).toBeVisible();
    await expect(links()).toHaveAttribute('aria-selected','true');await expect(dialog.locator('[data-file-share-result]')).not.toBeEmpty();
    await page.unroute(endpoint);await dialog.locator('[data-share-retry]').click();await expect(dialog.locator('[data-share-id]')).toHaveCount(3);expect(writes).toBe(1);page.off('request',count);
    await close();
    await open(empty.id);await expect(create()).toHaveAttribute('aria-selected','true');
    await links().click();await expect(dialog.locator('[data-share-start]')).toBeVisible();
    await dialog.locator('[data-share-start]').click();await expect(form).toBeVisible();
    expect(errors).toEqual([]);
  });
}
