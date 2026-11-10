// An admin grant can expire while an already-mounted invitation list is open.
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
const bundle=await build({entryPoints:['web/modules/admin/invitations.js'],bundle:true,write:false,format:'iife',globalName:'Invitations',loader:{'.css':'empty'}});
const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const browser=await ({chromium,webkit}[engine]).launch({headless:true});
try{
  const page=await browser.newPage();
  await page.route('http://invitation-review.test/**',route=>route.fulfill({contentType:'text/html',body:'<div id="app"><div data-admin-content><details open><summary>Invitations</summary><section id="invitation-host"></section></details></div></div>'}));
  await page.goto('http://invitation-review.test/');await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.clock.install();
  await page.evaluate(async()=>{
    const content=document.querySelector('[data-admin-content]'),host=document.querySelector('#invitation-host');
    const expires=Math.floor(Date.now()/1000)+2;
    window.invitationChecks={calls:0,hiddenCalls:0,errors:[]};
    await Invitations.mountRegistration(host,{state:{me:{role:'owner'}},toast:message=>invitationChecks.errors.push(message),api:async path=>{
      if(path==='/admin/registration')return {enabled:false};
      if(path!=='/invites')throw Error('Unexpected request '+path);
      invitationChecks.calls++;if(content.hidden)invitationChecks.hiddenCalls++;
      const stamp=Math.floor(Date.now()/1000);
      return {server_time:stamp,invitations:stamp<expires?[{id:'active-link',url:'http://invitation-review.test/invite/example',created:stamp,expires,status:'active',add_creator_as_friend:false,can_revoke:true}]:[]};
    }});
  });
  await expect(page.locator('[data-invitation-id]')).toHaveCount(1);
  await page.evaluate(()=>{document.querySelector('[data-admin-content]').hidden=true;});
  await page.clock.runFor(3100);
  expect(await page.evaluate(()=>({calls:invitationChecks.calls,hiddenCalls:invitationChecks.hiddenCalls}))).toEqual({calls:1,hiddenCalls:0});
  await page.evaluate(()=>{document.querySelector('[data-admin-content]').hidden=false;});
  await expect(page.locator('[data-invitation-id]')).toHaveCount(0);
  expect(await page.evaluate(()=>({calls:invitationChecks.calls,hiddenCalls:invitationChecks.hiddenCalls,errors:invitationChecks.errors}))).toEqual({calls:2,hiddenCalls:0,errors:[]});
  // Ordinary child updates and hidden descendants do not trigger refresh loops.
  await page.evaluate(()=>{const child=document.createElement('span');document.querySelector('#invitation-host').append(child);child.hidden=true;});
  await page.clock.runFor(100);
  expect(await page.evaluate(()=>invitationChecks.calls)).toBe(2);
  console.log('Invitation admin hide → expire → reveal:',engine,'PASS; no hidden requests.');
}finally{await browser.close();}
