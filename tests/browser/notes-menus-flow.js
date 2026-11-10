import {expect} from '@playwright/test';
import {appSection,pageAction} from './notes-ui.js';

async function api(page,path,method='GET',data){
  const result=await page.evaluate(async({path,method,data})=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(value=>value.startsWith('drg_csrf='))?.slice(9)||'');
    const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    return {status:response.status,data:await response.json()};
  },{path,method,data});
  expect(result.status,JSON.stringify(result.data)).toBe(200);return result.data;
}
const paragraph=(id,text)=>({id,type:'paragraph',content:[{type:'text',text,styles:{}}],children:[]});
async function blockMenu(page,id){
  await page.locator('[data-id="'+id+'"] [data-content-type]').first().hover();
  await page.getByRole('button',{name:'Открыть меню блока',exact:true}).click();
  await expect(page.getByLabel('Поиск действий блока')).toBeFocused();
}

export async function notesMenusFlow(page){
  const target=await api(page,'/notes','POST',{title:'Назначение меню',body:'Существующий текст',blocks:[paragraph('menu_target','Существующий текст')]});
  const source=await api(page,'/notes','POST',{title:'Проверка меню',body:'Первый блок\n\nПоследний блок',blocks:[
    paragraph('menu_first','Первый блок'),
    {id:'menu_nested',type:'toggleListItem',content:[{type:'text',text:'Подробности',styles:{}}],children:[{id:'menu_code',type:'codeBlock',props:{language:'bash'},content:'printf "Привет"',children:[]}]},
    paragraph('menu_last','Последний блок'),
  ]});
  await page.goto('/page/'+source.id);
  await expect(page.locator('.sidebar')).toHaveCount(0);
  await expect(page.locator('#notes-main-menu')).toBeVisible();
  await expect(page.locator('#share-page')).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
  await page.locator('#page-actions').click();
  const menu=page.locator('#note-page-menu'),search=page.getByLabel('Поиск действий страницы');
  await expect(search).toBeFocused();
  await search.fill('коп');await expect(menu.locator('[data-page-action="copy-link"]')).toBeVisible();
  await expect(menu.locator('[data-page-action="width"]')).toBeHidden();
  await search.fill('');await menu.getByRole('button',{name:'С засечками',exact:true}).click();
  await menu.getByRole('switch',{name:'Мелкий текст',exact:true}).click();
  await expect(page.locator('#notion-page')).toHaveAttribute('data-page-font','serif');
  await expect(page.locator('#notion-page')).toHaveClass(/page-small-text/);
  const menuBox=await menu.boundingBox();expect(menuBox.x).toBeGreaterThanOrEqual(0);expect(menuBox.y+menuBox.height).toBeLessThanOrEqual(900);
  await page.screenshot({path:'artifacts/notes-page-menu-desktop.png',fullPage:true});
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(page.locator('#page-actions')).toBeFocused();
  await page.reload();await expect(page.locator('#notion-page')).toHaveAttribute('data-page-font','serif');
  await expect(page.locator('#notion-page')).toHaveClass(/page-small-text/);
  await page.locator('#favorite-page').click();await expect(page.locator('#favorite-page')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.favorite-page')).toContainText('Проверка меню');
  await pageAction(page,'width');await expect(page.locator('#notion-page')).toHaveClass(/full-width/);
  await blockMenu(page,'menu_first');
  const controls=await page.locator('.bn-side-menu').boundingBox(),sidebar=await page.locator('#pages-sidebar').boundingBox();
  expect(controls.x).toBeGreaterThanOrEqual(sidebar.x+sidebar.width);
  await page.getByLabel('Поиск действий блока').fill('несуществующее действие');
  await expect(page.getByText('Действия не найдены',{exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  // Reopening the reused side menu must capture the new block and clear search.
  await blockMenu(page,'menu_last');await expect(page.getByLabel('Поиск действий блока')).toHaveValue('');
  await page.getByRole('menuitem',{name:'Создать копию',exact:true}).click();
  await expect(page.locator('#block-editor')).toContainText('Последний блок');
  await expect.poll(async()=>{
    const saved=await api(page,'/notes/'+source.id);
    return saved.blocks.filter(block=>block.content?.some(part=>part.text==='Последний блок')).length;
  }).toBe(2);
  const copied=(await api(page,'/notes/'+source.id)).blocks.filter(block=>block.content?.some(part=>part.text==='Последний блок'));
  expect(new Set(copied.map(block=>block.id)).size).toBe(2);
  await blockMenu(page,'menu_first');
  await page.getByRole('menuitem',{name:'Комментарий',exact:true}).click();
  await expect(page.locator('#comment-context')).toContainText('menu_first');
  await page.getByLabel('Комментарий',{exact:true}).fill('Комментарий к правильному блоку');
  await page.getByRole('button',{name:'Отправить комментарий',exact:true}).click();
  await expect(page.locator('#comment-list')).toContainText('Комментарий к правильному блоку');
  await page.locator('#discussions-panel > summary').click();
  await blockMenu(page,'menu_last');
  await page.getByRole('menuitem',{name:'Переместить',exact:true}).hover();
  await page.getByRole('menuitem',{name:'В другую страницу…',exact:true}).click();
  const picker=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Переместить блоки',exact:true})});
  await page.getByLabel('Поиск страницы для блоков').fill('Назначение меню');
  let releaseMove,moveStarted,moveForwarded;
  const heldMove=new Promise(resolve=>releaseMove=resolve),startedMove=new Promise(resolve=>moveStarted=resolve);
  const movePath='**/api/notes/'+source.id+'/blocks/move';
  await page.route(movePath,async route=>{moveStarted();moveForwarded=heldMove.then(()=>route.continue());await moveForwarded;},{times:1});
  try{
    await picker.locator('[data-move-blocks-target="'+target.id+'"]').click();
    await expect.poll(()=>Boolean(moveForwarded),{message:'The transfer must reach POST rather than stop at an inline error'}).toBe(true);await startedMove;
    await expect(page.getByLabel('Название заметки')).toBeDisabled();
    await page.keyboard.press('Escape');await expect(picker).toHaveCount(0);
    // Closing the picker must not allow new edits or navigation during POST.
    await page.locator('#notes-main-menu').click();
    await expect(page.getByLabel('Название заметки')).toHaveValue('Проверка меню');
    await expect(page.getByLabel('Название заметки')).toBeDisabled();
    await expect(page.locator('#block-editor [contenteditable="true"]')).toHaveCount(0);
  }finally{releaseMove();await moveForwarded;await page.unroute(movePath);}
  await expect(page.getByLabel('Название заметки')).toHaveValue('Назначение меню');
  const movedSource=await api(page,'/notes/'+source.id),movedTarget=await api(page,'/notes/'+target.id);
  expect(movedSource.blocks.some(block=>block.id==='menu_last')).toBe(false);
  expect(movedTarget.blocks.map(block=>block.id)).toEqual(['menu_target','menu_last']);
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('#notes-main-menu')).toBeVisible();
  await page.locator('#page-actions').click();
  const phoneBox=await menu.boundingBox();expect(phoneBox.x).toBeGreaterThanOrEqual(0);expect(phoneBox.x+phoneBox.width).toBeLessThanOrEqual(390);expect(phoneBox.y+phoneBox.height).toBeLessThanOrEqual(844);
  await expect(menu.locator('.note-menu-scroll')).toHaveCSS('overflow-y','auto');
  await page.screenshot({path:'artifacts/notes-page-menu-mobile.png',fullPage:true});
  await page.keyboard.press('Escape');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.setViewportSize({width:1280,height:900});await appSection(page,'Главная');
  // Only the synthetic pages created by this flow are removed.
  for(const note of [source,target])await api(page,'/notes/'+note.id,'DELETE');
}
