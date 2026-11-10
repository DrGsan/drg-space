import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {chromium,webkit} from 'playwright';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const bundle=await build({entryPoints:['web/modules/dashboard/widgets/clock.js'],bundle:true,format:'iife',globalName:'Clock',write:false});
const manifest=JSON.parse(await readFile('app/static/asset-manifest.json','utf8'));
const css=await readFile('app/static/assets/'+manifest.aliases['app.css'],'utf8');
const browser=await ({chromium,webkit}[engine]).launch({headless:true});
try{
  const page=await browser.newPage({viewport:{width:700,height:600}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://clock.test/**',r=>r.fulfill({contentType:'text/html',body:'<html data-theme="ocean" data-scheme="light"><body><div id="clock" style="width:260px;height:340px"></div></body></html>'}));
  await page.goto('http://clock.test/');await page.addStyleTag({content:css});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
    window.mountReviewClock=config=>{window.stopClock?.();window.stopClock=Clock.mountClock(document.querySelector('#clock'),{instance:{config:{timezone:'Europe/Moscow',...config}}});};
    mountReviewClock({});
  });
  const hand=page.locator('[data-hand=second]');
  const pivotError=()=>hand.evaluate(el=>{
    const center=new DOMPoint(120,120),actual=center.matrixTransform(el.getScreenCTM()),expected=center.matrixTransform(el.ownerSVGElement.getScreenCTM());
    return Math.hypot(actual.x-expected.x,actual.y-expected.y);
  });
  await page.waitForFunction(()=>document.querySelector('[data-hand=second]').getAnimations().length===1);
  assert(await pivotError()<0.1,'animated hand stays on its pivot');
  const samples=await hand.evaluate(async el=>{
    const values=[];
    for(let i=0;i<8;i++){
      await new Promise(requestAnimationFrame);
      const m=new DOMMatrix(getComputedStyle(el).transform);
      values.push((Math.atan2(m.b,m.a)*180/Math.PI+360)%360);
    }
    return values;
  });
  for(let i=1;i<samples.length;i++){const step=(samples[i]-samples[i-1]+360)%360;assert(step>0&&step<3,'continuous forward motion');}
  // A full revolution crosses twelve without a reverse sweep.
  await page.clock.setSystemTime(new Date('2026-10-28T12:34:59.800Z'));
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  const angle=()=>hand.evaluate(el=>{const m=new DOMMatrix(getComputedStyle(el).transform);return (Math.atan2(m.b,m.a)*180/Math.PI+360)%360;});
  const before=await angle();await page.waitForTimeout(300);const after=await angle();
  assert(before>358&&after<3,'crossing twelve');assert((after-before+360)%360<4,'no reverse sweep');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  const drift=await hand.evaluate(el=>{const phase=el.getAnimations()[0].currentTime%60000,now=Date.now()%60000,d=Math.abs(phase-now);return Math.min(d,60000-d);});
  assert(drift<100,'resume reanchors to wall time');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.clockHidden});window.clockHidden=true;document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await hand.evaluate(el=>el.getAnimations().length),0);
  await page.evaluate(()=>{window.clockHidden=false;document.dispatchEvent(new Event('visibilitychange'));});
  await page.waitForFunction(()=>document.querySelector('[data-hand=second]').getAnimations().length===1);
  await page.locator('#clock').evaluate(el=>el.style.marginTop='1200px');
  await page.waitForFunction(()=>document.querySelector('[data-hand=second]').getAnimations().length===0);
  await page.locator('#clock').evaluate(el=>{el.style.marginTop='0';scrollTo(0,0);});
  await page.waitForFunction(()=>document.querySelector('[data-hand=second]').getAnimations().length===1);
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>document.querySelector('[data-hand=second]').getAnimations().length===0);
  assert(await pivotError()<0.1,'stepped hand retains the same pivot');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>document.querySelector('[data-hand=second]').getAnimations().length===1);
  await page.evaluate(()=>mountReviewClock({show_seconds:false}));assert.equal(await hand.count(),0);
  await page.evaluate(()=>mountReviewClock({mode:'digital'}));assert.equal(await page.locator('.clock-digits').count(),1);assert.equal(await page.evaluate(()=>document.getAnimations().length),0);
  await page.evaluate(()=>{stopClock();document.querySelector('#clock').remove();window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await page.evaluate(()=>document.getAnimations().length),0);assert.deepEqual(errors,[]);
  console.log(`Clock sweep passed (${engine}): intermediate frames, rollover, wall-time sync, hidden/offscreen pause, reduced motion, digital/no-seconds and disposal.`);
}finally{await browser.close();}
