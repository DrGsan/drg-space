// Isolated real-API scenario; no developer or production account is touched.
import {chromium,webkit} from 'playwright';
import {expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {createHmac} from 'node:crypto';
import {mkdtemp,rm,mkdir,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {appSection} from './notes-ui.js';

const engine=process.env.DRG_BROWSER_ENGINE==='webkit'?'webkit':'chromium';
const baseline=process.env.DRG_WALLPAPER_BASELINE==='1';
const phase=baseline?'before':'after';
const catalog=JSON.parse(await readFile('app/wallpaper_catalog.json','utf8'));
const out='artifacts/wallpaper-categories-review'+(engine==='webkit'?'/webkit':'');
await mkdir(out,{recursive:true});
const origin='http://127.0.0.1:8058',directory=await mkdtemp(tmpdir()+'/drg-wallpapers-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8058','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-wallpaper-browser-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:'',FEDERATION_PUBLIC_URL:''},stdio:['ignore','ignore','pipe']});
let output='',browser;server.stderr.on('data',data=>output=(output+data).slice(-5000));
const exited=new Promise(resolve=>server.once('exit',resolve));
try{
  let ready=false;
  for(let attempt=0;attempt<150;attempt++){
    try{ready=(await fetch(origin+'/api/health')).ok;}catch{}
    if(ready)break;if(server.exitCode!==null)throw Error(output);
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  if(!ready)throw Error('Isolated wallpaper server did not start: '+output);
  browser=await ({chromium,webkit}[engine]).launch({headless:true});
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,viewport:{width:1440,height:1100},colorScheme:'light',timezoneId:'Europe/Moscow'});
  const page=await context.newPage();
  const requestedWallpapers=new Set();page.on('request',request=>{
    const filename=new URL(request.url()).pathname.match(/^\/assets\/wallpapers\/([^/]+)\.webp$/)?.[1];
    if(filename)requestedWallpapers.add(filename);
  });
  await page.route('**/api/widgets/*/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  const errors=[];page.on('pageerror',error=>{
    // WebKit can report cancelled authenticated fetches during navigation.
    if(engine==='webkit'&&error.name==='Fetch API cannot load http'&&error.message.endsWith('due to access control checks.'))return;
    errors.push(error.message);
  });
  let csrf='';
  const send=async(path,body,method='POST')=>{
    const response=await page.request.fetch('/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':csrf},data:body});
    expect(response.ok(),path+' '+response.status()+' '+await response.text()).toBe(true);return response.json();
  };
  csrf=(await send('/setup',{login:'alexander',password:'Wallpaper-test-password-123',token:'browser-test-setup-key'})).csrf;
  const {secret}=await send('/2fa/enroll',{password:'Wallpaper-test-password-123'});
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits=[...secret].map(c=>alphabet.indexOf(c).toString(2).padStart(5,'0')).join('');
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bits.match(/.{8}/g).map(x=>parseInt(x,2)))).update(counter).digest(),offset=hash[19]&15;
  await send('/2fa/confirm',{code:String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0')});
  csrf=(await send('/2fa/activate',{saved_recovery_codes:true})).csrf;
  await send('/appearance',{theme:'ocean',mode:'light',style:'glass',wallpaper:'mountains'},'PUT');
  await page.goto('/');
  let language='ru';
  async function openAppearance(){
    if(language==='ru')await appSection(page,'Настройки');
    else{
      await page.locator('.workspace').waitFor();
      await page.locator('.sidebar nav').getByRole('button',{name:'Settings',exact:true}).click();
      const appearance=page.locator('[data-preference-key="appearance"]');
      if(await appearance.getAttribute('open')===null)await appearance.locator(':scope>summary').click();
    }
    for(const row of await page.locator('.preference-row').all())if(await row.getAttribute('data-preference-key')!=='appearance'&&await row.getAttribute('open')!==null)await row.locator(':scope>summary').click();
    await page.locator('[data-appearance]').waitFor();
  }
  async function screenshotSettings(){
    await page.setViewportSize({width:1440,height:1100});await openAppearance();
    await page.locator('[data-preference-key="appearance"]').screenshot({path:out+'/'+phase+'-desktop-settings.png'});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await openAppearance();
    await page.evaluate(()=>document.documentElement.classList.add('app-standalone'));
    await page.locator('[data-wallpapers]').scrollIntoViewIfNeeded();
    await page.screenshot({path:out+'/'+phase+'-webapp-settings.png'});
    await page.locator('[data-wallpapers]').screenshot({path:out+'/'+phase+'-webapp-wallpapers.png'});
  }
  await screenshotSettings();
  if(!baseline){
    expect(catalog.map(category=>category.items.length)).toEqual([6,6,6,6]);
    expect(catalog.flatMap(category=>category.items)).toHaveLength(24);
    expect(catalog.flatMap(category=>category.items.map(item=>item.id))).not.toContain('nature-coast');
    expect([...requestedWallpapers].some(id=>id.startsWith('space-')||id.startsWith('abstract-')||id.startsWith('cozy-')),'Hidden category artwork must not load before a category opens.').toBe(false);
    const value=()=>page.locator('[name=wallpaper]').inputValue();
    const selectedCategory=()=>page.locator('[data-wallpaper-category][aria-pressed=true]').getAttribute('data-wallpaper-category');
    const saved=async()=>(await send('/appearance',undefined,'GET')).preferences.wallpaper;
    const selectCategory=category=>page.locator('[data-wallpaper-category="'+category+'"]').click();
    const choose=id=>page.locator('button[data-wallpaper="'+id+'"]').click();
    const save=()=>page.locator('[data-appearance] [type=submit]').click();
    const cancel=()=>page.locator('[data-appearance-reset]').click();
    const waitSaved=()=>expect(page.locator('[data-appearance-status]')).toHaveText(language==='ru'?'Оформление сохранено':'Layout saved');
    const within=async()=>{
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
      const buttons=await page.locator('[data-wallpaper-category]').evaluateAll(items=>items.map(button=>({width:button.clientWidth,content:button.scrollWidth,left:button.getBoundingClientRect().left,right:button.getBoundingClientRect().right})));
      for(const button of buttons){expect(button.content).toBeLessThanOrEqual(button.width+1);expect(button.left).toBeGreaterThanOrEqual(0);expect(button.right).toBeLessThanOrEqual(page.viewportSize().width+1);}
    };
    // Retiring a preset removes only its picker button. Accounts which already
    // selected it retain their actual background through reload/partial saves.
    await send('/appearance',{wallpaper:'nature-coast'},'PUT');await page.reload();await openAppearance();
    expect(await value()).toBe('nature-coast');expect(await selectedCategory()).toBe('nature');
    await expect(page.locator('html')).toHaveAttribute('data-wallpaper','nature-coast');
    await expect(page.locator('[data-wallpaper-panel="nature"] button')).toHaveCount(6);
    await expect(page.locator('button[data-wallpaper="nature-coast"]')).toHaveCount(0);
    expect(await page.locator('.wallpaper-alternatives [data-wallpaper]').evaluateAll(buttons=>buttons.map(button=>button.dataset.wallpaper))).toEqual(['dusk','mist','none']);
    await expect(page.locator('[data-custom-wallpaper]')).toBeHidden();
    expect(await page.locator('html').evaluate(el=>el.style.getPropertyValue('--app-wallpaper'))).toContain('/assets/wallpapers/nature-coast.webp');
    await selectCategory('space');await choose('space-rings');await cancel();
    expect(await value()).toBe('nature-coast');expect(await saved()).toBe('nature-coast');
    await send('/appearance',{theme:'sage'},'PUT');expect(await saved()).toBe('nature-coast');
    await page.reload();await openAppearance();await expect(page.locator('html')).toHaveAttribute('data-wallpaper','nature-coast');
    await send('/appearance',{theme:'ocean',wallpaper:'mountains'},'PUT');await page.reload();await openAppearance();
    expect(await selectedCategory()).toBe('nature');expect(await value()).toBe('mountains');
    await selectCategory('space');
    expect(await value()).toBe('mountains');expect(await saved()).toBe('mountains');
    await expect(page.locator('[data-appearance-status]')).toHaveText('');
    await choose('space-rings');
    await expect(page.locator('html')).toHaveAttribute('data-wallpaper','space-rings');expect(await saved()).toBe('mountains');
    await selectCategory('abstract');expect(await value()).toBe('space-rings');
    await cancel();expect(await selectedCategory()).toBe('nature');expect(await value()).toBe('mountains');
    await selectCategory('space');await choose('space-nebula');await save();await waitSaved();
    expect(await saved()).toBe('space-nebula');
    await page.reload();await openAppearance();expect(await selectedCategory()).toBe('space');
    await expect(page.locator('button[data-wallpaper="space-nebula"]')).toHaveAttribute('aria-pressed','true');

    // Changing categories must not discard a not-yet-uploaded private photo.
    const mediaBefore=await send('/media',undefined,'GET');
    await page.locator('[data-wallpaper-file]').setInputFiles('tests/fixtures/blue.png');
    await expect(page.locator('[data-wallpaper-status]')).toContainText('Изображение готово');
    const pendingBackground=await page.locator('html').evaluate(el=>el.style.getPropertyValue('--app-wallpaper'));
    expect(pendingBackground).toContain('blob:');await selectCategory('cozy');
    expect(await page.locator('html').evaluate(el=>el.style.getPropertyValue('--app-wallpaper'))).toBe(pendingBackground);
    expect((await send('/media',undefined,'GET')).length).toBe(mediaBefore.length);
    await cancel();expect(await selectedCategory()).toBe('space');expect(await value()).toBe('space-nebula');
    await expect(page.locator('[data-custom-wallpaper]')).toBeHidden();

    await page.locator('[data-wallpaper-file]').setInputFiles('tests/fixtures/blue.png');
    await expect(page.locator('[data-wallpaper-status]')).toContainText('Изображение готово');
    await selectCategory('cozy');await choose('cozy-rain');
    await expect(page.locator('[data-custom-wallpaper]')).toBeHidden();await save();await waitSaved();
    expect(await saved()).toBe('cozy-rain');expect((await send('/media',undefined,'GET')).length).toBe(mediaBefore.length);
    await page.reload();await openAppearance();expect(await selectedCategory()).toBe('cozy');

    await page.locator('[data-wallpaper-file]').setInputFiles('tests/fixtures/blue.png');
    await expect(page.locator('[data-wallpaper-status]')).toContainText('Изображение готово');await save();await waitSaved();
    const custom=await saved();expect(custom).toMatch(/^[a-f0-9]{32}$/);
    await page.reload();await openAppearance();await expect(page.locator('[data-custom-wallpaper]')).toBeVisible();
    await selectCategory('abstract');expect(await value()).toBe(custom);await choose('none');
    await expect(page.locator('[data-custom-wallpaper]')).toBeHidden();await cancel();
    expect(await value()).toBe(custom);await expect(page.locator('[data-custom-wallpaper]')).toBeVisible();

    // Existing media and the six original IDs remain available alongside all
    // new presets, with real image decoding after category changes.
    for(const category of catalog){
      await selectCategory(category.id);
      await expect(page.locator('[data-wallpaper-panel="'+category.id+'"] [data-wallpaper]')).toHaveCount(category.items.length);
      expect(await page.evaluate(async ids=>Promise.all(ids.map(async id=>{const image=new Image();image.src='/assets/wallpapers/'+id+'.webp';await image.decode();return image.naturalWidth>0&&image.naturalHeight>0;})),category.items.map(item=>item.id))).toEqual(category.items.map(()=>true));
    }
    for(const lang of ['ru','en']){
      language=lang;await send('/language',{language},'PUT');await page.reload();await openAppearance();
      await expect(page.locator('[data-wallpaper-category]')).toHaveText(language==='ru'?['Природа','Космос','Абстракция','Уют']:['Nature','Space','Abstract','Cozy']);
      for(const mode of ['light','dark']){
        await page.locator('[name=mode]').selectOption(mode);
        for(const width of [1440,390,320]){
          await page.setViewportSize({width,height:width===1440?1100:844});
          await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
          await openAppearance();
          await page.locator('[name=mode]').selectOption(mode);
          for(const category of catalog){await selectCategory(category.id);await within();}
          await selectCategory('space');await choose('space-rings');
          await page.locator('[data-wallpapers]').scrollIntoViewIfNeeded();
          if(engine==='chromium'&&width!==1440)await page.screenshot({path:out+'/after-'+language+'-'+mode+'-'+width+'.png'});
        }
      }
    }
    // Leave the review images on a recognizable saved choice in each category.
    language='ru';await send('/language',{language},'PUT');await page.reload();await openAppearance();
    await page.setViewportSize({width:390,height:844});await page.locator('[name=mode]').selectOption('light');
    for(const [category,id] of [['nature','mountains'],['space','space-rings'],['abstract','abstract-glass'],['cozy','cozy-rain']]){
      await selectCategory(category);await choose(id);
      await page.locator('[data-wallpapers]').screenshot({path:out+'/after-category-'+category+'.png'});
    }
    await selectCategory('space');await choose('space-rings');await save();await waitSaved();
    await page.locator('[data-wallpapers]').scrollIntoViewIfNeeded();await page.screenshot({path:out+'/after-webapp-space.png'});
    await page.setViewportSize({width:1440,height:1100});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await openAppearance();
    await page.locator('[data-preference-key="appearance"]').screenshot({path:out+'/after-desktop-space.png'});
  }
  expect(errors).toEqual([]);
  console.log('Wallpaper '+phase+' '+engine+' checks passed.');
}finally{
  await browser?.close();server.kill('SIGTERM');await exited;
  await rm(directory,{recursive:true,force:true});
}
