import {expect} from '@playwright/test';
import {chromium,webkit} from 'playwright';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,mkdir,readFile} from 'node:fs/promises';
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
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:390,height:844});
  await page.context().addInitScript(()=>Object.defineProperty(navigator,'standalone',{value:true,configurable:true}));
  await page.emulateMedia({colorScheme:'dark'});
  const ctx=page.context(),password='Chat-media-password-123';
  async function post(path,data,csrf=''){
    const response=await ctx.request.post(origin+'/api'+path,{headers:{Origin:origin,'X-CSRF-Token':csrf},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  let csrf=(await post('/setup',{login:'mediaowner',password,token:'browser-test-setup-key'})).csrf;
  const enrolled=await post('/2fa/enroll',{password},csrf);
  await post('/2fa/confirm',{code:totp(enrolled.secret)},csrf);
  csrf=(await post('/2fa/activate',{saved_recovery_codes:true},csrf)).csrf;
  const {id:sid}=await post('/social/families',{name:'Проверка медиа'},csrf);
  const bytes=await readFile('tests/fixtures/chat-video.mp4');
  async function upload(name,body,original=false){return post('/chat/'+sid+'/attachments?name='+encodeURIComponent(name)+'&as_file='+original,body,csrf);}
  const video=await upload('Отправленное видео.mp4',bytes),legacy=await upload('Старое видео.MOV',bytes,true);
  const portrait=await upload('portrait.png',await readFile('tests/fixtures/chat-portrait.png'));
  const original=await upload('Оригинал.png',await readFile('tests/fixtures/blue.png'),true);
  const fake=await upload('fake.mp4',Buffer.from('<html>Not a video</html>'));
  const ids=[];
  for(const file of [portrait,video,legacy,original,fake])ids.push((await post('/chat/'+sid+'/messages',{body:'',attachments:[file.id],client_id:'media-'+file.id},csrf)).id);
  await page.goto('/');await expect(page.locator('[data-room="'+sid+'"]')).toBeVisible();await page.locator('[data-room="'+sid+'"]').click();
  for(const file of [portrait,video,legacy,original])await expect(page.locator('[data-chat-media-id="'+file.id+'"]')).toBeVisible();
  await expect(page.locator('[data-message-id="'+ids[4]+'"] .chat-attachment')).toBeVisible();
  await expect(page.locator('.chat-media-album')).toHaveCount(4);
  for(const width of [390,1280]){
    await page.setViewportSize({width,height:844});await page.waitForLoadState('networkidle');await page.reload();if(width>760)await page.locator('[data-view=chat]').click();await page.locator('[data-room="'+sid+'"]').click();await expect(page.locator('.chat-media-album')).toHaveCount(4);
    for(const file of [portrait,video,legacy,original]){
      const tile=page.locator('[data-chat-media-id="'+file.id+'"]');await tile.scrollIntoViewIfNeeded();
      await expect.poll(()=>tile.locator('img,video').evaluate(el=>(el.naturalWidth||el.videoWidth)>0)).toBe(true);
      const ratio=await tile.locator('img,video').evaluate(el=>{const r=el.getBoundingClientRect();return {actual:r.width/r.height,source:(el.naturalWidth||el.videoWidth)/(el.naturalHeight||el.videoHeight),fit:getComputedStyle(el).objectFit};});
      expect(Math.abs(ratio.actual-ratio.source)).toBeLessThan(.02);expect(ratio.fit).toBe('contain');
      const box=await tile.boundingBox();expect(box.height).toBeLessThanOrEqual(421);expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
    }
  }
  await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('[data-room="'+sid+'"]').click();await expect(page.locator('.chat-media-album')).toHaveCount(4);
  for(const file of [video,legacy]){
    const tile=page.locator('[data-chat-media-id="'+file.id+'"]'),preview=tile.locator('video');await tile.evaluate(el=>{el.closest('.chat-messages').dispatchEvent(new WheelEvent('wheel'));el.scrollIntoView({block:'center'});});
    await expect.poll(()=>preview.evaluate(v=>v.poster.startsWith('data:image/jpeg'))).toBe(true);
    await expect.poll(()=>preview.evaluate(v=>!v.paused&&v.muted)).toBe(true);
    const oldPreview=await preview.elementHandle();
    await post('/chat/'+sid+'/messages',{body:'Новое сообщение не сбрасывает миниатюру '+file.id,client_id:'poster-refresh-'+file.id},csrf);
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await expect(page.locator('#chat-messages')).toContainText('Новое сообщение не сбрасывает миниатюру '+file.id);
    expect(await oldPreview.evaluate(v=>v.isConnected)).toBe(true);await tile.evaluate(el=>{el.closest('.chat-messages').dispatchEvent(new WheelEvent('wheel'));el.scrollIntoView({block:'center'});});await expect.poll(()=>preview.evaluate(v=>!v.paused&&v.muted)).toBe(true);
    await tile.scrollIntoViewIfNeeded();
    await page.screenshot({path:'artifacts/chat-video-tile-'+(file===legacy?'legacy':'native')+'.png',animations:'disabled'});
    await tile.click();const player=page.locator('.fp-viewer video');await expect(player).toBeVisible();const closeBox=await page.locator('.fp-viewer-dialog .modal-close').boundingBox();expect(closeBox.width).toBe(closeBox.height);expect(closeBox.width).toBe(36);
    expect(await player.getAttribute('poster')).toMatch(/^data:image\/jpeg/);
    await player.evaluate(v=>v.play());await expect.poll(()=>player.evaluate(v=>v.currentTime)).toBeGreaterThan(.2);
    await player.evaluate(v=>v.pause());const time=await player.evaluate(v=>v.currentTime);
    await page.screenshot({path:'artifacts/chat-video-'+(file===legacy?'legacy':'native')+'.png',animations:'disabled'});
    expect(await player.evaluate(v=>v.paused)).toBe(true);expect(time).toBeGreaterThan(0);
    const download=page.locator('.fp-viewer-caption a');await expect(download).toHaveAttribute('aria-label','Скачать файл');const box=await download.boundingBox();expect(Math.abs(box.x+box.width/2-(await page.viewportSize()).width/2)).toBeLessThan(2);expect(await download.getAttribute('href')).toContain(file===legacy?'?preview=true&download=1':'?download=1');
    const old=await player.elementHandle();if(file===legacy)await page.keyboard.press('Escape');else await page.locator('.modal-close').click();expect(await old.evaluate(v=>v.paused&&!v.getAttribute('src'))).toBe(true);
  }
  // Real composer upload: a video selected as an original still becomes visual.
  await page.locator('[data-attach-chat]').click();await page.locator('[data-upload-mode="file"]').click();
  await page.locator('[data-chat-files]').setInputFiles({name:'Новое видео.mov',mimeType:'video/quicktime',buffer:bytes});
  await expect(page.locator('[data-chat-drafts] b')).toHaveText('Новое видео.mov');
  await page.locator('#send-message button[type=submit]').click();
  await expect(page.locator('.chat-media-album')).toHaveCount(5);
  await expect(page.locator('[data-chat-drafts] b')).toHaveCount(0);
  const uploaded=page.locator('.chat-media-tile:has(video)').last();await uploaded.scrollIntoViewIfNeeded();
  await expect.poll(()=>uploaded.evaluate(el=>getComputedStyle(el).backgroundImage.startsWith('url("data:image/jpeg'))).toBe(true);
  await page.locator('[data-attach-chat]').click();await page.locator('[data-upload-mode=preview]').click();
  await page.locator('[data-chat-files]').setInputFiles({name:'С превью.mov',mimeType:'video/quicktime',buffer:bytes});
  await expect(page.locator('[data-chat-drafts] video')).toBeVisible();
  await expect.poll(()=>page.locator('[data-chat-drafts] video').evaluate(v=>v.poster.startsWith('data:image/jpeg'))).toBe(true);
  await page.locator('[data-remove-draft]').click();await expect(page.locator('[data-chat-drafts] video')).toHaveCount(0);
  // An album stays still even when its video occupies most of the viewport.
  const albumVideo=await upload('Видео в альбоме.mp4',bytes),albumPhoto=await upload('Фото в альбоме.png',await readFile('tests/fixtures/blue.png'));
  const albumMessage=await post('/chat/'+sid+'/messages',{body:'',attachments:[albumVideo.id,albumPhoto.id],client_id:'mixed-album-'+albumVideo.id},csrf);
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));const album=page.locator('[data-message-id="'+albumMessage.id+'"]');await expect(album).toBeVisible();
  await album.evaluate(el=>{el.closest('.chat-messages').dispatchEvent(new WheelEvent('wheel'));el.scrollIntoView({block:'center'});});
  const albumPreview=album.locator('video');await expect.poll(()=>albumPreview.evaluate(v=>v.readyState>=1)).toBe(true);await page.waitForTimeout(500);expect(await albumPreview.evaluate(v=>v.paused)).toBe(true);await expect(album.locator('.is-preview-playing')).toHaveCount(0);
  const frame=await album.locator('.chat-media-frame').boundingBox(),stamp=await album.locator('.chat-media-frame>.chat-message-footer').boundingBox();expect(stamp.x+stamp.width).toBeLessThanOrEqual(frame.x+frame.width);expect(stamp.y+stamp.height).toBeLessThanOrEqual(frame.y+frame.height);expect(stamp.y).toBeGreaterThan(frame.y+frame.height-40);await expect(album.locator('.chat-message-actions')).toHaveCount(0);
  expect(errors).toEqual([]);
}
// This scenario owns a separate server and database, so the ordinary workspace
// suite can retain its own first-account setup and unrelated fixtures.
const origin='http://127.0.0.1:8025',directory=await mkdtemp(tmpdir()+'/drg-chat-media-');
const server=spawn(process.env.DRG_PYTHON||'.venv/bin/python',['-m','gunicorn','--config','gunicorn.conf.py','--bind','127.0.0.1:8025','tests.browser_app:app'],{env:{...process.env,DATA_DIR:directory,DATABASE_URL:'',DATA_ENCRYPTION_ENABLED:'true',DATA_ENCRYPTION_KEY:'dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU=',APP_SECRET:'isolated-mobile-navigation-secret-'.repeat(3),SETUP_TOKEN:'browser-test-setup-key',PUBLIC_URL:origin,CALL_STUN_URLS:'',CALL_TURN_URLS:''},stdio:['ignore','ignore','pipe']});
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
  const context=await browser.newContext({locale:'ru-RU',baseURL:origin,hasTouch:true,isMobile:true}),page=await context.newPage();
  await mkdir('artifacts',{recursive:true});await scenario(page);
  console.log('Chat media passed: native/legacy previews, original upload, uncropped mobile/desktop singles, video posters, playback and cleanup.');
}finally{
  await browser?.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.exitCode!==null?resolve():server.once('exit',resolve));
  await rm(directory,{recursive:true,force:true});
}
