import {appSection,pageAction} from './notes-ui.js';
import {expect} from '@playwright/test';
async function api(page,path,method='GET',data){const r=await page.evaluate(async({path,method,data})=>{const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');const r=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});return {status:r.status,data:await r.json()};},{path,method,data});expect(r.status,JSON.stringify(r.data)).toBe(200);return r.data;}
export async function notionFlow(page,browser,totp){
 await page.setViewportSize({width:1600,height:1000});
 await api(page,'/appearance','PUT',{theme:'ocean',mode:'light'});await page.reload();
 const extra=await browser.newContext({baseURL:'http://127.0.0.1:8001'}),guest=await extra.newPage(),errors=[];guest.on('pageerror',e=>errors.push(e.message));
 const invite=await api(page,'/invites','POST',{login:'notionfriend'});const registered=await extra.request.post('/api/register',{data:{login:'notionfriend',password:'Notion-friend-password-123',invite:new URL(invite.url).pathname.split('/').at(-1)}});expect(registered.status()).toBe(200);await guest.goto('/');
 const enrollment=await api(guest,'/2fa/enroll','POST',{password:'Notion-friend-password-123'});await api(guest,'/2fa/confirm','POST',{code:totp(enrollment.secret)});await api(guest,'/2fa/activate','POST',{saved_recovery_codes:true});
 const family=await api(page,'/social/families','POST',{name:'Наши проекты'}),req=await api(page,`/social/families/${family.id}/invites`,'POST',{login:'notionfriend'});await api(guest,'/social/requests/'+req.id+'/accept','POST');
 await page.reload();await appSection(page, 'Заметки');await page.getByLabel('Пространство заметок',{exact:true}).selectOption(family.id);
 await page.locator('#pages-tools > summary').click();await page.getByRole('button',{name:'Новая база',exact:false}).click();await page.getByLabel('Название базы').fill('Планы и проекты');await page.getByRole('button',{name:'Создать базу',exact:true}).click();
 await expect(page.getByRole('tab',{name:'Таблица',exact:true})).toBeVisible();const databaseId=new URL(page.url()).pathname.split('/').at(-1);
 await page.getByLabel('Название заметки').fill('Планы и проекты · 2026');await expect(page.locator('#page-save-status')).toHaveText('Сохранено',{timeout:10000});expect((await api(page,'/notes/'+databaseId)).title).toBe('Планы и проекты · 2026');
 const day=new Date().toISOString().slice(0,10),end=new Date(Date.now()+3*86400000).toISOString().slice(0,10);
 for(const [title,status,tags] of [['Поездка во Владивосток','Запланировано','Семья'],['Личный сайт','В работе','Работа'],['Прочитать новую книгу','Готово','Личное']]){
  await page.getByRole('button',{name:'Создать',exact:true}).click();await page.getByLabel('Название записи').fill(title);await page.getByLabel('Статус',{exact:true}).selectOption(status);await page.getByLabel('Срок: начало').fill(day);await page.getByLabel('Срок: конец').fill(end);await page.getByLabel('Метки',{exact:true}).selectOption(tags);await page.getByRole('button',{name:'Добавить запись',exact:true}).click();await expect(page.locator('.db-table')).toContainText(title);
 }
 await page.getByRole('button',{name:'Настройки базы',exact:true}).click();await page.getByRole('button',{name:'＋ Свойство',exact:true}).click();await page.getByLabel('Имя свойства 4').fill('Бюджет');await page.getByLabel('Тип свойства 4').selectOption('number');await page.getByRole('button',{name:'Сохранить свойства базы',exact:true}).click();await expect(page.locator('.db-table th')).toContainText(['Название','Статус','Срок','Метки','Бюджет']);
 await page.getByRole('tab',{name:'Доска',exact:true}).click();await expect(page.locator('.db-board')).toContainText('Личный сайт');
 const card=page.locator('.db-card').filter({hasText:'Личный сайт'}),target=page.locator('.db-column').filter({has:page.getByRole('heading',{name:/Готово/})});
 const transfer=await page.evaluateHandle(()=>new DataTransfer());await card.dispatchEvent('dragstart',{dataTransfer:transfer});await target.dispatchEvent('dragover',{dataTransfer:transfer});await target.dispatchEvent('drop',{dataTransfer:transfer});await expect(target).toContainText('Личный сайт');await transfer.dispose();
 await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'artifacts/notes-database-board.png',fullPage:true,animations:'disabled'});
 await page.getByRole('tab',{name:'Таблица',exact:true}).click();await expect(page.locator('tr').filter({hasText:'Личный сайт'})).toContainText('Готово');
 await page.getByRole('tab',{name:'Календарь',exact:true}).click();await expect(page.locator('.db-calendar')).toContainText('Личный сайт');await page.screenshot({path:'artifacts/notes-database-calendar.png',fullPage:true});
 await page.getByRole('tab',{name:'Галерея',exact:true}).click();await expect(page.locator('.db-gallery .db-card')).toHaveCount(3);
 await page.getByRole('tab',{name:'Временная шкала',exact:true}).click();await expect(page.locator('.db-timeline-bar')).toHaveCount(3);
 await page.getByRole('button',{name:'Вид и фильтры',exact:true}).click();await page.getByLabel('Фильтр по свойству').selectOption('status');await page.getByLabel('Условие фильтра').selectOption('equals');await page.getByLabel('Значение фильтра').fill('Готово');await page.getByRole('button',{name:'Применить',exact:true}).click();await expect(page.locator('.db-timeline-bar')).toHaveCount(2);
 await page.getByRole('tab',{name:'Доска',exact:true}).click();await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'artifacts/notes-database-mobile.png',fullPage:true});await page.setViewportSize({width:1280,height:900});
 await page.locator('.db-board .db-open').filter({hasText:'Личный сайт'}).click();await expect(page.getByRole('textbox',{name:'Содержимое страницы'})).toBeVisible();await page.getByRole('textbox',{name:'Содержимое страницы'}).fill('Заметки к проекту: обсудить дизайн и план запуска.');await expect(page.locator('#page-save-status')).toContainText('Сохранено');
 const noteId=new URL(page.url()).pathname.split('/').at(-1),friendId=(await api(guest,'/me')).id;
 await pageAction(page,'permissions');await page.locator(`[data-access-role="${friendId}"]`).click();await page.locator('[data-set-role=commenter]').click();await expect(page.getByRole('dialog',{name:'Доступ к странице',exact:true})).toBeVisible();await page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await guest.goto('/page/'+noteId);await expect(guest.getByLabel('Название заметки')).toBeDisabled();await expect(guest.locator('#live-presence')).toContainText('комментарии');
 await page.locator('#discussions-panel > summary').click();await guest.locator('#discussions-panel > summary').click();
 await page.getByLabel('Комментарий',{exact:true}).fill('@notionfriend Посмотри план запуска — подходит такой порядок?');await page.getByRole('button',{name:'Отправить комментарий',exact:true}).click();await expect(guest.locator('#comment-list')).toContainText('подходит такой порядок',{timeout:10000});
 await guest.getByRole('button',{name:'Ответить',exact:true}).click();await guest.getByLabel('Комментарий',{exact:true}).fill('Да! Сначала заметки и базы, затем остальные интеграции.');await guest.getByRole('button',{name:'Отправить комментарий',exact:true}).click();await expect(page.locator('.comment-reply')).toContainText('остальные интеграции',{timeout:10000});
 await guest.locator('#pages-tools > summary').click();await guest.getByRole('button',{name:'Упоминания',exact:false}).click();await expect(guest.locator('.mention-notification')).toContainText('Посмотри план');await guest.getByRole('button',{name:'Закрыть',exact:true}).click();await expect(guest.locator('#pages-tools > summary')).toBeFocused();
 await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'artifacts/notes-discussions-desktop.png',fullPage:true,animations:'disabled'});
 // Downgrading an already connected editor closes write access without a reload.
 await api(page,'/notes/'+noteId+'/permissions','PUT',{user_id:friendId,role:'editor'});await guest.reload();await expect(guest.getByLabel('Название заметки')).toBeEnabled();
 await api(page,'/notes/'+noteId+'/permissions','PUT',{user_id:friendId,role:'viewer'});await expect(guest.getByLabel('Название заметки')).toBeDisabled({timeout:15000});await expect(guest.locator('#comment-form')).toBeHidden();
 await guest.goto('/page/'+databaseId);await expect(guest.getByRole('tab',{name:'Таблица',exact:true})).toBeVisible();
 expect(errors).toEqual([]);await extra.close();
}
