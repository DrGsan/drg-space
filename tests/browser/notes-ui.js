import {expect} from '@playwright/test';

const adminPasswords=new WeakMap();
export function setAdminPassword(page,password){adminPasswords.set(page.context(),password);}

export async function appSection(page, name) {
  await page.locator('.workspace').waitFor({state:'visible'});
  // A restored focused section mounts asynchronously after the shell appears.
  await page.locator('.sidebar nav,#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu').first().waitFor({state:'attached'});
  const back = page.locator('#notes-main-menu,#tasks-main-menu,#chat-main-menu,#files-main-menu');
  if (await back.count()) {
    if (!(await back.isVisible())) {
      await page.locator('#toggle-pages-sidebar,[data-toggle-tasks-sidebar],[data-chat-back]').click();
    }
    await back.click();
    await page.locator('.sidebar').waitFor({state:'visible'});
  }
  await page.locator('.sidebar nav').getByRole('button', {name, exact:true}).click();
  // Existing feature flows operate on expanded forms. The standalone
  // preferences scenario verifies the compact, initially collapsed menu.
  if(name==='Настройки'||name==='Управление'){
    if(name==='Управление'){
      await page.locator('[data-admin-unlock],.preference-row').first().waitFor();
      if(await page.locator('[data-admin-unlock]').count()){
        await page.locator('[data-admin-unlock] [name=password]').fill(adminPasswords.get(page.context())||'Test-long-password-123');
        await page.locator('[data-admin-unlock] [type=submit]').click();
        await page.locator('[data-admin-unlock]').waitFor({state:'detached'});
      }
    }
    const groups=page.locator('.preference-row');await groups.first().waitFor();
    for(const row of await groups.all())if(await row.getAttribute('open')===null)await row.locator(':scope > summary').click();
    if(name==='Управление'){
      await page.locator('[data-admin-user]').first().waitFor();
    }
  }

}
export async function pageAction(page, action) {
  await page.locator('#page-actions').click();
  await page.locator('[data-page-action="'+action+'"]').click();
}
export async function newNote(page) {
  await page.locator('.workspace').waitFor({state:'visible'});
  // A resize queues matchMedia adaptation; let it settle before deciding
  // whether the drawer needs opening, so its queued change cannot invert it.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const add = page.locator('#add-root-page');
  if (await add.count()) {
    if (!(await add.isVisible())) await page.locator('#toggle-pages-sidebar').click();
    await expect(add).toBeVisible();
    await add.click();
  } else {
    const quick=page.getByRole('button', {name:'Новая заметка'});
    if(await quick.isVisible())await quick.click();
    else{await appSection(page,'Заметки');await newNote(page);}
  }
}
export async function editorMode(page, name) {
  if (await page.locator('#page-actions').count()) await page.locator('#page-actions').click();
  const mode = page.getByRole('button', {name, exact:true});
  // Page menu actions use menuitem roles; publication controls remain buttons.
  if (await mode.count()) await mode.click();
  else await page.getByRole('menuitem', {name, exact:true}).click();
}

export async function openAdminAccount(page,id){
  const search=page.locator('[data-account-search]');
  if(await search.count()){
    // The caller may target an account outside the first table page.
    const row=page.locator(`[data-admin-user="${id}"]`);
    if(!await row.count()){
      const users=await page.evaluate(async()=>{const r=await fetch('/api/admin/users');return (await r.json()).users;});
      const user=users.find(u=>u.id===id);await search.fill(user.login);
    }
  }
  await page.locator(`[data-open-admin-user="${id}"]`).click();
  return page.locator('.admin-account-details');
}
