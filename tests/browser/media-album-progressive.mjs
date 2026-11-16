// A known album reserves every tile before thumbnails arrive, in any order.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const bundle=await build({entryPoints:['web/modules/family-photos/index.js'],bundle:true,format:'iife',globalName:'AlbumTest',write:false,loader:{'.css':'empty'}});
const styles=(await Promise.all(['web/style.css','web/modules/chat/workspace.css','web/modules/chat/media.css','web/modules/family-photos/family-photos.css'].map(f=>readFile(f,'utf8')))).join('\n');
const portrait=await readFile('tests/fixtures/chat-portrait.png'),wide=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="300"><path fill="#467d91" d="M0 0h900v300H0z"/></svg>');
const browser=await(process.env.DRG_BROWSER_ENGINE==='webkit'?webkit:chromium).launch();
try{
 for(const width of [390,1350]){
  const page=await browser.newPage({viewport:{width,height:900},reducedMotion:'reduce'}),waiting=new Map(),errors=[];page.on('pageerror',e=>errors.push(e.message));let count=0;
  await page.route('http://album.test/**',async r=>{if(r.request().url().includes('/thumb/')){const name=new URL(r.request().url()).pathname;await new Promise(done=>waiting.set(name,done));return r.fulfill({body:name.includes('wide')?wide:portrait,contentType:name.includes('wide')?'image/svg+xml':'image/png',headers:{'Cache-Control':'no-store'}});}return r.fulfill({contentType:'text/html',body:'<div class="chat-shell"><main id="fixture"></main></div>'});});
  await page.goto('http://album.test/');await page.addStyleTag({content:styles+'\nhtml,body{margin:0;height:100%;overflow:hidden}.chat-shell{display:block!important;height:100dvh!important;width:100%!important}#fixture{height:100%;min-height:0;padding:0}'});await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(()=>{
   window.s={rows:[],deny:false};window.ctx={state:{me:{id:'viewer',role:'owner'},chatSpace:'telegram:-1001'},moduleEnabled:()=>true,toast(){},navigate(){},async api(path){if(path==='/family-photos/groups'){if(s.deny)throw Object.assign(Error('Denied'),{status:403});return {items:[{id:'-1001',title:'Synthetic',can_send:true}],status:{bot_messaging:true}};}if(path.endsWith('/read'))return {ok:true};if(path.includes('/site-sends'))return {items:[]};if(path.includes('/messages'))return {items:structuredClone(s.rows),more:false};throw Error(path);}};
   window.show=count=>{window.mounted?.destroy();s.rows=Array.from({length:40+count},(_,i)=>({id:i+1,group_id:'-1001',created:1700000000+i,sender:'Synthetic',text:i<20||i>=20+count?'Text '+i+'\nSecond line':'',album_id:i>=20&&i<20+count?'album':null,media:i>=20&&i<20+count?[{id:'album'+count+'-'+i,kind:'photo',state:'ready',url:'/original/'+i,thumbnail_url:'/thumb/'+count+'-'+(i%2?'portrait':'wide')+'-'+i}]:[]}));window.mounted=AlbumTest.mountTelegramChat(document.querySelector('#fixture'),ctx,'-1001',()=>{});};
   window.measure=()=>{const h=document.querySelector('[data-fp-history]'),album=h.querySelector('.chat-media-album'),rect=n=>{const r=n.getBoundingClientRect();return [r.left,r.top,r.width,r.height];};return {album:rect(album),tiles:[...album.querySelectorAll('.chat-media-tile')].map(rect),next:rect(album.closest('.chat-message').nextElementSibling),scrollTop:h.scrollTop};};
  });
  for(const size of (process.env.DRG_MEDIA_ALBUM_SIZES||'6,2,3,4,10').split(',').map(Number)){
   await page.evaluate(size=>show(size),size);await page.waitForFunction(size=>document.querySelectorAll('.chat-media-tile').length===size,size);
   await page.evaluate(()=>{const h=document.querySelector('[data-fp-history]'),album=h.querySelector('.chat-media-album');h.scrollTop+=album.getBoundingClientRect().top-h.getBoundingClientRect().top-80;});await page.waitForTimeout(100);
   const before=await page.evaluate(()=>measure());
   if(process.env.DRG_MEDIA_REVIEW_OUT&&size===6){await mkdir(process.env.DRG_MEDIA_REVIEW_OUT,{recursive:true});await page.screenshot({path:process.env.DRG_MEDIA_REVIEW_OUT+'/'+width+'-album-before.png'});}
   // Release in reverse order: neither the first photo nor row order is special.
   for(const tile of [...await page.locator('.chat-media-tile img').all()].reverse()){
    const path=await tile.getAttribute('src');for(let tries=0;!waiting.has(path)&&tries<100;tries++)await page.waitForTimeout(10);assert(waiting.has(path),'all reserved visible tiles should request their previews');waiting.get(path)();waiting.delete(path);await tile.evaluate(i=>new Promise(done=>{if(i.complete&&i.naturalWidth)done();else i.addEventListener('load',done,{once:true});}));await page.waitForTimeout(200);
    const after=await page.evaluate(()=>measure());
    for(const field of ['album','next'])for(let k=0;k<4;k++)assert(Math.abs(after[field][k]-before[field][k])<1,`${size}-file ${field} moves after one thumbnail: ${JSON.stringify({before:before[field],after:after[field]})}`);
    for(let i=0;i<size;i++)for(let k=0;k<4;k++)assert(Math.abs(after.tiles[i][k]-before.tiles[i][k])<1,`${size}-file tile ${i} changes as another image arrives`);
    assert(Math.abs(after.scrollTop-before.scrollTop)<1,'loading an album cannot correct the scroll position');count++;
   }
   if(process.env.DRG_MEDIA_REVIEW_OUT&&size===6)await page.screenshot({path:process.env.DRG_MEDIA_REVIEW_OUT+'/'+width+'-album-loaded.png'});
   // A later unrelated refresh must preserve the same mosaic and source nodes.
   await page.evaluate(()=>{window.kept=document.querySelector('.chat-media-tile img');s.rows.at(-1).text+=' changed';document.dispatchEvent(new Event('visibilitychange'));});await page.waitForTimeout(200);assert(await page.evaluate(()=>kept===document.querySelector('.chat-media-tile img')));assert.deepEqual((await page.evaluate(()=>measure())).album,before.album);
   if(size===6){await page.evaluate(()=>{s.rows[20].media[0].state='pending';s.rows[21].media[0].state='failed';document.dispatchEvent(new Event('visibilitychange'));});await page.waitForFunction(()=>document.querySelectorAll('.chat-media-wait').length===2);assert.deepEqual((await page.evaluate(()=>measure())).album,before.album,'pending/failed files keep their reserved cells');}
  }
  assert.deepEqual(errors,[]);await page.evaluate(()=>mounted.destroy());await page.close();console.log(JSON.stringify({engine:process.env.DRG_BROWSER_ENGINE||'chromium',width,albums:(process.env.DRG_MEDIA_ALBUM_SIZES||'6,2,3,4,10').split(',').map(Number),individualArrivals:count,geometryDrift:'<1px',refresh:'source nodes and mosaic preserved'}));
 }
}finally{await browser.close();}
