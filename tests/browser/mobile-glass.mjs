import {readAppStyles,openTestOrigin} from './fixtures.mjs';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {chromium,webkit} from 'playwright';

const css=await readAppStyles();
const browser=await (process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch({headless:true});
await mkdir('artifacts',{recursive:true});
try{
  for(const [width,height,top,bottom,side] of [[390,844,59,34,0],[320,568,0,0,0],[740,390,0,21,44]]){
    const page=await browser.newPage({locale:'ru-RU',viewport:{width,height},hasTouch:true});
    await openTestOrigin(page);await page.setContent(`<!doctype html><style>${css}</style><div id="app-status-background"></div><div id="app"><div class="workspace"><main class="content"><div style="height:1800px">Прокрутите вниз</div><button id="last-action">Последняя строка</button></main><nav class="mobile-app-tabs">${['Сообщения','Заметки','Дела','Настройки'].map((name,i)=>`<button ${i===0?'aria-current="page"':''}><svg viewBox="0 0 24 24"><path d="M4 5h16v12H8l-4 3z" fill="none" stroke="currentColor"/></svg><span>${name}</span></button>`).join('')}</nav></div></div>`);
    await page.evaluate(({top,bottom,side})=>{
      const root=document.documentElement;root.classList.add('app-standalone');root.dataset.scheme='dark';root.dataset.theme='ocean';
      for(const [key,value] of Object.entries({top,bottom,left:side,right:side}))root.style.setProperty('--app-safe-'+key,value+'px');
    },{top,bottom,side});
    const nav=page.locator('.mobile-app-tabs'),box=await nav.boundingBox();
    assert.equal(box.x,side+12);assert.equal(box.width,width-2*side-24);
    assert.equal(box.y+box.height,height-Math.max(12,bottom-10));
    for(const button of await nav.locator('button').all())assert(await button.evaluate(el=>{const r=el.getBoundingClientRect();return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}));
    await page.locator('.content').evaluate(el=>el.scrollTop=el.scrollHeight);
    const last=await page.locator('#last-action').boundingBox();assert(last.y+last.height<=box.y);
    await page.locator('#last-action').click();
    // A changing canvas must be visible through the glass itself, away from
    // labels/icons. Comparing a crop also catches an accidental opaque layer.
    await page.evaluate(()=>{const layer=document.createElement('div');layer.id='glass-underlay';layer.style.cssText='position:absolute;inset:0;z-index:1;background:#ee5555;pointer-events:none';document.querySelector('.workspace').append(layer);});
    const clip={x:Math.floor(box.x+box.width/2),y:Math.floor(box.y+3),width:4,height:4};
    const warm=await page.screenshot({clip});
    await page.locator('#glass-underlay').evaluate(el=>el.style.background='#4466ee');
    const cool=await page.screenshot({clip});assert(!warm.equals(cool),'The island must transmit its backdrop');
    const filter=await nav.evaluate(el=>{const s=getComputedStyle(el);return s.backdropFilter||s.webkitBackdropFilter;});assert(filter.includes('blur('));
    await page.locator('#glass-underlay').evaluate(el=>el.style.background='repeating-linear-gradient(90deg,#e55 0 8px,#46e 8px 16px)');
    if(process.env.DRG_BROWSER_ENGINE!=='webkit'){
      const pixels=await page.screenshot({clip:{x:Math.floor(box.x+box.width/2-50),y:Math.floor(box.y+4),width:100,height:2}});
      const spread=JSON.parse(execFileSync(process.env.DRG_PYTHON||'.venv/bin/python',['-c','import io,json,sys; from PIL import Image; im=Image.open(io.BytesIO(sys.stdin.buffer.read())).convert("RGB"); print(json.dumps([max(c)-min(c) for c in zip(*[im.getpixel((x,0)) for x in range(im.width)])]))'],{input:pixels}).toString());
      assert(Math.max(...spread)<15,'Blur must blend the sharp stripes, not only tint them');
    }
    await page.screenshot({path:`artifacts/mobile-glass-${width}.png`});
    await page.locator('#glass-underlay').evaluate(el=>el.remove());
    await page.evaluate(()=>document.querySelector('.workspace').classList.add('chat-open'));
    await page.locator('.content').evaluate(el=>el.innerHTML='<div class="chat-shell room-open"></div>');
    assert(await nav.isHidden());
    // The home-indicator and lateral safe areas use the conversation canvas,
    // instead of the old differently coloured strip below the composer.
    for(const scheme of ['light','dark'])for(const theme of ['ocean','sage','amber','lilac','rose','slate']){
      const colors=await page.evaluate(({scheme,theme})=>{
        const root=document.documentElement;root.dataset.scheme=scheme;root.dataset.theme=theme;
        const probe=document.createElement('div');probe.style.background='var(--chat-canvas)';document.body.append(probe);
        const values=[root,document.body,document.querySelector('#app'),document.querySelector('#app-status-background'),probe].map(el=>getComputedStyle(el).backgroundColor);probe.remove();return values;
      },{scheme,theme});
      assert.equal(new Set(colors).size,1,theme+' '+scheme+' canvas');
    }
    await page.close();
  }
  console.log('Floating glass passed: safe bounds, last-row reachability, hit targets, actual backdrop transmission, blur declaration and all-theme full-screen canvas. Chromium additionally checks blur pixels; headless WebKit omits compositor blur from screenshots even in the minimal reproduction.');
}finally{await browser.close();}
