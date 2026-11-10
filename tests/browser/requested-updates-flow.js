import {expect} from '@playwright/test';
const p=(id,text)=>({id,type:'paragraph',content:[{type:'text',text,styles:{}}],children:[]});
async function api(page,path,method='GET',data){const result=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(s=>s.startsWith('drg_csrf='))?.slice(9)||'');const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:response.status,data:await response.json()};},{path,method,data});expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;}
async function drop(page,selector,mode='file',ratio=.5){await page.locator(selector).evaluate((el,{mode,ratio})=>{const dt=new DataTransfer();if(mode==='file')dt.items.add(new File(['uploaded by drag'],'drop.txt',{type:'text/plain'}));else dt.setData('application/drg-page',mode);const r=el.getBoundingClientRect();for(const type of ['dragenter','dragover','drop'])el.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:dt,clientX:r.left+r.width/2,clientY:r.top+r.height*ratio}));},{mode,ratio});}

export async function requestedUpdatesFlow(page){
  const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.setViewportSize({width:1280,height:900});
  const first=await api(page,'/notes','POST',{title:'Первая',body:'',blocks:[p('parent','Верхний блок'),p('child','Вложенный блок'),p('last','Последний блок')]});
  const second=await api(page,'/notes','POST',{title:'Вторая',body:''});const third=await api(page,'/notes','POST',{title:'Третья',body:''});await page.goto('/page/'+first.id);
  const child=page.locator('[data-id=child] [contenteditable=true], [data-id=child] .bn-inline-content').first();await child.click();await page.keyboard.press('Tab');
  await expect.poll(async()=>{const n=await api(page,'/notes/'+first.id);return n.blocks[0].children?.[0]?.id;}).toBe('child');
  await page.keyboard.press('Shift+Tab');await expect.poll(async()=>{const n=await api(page,'/notes/'+first.id);return n.blocks[1]?.id;}).toBe('child');
  await drop(page,`[data-tree-row="${first.id}"]`,third.id,.05);
  await expect.poll(()=>page.locator('[data-tree-row]').evaluateAll((rows,ids)=>rows.map(r=>r.dataset.treeRow).filter(id=>ids.includes(id)),[first.id,second.id,third.id])).toEqual([third.id,first.id,second.id]);
  await drop(page,`[data-tree-row="${first.id}"]`,third.id,.95);
  await expect.poll(()=>page.locator('[data-tree-row]').evaluateAll((rows,ids)=>rows.map(r=>r.dataset.treeRow).filter(id=>ids.includes(id)),[first.id,second.id,third.id])).toEqual([first.id,third.id,second.id]);
  // Notion's plain text also resembles Markdown; rich HTML must take priority.
  await page.locator('[data-id=last] .bn-inline-content').click();await page.locator('[data-id=last] .bn-inline-content').first().evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);range.collapse(false);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);});await page.keyboard.press('Enter');
  await page.locator('#block-editor [contenteditable=true]').evaluate(el=>{const data=new DataTransfer();data.setData('text/plain','- SSH Phrase\n  ssh-add ~/.ssh/id_rsa');data.setData('text/html','<!-- notionvc: test --><ul class="toggle"><li><details><summary>SSH <strong>Phrase</strong></summary><pre><code class="language-bash">ssh-add ~/.ssh/id_rsa</code></pre></details></li></ul><hr><p>После разделителя</p>');el.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));});
  await expect(page.locator('[data-content-type=toggleListItem]')).toContainText('SSH Phrase');
  await expect.poll(async()=>{const n=await api(page,'/notes/'+first.id);const toggle=n.blocks.find(b=>b.type==='toggleListItem');return toggle?.children?.[0]?.type;}).toBe('codeBlock');
  // A parent's drag handle must move its complete subtree.
  await page.locator('[data-id=child] .bn-inline-content').first().click();await page.keyboard.press('Tab');
  await page.locator('[data-id=parent] [data-content-type=paragraph]').first().hover();
  const handle=page.locator('.bn-side-menu button[draggable=true]');
  await handle.dragTo(page.locator('[data-content-type=toggleListItem]'),{targetPosition:{x:150,y:20}});
  await expect.poll(async()=>{const n=await api(page,'/notes/'+first.id);return n.blocks.findIndex(b=>b.id==='parent')>0&&n.blocks.find(b=>b.id==='parent')?.children?.[0]?.id;}).toBe('child');
  await drop(page,'.bn-block-outer[data-id=parent]');await expect.poll(async()=>{const n=await api(page,'/notes/'+first.id);return n.blocks.some(b=>b.type==='file'&&b.props.url.includes('/files/'));}).toBe(true);
  await page.screenshot({path:'artifacts/requested-notes-desktop.png',fullPage:true});
  const room=await api(page,'/social/families','POST',{name:'Компактный диалог'});await api(page,'/modules','PUT',{enabled:{chat:true}});await page.goto('/#chat/'+room.id);
  await expect(page.locator('#message-body')).toBeVisible();await page.locator('#message-body').fill('Короткое сообщение');await page.getByRole('button',{name:'Отправить',exact:true}).click();await expect(page.locator('.chat-message-text>.chat-message-footer')).toHaveCount(1);
  await page.locator('#chat-main').evaluate(el=>{const dt=new DataTransfer();dt.items.add(new File(['file'],'sample.txt',{type:'text/plain'}));el.dispatchEvent(new DragEvent('dragenter',{bubbles:true,cancelable:true,dataTransfer:dt}));});await expect(page.locator('.chat-drop-overlay')).toBeVisible();await page.screenshot({path:'artifacts/requested-chat-drop.png'});
  await drop(page,'[data-drop-mode=file]');await expect(page.locator('.chat-drafts>span')).toHaveCount(1);await expect(page.locator('.chat-drop-overlay')).toBeHidden();await page.getByRole('button',{name:'Отправить',exact:true}).click();await expect(page.locator('.chat-attachment a')).toContainText('drop.txt');
  await page.locator('#chat-main').evaluate(async el=>{
    const canvas=document.createElement('canvas');canvas.width=2400;canvas.height=1200;const ctx=canvas.getContext('2d');ctx.fillStyle='#399ada';ctx.fillRect(0,0,canvas.width,canvas.height);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));const dt=new DataTransfer();dt.items.add(new File([blob],'quick.png',{type:'image/png'}));
    el.dispatchEvent(new DragEvent('dragenter',{bubbles:true,cancelable:true,dataTransfer:dt}));el.querySelector('[data-drop-mode=preview]').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));
  });
  await expect(page.locator('.chat-drafts img')).toHaveCount(1);await expect.poll(()=>page.locator('.chat-drafts img').evaluate(img=>img.naturalWidth)).toBe(1920);
  await page.getByRole('button',{name:'Отправить',exact:true}).click();await expect(page.locator('.chat-media-album img')).toHaveCount(1);await expect.poll(()=>page.locator('.chat-media-album img').evaluate(img=>img.naturalWidth)).toBe(1920);
  await page.screenshot({path:'artifacts/requested-chat-desktop.png'});await page.setViewportSize({width:390,height:844});await page.screenshot({path:'artifacts/requested-chat-mobile.png'});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);await page.setViewportSize({width:1280,height:900});
}
