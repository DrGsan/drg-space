import {expect} from '@playwright/test';
import {appSection} from './notes-ui.js';

// Native delivery is tested on a real installed device. This UI flow uses a
// fake local push service and never requests OS permission or sends a push.
export async function pushFlow(page){
  const publicKey=Buffer.from([4,...Array(64).fill(7)]).toString('base64url');
  let subscribed=false;
  const config=route=>route.fulfill({json:{configured:true,public_key:publicKey}});
  const status=route=>route.fulfill({json:{subscribed}});
  const subscription=route=>{subscribed=route.request().method()==='POST';return route.fulfill({json:subscribed?{subscribed:true}:{ok:true}});};
  await appSection(page,'Главная');
  await page.route('**/api/push',config);
  await page.route('**/api/push/status',status);
  await page.route('**/api/push/subscriptions',subscription);
  await page.evaluate(()=>{
    const original={serviceWorker:Object.getOwnPropertyDescriptor(navigator,'serviceWorker'),Notification:Object.getOwnPropertyDescriptor(window,'Notification'),PushManager:Object.getOwnPropertyDescriptor(window,'PushManager'),userAgent:Object.getOwnPropertyDescriptor(navigator,'userAgent'),standalone:Object.getOwnPropertyDescriptor(navigator,'standalone')};
    let local;
    const notification={permission:'default',requestPermission(){window.__pushPermissionCalls++;this.permission='granted';return Promise.resolve('granted');}};
    const push={endpoint:'https://push.invalid/disposable-browser-test',toJSON(){return {endpoint:this.endpoint,keys:{p256dh:'fixture',auth:'fixture'}};},async unsubscribe(){local=null;return true;}};
    const registration={active:{scriptURL:location.origin+'/push-sw.js'},pushManager:{async getSubscription(){return local;},async subscribe(){local=push;return push;}},async getNotifications(){return [];}};
    Object.defineProperty(navigator,'serviceWorker',{configurable:true,value:{async getRegistration(){return registration;},async register(){return registration;},ready:Promise.resolve(registration)}});
    Object.defineProperty(window,'Notification',{configurable:true,value:notification});
    Object.defineProperty(window,'PushManager',{configurable:true,value:function(){}});
    window.__pushPermissionCalls=0;
    window.__restorePushFixture=()=>{
      for(const [name,object] of [['serviceWorker',navigator],['Notification',window],['PushManager',window],['userAgent',navigator],['standalone',navigator]]){
        if(original[name])Object.defineProperty(object,name,original[name]);else delete object[name];
      }
      delete window.__pushPermissionCalls;delete window.__restorePushFixture;
    };
  });
  try{
    await appSection(page,'Настройки');
    const enable=page.getByRole('button',{name:'Включить push-уведомления',exact:true});
    const disable=page.getByRole('button',{name:'Отключить на этом устройстве',exact:true});
    await expect(enable).toBeEnabled();
    expect(await page.evaluate(()=>window.__pushPermissionCalls)).toBe(0);
    await enable.click();
    await expect(disable).toBeVisible();await expect(enable).toBeHidden();
    await expect(page.locator('[data-push-status]')).toHaveText('Push-уведомления включены на этом устройстве.');
    expect(await page.evaluate(()=>window.__pushPermissionCalls)).toBe(1);
    await disable.click();await expect(enable).toBeEnabled();await expect(disable).toBeHidden();
    expect(subscribed).toBe(false);
    await page.evaluate(()=>{Notification.permission='denied';});
    await appSection(page,'Главная');await appSection(page,'Настройки');
    await expect(enable).toBeDisabled();await expect(page.locator('[data-push-status]')).toContainText('Доступ к уведомлениям запрещён');
    await page.evaluate(()=>{
      Notification.permission='default';
      Object.defineProperty(navigator,'userAgent',{configurable:true,value:'iPhone Safari'});
      Object.defineProperty(navigator,'standalone',{configurable:true,value:false});
    });
    await appSection(page,'Главная');await appSection(page,'Настройки');
    await expect(enable).toBeDisabled();await expect(page.locator('[data-push-status]')).toContainText('добавьте сайт на экран «Домой»');
    expect(await page.evaluate(()=>window.__pushPermissionCalls)).toBe(1);
  }finally{
    await page.evaluate(()=>window.__restorePushFixture());
    await page.unroute('**/api/push',config);await page.unroute('**/api/push/status',status);await page.unroute('**/api/push/subscriptions',subscription);
    await appSection(page,'Главная');
  }
}
