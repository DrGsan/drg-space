import {adminAccountsFlow} from './admin-accounts-flow.js';
import {chatControlsFlow} from './chat-controls-flow.js';
import {requestedUpdatesFlow} from './requested-updates-flow.js';
import {appSection,editorMode,newNote,pageAction} from './notes-ui.js';
import {notesMenusFlow} from './notes-menus-flow.js';
import {clockAppearanceFlow} from './clock-appearance-flow.js';
import {widgetEditFlow} from './widget-edit-flow.js';
import {communityFlow} from './community-flow.js';
import {filesTrashFlow} from './files-trash-flow.js';
import {dashboardUpdateFlow,widgetSizingFlow} from './dashboard-update-flow.js';
import {nextFeaturesFlow} from './next-features-flow.js';
import {expenseWidgetFlow} from './expense-widget-flow.js';
import {taskWidgetFlow} from './task-widget-flow.js';
import {pushFlow} from './push-flow.js';
import {voiceCallsFlow} from './voice-calls-flow.js';
import {chatWorkspaceFlow} from './chat-workspace-flow.js';
import {siteRefreshFlow} from './site-refresh-flow.js';
import {tasksFlow} from './tasks-flow.js';
import { test, expect } from "@playwright/test";
import { interfaceFlow } from './interface-flow.js';
import { notionFlow } from './notion-flow.js';
import { socialFlow } from "./social-flow.js";
import { adminFlow } from "./admin-flow.js";
import { accountSecurityFlow } from './account-security-flow.js';
import { clocksFlow } from './clocks-flow.js';
import { personalDataFlow } from './personal-data-flow.js';
import { shareEditFlow } from './share-edit-flow.js';
import { widgetsFlow,weatherSizingFlow } from "./widgets-flow.js";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
function totp(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret)
    bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8)
    bytes.push(parseInt(bits.slice(i, i + 8), 2));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const hash = createHmac("sha1", Buffer.from(bytes)).update(counter).digest();
  const offset = hash[19] & 15;
  return String((hash.readUInt32BE(offset) & 0x7fffffff) % 1000000).padStart(
    6,
    "0",
  );
}
test("complete account, notes, sharing, tasks, invitations and mobile flow", async ({
  page,
  browser,
}) => {
  // This covers the complete workspace; individual action timeouts stay short.
  test.setTimeout(Number(process.env.DRG_BROWSER_TIMEOUT_MS || 900000));
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route('**/api/widgets/home_rss/rss',route=>route.fulfill({json:{items:[],errors:[]}}));
  await page.goto("/");
  await page
    .getByLabel("Ключ первичной настройки")
    .fill("browser-test-setup-key");
  await page.getByLabel("Логин", { exact: true }).fill("demoowner");
  await page
    .getByLabel("Пароль", { exact: true })
    .fill("Test-long-password-123");
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await page.getByLabel("Подтвердите пароль").fill("Test-long-password-123");
  await page.getByRole("button", { name: "Настроить аутентификатор" }).click();
  const secret = await page.locator(".secret").textContent();
  await page.getByLabel("Код из приложения").fill(totp(secret));
  await page.getByRole("button", { name: "Проверить код" }).click();
  await expect(page.locator(".recovery")).toBeVisible();
  const recoveryCodes = (await page.locator(".recovery").textContent()).trim().split("\n");
  await page.getByLabel("Я сохранил резервные коды").check();
  await page.getByRole("button", { name: "Открыть пространство" }).click();
  await expect(
    page.getByRole("heading", { name: "Привет, demoowner." }),
  ).toBeVisible();
  await expect(page.locator("[data-widget]")).toHaveCount(13);
  expect(await page.locator('[data-widget]').evaluateAll(items=>items.map(item=>item.dataset.widget))).toEqual([
    'clock','files','tasks','favorites','calendar','weather','rss','chat',
    'bookmarks','notes','media','authenticator','expenses',
  ]);
  await expect(page.locator('[data-widget=clock] .clock-widget')).toHaveClass(/clock-theme-classic/);
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await expect(page.getByLabel('Новый виджет').locator('option')).toHaveCount(13);
  expect(await page.getByLabel('Новый виджет').locator('option').evaluateAll(options=>options.map(option=>option.value))).toEqual([
    'clock','files','tasks','favorites','calendar','weather','rss','chat','bookmarks','notes','media','authenticator','expenses',
  ]);
  await expect(page.getByLabel('Новый виджет')).toHaveValue('clock');
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  await appSection(page, 'Настройки');
  const releaseVersion=readFileSync(new URL('../../VERSION',import.meta.url),'utf8').trim();
  expect(await page.evaluate(async()=>(await (await fetch('/api/me')).json()).app_version)).toBe(releaseVersion);
  await expect(page.locator('[data-app-version]')).toHaveText(releaseVersion);
  await expect(page.locator('#module-switches input')).toHaveCount(6);
  expect(await page.locator('#module-switches input').evaluateAll(inputs=>inputs.map(input=>input.name))).toEqual(['tracker','files','tasks','chat','notes','expenses']);
  await expect(page.getByRole('checkbox',{name:'Модуль: Погода',exact:true})).toHaveCount(0);
  await test.step('Message Web Push settings', () => pushFlow(page));
  await appSection(page, 'Главная');
  await page.screenshot({path:'artifacts/default-dashboard-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'artifacts/default-dashboard-mobile.png',fullPage:true});
  await page.setViewportSize({width:1280,height:900});
  await test.step('Widget edit mode and removal', () => widgetEditFlow(page));
  // Continue with a saved legacy layout to cover optional-module configuration
  // and existing accounts independently of the new starting arrangement.
  const initialized=await page.evaluate(async()=>{
    const csrf=decodeURIComponent(document.cookie.split('; ').find(x=>x.startsWith('drg_csrf='))?.slice(9)||'');
    const send=(path,data)=>fetch('/api'+path,{method:'PUT',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
    const modules=await (await fetch('/api/modules')).json();
    const enabled=Object.fromEntries(modules.catalog.filter(module=>module.kind==='module').map(module=>[module.id,['notes','tasks'].includes(module.id)]));
    const current=await (await fetch('/api/dashboard/layout')).json();
    const instances=['tasks','notes','calendar'].map(id=>({id,module_id:id,size:'2x1',config:{}}));
    return (await send('/modules',{enabled})).ok&&(await send('/dashboard/layout',{version:current.version,instances})).ok;
  });
  expect(initialized).toBe(true);
  await page.reload();
  await expect(page.locator('[data-widget]')).toHaveCount(3);
  await test.step('Compact navigation and theme', () => interfaceFlow(page));
  await test.step('Notes page and block menus', () => notesMenusFlow(page));
  await test.step('Dashboard module settings', () => dashboardUpdateFlow(page));
  await test.step('Reminder widgets in every size', () => taskWidgetFlow(page));
  await test.step('Reminder lists, subtasks and sharing', () => tasksFlow(page));
  await newNote(page);
  await page.getByLabel("Название заметки").fill("План на неделю");
  await editorMode(page, 'Markdown');
  await page
    .getByLabel("Текст заметки в Markdown")
    .fill(
      "# Важное\n\n**Прочитать книгу**\n\n<script>window.hacked=true</script>\n\n[Небезопасная ссылка](javascript:alert(1))",
    );
  await editorMode(page, 'Блоки');
  await expect(page.locator(".bn-editor strong")).toHaveText("Прочитать книгу");
  expect(await page.evaluate(() => window.hacked)).toBeUndefined();
  expect(await page.locator('.bn-editor a[href^="javascript:"]').count()).toBe(
    0,
  );
  await expect(page.locator("#page-save-status")).toContainText("Сохранено");
  await expect(page.getByRole("button", { name: "Поделиться" })).toBeVisible();
  await page.getByRole("button", { name: "Поделиться" }).click();
  await page.getByLabel("Способ доступа").selectOption("password");
  await page
    .getByLabel("Пароль страницы", { exact: true })
    .fill("page-password");
  await page.getByLabel("Срок", { exact: true }).fill("13");
  await page.getByLabel("Единица").selectOption("minutes");
  await page.getByRole("button", { name: "Создать ссылку" }).click();
  const url = await page.locator("#share-url").inputValue();
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  const anonymous = await browser.newContext();
  const friend = await anonymous.newPage();
  await friend.goto(url);
  await expect(
    friend.getByRole("heading", { name: "Нужен пароль" }),
  ).toBeVisible();
  await friend
    .getByLabel("Пароль страницы", { exact: true })
    .fill("page-password");
  await friend.getByRole("button", { name: /^Открыть →$/ }).click();
  await expect(
    friend.getByRole("heading", { name: "План на неделю" }),
  ).toBeVisible();
  await expect(friend.locator(".markdown strong")).toHaveText(
    "Прочитать книгу",
  );
  await appSection(page, 'Главная');
  const reminderInput=page.getByLabel("Название напоминания");
  await reminderInput.click();
  await reminderInput.fill("Проверить личный сайт");
  const reminderCreated=page.waitForResponse(response=>response.url().endsWith('/api/tasks')&&response.request().method()==='POST'&&response.request().postDataJSON()?.title==='Проверить личный сайт');
  await reminderInput.press('Enter');
  expect((await reminderCreated).ok()).toBe(true);
  await page
    .getByRole("checkbox", { name: "Выполнено: Проверить личный сайт" })
    .check();
  await expect(page.locator(".task-row.done")).toBeVisible();
  await page.reload();
  await expect(page.locator(".task-row.done")).toBeVisible();
  await appSection(page, 'Управление');
  await page.getByRole("button", { name: "Пригласить человека", exact:true }).click();
  await page.getByRole("button", {name:"Создать приглашение",exact:true}).click();
  await expect(page.getByLabel("Ссылка приглашения")).toHaveValue(/\/invite\//);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await appSection(page, 'Заметки');
  await page.locator(".tree-open").filter({hasText:"План на неделю"}).click();
  await page.getByRole("button", { name: "Поделиться" }).click();
  await page.getByRole("button", { name: "Отозвать", exact: true }).click();
  await friend.reload();
  await expect(
    friend.getByRole("heading", { name: "Страница недоступна" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  await editorMode(page, 'Markdown');
  await page
    .getByLabel("Текст заметки в Markdown")
    .fill(
      "## Небольшие шаги\n\n- Прочитать новую книгу\n- Разобрать идеи для проекта\n- Оставить время на прогулку",
    );
  await expect(page.locator("#page-save-status")).toContainText("Сохранено");
  await expect(page.getByRole("button", { name: "Поделиться" })).toBeVisible();
  await appSection(page, 'Главная');
  await expect(page.locator("#toast")).not.toHaveClass(/show/, {
    timeout: 7000,
  });
  await page.screenshot({
    path: "artifacts/dashboard-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "artifacts/dashboard-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await newNote(page);
  await page.getByLabel("Название заметки").fill("Мобильная заметка");
  await editorMode(page, 'Markdown');
  await page.getByLabel("Текст заметки в Markdown").fill("Работает с телефона");
  await expect(page.locator("#page-save-status")).toContainText("Сохранено");
  await expect(page.getByLabel("Название заметки")).toHaveValue(
    "Мобильная заметка",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1280, height: 900 });
  await newNote(page);
  await page.getByLabel("Название заметки").fill("База знаний");
  const editable = page.getByRole("textbox", { name: "Содержимое страницы" });
  await editable.click();
  await editable.pressSequentially("/");
  await page.getByText("Контрольный список", { exact: true }).click();
  await editable.pressSequentially("Проверить блочный редактор");
  await expect(page.locator("[data-content-type=checkListItem]")).toContainText(
    "Проверить блочный редактор",
  );
  await expect
    .poll(async () => {
      const r = await page.request.get("/api/notes");
      return (await r.json()).find((n) => n.title === "База знаний")
        ?.blocks?.[0]?.type;
    })
    .toBe("checkListItem");
  await page.reload();
  await expect(page.getByLabel("Название заметки")).toHaveValue("База знаний");
  await expect(page.locator("[data-content-type=checkListItem]")).toContainText(
    "Проверить блочный редактор",
  );
  await page
    .getByRole("button", { name: "Добавить в избранное", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Убрать из избранного", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Действия страницы" }).click();
  await page.getByRole("button", { name: "Добавить подстраницу" }).click();
  await expect(page.getByLabel("Название заметки")).toHaveValue("");
  await page.getByLabel("Название заметки").fill("Вложенная страница");
  await editorMode(page, 'Markdown');
  await page
    .getByLabel("Текст заметки в Markdown")
    .fill("Текст внутри дочерней страницы");
  await expect(page.locator("#page-save-status")).toContainText("Сохранено");
  await expect(page.locator(".breadcrumbs")).toContainText("База знаний");
  await page
    .locator(".breadcrumbs")
    .getByRole("button", { name: /База знаний/ })
    .click();
  await expect(
    page.getByRole("button", { name: /Вложенная страница/ }).first(),
  ).toBeVisible();
  await pageAction(page, 'width');
  await expect(page.locator("#notion-page")).toHaveClass(/full-width/);
  await pageAction(page, 'duplicate');
  await expect(page.getByLabel("Название заметки")).toHaveValue(
    "База знаний (копия)",
  );
  const pageUrl = page.url();
  await appSection(page, 'Главная');
  await page.getByRole('button',{name:'Разблокировать расположение виджетов'}).click();
  await page.locator('[data-widget=calendar] [data-move-widget]').press('ArrowUp');
  await page.locator('[data-widget=calendar] [data-move-widget]').press('ArrowUp');
  await expect(page.locator('[data-layout-status]')).toHaveText('Порядок сохранён');
  await expect(page.locator("[data-widget=weather]")).toHaveCount(0);
  await expect(page.locator("[data-widget]").first()).toHaveAttribute(
    "data-widget",
    "calendar",
  );
  await page.reload();
  await expect(page.locator("[data-widget]").first()).toHaveAttribute(
    "data-widget",
    "calendar",
  );
  await expect(page.locator("[data-widget=weather]")).toHaveCount(0);
  await page.goto(pageUrl);
  await expect(page.getByLabel("Название заметки")).toHaveValue(
    "База знаний (копия)",
  );
  await page.screenshot({
    path: "artifacts/notes-blocks-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "artifacts/notes-blocks-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  // Account switches hide sections and stop their requests without losing data.
  await page.setViewportSize({ width: 1280, height: 900 });
  await appSection(page, 'Настройки');
  await page.getByRole("checkbox", { name: "Модуль: Заметки", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "Модуль: Напоминания", exact: true }).uncheck();
  await page.getByRole("button", { name: "Сохранить модули", exact: true }).click();
  await expect(page.locator("[data-view=notes]")).toHaveCount(0);
  await expect(page.locator("[data-view=tasks]")).toHaveCount(0);
  await expect(page.locator("#new-note")).toHaveCount(0);
  await expect(page.locator("#city-form")).toHaveCount(0);
  await page.screenshot({ path: "artifacts/module-settings-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/module-settings-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  const disabledRequests = [];
  const capture = (req) => {
    if (/\/api\/(notes|tasks|weather)(?:[/?]|$)/.test(new URL(req.url()).pathname)) disabledRequests.push(req.url());
  };
  page.on("request", capture);
  await page.goto(pageUrl);
  await expect(page.locator("[data-widget=calendar]")).toBeVisible();
  await expect(page.locator("[data-widget]")).toHaveCount(1);
  await expect(page.locator("[data-view=notes]")).toHaveCount(0);
  await page.locator("[data-day]").first().click();
  await expect(page.locator("[data-widget=calendar]")).toBeVisible();
  await expect(page.locator(".calendar-day.selected")).toHaveCount(1);
  await page.getByRole("button", { name: "Настроить главную" }).click();
  await expect(page.getByRole("checkbox", { name: /Последние заметки/ })).toBeDisabled();
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  expect(disabledRequests).toEqual([]);
  page.off("request", capture);
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await page.getByRole('checkbox',{name:'Календарь',exact:true}).uncheck();
  await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.getByRole("heading", { name: "Главная пока пуста" })).toBeVisible();
  await appSection(page, 'Настройки');
  for (const title of ["Заметки", "Напоминания"])
    await page.getByRole("checkbox", { name: "Модуль: " + title, exact: true }).check();
  await page.getByRole("button", { name: "Сохранить модули", exact: true }).click();
  await expect(page.locator("[data-view=notes]")).toBeVisible();
  await appSection(page, 'Напоминания');
  await expect(page.locator('.tasks-sidebar')).toBeVisible();
  await expect(page.locator('.sidebar')).not.toBeVisible();
  await page.locator('[data-smart-view=completed]').click();
  await expect(page.getByRole('checkbox', {name:'Выполнено: Проверить личный сайт',exact:true})).toBeChecked();
  await appSection(page, 'Главная');
  await page.getByRole('button',{name:'Настроить главную',exact:true}).click();
  await page.getByRole('checkbox',{name:'Календарь',exact:true}).check();
  await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();
  await expect(page.locator("[data-widget]").first()).toHaveAttribute("data-widget", "calendar");
  await expect(page.locator("[data-widget=weather]")).toHaveCount(0); // Enabling a module does not automatically create an optional widget.
  await page.goto(pageUrl);
  await expect(page.getByLabel("Название заметки")).toHaveValue("База знаний (копия)");
  await test.step('Weather, RSS, bookmarks and gallery', () => widgetsFlow(page));
  await test.step('Weather widget sizes', () => weatherSizingFlow(page));
  await test.step('Dashboard widget sizes', () => widgetSizingFlow(page));
  await test.step('Clock settings and time zones', () => clocksFlow(page));
  await test.step('Clock appearance', () => clockAppearanceFlow(page));
  await page.goto(pageUrl);
  await expect(page.getByLabel("Название заметки")).toHaveValue("База знаний (копия)");
  // The live editor follows scheduled night mode without dropping its blocks.
  await page.clock.setSystemTime(new Date('2026-10-11T23:00:00Z'));
  await page.clock.runFor(61000);
  await expect(page.locator('html')).toHaveAttribute('data-scheme','dark');
  await expect(page.locator('.bn-container')).toHaveAttribute('data-color-scheme','dark');
  await expect(page.locator('[data-content-type=checkListItem]')).toContainText('Проверить блочный редактор');
  await page.clock.runFor(5000);
  await page.screenshot({animations:"disabled",path: "artifacts/notes-night-desktop.png",fullPage:true});
  await page.clock.setSystemTime(new Date());
  // Invalid/expired access is refreshed without exposing JWT to JavaScript.
  const authCookies = await page.context().cookies();
  const accessCookie = authCookies.find((cookie) => cookie.name === "drg_session");
  const refreshCookie = authCookies.find((cookie) => cookie.name === "drg_refresh");
  expect(accessCookie.httpOnly).toBe(true);
  expect(refreshCookie.httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain("drg_session");
  expect(await page.evaluate(() => document.cookie)).not.toContain("drg_refresh");
  const parts = accessCookie.value.split(".");
  await page.context().addCookies([{ ...accessCookie, value: parts[0] + ".invalid." + parts[2] }]);
  let refreshCount = 0;
  const countRefresh = (req) => { if (new URL(req.url()).pathname === "/api/refresh") refreshCount++; };
  page.context().on("request", countRefresh);
  const anotherTab = await page.context().newPage();
  anotherTab.on("pageerror", (e) => errors.push(e.message));
  await Promise.all([page.reload(), anotherTab.goto(pageUrl)]);
  await expect(page.getByLabel("Название заметки")).toHaveValue("База знаний (копия)");
  await expect(anotherTab.getByLabel("Название заметки")).toHaveValue("База знаний (копия)", {timeout:15000});
  expect(refreshCount).toBe(1);
  page.context().off("request", countRefresh);
  expect((await page.context().cookies()).find((cookie) => cookie.name === "drg_refresh").value).not.toBe(refreshCookie.value);
  await anotherTab.close();
  // Another independently authenticated browser can be revoked in Settings.
  const secondContext = await browser.newContext();
  const secondOwner = await secondContext.newPage();
  await secondOwner.goto("/");
  await secondOwner.getByLabel("Логин", { exact: true }).fill("demoowner");
  await secondOwner.getByLabel("Пароль", { exact: true }).fill("Test-long-password-123");
  await secondOwner.locator('#auth-form button[type=submit]').click();
  await secondOwner.getByLabel("Код 2FA или резервный код").fill(recoveryCodes[0]);
  await secondOwner.locator('#auth-form button[type=submit]').click();
  await expect(secondOwner.getByRole("heading", { name: "Привет, demoowner." })).toBeVisible({timeout:15000});
  await appSection(page, 'Настройки');
  await page.getByRole("button", { name: "Безопасность", exact: true }).click();
  await page.getByRole("button", { name: "Завершить сеанс", exact: true }).click();
  await secondOwner.reload();
  await expect(secondOwner.getByRole("heading", { name: "Войти в свой мир." })).toBeVisible();
  await secondContext.close();
  const attacker = await browser.newContext();
  for (let i = 0; i < 8; i++) {
    const failed = await attacker.request.post("http://127.0.0.1:8001/api/login", { data: { login: "intruder", password: "wrong-password-long" } });
    expect(failed.status()).toBe(i === 7 ? 429 : 401);
  }
  await page.reload();
  await appSection(page, 'Настройки');
  await page.getByRole("button", { name: "Безопасность", exact: true }).click();
  await expect(page.locator('[data-admin-security]')).toBeVisible();
  await expect(page.locator("#toast")).not.toHaveClass(/show/, { timeout: 7000 });
  await page.screenshot({ path: "artifacts/security-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/security-mobile.png", fullPage: true });
  await page.locator('[data-admin-security]').click();
  await page.locator('[data-admin-unlock],[data-as-view]').first().waitFor();
  if(await page.locator('[data-admin-unlock]').count()){
    await page.locator('[data-admin-unlock] [name=password]').fill('Test-long-password-123');
    await page.locator('[data-admin-unlock] [type=submit]').click();
  }
  await page.locator('[data-as-tab=bans]').click();
  await page.locator('[data-as-unban]').first().click();
  await page.locator('[data-as-confirm] [name=reason]').fill('Проверка восстановления доступа в изолированном тесте');
  await page.locator('[data-as-confirm] [name=password]').fill('Test-long-password-123');
  await page.locator('[data-as-confirm] [name=code]').fill(totp(secret));
  await page.locator('[data-as-confirm] [type=submit]').click();
  await page.locator('[data-as-confirm]').waitFor({state:'detached'});
  await expect(page.getByText("Активных блокировок нет.", { exact: true })).toBeVisible();
  expect((await attacker.request.post("http://127.0.0.1:8001/api/login", { data: { login: "intruder", password: "wrong-password-long" } })).status()).toBe(401);
  await attacker.close();
  await test.step('Block editor and databases', () => notionFlow(page,browser,totp));
  await test.step('Family, friends and messaging', () => socialFlow(page,browser,totp));
  await test.step('Conversation menu, names, mentions and clearing',()=>chatControlsFlow(page,browser,totp));
  await test.step('Managed accounts without invitations',()=>adminAccountsFlow(page,browser));
  await test.step('Focused message workspace, replies and mobile navigation', () => chatWorkspaceFlow(page));
  await test.step('Voice calls, contact tabs and global incoming audio',()=>voiceCallsFlow(page,browser,totp));
  await test.step('Admin backup, restore and quotas', () => adminFlow(page,browser,totp,recoveryCodes));
  await test.step('Account security and sessions', () => accountSecurityFlow(page,browser,totp,recoveryCodes,secret));
  await test.step('Shared editing and access revocation', () => shareEditFlow(page,browser));
  await test.step('Public and private note access', () => communityFlow(page,browser));
  await test.step('File trash and restoration', () => filesTrashFlow(page));
  await test.step('Personal export and import', () => personalDataFlow(page));
  await test.step('Remaining modules and protection', () => nextFeaturesFlow(page,browser,totp));
  await test.step('Split bill widget sizes', () => expenseWidgetFlow(page));
  await test.step('Stable background refresh across every workspace', () => siteRefreshFlow(page,browser));
  await test.step('Notion paste, nested blocks, file drops and page ordering',()=>requestedUpdatesFlow(page));
  expect(errors).toEqual([]);
  await anonymous.close();
});
