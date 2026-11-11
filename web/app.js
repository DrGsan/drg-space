import {showAppUpdate} from './core/app-update.js';
import {tText,tHtml} from "./core/i18n.js";
import {accountLanguageChanged,locale,translateSystemText} from './core/i18n.js';
import {mountLanguageSettings,mountPublicLanguage} from './modules/localization/index.js';
import './modules/localization/localization.css';
import {modal} from './core/modal.js';
import {federationSharingDialog} from './modules/federation/sharing.js';
import {federationErrorMessage} from './modules/federation/errors.js';
import {setPeopleNames,rememberPeople} from './core/person-names.js';
import {clearAvatarCache} from './core/avatar-cache.js';
import {setSeasonalAccount} from './core/seasonal.js';
import {mountDatabase} from './modules/notes/database.js';
import {brand} from './core/brand.js';
import {sectionHeader} from './core/section-header.js';
import {chatUnreadController} from './core/chat-unread.js';
import {authenticatePasskey,passkeysSupported,passkeyError} from './core/passkeys.js';
import './core/auth-screen.css';
import {mountAuthMotion,codeProgress,mountWorkspaceLoading,transitionEntry} from './core/entry-motion.js';
import {preferenceSections} from './core/preferences.js';
import {bindMobileBack} from './core/mobile-back.js';
import {mobileApp,mobileStart,mobileTabs,bindMobileViewport} from './core/mobile.js';
import {captureMobileTabs,restoreMobileTabs} from './core/mobile-tabs.js';
import './mobile-app.css';
import {abortable} from './core/request.js';
import {workspaceView,rememberWorkspace,writeSession} from './core/navigation.js';
import {sidebarIcon} from './core/sidebar-icons.js';
import './workspace-chrome.css';
import {inboxController,inboxIcon,mountNotificationPreferences} from './modules/notifications/index.js';
import {pageIcon,pageCover,coverAttribution} from './core/page-assets.js';
import {mountFiles,mountSharedFiles} from './modules/files/index.js';
import {mountExpenses} from './modules/expenses/index.js';
import {bindExpensePhotos} from './modules/expenses/photos.js';
import {expenseReport} from './modules/expenses/report.js';
import { mountSocial } from "./modules/social/index.js";
import {mountGuestCall} from './modules/chat/guest-calls.js';
import { mountChat, chatController } from "./modules/chat/index.js";
import { mountAdmin } from "./modules/admin/index.js";
import { mountPersonalData } from "./modules/portability/index.js";
import { notionImportDialog } from './modules/portability/notion.js';
import { mountSharedEditor } from "./modules/sharing/editor.js";
import { initAppearance, setAppearance } from "./core/appearance.js";
import {mountAccountDeletion} from './modules/profile/account-deletion.js';
import {mountProfileSettings,avatarMarkup,displayName} from './modules/profile/index.js';
import { mountAppearanceSettings } from "./modules/appearance/index.js";
import { mountEnrollment } from "./modules/security/enrollment.js";
import { invitationDisclosure } from "./core/registration-invite.js";
import { requireStrongPassword } from "./core/passwords.js";
import { csrfCookie, refreshSession } from "./core/auth.js";
import {callsController} from './modules/chat/calls.js';
import {createPushClient,chatFromPushUrl,familyFromPushUrl} from './core/push.js';
import { mountSecurity } from "./modules/security/index.js";
import { moduleEnabled } from "./core/modules.js";
import {mountFamilyPhotos,familyPhotosController} from './modules/family-photos/index.js';
import { $, esc, md, date, today, formBind, toast } from "./core/ui.js";
import { mountNotes, notesController } from "./modules/notes/index.js";
import {vaultController} from './modules/security/vault.js';
import {loadEditor,mountBlockEditor} from "./modules/notes/editor-runtime.js";
import {
  mountDashboard,
  dashboardController,
} from "./modules/dashboard/index.js";
import {mountTracker,trackerController} from './modules/tracker/index.js';
import {mountTaskWorkspace,tasksController} from "./modules/tasks/index.js";
initAppearance();
bindMobileViewport();
const root = document.querySelector("#app");
bindMobileBack(root);
let state = {
    me: null,
    moduleCatalog: [],
    notes: [],
    tasks: [],
    selected: null,
    day: today(),
    month: new Date(),
    view: "home",
    dirty: false,
  },
  csrf = "";
let expiryTimer;
let acceptAccountLanguage=true;
let sharedHandle;
let workspaceUpdate;
let disposeAppearance;
let disposeAdmin;
let registrationOpen=false;
let restoredWorkspaceFor=null;
let navigationScroll;
// Resizing an installed window can cross the phone breakpoint. Preserve any
// pending editor changes before rebuilding navigation for the new width.
matchMedia('(max-width:760px)').addEventListener('change',async()=>{
  if(!document.documentElement.classList.contains('app-standalone')||!state.me||!root.querySelector('.workspace'))return;
  if(workspaceUpdate){try{await workspaceUpdate;}catch{return;}}
  if(await flushWorkspace())renderWorkspace({remember:false,chatList:!root.querySelector('.chat-shell.room-open')});
});

const pushClient=createPushClient({api});
let pendingPushSpace=chatFromPushUrl(location.href,location.origin);
let pendingFamilyExport=familyFromPushUrl(location.href,location.origin);
let pendingSecurity=location.pathname==='/'&&location.hash==='#admin';
navigator.serviceWorker?.addEventListener('message',event=>{
  if(!event.source?.scriptURL)return;
  const workerUrl=new URL(event.source.scriptURL,location.origin);
  if(workerUrl.origin!==location.origin||workerUrl.pathname!=='/push-sw.js')return;
  if(event.data?.type==='drg-open-family-export'){
    const gid=familyFromPushUrl('/#admin/family-photos/'+event.data.group_id,location.origin);if(!gid)return;
    pendingFamilyExport=gid;
    if(state.me?.verified){
      if(state.me.role==='owner'){state.adminFamilyExportRequested=gid;context().navigate('admin');}
      pendingFamilyExport=null;
    }
    return;
  }
  if(event.data?.type==='drg-open-security'){
    pendingSecurity=!state.me?.verified;
    if(state.me?.verified&&state.me.role==='owner'){
      pendingSecurity=false;state.adminSecurityRequested=true;context().navigate('admin');
    }
    return;
  }
  if(event.data?.type!=='drg-open-chat'||!/^(?:[A-Za-z0-9_-]{1,128}|telegram:(?:-\d{1,20}|export:[a-f0-9]{8,24}))$/.test(event.data.space_id||''))return;
  pendingPushSpace=event.data.space_id;
  if(!state.me?.verified)return;
  if(!moduleEnabled(state,'chat')){toast(tText("app:enableMessagesInSettings"));return;}
  state.chatSpace=pendingPushSpace;pendingPushSpace=null;
  context().navigate('chat',{chatList:false});
});
async function flushWorkspace(){return await vaultController.flush() && await dashboardController.flush() && await notesController.flush() && await tasksController.flush() && await trackerController.flush() && await chatController.flush();}
const modes = {
  public: tText("app:public"),
  password: tText("app:passwordProtected"),
  authenticated: tText("app:signedInUsers"),
  users: tText("app:selectedUsernames"),
};
async function api(path, method = "GET", body, retry = true, extraHeaders = {}, options = {}) {
  const binary=body instanceof Blob;
  const serialized=body === undefined ? undefined : binary ? body : JSON.stringify(body);
  return abortable(async signal=>{
    const r=await fetch('/api'+path,{
      method,credentials:'same-origin',signal,
      headers:{'Content-Type':binary?'application/octet-stream':'application/json','X-CSRF-Token':csrfCookie()||csrf,'X-DrG-Language':locale(),'Accept-Language':locale(),'X-Time-Zone':Intl.DateTimeFormat().resolvedOptions().timeZone,'X-DrG-Client':navigator.standalone===true||window.matchMedia?.('(display-mode: standalone)').matches?'web-app':'web','X-DrG-Platform':/Macintosh/.test(navigator.userAgent)&&navigator.maxTouchPoints>1?'iPadOS':'',...extraHeaders},
      body:serialized,keepalive:path==='/dashboard/layout'&&method==='PUT'&&new TextEncoder().encode(serialized).length<60000,
    }).catch(error=>{if(error.name==='TypeError')throw new Error(tText("app:unableToConnectToTheServerPleaseCheck"));throw error;});
    let data;try{data=await r.json();}catch{
      data={detail:tText("app:theServerIsTemporarilyUnavailablePleaseTryAgain")};
      if(r.ok)throw Object.assign(new Error(data.detail),{status:502});
    }
    const noRefresh=/^\/(login|setup|register|2fa|refresh|passkeys)(?:[/?]|$)/.test(path);
    if(r.status===401&&retry&&!noRefresh&&csrfCookie()&&await abortable(()=>refreshSession(),{signal}))return api(path,method,body,false,extraHeaders,{...options,signal});
    if(r.headers.get('X-Account-Deletion')==='pending')document.dispatchEvent(new Event('drg-account-locked'));
    if(!r.ok){const fallback=typeof data.detail==='string'?data.detail:typeof data.detail?.message==='string'?data.detail.message:tText("app:checkTheFormFields"),message=path.includes('/federation/')?federationErrorMessage(data,fallback):fallback;const error=new Error(message);error.status=r.status;error.retryAfter=r.headers.get('Retry-After');error.adminUnlock=r.headers.get('X-Admin-Unlock')==='required';error.data=data;throw error;}
    if(path==='/me'&&data.settings){
      if(acceptAccountLanguage&&accountLanguageChanged(data.settings)){location.reload();return new Promise(()=>{});}
      acceptAccountLanguage=false;
      data.role_label=translateSystemText(data.role_label);
      if(data.app_release)data.app_release={...data.app_release,title:translateSystemText(data.app_release.title),changes:data.app_release.changes.map(translateSystemText)};
    }
    if(path==='/modules'&&data.catalog)data.catalog=data.catalog.map(item=>({...item,title:translateSystemText(item.title),description:translateSystemText(item.description)}));
    if(data.csrf)csrf=data.csrf;
    return data;
  },{signal:options.signal,timeoutMs:options.timeoutMs??(path.startsWith('/chat/')?(binary?120000:30000):0)});
}

function authScreen(mode = "login", message = "", existingInvite = false) {
  if(root.querySelector('.auth-layout'))return transitionEntry(()=>renderAuthScreen(mode,message,existingInvite));
  return renderAuthScreen(mode,message,existingInvite);
}
function renderAuthScreen(mode = "login", message = "", existingInvite = false) {
  setSeasonalAccount(null);
  acceptAccountLanguage=true;
  vaultController.clear();
  clearAvatarCache();
  disposeAppearance?.();disposeAppearance=null;
  disposeAdmin?.();disposeAdmin=null;
  sharedHandle?.destroy();
  sharedHandle = null;
  const previousForm=root.querySelector('#auth-form');previousForm?.dispatchEvent(new Event('drg-auth-close'));
  const invite = location.pathname.startsWith("/invite/")
    ? location.pathname.split("/")[2]
    : null;
  if (invite && mode === "login" && !existingInvite) mode = "register";
  const canUsePasskey=mode==='login'&&passkeysSupported();
  const heading={login:tText("app:signInToYourWorld"),setup:tText("app:createYourWorkspace"),register:tText("app:welcome"), '2fa':tText("app:confirmSignIn")}[mode];
  const intro=mode==='login'?tText("app:personalPagesSharedPlansAndThePeopleYou"):mode==='2fa'?tText("app:oneMoreStepToYourWorkspace"):tText("app:notesFilesPlansAndConversationsInOneWorkspace");
  const formMessage=message||(mode==='2fa'?tText("app:enterACodeFromYourAuthenticatorAppOr"):mode==='login'?(canUsePasskey?tText("app:chooseHowToSignIn"):tText("app:signInWithYourUsernameAndPassword")):tText("app:usernamePasswordAndTwoFactorAuthentication"));
  const submitLabel=mode==='setup'?tText("app:createAccount"):mode==='register'?tText("app:acceptInvitation"):mode==='2fa'?tText("app:confirmSignIn2"):canUsePasskey?tText("app:signInWithPassword"):tText("app:signIn");
  const newAccount=mode==='setup'||mode==='register';
  const markup = `<div class="auth-layout" data-auth-mode="${mode}" data-passkeys="${canUsePasskey}">
    <header class="auth-header"><a class="brand" href="/" aria-label="DrG Space">${brand()}</a></header>
    <main class="auth-main">
      <section class="auth-intro" aria-labelledby="auth-title"><p class="eyebrow">${mode==='setup'?tText("app:initialSetup"):mode==='register'?(invite?tText("app:workspaceInvitation"):tText("app:yourWorkspaceStartsHere")):mode==='2fa'?tText("app:protectYourWorkspace"):tText("app:yourWorkspaceStartsHere")}</p><h1 id="auth-title">${heading}</h1><p class="auth-description">${intro}</p></section>
      <form id="auth-form" class="auth-card" aria-labelledby="auth-title">
        <p class="auth-message">${esc(formMessage)}</p>
        ${canUsePasskey?`<button type="button" class="passkey-login" data-passkey-login>${tHtml("app:signInWithAPasskey", {value0:(sidebarIcon('fingerprint'))})}</button><p data-passkey-login-status role="status"></p><div class="auth-divider">${tHtml("app:orWithAPassword")}</div>`:''}
        <div class="auth-fields">
          ${mode==='setup'?`<label>${tHtml("app:initialSetupKey")}<input name="token" type="password" required autocomplete="off"></label>`:''}
          ${mode==='2fa'?`<label>${tHtml("app:label2faOrRecoveryCode")}<input name="code" autocomplete="one-time-code" required autofocus></label>${codeProgress()}`:`<label>${tHtml("app:username")}<input name="login" pattern="[a-zA-Z0-9_.-]{3,40}" autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="${tHtml("app:yourUsername")}" required autofocus></label><label>${tHtml("app:password")}<input name="password" type="password" minlength="12" maxlength="256" autocomplete="${mode==='login'?'current-password':'new-password'}" placeholder="${tHtml("app:yourPassword")}" required></label>`}
          ${newAccount?`<p class="hint">${tHtml("app:useAtLeast12CharactersIncludingLowercaseAnd")}</p><label>${tHtml("app:passwordHintOptional")}<input name="password_hint" maxlength="200" autocomplete="off" placeholder="${tHtml("app:doNotEnterYourPasswordHere")}"></label>`:''}
        </div>
        <div data-password-hint></div>
        <button class="primary auth-submit" type="submit">${submitLabel}<span aria-hidden="true">→</span></button>
        ${location.pathname.startsWith('/share/')?`<a class="text-link auth-return" href="${esc(location.pathname)}">${tHtml("app:returnToPage")}</a>`:''}
        <div class="auth-foot">${mode==='login'?(registrationOpen?`<button type="button" class="text-link" id="public-register">${tHtml("app:createAccount")}</button>`:tText("app:invitationRequired")):mode==='setup'?tText("app:administratorsMustEnable2Fa"):mode==='2fa'?`<button type="button" class="text-link" id="back-login">${tHtml("app:useAnotherSignInMethod")}</button>`:`${tHtml("app:youCanEnable2FaNowOrLater")} <button type="button" class="text-link" id="back-login">${tHtml("app:signIn")}</button>`}</div>
      </form>
    </main>
    <footer class="auth-footer">${tHtml("app:everythingThatMattersJustASignInAway")}</footer>
  </div>`.replaceAll(' autofocus',matchMedia('(max-width:760px)').matches?'':' autofocus');
  const current=root.querySelector('.auth-layout');
  if(current){
    const template=document.createElement('template');template.innerHTML=markup;
    const next=template.content.firstElementChild;
    current.dataset.authMode=next.dataset.authMode;current.dataset.passkeys=next.dataset.passkeys;
    current.dataset.motionState='idle';current.classList.add('auth-continuing');
    current.querySelector('.auth-main').replaceWith(next.querySelector('.auth-main'));
  }else{root.innerHTML=markup;mountPublicLanguage(root.querySelector('.auth-header'));}
  $('#auth-form [name=login]')?.addEventListener('input',()=>{$('[data-password-hint]').innerHTML='';});
  const motion=mountAuthMotion(root.querySelector('.auth-layout'));
  const invitationReady=mode==='register'&&invite?invitationDisclosure($("#auth-form"),invite,api):Promise.resolve();
  let authBusy=false;
  const passkeyAbort=new AbortController();
  $('#auth-form').addEventListener('drg-auth-close',()=>passkeyAbort.abort(),{once:true});
  const completeLogin=async()=>{
    state.me=await api('/me');
    if(state.me.needs_enrollment)return enrollment();
    if(location.pathname.startsWith('/f/'))return fileSharedScreen();
    if(location.pathname.startsWith('/share/'))return sharedScreen();
    if(invite&&location.pathname.startsWith('/invite/'))return workspaceInvitation();
    if(location.pathname.startsWith('/page/')){state.view='notes';state.selected=location.pathname.split('/')[2];}
    else{state.view=mobileApp()?'chat':'home';state.selected=null;}
    restoredWorkspaceFor=state.me.id;
    await loadWorkspace();
  };
  if(canUsePasskey){
    $('[data-passkey-login]').onclick=async()=>{
      if(authBusy)return;authBusy=true;motion('checking');
      const button=$('[data-passkey-login]'),status=$('[data-passkey-login-status]');button.disabled=true;$('#auth-form button[type=submit]').disabled=true;status.textContent=tText("app:confirmSignInOnYourDevice");
      try{await authenticatePasskey(api,{signal:passkeyAbort.signal});await completeLogin();}
      catch(error){motion('error');if(button.isConnected)status.textContent=passkeyError(error);}
      finally{authBusy=false;if(button.isConnected){button.disabled=false;$('#auth-form button[type=submit]').disabled=false;}}
    };
  }
  $("#public-register")?.addEventListener("click",()=>authScreen("register"));
  $("#back-login")?.addEventListener("click",()=>authScreen("login","",true));
  formBind("#auth-form", async (f) => {
    if(authBusy)return;authBusy=true;motion('checking');
    try{
    await invitationReady;
    if(mode==='setup'||mode==='register')requireStrongPassword(f.get('password'));
    let data;
    if (mode === "2fa")
      data = await api("/2fa/verify", "POST", { code: f.get("code") });
    else if (mode === "setup")
      data = await api("/setup", "POST", Object.fromEntries(f));
    else if (mode === "register")
      data = await api("/register", "POST", {
        ...Object.fromEntries(f),
        invite,
      });
    else {
      try{data=await api('/login','POST',Object.fromEntries(f));}
      catch(error){
        if(error.data?.hint_available){
          $('[data-password-hint]').innerHTML=`<button type="button" class="text-link" data-show-hint>${tHtml("app:showPasswordHint")}</button><p class="hint" data-hint-result></p>`;
          $('[data-show-hint]').onclick=async()=>{try{const result=await api('/password-hint');$('[data-hint-result]').textContent=result.hint||tText("app:noPasswordHintSet");}catch(e){toast(e.message);}};
        }
        throw error;
      }
    }
    if (data.needs_2fa) return authScreen("2fa");
    if(mode==="register"&&invite)history.replaceState({},"","/");
    await completeLogin();
    }catch(error){motion('error');throw error;}finally{authBusy=false;}
  });
}
function enrollment() {
  root.querySelector('#auth-form')?.dispatchEvent(new Event('drg-auth-close'));
  root.innerHTML=`<main class="enroll"><a class="brand" href="/">${brand()}</a><div class="auth-card"><span class="tag">${tHtml("app:accountSecurity")}</span><h2>${tHtml("app:setUp2Fa")}</h2><p class="muted">${tHtml("app:scanTheQrCodeOrEnterTheKey")}</p><div id="enroll-flow"></div>${!['owner','admin'].includes(state.me.role)?`<button class="secondary" id="skip-enrollment">${tHtml("app:setUpLater")}</button>`:''}<button class="text-link" id="logout">${tHtml("app:signOut")}</button></div></main>`;
  $('#logout').onclick=logout;
  mountPublicLanguage(root.querySelector('.auth-header'));
  const finish=async result=>{
    state.me=await api("/me");
    try{
      if(location.pathname.startsWith("/f/"))return await fileSharedScreen();
      if(location.pathname.startsWith("/share/"))return await sharedScreen();
      if(location.pathname.startsWith("/invite/"))return await workspaceInvitation();
      history.replaceState({},"","/");state.view=mobileApp()?"chat":"home";state.selected=null;restoredWorkspaceFor=state.me.id;await loadWorkspace();
    }finally{
      if(result?.invitation_friendship?.status==='unavailable')toast(result.invitation_friendship.message);
    }
  };
  $('#skip-enrollment')?.addEventListener('click',async()=>{try{await finish(await api('/2fa/skip','POST'));}catch(error){toast(error.message);}});
  mountEnrollment($("#enroll-flow"),context(),finish,tText("app:openWorkspace"));
}
async function logout() {
  try {
    await api("/logout", "POST");
  } catch (e) {
    if(e.status!==401){toast(e.message);return;}
  }
  // The server removed this session's subscriptions by cascade. Unsubscribe the
  // device only after logout succeeds (emergency policy can reject the action).
  await pushClient.logout();
  vaultController.clear();
  chatController.destroy();
  familyPhotosController.destroy();
  dashboardController.destroy();
  notesController.destroy();
  tasksController.destroy();
  trackerController.destroy();
  inboxController.destroy();
  chatUnreadController.destroy();
  callsController.destroy();
  if(state.me){writeSession('drg-workspace-view:'+state.me.id,null);writeSession('drg-mobile-view:'+state.me.id,null);}
  restoredWorkspaceFor=null;state.view=mobileApp()?'chat':'home';state.selected=null;state.chatSpace=null;state.chatDrafts=null;
  state.me = null;setPeopleNames(null,{});
  pendingPushSpace=null;
  pendingSecurity=false;state.adminSecurityRequested=false;pendingFamilyExport=null;delete state.adminFamilyExportRequested;
  csrf = "";
  authScreen();
}
async function loadWorkspace() {
  setSeasonalAccount(state.me?.id);
  const load=path=>api(path,'GET',undefined,true,{}, {timeoutMs:15000});
  try {
    if(state.me.account_deletion)return accountWaiting();
    void pushClient.syncLanguage().catch(()=>{});
    setPeopleNames(state.me.id,state.me.people_names||{});
    rememberPeople([{id:state.me.id,login:state.me.login,...state.me.settings.profile}]);
    if(!root.querySelector('.auth-layout'))setAppearance(state.me.settings.appearance);
    if(!root.querySelector('.workspace'))mountWorkspaceLoading(root);
    const modules = await load("/modules");
    state.moduleCatalog = modules.catalog;
    state.me.settings.modules = modules.enabled;
    [state.notes, state.tasks] = await Promise.all([
      moduleEnabled(state, "notes") ? load("/notes") : [],
      moduleEnabled(state, "tasks") ? load("/tasks") : [],
    ]);
    state.spaces=(await load("/social")).spaces;
    rememberPeople(state.spaces.flatMap(space=>space.members||[]));
    if(restoredWorkspaceFor!==state.me.id){
      if(location.pathname==='/'&&!pendingPushSpace)state.view=workspaceView(state.me.id,mobileApp());
      restoredWorkspaceFor=state.me.id;
    }
    const openRequestedChat=Boolean(pendingPushSpace);
    if(pendingPushSpace){
      if(moduleEnabled(state,'chat')){state.view='chat';state.chatSpace=pendingPushSpace;}
      pendingPushSpace=null;
    }
    if(pendingFamilyExport){if(state.me.role==='owner'){state.view='admin';state.adminFamilyExportRequested=pendingFamilyExport;}pendingFamilyExport=null;}
    if(pendingSecurity){
      if(state.me.role==='owner'){state.view='admin';state.adminSecurityRequested=true;}
      pendingSecurity=false;
    }
    if(state.view==='home'){
      try{
        state.me.dashboardReady=await load('/dashboard/layout');
        state.me.settings.dashboard=state.me.dashboardReady;
      }catch(error){if([401,403,423].includes(error.status))throw error;}
    }
    await renderWorkspace({chatList:!openRequestedChat});
  } catch (e) {
    if(root.querySelector('.workspace')){toast(e.message);return;}
    root.querySelector('#auth-form')?.dispatchEvent(new Event('drg-auth-close'));
    root.innerHTML=`<main class="enroll workspace-error"><section class="auth-card" aria-labelledby="workspace-error-title"><h2 id="workspace-error-title" tabindex="-1">${tHtml("app:couldNotLoadWorkspace")}</h2><p class="workspace-error-message" role="status">${esc(e.message)}</p><div class="workspace-error-actions"><button class="primary" data-workspace-retry>${tHtml("app:tryLoadingAgain")}</button>${moduleEnabled(state,'chat')?`<button class="secondary" data-workspace-chat>${tHtml("app:openMessages")}</button>`:''}<a class="text-link" href="/">${tHtml("app:goToHome")}</a></div></section></main>`;
    $('#workspace-error-title').focus();
    $('[data-workspace-retry]').onclick=async e=>{const button=e.currentTarget;button.disabled=true;try{await loadWorkspace();}finally{if(button.isConnected)button.disabled=false;}};
    $('[data-workspace-chat]')?.addEventListener('click',()=>{state.view='chat';renderWorkspace();});
  }
}
function renderWorkspace(options={}) {
  if(!root.querySelector('.workspace'))return transitionEntry(()=>{setAppearance(state.me.settings.appearance);renderWorkspaceNow(options);});
  return renderWorkspaceNow(options);
}
function renderWorkspaceNow({remember=true,chatList=true}={}) {
  root.querySelector('#auth-form')?.dispatchEvent(new Event('drg-auth-close'));
  const previousMobileTabs=captureMobileTabs(root);
  const previousNavigation=root.querySelector('.sidebar nav');
  vaultController.destroy();
  if(previousNavigation)navigationScroll={uid:state.me.id,left:previousNavigation.scrollLeft,top:previousNavigation.scrollTop};
  disposeAppearance?.();disposeAppearance=null;
  disposeAdmin?.();disposeAdmin=null;
  dashboardController.destroy();
  notesController.destroy();
  tasksController.destroy();
  trackerController.destroy();
  chatController.destroy();
  familyPhotosController.destroy();
  if (state.view !== "notes") history.replaceState({}, "", "/");
  if (state.view==="admin" && !["owner","admin"].includes(state.me.role)) state.view="settings";
  if (["notes", "tracker", "tasks", "chat", "expenses", "files", "family_photos"].includes(state.view) && !moduleEnabled(state, state.view)) {
    state.view = mobileApp()?mobileStart(id=>moduleEnabled(state,id)):"home";
    state.selected = null;
    history.replaceState({}, "", "/");
  }
  const name = displayName(state.me);
  if(remember)rememberWorkspace(state.me.id,state.view,mobileApp());
  const notesOpen=state.view === "notes";
  const tasksOpen=state.view === "tasks";
  const chatOpen=state.view === "chat";
  const filesOpen=state.view === "files";
  const focusedWorkspace=notesOpen||tasksOpen||chatOpen||filesOpen;
  const inboxButton=`<button class="icon-btn inbox-button" id="open-inbox" aria-label="${tHtml("app:notifications")}" title="${tHtml("app:notifications")}">${inboxIcon}</button>`;
  root.innerHTML = `<div data-current-view="${state.view}" class="workspace ${notesOpen?"notes-open":tasksOpen?"tasks-open":chatOpen?"chat-open":filesOpen?"files-open":""}">${focusedWorkspace?"":`<aside class="sidebar"><a href="/" class="brand" aria-label="DrG Space">${brand()}</a><div class="nav-label">${tHtml("app:workspace")}</div><nav><button class="nav-btn ${state.view === "home" ? "active" : ""}" data-view="home" aria-label="${tHtml("app:home")}" title="${tHtml("app:home")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('house')}</span><span class="nav-text">${tHtml("app:home")}</span></button>${moduleEnabled(state, "notes") ? `<button class="nav-btn ${state.view === "notes" ? "active" : ""}" data-view="notes" aria-label="${tHtml("app:notes")}" title="${tHtml("app:notes")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('notebook-text')}</span><span class="nav-text">${tHtml("app:notes")}</span></button>` : ""}${moduleEnabled(state,"tracker")?`<button class="nav-btn ${state.view==="tracker"?"active":""}" data-view="tracker" aria-label="${tHtml("tracker:title")}" title="${tHtml("tracker:title")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon("kanban")}</span><span class="nav-text">${tHtml("tracker:title")}</span></button>`:""}${moduleEnabled(state, "tasks") ? `<button class="nav-btn ${state.view === "tasks" ? "active" : ""}" data-view="tasks" aria-label="${tHtml("app:reminders")}" title="${tHtml("app:reminders")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('list-todo')}</span><span class="nav-text">${tHtml("app:reminders")}</span></button>` : ""}${moduleEnabled(state, "chat") ? `<button class="nav-btn ${state.view === "chat" ? "active" : ""}" data-view="chat" aria-label="${tHtml("app:messages")}" title="${tHtml("app:messages")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('messages-square')}</span><span class="nav-text">${tHtml("app:messages")}</span><small data-chat-unread aria-hidden="true" hidden></small></button>` : ""}${moduleEnabled(state,"files")?`<button class="nav-btn ${state.view==='files'?'active':''}" data-view="files" aria-label="${tHtml("app:files")}" title="${tHtml("app:files")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('folder-closed')}</span><span class=nav-text>${tHtml("app:files")}</span></button>`:""}${moduleEnabled(state,"family_photos")?`<button class="nav-btn ${state.view==="family_photos"?"active":""}" data-view="family_photos" aria-label="${tHtml("app:familyPhotos")}" title="${tHtml("app:familyPhotos")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('image')}</span><span class="nav-text">${tHtml("app:familyPhotos")}</span></button>`:""}<button class="nav-btn ${state.view === "social" ? "active" : ""}" data-view="social" aria-label="${tHtml("app:peopleAndGroups")}" title="${tHtml("app:peopleAndGroups")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('users-round')}</span><span class="nav-text">${tHtml("app:peopleAndGroups")}</span></button>${moduleEnabled(state,"expenses")?`<button class="nav-btn ${state.view==='expenses'?'active':''}" data-view="expenses" aria-label="${tHtml("app:splitBills")}" title="${tHtml("app:splitBills")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('receipt-text')}</span><span class="nav-text">${tHtml("app:splitBills")}</span></button>`:""}<button class="nav-btn ${["settings","security","personal-data"].includes(state.view) ? "active" : ""}" data-view="settings" aria-label="${tHtml("app:settings")}" title="${tHtml("app:settings")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('settings')}</span><span class="nav-text">${tHtml("app:settings")}</span></button>${["owner","admin"].includes(state.me.role)?`<button class="nav-btn ${state.view==="admin"?"active":""}" data-view="admin" aria-label="${tHtml("app:administration")}" title="${tHtml("app:administration")}"><span class="nav-icon" aria-hidden="true">${sidebarIcon('shield-user')}</span><span class="nav-text">${tHtml("app:administration")}</span></button>`:""}</nav><div class="sidebar-bottom">${state.me.two_factor?"":`<button class="security-badge security-warning" data-warning-2fa>${tHtml("app:accountIsNotProtectedBy2Fa")}</button>`}<div class="user"><span class="avatar">${avatarMarkup(state.me)}</span><div><strong>${esc(name)}</strong><small>${esc(state.me.role_label)}</small></div><button id="logout" class="icon-btn" aria-label="${tHtml("app:signOut")}">↪</button></div><small class="sidebar-version" data-sidebar-version aria-label="${tHtml("app:appVersion", {value0:(esc(state.me.app_version))})}">v${esc(state.me.app_version)}</small><small class="sidebar-update" data-sidebar-update hidden></small></div></aside>`}<main class="content">${focusedWorkspace?"":`<header class="topbar"><button type="button" class="mobile-secondary-back" data-mobile-open="settings" aria-label="${tHtml("app:backToSettings")}">‹</button><strong class="mobile-screen-title">${esc(({home:tText("app:home"),tracker:tText("tracker:title"),settings:tText("app:settings"),files:tText("app:files"),family_photos:tText("app:familyPhotos"),social:tText("app:peopleAndGroups"),expenses:tText("app:splitBills"),security:tText("app:security"),"personal-data":tText("app:personalData"),admin:tText("app:administration")})[state.view]||"")}</strong>${inboxButton}<span>${new Date().toLocaleDateString(locale(), { day: "numeric", month: "long", weekday: "long" })}</span>${moduleEnabled(state, "notes") ? `<button id="new-note" class="primary compact">${tHtml("app:newNote")}</button>` : ""}</header>`}<div id="view-content"></div></main><nav class="mobile-app-tabs" aria-label="${tHtml("app:mainSections")}">${mobileTabs.filter(([id])=>id==="settings"||moduleEnabled(state,id)).map(([id,icon,label])=>`<button type="button" data-mobile-view="${id}" ${state.view===id||id==='settings'&&!mobileTabs.some(([tab])=>tab===state.view)?'aria-current="page"':''}>${sidebarIcon(icon)}<span>${label}</span></button>`).join('')}</nav></div>`;
  showAppUpdate($('[data-sidebar-update]'),state.me,api);
  restoreMobileTabs(root,previousMobileTabs,state.me.id);
  root.querySelectorAll('[data-mobile-view],[data-mobile-open]').forEach(button=>button.onclick=()=>context().navigate(button.dataset.mobileView||button.dataset.mobileOpen,{chatList:button.dataset.mobileView==='chat'}));
  const navigation=root.querySelector('.sidebar nav');
  if(navigation&&navigationScroll?.uid===state.me.id){navigation.scrollLeft=navigationScroll.left;navigation.scrollTop=navigationScroll.top;}
  $(".sidebar > .brand")?.addEventListener("click",e=>{e.preventDefault();context().navigate("home");});
  $("[data-warning-2fa]")?.addEventListener("click",()=>context().navigate("security"));
  const logoutButton=$("#logout");
  if(logoutButton) logoutButton.onclick = async () => {
    if (await flushWorkspace()) logout();
  };
  $("#new-note")?.addEventListener("click", async () => {
    if (await flushWorkspace()) {
      state.view = "notes";
      state.notesSpace = "private";
      state.selected = null;
      state.dirty = false;
      renderWorkspace();
      notesController.newPage();
    }
  });
  document.querySelectorAll("[data-view]").forEach(
    (b) =>
      (b.onclick = async () => {
        if(workspaceUpdate){try{await workspaceUpdate;}catch{return;}}
        if (!(await flushWorkspace())) return;
        state.dirty = false;
        state.view = b.dataset.view;
        renderWorkspace();
      }),
  );
  if (state.view === "files") mountFiles($("#view-content"),context());
  if (state.view === "home") dashboard();
  if (state.view === "notes") notesView();
  if (state.view === "tasks") tasksView();
  if (state.view === "tracker") mountTracker($("#view-content"),context());
  if (state.view === "settings") settingsView();
  if (state.view === "social") mountSocial($("#view-content"),context());
  if (state.view === "chat") mountChat($("#view-content"),context(),{listOnly:chatList});
  if (state.view === "family_photos") mountFamilyPhotos($("#view-content"),context()).catch(e=>toast(e.message));
  if (state.view === "expenses") mountExpenses($("#view-content"),context()).catch(e=>toast(e.message));
  if (state.view === "security") mountSecurity($("#view-content"),context());
  if (state.view === "admin") disposeAdmin=mountAdmin($("#view-content"),context());
  if (state.view === "personal-data") mountPersonalData($("#view-content"),context());
  if(new URLSearchParams(location.search).get('notion')==='connected'){
    const returned=new URL(location.href);returned.searchParams.delete('notion');history.replaceState(null,'',returned);
    import('./modules/portability/notion-sync.js').then(module=>module.notionSyncDialog(context()));
  }
  chatUnreadController.attach(context());
  inboxController.attach(context());
  callsController.attach(context());
}
function context() {
  return {
    state,
    api,
    push:pushClient,
    toast,
    modal,
    sharePanel,
    notesInboxButton: `<button class="icon-btn inbox-button" id="open-inbox" aria-label="${tHtml("app:notifications")}" title="${tHtml("app:notifications")}">${inboxIcon}</button>`,
    moduleEnabled: (id) => moduleEnabled(state, id),
    renderWorkspace,
    loadWorkspace,
    restored: (login) => {
      setSeasonalAccount(null);
      disposeAdmin?.();disposeAdmin=null;
      inboxController.destroy();
      chatUnreadController.destroy();
      callsController.destroy();
      tasksController.destroy();
      trackerController.destroy();
      state.me=null;state.notes=[];state.tasks=[];csrf="";
      pendingSecurity=false;state.adminSecurityRequested=false;pendingFamilyExport=null;delete state.adminFamilyExportRequested;
      root.innerHTML=`<main class="enroll"><div class="brand">${brand()}</div><h1>${tHtml("app:restoreComplete")}</h1><p>${tHtml("app:signInUsingTheAdministratorAccountFromThe")} <strong>${esc(login)}</strong>.</p><a class="primary" href="/">${tHtml("app:signInAgain")}</a></main>`;
    },
    navigate: async (view,{chatList=true}={}) => {
      if(workspaceUpdate){try{await workspaceUpdate;}catch{return;}}
      if(view==="admin" && !["owner","admin"].includes(state.me.role))return;
      if (["notes", "tracker", "tasks", "chat", "expenses", "files", "family_photos"].includes(view) && !moduleEnabled(state, view)) return;
      if(!(await flushWorkspace()))return;
      state.view = view;
      renderWorkspace({chatList});
    },
    openPage: async (id) => {
      if (!moduleEnabled(state, "notes")) return;
      if(!(await flushWorkspace()))return;
      state.view = "notes";
      state.selected = id;
      state.notesList = false;
      state.notesSpace = state.notes.find(n=>n.id===id)?.space_id || "private";
      renderWorkspace();
    },
  };
}
function dashboard() {
  const initialLayout=state.me.dashboardReady;delete state.me.dashboardReady;
  mountDashboard($("#view-content"), context(), {initialLayout});
}
function notesView() {
  mountNotes($("#view-content"), context());
}
function safeMarkdownLinks() {
  document.querySelectorAll(".markdown a").forEach((a) => {
    a.rel = "noopener noreferrer";
    a.target = "_blank";
  });
}
async function sharePanel(nid,{database=false}={}) {
  const el = modal(
    `<p class="eyebrow">${tHtml("app:sharePage")}</p><h2>${tHtml("app:whoCanAccessThisPage")}</h2><form id="share-form"><label>${tHtml("app:accessMethod")}<select name="mode">${Object.entries(
      modes,
    )
      .map(([k, v]) => `<option value="${k}">${v}</option>`)
      .join(
        "",
      )}</select></label><label>${tHtml("app:linkPermissions")}<select name="permission"><option value="view">${tHtml("app:readOnly")}</option><option value="edit">${tHtml("app:editing")}</option></select></label><p class="hint">${tHtml("app:editingChangesTheOriginalPageAnyoneWithA")}</p><label id="share-password" hidden>${tHtml("app:pagePassword")}<input type="password" name="password" aria-label="${tHtml("app:pagePassword")}" minlength="8" autocomplete="new-password"><span class="hint">${tHtml("app:doNotUseYourAccountPassword")}</span></label><label id="share-users" hidden>${tHtml("app:allowedUsernames")}<input name="allowed" placeholder="friend, another_friend"><span class="hint">${tHtml("app:accountsMustAlreadyBeRegisteredSeparateUsernamesWith")}</span></label><label class="check-label"><input id="no-expiry" type="checkbox" name="forever">${tHtml("app:noExpiry")}</label><div class="duration-fields"><label>${tHtml("app:duration")}<input type="number" name="duration" value="2" min="1" max="10000" required></label><label>${tHtml("app:unit")}<select name="unit"><option value="minutes">${tHtml("app:minutes")}</option><option value="hours">${tHtml("app:hours")}</option><option value="days" selected>${tHtml("app:days")}</option><option value="weeks">${tHtml("app:weeks")}</option><option value="months">${tHtml("app:months")}</option><option value="years">${tHtml("app:years")}</option></select></label></div><p class="hint">${tHtml("app:theDurationStartsWhenTheLinkIsCreated")}</p><button class="primary" type="submit">${tHtml("app:createLink")}</button></form><div id="created-link"></div><h3>${tHtml("app:createdLinks")}</h3><div id="existing-shares"></div>`,
  );
  if(database){const permission=$('[name=permission]',el);permission.value='view';permission.disabled=true;permission.closest('label').nextElementSibling.textContent=tText("app:thisLinkOpensTheEntireDatabaseAndIts");}
  const select = $("[name=mode]", el);
  const federated=document.createElement('section');federated.innerHTML=`<h3>${tHtml("app:otherDrgServers")}</h3><p class="hint">${tHtml("app:grantAccessToAConfirmedFriendWhoHas")}</p><button class="secondary compact" data-federation-share>${tHtml("app:selectAFriendFromAnotherServer")}</button>`;$('.modal',el).append(federated);
  $('[data-federation-share]',el).onclick=()=>{$('.modal-close',el).click();federationSharingDialog(context(),nid);};
  select.onchange = () => {
    $("#share-password", el).hidden = select.value !== "password";
    $("[name=password]", el).required = select.value === "password";
    $("#share-users", el).hidden = select.value !== "users";
    $("[name=allowed]", el).required = select.value === "users";
  };
  $("#no-expiry", el).onchange = (e) => {
    document
      .querySelectorAll(".duration-fields input,.duration-fields select")
      .forEach((x) => (x.disabled = e.target.checked));
  };
  async function list() {
    try {
      const shares = await api("/notes/" + nid + "/shares");
      $("#existing-shares", el).innerHTML =
        shares
          .map(
            (s) =>
              `<div class="share-row"><div><strong>${modes[s.mode]}</strong><span class="small">${s.permission === "edit" ? tText("app:editing") : tText("app:readOnly")}</span><span class="small">${s.revoked ? tText("app:revoked") : s.expires && s.expires * 1000 < Date.now() ? tText("app:expired") : s.expires ? tText("app:until", {value0:(date(s.expires))}) : tText("app:noExpirationDate")}</span>${s.mode === "users" ? '<span class="small">' + esc(JSON.parse(s.allowed).join(", ")) + "</span>" : ""}${!s.revoked&&!database ? `<select class="compact" data-share-permission="${esc(s.token)}" aria-label="${tHtml("app:linkPermissions2", {value0:(esc(s.token))})}"><option value="view" ${s.permission === "view" ? "selected" : ""}>${tHtml("app:readOnly")}</option><option value="edit" ${s.permission === "edit" ? "selected" : ""}>${tHtml("app:editing")}</option></select>` : ""}${!s.revoked ? `<a class="text-link small" href="${esc(s.url)}" target="_blank" rel="noopener">${tHtml("app:openPage")}</a>` : ""}</div>${s.revoked ? "" : `<button class="text-link danger" data-revoke="${esc(s.token)}">${tHtml("app:revoke")}</button>`}</div>`,
          )
          .join("") || `<p class="muted small">${tHtml("app:noLinksYet")}</p>`;
      el.querySelectorAll("[data-share-permission]").forEach(select=>select.onchange=async()=>{
        select.disabled=true;
        try{await api('/shares/'+select.dataset.sharePermission,'PATCH',{permission:select.value});await list();toast(tText("app:linkPermissionsUpdated"));}
        catch(error){toast(error.message);await list();}
      });
      el.querySelectorAll("[data-revoke]").forEach(
        (b) =>
          (b.onclick = async () => {
            try {
              await api("/shares/" + b.dataset.revoke, "DELETE");
              await list();
              toast(tText("app:linkAccessRevoked"));
            } catch (e) {
              toast(e.message);
            }
          }),
      );
    } catch (e) {
      toast(e.message);
    }
  }
  formBind("#share-form", async (f) => {
    const r = await api("/notes/" + nid + "/shares", "POST", {
      mode: f.get("mode"),
      permission: database?"view":f.get("permission"),
      password: f.get("password") || null,
      allowed: String(f.get("allowed") || "")
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean),
      duration: f.get("forever") ? null : Number(f.get("duration")),
      unit: f.get("unit") || "days",
    });
    $("#created-link", el).innerHTML =
      `<div class="link-result"><label>${tHtml("app:yourLink")}<input readonly value="${esc(r.url)}" id="share-url"></label><button class="secondary" id="copy-link">${tHtml("app:copy")}</button></div>`;
    $("#copy-link", el).onclick = async () => {
      try {
        await navigator.clipboard.writeText(r.url);
        toast(tText("app:linkCopied"));
      } catch {
        $("#share-url", el).select();
        toast(tText("app:copyTheSelectedLink"));
      }
    };
    await list();
  });
  await list();
}
function tasksView() {
  mountTaskWorkspace($("#view-content"),context());
}
function settingsView() {
  const release=state.me.app_release;
  const releaseNotes=release&&release.version===state.me.app_version&&Array.isArray(release.changes)&&release.changes.length?`<div class="app-release" data-app-release><h3>${tHtml("app:whatsNew")}</h3><p class="muted">${esc(release.title)}</p><p class="hint" data-release-date>${esc(new Date(release.date+'T00:00:00Z').toLocaleDateString(locale(),{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}))}</p><ul data-release-changes>${release.changes.map(change=>`<li>${esc(change)}</li>`).join('')}</ul></div>`:'';
  $("#view-content").innerHTML =
    `<div class="page-heading"><p class="eyebrow">${tHtml("app:asYouPrefer")}</p><h1>${tHtml("app:settings")}<span class="hello-dot">.</span></h1></div><div class="settings-grid"><section class="panel" id="profile-settings"></section><section class="panel" id="notification-settings"></section><section class="panel module-settings"><h2>${tHtml("app:modules")}</h2><p class="muted">${tHtml("app:selectTheSectionsYouUseDataIsSaved")}</p><form id="modules-form"><div id="module-switches"></div><button class="primary" type="submit">${tHtml("app:saveModules")}</button></form><p class="hint">${tHtml("app:thisAppliesToYourAccountPublishedLinksRetain")}</p></section><section class="panel" id="language-settings"></section><section class="panel" id="appearance-settings"></section><section class="panel settings-security"><h2>${tHtml("app:security")}</h2><p class="muted">${tHtml("app:changePasswordEnableDisable2FaActiveSessions")}</p><p class="hint">${tHtml("app:account", {value0:(esc(state.me.login)), value1:(state.me.two_factor ? tText("app:label2faEnabled") : tText("app:label2faDisabled"))})}</p><button class="secondary" id="open-security">${tHtml("app:security")}</button></section><section class="panel settings-personal-data"><h2>${tHtml("app:personalData")}</h2><p class="muted">${tHtml("app:encryptedExportAndImportOfYourNotesReminders")}</p><button class="secondary" id="open-personal-data">${tHtml("app:exportAndImport")}</button></section><section class="panel" id="about-settings"><h2>${tHtml("app:about")}</h2><p>DrG Space</p><p class="muted">${tHtml("app:version")} <strong data-app-version>${esc(state.me.app_version)}</strong> <span data-app-update hidden aria-live="polite"></span></p>${releaseNotes}</section></div>`;
  preferenceSections($('#view-content .settings-grid'),[
    {id:'profile',selector:'#profile-settings',group:tText("app:personal"),title:tText("app:profile"),description:tText("app:yourAccountNameAndAvatar"),icon:'users-round'},
    {id:'notifications',selector:'#notification-settings',group:tText("app:personal"),title:tText("app:notifications"),description:tText("app:soundsEventsAndDeviceNotifications"),icon:'messages-square'},
    {id:'modules',selector:'.module-settings',group:tText("app:personal"),title:tText("app:modules"),description:tText("app:chooseTheSectionsInYourWorkspace"),icon:'list-todo'},
    {id:'language',selector:'#language-settings',group:tText("app:personal"),title:tText("app:language"),description:tText("app:interfaceLanguageAndFormatting"),icon:'settings'},
    {id:'appearance',selector:'#appearance-settings',group:tText("app:personal"),title:tText("app:appearance"),description:tText("app:themeLightAndDarkMode"),icon:'settings'},
    {id:'security',selector:'.settings-security',group:tText("app:account2"),title:tText("app:security"),description:tText("app:passwordTwoFactorAuthenticationAndActiveSessions"),icon:'shield-user'},
    {id:'data',selector:'.settings-personal-data',group:tText("app:account2"),title:tText("app:personalData"),description:tText("app:exportAndImportYourData"),icon:'folder-closed'},
    {id:'about',selector:'#about-settings',group:tText("app:app"),title:tText("app:about"),description:`DrG Space ${state.me.app_version}`,icon:'house'},
  ],{userId:state.me.id,scope:'settings'});
  showAppUpdate($('#about-settings [data-app-update]'),state.me,api);
  sectionHeader($('#view-content'),context());
  if(mobileApp()){
    const destinations=[['home','house',tText("app:homeAndWidgets")],['family_photos','image',tText("app:familyPhotos")],['files','folder-closed',tText("app:files")],['social','users-round',tText("app:peopleAndGroups")],['expenses','receipt-text',tText("app:splitBills")],...(['owner','admin'].includes(state.me.role)?[['admin','shield-user',tText("app:administration")]]:[])];
    const extra=document.createElement('section');extra.className='mobile-more-sections';
    extra.innerHTML=`<div class="mobile-account"><span class="avatar" data-profile-avatar>${avatarMarkup(state.me)}</span><div><strong data-profile-display-name>${esc(displayName(state.me))}</strong><span>@${esc(state.me.login)}</span></div></div><h2>${tHtml("app:sections")}</h2><div class="mobile-section-links">${destinations.filter(([id])=>!['family_photos','files','expenses'].includes(id)||moduleEnabled(state,id)).map(([id,icon,label])=>`<button type="button" data-mobile-open="${id}">${sidebarIcon(icon)}<span>${label}</span><span aria-hidden="true">›</span></button>`).join('')}</div>`;
    $('#view-content .page-heading').after(extra);
    extra.querySelectorAll('[data-mobile-open]').forEach(button=>button.onclick=()=>context().navigate(button.dataset.mobileOpen));
    const signout=document.createElement('button');signout.type='button';signout.className='mobile-signout';signout.textContent=tText("app:signOut2");signout.onclick=async()=>{if(await flushWorkspace())logout();};$('#view-content').append(signout);
  }
  mountLanguageSettings($('#language-settings'),context(),flushWorkspace);
  mountProfileSettings($('#profile-settings'),context());
  mountNotificationPreferences($('#notification-settings'),context());
  disposeAppearance=mountAppearanceSettings($("#appearance-settings"),context());
  $("#open-security").onclick=()=>context().navigate("security");
  $("#open-personal-data").onclick=()=>context().navigate("personal-data");
  const notionButton=document.createElement('button');notionButton.type='button';notionButton.className='secondary';notionButton.textContent=tText("app:importFromNotion");notionButton.onclick=()=>notionImportDialog(context());
  const dataActions=document.createElement('div');dataActions.className='instance-actions';$('#open-personal-data').after(dataActions);dataActions.append($('#open-personal-data'),notionButton);
  const sections=state.moduleCatalog.filter(module=>module.kind==='module');
  $("#module-switches").innerHTML = sections.map((module) =>
    `<label class="module-switch" title="${esc(module.description)}"><input type="checkbox" name="${esc(module.id)}" aria-label="${tHtml("app:module", {value0:(esc(module.title))})}" ${moduleEnabled(state, module.id) ? "checked" : ""} ${module.available===false&&!moduleEnabled(state,module.id)?"disabled":""}><span><strong>${esc(module.title)}</strong></span></label>`
  ).join("");
  formBind("#modules-form", async (form) => {
    const navigation=[...root.querySelectorAll('[data-view]')];
    for(const button of navigation)button.disabled=true;
    workspaceUpdate=(async()=>{
      if (!(await flushWorkspace())) return;
      const enabled = Object.fromEntries(sections.map((module) => [module.id, form.has(module.id)]));
      const result = await api("/modules", "PUT", { enabled });
      state.moduleCatalog = result.catalog;
      state.me.settings.modules = result.enabled;
      await loadWorkspace();
      toast(tText("app:moduleSettingsSaved"));
    })();
    try{await workspaceUpdate;}finally{workspaceUpdate=null;for(const button of navigation)button.disabled=false;}
  });

}
async function sharedScreen() {
  sharedHandle?.destroy();
  sharedHandle = null;
  const token = location.pathname.split("/")[2];
  clearTimeout(expiryTimer);
  root.innerHTML =
    `<main class="shared-shell"><a class="brand" href="/">${brand()}</a><div id="shared-content" class="shared-content"><p class="muted">${tHtml("app:openingPage")}</p></div></main>`;
  try {
    const record=new URLSearchParams(location.search).get('record'),query=record?'?record='+encodeURIComponent(record):'';
    const n = await api("/shared/" + encodeURIComponent(token)+query);
    if(!n.database&&(n.permission==='edit'||n.blocks?.length)){const url=location.href;await loadEditor();if(location.href!==url)return;}
    const renderedBody=n.body.replace(/(?:https?:\/\/[^/\s<>"']+)?\/api\/notes\/[A-Za-z0-9_-]{16}\/files\/([A-Za-z0-9_-]{16})/g,(_,fid)=>'/api/shared/'+encodeURIComponent(token)+'/files/'+fid+query);
    $("#shared-content").innerHTML =
      `<div class="shared-head"><span class="tag">${n.permission === "edit" ? tText("app:canBeEdited") : tText("app:readOnly")}</span><span class="small muted">${n.expires ? tText("app:accessTo", {value0:(date(n.expires))}) : tText("app:noExpiry")}</span></div><div class="page-cover ${n.cover&&!n.cover_url?'cover-'+esc(n.cover):''}" ${n.cover?'':'hidden'}>${pageCover(n)}</div>${coverAttribution(n)}<h1><span aria-hidden=true>${pageIcon(n)}</span> ${esc(n.title)}</h1><article class="markdown">${md(renderedBody)}</article><footer class="shared-footer">${tHtml("app:updatedDrgSite", {value0:(date(n.updated))})}</footer>`;
    $("#shared-content").classList.toggle("full-width", !!n.full_width);
    if(record){const back=document.createElement('a');back.className='text-link';back.href='/share/'+encodeURIComponent(token);back.textContent=tText("app:toDatabase");$('#shared-content').prepend(back);}
    if(n.database){
      const host=$('.markdown');host.innerHTML='';
      sharedHandle=mountDatabase(host,n,{api:async()=> (await api('/shared/'+encodeURIComponent(token))).database,state:{notes:[],dirty:true},toast,modal,openPage:id=>{history.pushState({},'','/share/'+encodeURIComponent(token)+'?record='+encodeURIComponent(id));sharedScreen();}},async()=>{});
    }else if(n.permission==='edit'){
      $('h1',$('#shared-content')).remove();
      sharedHandle=mountSharedEditor($('.markdown'),n,token,{api,toast,onDirty:value=>state.dirty=value});
    }else if (n.blocks?.length)
      sharedHandle = mountBlockEditor($(".markdown"), n, { readOnly: true, fileUrl:fid=>location.origin+'/api/shared/'+encodeURIComponent(token)+'/files/'+encodeURIComponent(fid)+query });
    safeMarkdownLinks();
    if (n.expires) {
      const left = n.expires * 1000 - Date.now();
      expiryTimer = setTimeout(
        () => {
          if (n.expires * 1000 > Date.now()) return sharedScreen();
          sharedHandle?.destroy();
          sharedHandle = null;
          $("#shared-content").innerHTML =
            `<h2>${tHtml("app:accessExpired")}</h2><p class="muted">${tHtml("app:theAuthorCanCreateANewLink")}</p>`;
        },
        Math.max(0, Math.min(left, 2147483647)),
      );
    }
  } catch (e) {
    if (e.status === 423) {
      $("#shared-content").innerHTML =
        `<span class="tag">${tHtml("app:privatePage")}</span><h1>${tHtml("app:passwordRequired")}</h1><p class="muted">${tHtml("app:enterThePasswordProvidedByTheAuthor")}</p><form id="unlock-form"><label>${tHtml("app:pagePassword")}<input name="password" type="password" autocomplete="off" required></label><button class="primary" type="submit">${tHtml("app:open")}</button></form>`;
      formBind("#unlock-form", async (f) => {
        await api("/shared/" + token + "/unlock", "POST", {
          password: f.get("password"),
        });
        await sharedScreen();
      });
    } else if (e.status === 401) {
      $("#shared-content").innerHTML =
        `<h1>${tHtml("app:signInToView")}</h1><p class="muted">${tHtml("app:thisPageIsOnlyAvailableToAuthorizedUsers")}</p><button id="share-login" class="primary">${tHtml("app:signIn2")}</button>`;
      $("#share-login").onclick = () => authScreen();
    } else if (e.status === 403 && state.me?.needs_enrollment) {
      enrollment();
    } else {
      $("#shared-content").innerHTML =
        `<h1>${tHtml("app:pageNotAvailable")}</h1><p class="muted">${esc(e.message)}</p>${e.status === 403
          ? `<button id="switch-account" class="secondary">${tHtml("app:signInWithAnotherAccount")}</button>`
          : ""}`;
      $("#switch-account")?.addEventListener("click", async () => {
        await logout();
        authScreen();
      });
    }
  }
}
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
async function start() {
  if(/^\/(share|f|expense-share|call|reset-password|invite)\//.test(location.pathname)){
    const bar=document.createElement('div');bar.className='public-language-bar';document.body.prepend(bar);mountPublicLanguage(bar);
  }
  void pushClient.refreshWorker();
  if(location.pathname.startsWith('/call/'))return mountGuestCall(root,location.pathname.split('/')[2],{brand:brand(),toast});
  try {
    try {
      state.me = await api('/me','GET',undefined,true,{}, {timeoutMs:15000});
      csrf = state.me.csrf;
    } catch (e) {
      if (e.status !== 401) throw e;
    }
    if(location.pathname.startsWith("/expense-share/"))return expenseSharedScreen();
    if(location.pathname.startsWith("/reset-password/"))return resetPasswordScreen();
    if (location.pathname.startsWith("/f/")) return fileSharedScreen();
    if (location.pathname.startsWith("/share/")) return sharedScreen();
    if (state.me) {
      if (!state.me.verified) return authScreen("2fa");
      if (state.me.needs_enrollment) return enrollment();
      if (location.pathname.startsWith('/invite/'))return workspaceInvitation();
      if (location.pathname.startsWith("/page/")) {
        state.view = "notes";
        state.selected = location.pathname.split("/")[2];
      }
      return loadWorkspace();
    }
    const s = await api("/status");
    registrationOpen=s.registration_open;
    authScreen(s.configured ? "login" : "setup");
  } catch (e) {
    root.innerHTML =
      `<main class="enroll"><div class="auth-card"><h2>${tHtml("app:failedToConnect")}</h2><p>${esc(e.message)}</p><button id="retry" class="primary">${tHtml("app:retry")}</button></div></main>`;
    $("#retry").onclick = start;
  }
}
async function expenseSharedScreen(){
  const token=location.pathname.split('/')[2],languageBar=document.querySelector('.public-language-bar');clearTimeout(expiryTimer);
  try{const e=await api('/shared-expenses/'+token);expiryTimer=setTimeout(()=>expenseSharedScreen(),Math.max(1000,Math.min(e.share_expires*1000-Date.now(),2147483647)));root.innerHTML=`<main class="expense-public"><header class="expense-public-header"><a class="brand" href="/">${brand()}</a><div data-expense-language></div></header><section class="panel"><h1>${esc(e.title)}</h1><p class="hint">${tHtml("app:linkIsAvailableUntil", {value0:(date(e.share_expires))})}</p>${expenseReport(e,false,token)}</section></main>`;if(languageBar)$('[data-expense-language]',root).append(languageBar);bindExpensePhotos(root,e,{token});}catch(e){root.innerHTML=`<main class="enroll"><section class="auth-card"><h2>${tHtml("app:billDetailsAreUnavailable")}</h2><p>${esc(e.message)}</p><a href="/">${tHtml("app:goToHome")}</a></section></main>`;if(languageBar)document.body.prepend(languageBar);}
}
function resetPasswordScreen(){
  const token=location.pathname.split('/')[2];
  root.innerHTML=`<main class="enroll"><section class="auth-card"><h2>${tHtml("app:newPassword")}</h2><p class="muted">${tHtml("app:thisIsASingleUseLinkChangingThe")}</p><form data-reset-password><label>${tHtml("app:newPassword")}<input name="password" type="password" minlength="12" maxlength="256" required autocomplete="new-password"></label><label>${tHtml("app:repeatPassword")}<input name="repeat" type="password" minlength="12" maxlength="256" required autocomplete="new-password"></label><label>${tHtml("app:hintOptional")}<input name="password_hint" maxlength="200" placeholder="${tHtml("app:doNotEnterThePasswordItself")}"></label><button class="primary" type="submit">${tHtml("app:changePassword")}</button></form></section></main>`;
  formBind($('[data-reset-password]'),async f=>{requireStrongPassword(f.get('password'));if(f.get('password')!==f.get('repeat'))throw new Error(tText("app:passwordsDoNotMatch"));await api('/password-reset','POST',{token,password:f.get('password'),password_hint:f.get('password_hint')});state.me=await api('/me');history.replaceState({},'', '/');enrollment();});
}
async function fileSharedScreen(){return mountSharedFiles(root,{...context(),login:()=>authScreen()},location.pathname.split('/')[2]);}
start();

async function workspaceInvitation(){
  const token=location.pathname.split('/')[2];
  // Registration already consumes a workspace invite; a newly created account
  // can proceed directly after completing enrollment.
  try{
    const data=await api('/social/invite-links/'+encodeURIComponent(token));
    root.innerHTML=`<main class="enroll"><div class="auth-card"><h2>${tHtml("app:invitationTo", {value0:(esc(data.name))})}</h2><p>${tHtml("app:youWillSignInAs", {value0:(esc(state.me.login))})}</p><button class="primary" data-join-workspace>${tHtml("app:acceptInvitation")}</button><button class="text-link" data-ignore-workspace>${tHtml("app:later")}</button><p class="error" role="alert" data-join-error></p></div></main>`;
    $('[data-join-workspace]').onclick=async e=>{e.target.disabled=true;try{await api('/social/invite-links/'+encodeURIComponent(token)+'/accept','POST');history.replaceState({},'', '/');state.view='social';restoredWorkspaceFor=state.me.id;await loadWorkspace();}catch(error){$('[data-join-error]').textContent=error.message;e.target.disabled=false;}};
    $('[data-ignore-workspace]').onclick=async()=>{history.replaceState({},'', '/');state.view='social';restoredWorkspaceFor=state.me.id;await loadWorkspace();};
  }catch(error){
    root.innerHTML=`<main class="enroll"><div class="auth-card"><h2>${tHtml("app:invitationNotAvailable")}</h2><p>${esc(error.message)}</p><button class="primary" data-invite-home>${tHtml("app:openTheApplication")}</button></div></main>`;
    $('[data-invite-home]').onclick=async()=>{history.replaceState({},'', '/');state.view='social';restoredWorkspaceFor=state.me.id;await loadWorkspace();};
  }
}

function accountWaiting(){
  disposeAppearance?.();disposeAppearance=null;disposeAdmin?.();disposeAdmin=null;
  dashboardController.destroy();notesController.destroy();tasksController.destroy();trackerController.destroy();chatController.destroy();familyPhotosController.destroy();callsController.destroy();inboxController.destroy();chatUnreadController.destroy();
  document.querySelectorAll('.modal-overlay').forEach(el=>el.remove());
  mountAccountDeletion(root,{api,state,loadWorkspace,logout,expired(){state.me=null;csrf='';authScreen('login',tText("app:sessionEndedSignInAgain"));}});
}
let checkingDeletion=false;
document.addEventListener('drg-account-locked',async()=>{
  if(!state.me||checkingDeletion||root.querySelector('[data-account-waiting]'))return;
  checkingDeletion=true;
  try{state.me=await api('/me');if(state.me.account_deletion)accountWaiting();}catch{}finally{checkingDeletion=false;}
});
