import {test,expect} from './installed-test.mjs';
import {createHmac} from 'node:crypto';

function totp(secret){
  let bits='';for(const c of secret)bits+='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5,'0');
  const bytes=[];for(let i=0;i+8<=bits.length;i+=8)bytes.push(parseInt(bits.slice(i,i+8),2));
  const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));
  const hash=createHmac('sha1',Buffer.from(bytes)).update(counter).digest(),offset=hash[19]&15;
  return String((hash.readUInt32BE(offset)&0x7fffffff)%1000000).padStart(6,'0');
}

test('copies exact code and changes language or wrapping from the block menu',async ({page})=>{
  const origin='http://127.0.0.1:8001';
  const errors=[];page.on('pageerror',error=>{errors.push(error.message);console.error(error.message);});
  await page.setViewportSize({width:1280,height:900});
  const context=page.context(),password='Code-actions-password-123';
  async function api(path,method='GET',data,token=''){
    const response=await context.request.fetch(origin+'/api'+path,{method,headers:{Origin:origin,'X-CSRF-Token':token},data});
    expect(response.ok(),path+' '+await response.text()).toBeTruthy();return response.json();
  }
  let csrf=(await api('/setup','POST',{login:'codeactionsowner',password,token:'browser-test-setup-key'})).csrf;
  const enrollment=await api('/2fa/enroll','POST',{password},csrf);
  await api('/2fa/confirm','POST',{code:totp(enrollment.secret)},csrf);
  csrf=(await api('/2fa/activate','POST',{saved_recovery_codes:true},csrf)).csrf;
  const menu=page.locator('.drg-block-menu');
  const source='def summary():\n\t  return "'+('draft '.repeat(32)).trim()+'"  \n\n';
  const marked=[{type:'text',text:source.slice(0,16),styles:{bold:true}},{type:'text',text:source.slice(16),styles:{}}];
  const sample=await api('/notes','POST',{title:'Действия блока кода',body:'',blocks:[
    {id:'sample_code',type:'codeBlock',props:{language:'python',wrapLines:false},content:marked,children:[]},
    {id:'container',type:'numberedListItem',content:'Вложенный блок',children:[
      {id:'other_code',type:'codeBlock',props:{language:'unrecognized-language'},content:'other block',children:[]},
    ]},
  ]},csrf);
  const captureClipboard=()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.copiedCode=value;}}});
  await page.addInitScript(captureClipboard);
  await page.goto('/page/'+sample.id);
  const sampleBlock=page.locator('.bn-block-outer[data-id="sample_code"] [data-content-type=codeBlock]');
  const quick=sampleBlock.getByRole('button',{name:'Копировать код',exact:true});
  const overflow=()=>sampleBlock.locator('pre').evaluate(pre=>pre.scrollWidth-pre.clientWidth);
  await expect(sampleBlock.locator('pre code')).toHaveCSS('white-space','pre');
  expect(await overflow()).toBeGreaterThan(0);
  await page.mouse.move(5,5);
  await expect(sampleBlock.locator('.note-code-actions')).toHaveCSS('opacity','0');
  await sampleBlock.locator('pre').hover();
  await expect(sampleBlock.locator('.note-code-actions')).toHaveCSS('opacity','1');
  await quick.click();
  await expect(sampleBlock.getByRole('status')).toHaveText('Код скопирован');
  expect(await page.evaluate(()=>window.copiedCode)).toBe(source);
  await quick.focus();await page.keyboard.press('Enter');
  expect(await page.evaluate(()=>window.copiedCode)).toBe(source);

  async function sampleMenu(block=sampleBlock){
    await block.locator('pre').hover();
    await page.getByRole('button',{name:'Открыть меню блока',exact:true}).click();
    await expect(page.getByLabel('Поиск действий блока')).toBeFocused();
  }
  await sampleMenu();
  await menu.getByRole('menuitem',{name:'Копировать код',exact:true}).click();
  expect(await page.evaluate(()=>window.copiedCode)).toBe(source);
  await sampleMenu();
  await menu.getByRole('menuitem',{name:/^Язык/}).click();
  const languageSearch=menu.getByRole('searchbox',{name:'Поиск языка…'});
  await expect(languageSearch).toBeFocused();
  await languageSearch.fill('yml');await languageSearch.press('Enter');
  await expect(sampleBlock.getByLabel('Язык кода')).toHaveValue('yaml');
  await expect(page.locator('.bn-block-outer[data-id="other_code"]').getByLabel('Язык кода')).toHaveValue('unrecognized-language');
  await sampleMenu();
  await menu.getByRole('menuitem',{name:'Перенос строк',exact:true}).click();
  await expect(sampleBlock.locator('pre code')).toHaveCSS('white-space','pre-wrap');
  expect(await overflow()).toBe(0);
  await expect.poll(async()=>(await api('/notes/'+sample.id)).blocks.find(block=>block.id==='sample_code').props.wrapLines).toBe(true);
  const nestedBlock=page.locator('.bn-block-outer[data-id="other_code"] [data-content-type=codeBlock]');
  await sampleMenu(nestedBlock);
  await menu.getByRole('menuitem',{name:'Копировать код',exact:true}).click();
  expect(await page.evaluate(()=>window.copiedCode)).toBe('other block');
  await api('/notes/'+sample.id+'/lock','POST',{mode:'lock'},csrf);
  const touch=await context.browser().newContext({locale:'en-US',baseURL:origin,hasTouch:true,isMobile:true,
    viewport:{width:390,height:844},storageState:await context.storageState()});
  try{
    const viewer=await touch.newPage();
    await viewer.addInitScript(captureClipboard);
    await viewer.addInitScript(()=>{
      localStorage.setItem('drg-language','en');
      Object.defineProperty(navigator,'standalone',{get:()=>true});
    });
    await viewer.goto('/page/'+sample.id);
    const block=viewer.locator('.bn-block-outer[data-id="sample_code"] [data-content-type=codeBlock]');
    await expect(block.getByLabel('Code language')).toBeDisabled();
    await expect(block.locator('pre')).toHaveCSS('white-space','pre-wrap');
    await expect(block.locator('.note-code-actions')).toHaveCSS('opacity','1');
    await block.getByRole('button',{name:'Copy code',exact:true}).tap();
    await expect(block.getByRole('status')).toHaveText('Code copied');
    expect(await viewer.evaluate(()=>window.copiedCode)).toBe(source);

    // A linked Notion note enters the read-only editor through its own branch.
    await viewer.route('**/api/notes/'+sample.id,async route=>{
      const response=await route.fetch();
      const note=await response.json();
      await route.fulfill({response,json:{...note,locked:false,role:'viewer',notion_sync:{status:'active',read_only:true}}});
    });
    await viewer.reload();
    await block.getByRole('button',{name:'Copy code',exact:true}).tap();
    await expect(block.getByRole('status')).toHaveText('Code copied');
    expect(await viewer.evaluate(()=>window.copiedCode)).toBe(source);
  }finally{await touch.close();}
  expect(errors).toEqual([]);
});
