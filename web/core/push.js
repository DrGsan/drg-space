import {locale,tText} from "./i18n.js";
const workerPath = '/push-sw.js';

export function pushSupport(env = globalThis) {
  const nav = env.navigator || {};
  const ios = /iPad|iPhone|iPod/.test(nav.userAgent || '') ||
    (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1);
  const installed = !!nav.standalone || !!env.matchMedia?.('(display-mode: standalone)').matches;
  if (!env.isSecureContext) return {supported: false, reason: 'insecure'};
  if (ios && !installed) return {supported: false, reason: 'install'};
  if (!nav.serviceWorker || !env.PushManager || !env.Notification)
    return {supported: false, reason: 'unsupported'};
  return {supported: true, reason: 'ready'};
}

export function pushSupportMessage(reason) {
  return {
    insecure: tText("common:notificationsAreAvailableWhenConnectingViaHttps"),
    install: tText("common:onIphoneAndIpadAddTheWebsiteTo"),
    unsupported: tText("common:thisBrowserDoesNotSupportNotificationsWhenThe"),
  }[reason] || '';
}

export function applicationServerKey(value, env = globalThis) {
  const encoded = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
  const decoded = env.atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '='));
  const bytes = Uint8Array.from(decoded, char => char.charCodeAt(0));
  if (bytes.length !== 65 || bytes[0] !== 4) throw new Error(tText("common:theServerReturnedAnInvalidNotificationKey"));
  return bytes;
}

export function chatFromPushUrl(value, origin) {
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin || url.pathname !== '/' || url.search) return null;
    const match = /^#chat\/((?:[A-Za-z0-9_-]{1,128}|telegram:(?:-\d{1,20}|export:[a-f0-9]{8,24})))$/.exec(url.hash);
    return match ? match[1] : null;
  } catch { return null; }
}

export function familyFromPushUrl(value,origin){
  try{
    const url=new URL(value,origin);
    if(url.origin!==origin||url.pathname!=='/'||url.search)return null;
    return /^#admin\/family-photos\/(-\d{1,20}|export:[a-f0-9]{8,24})$/.exec(url.hash)?.[1]||null;
  }catch{return null;}
}

function timeout(promise, milliseconds = 10000) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(tText("common:unableToConnectNotificationsTryAgain"))), milliseconds);
  })]).finally(() => clearTimeout(timer));
}

// Subscriptions belong to this browser installation and to a server session.
// Merely opening the site never grants permission or binds another account.
export function createPushClient({api, env = globalThis}) {
  let config;
  let preparedRegistration, knownSubscription;
  async function findRegistration() {
    if (!env.navigator?.serviceWorker) return null;
    const registration = await env.navigator.serviceWorker.getRegistration('/');
    const worker = registration?.active || registration?.waiting || registration?.installing;
    if (!worker) return null;
    return new URL(worker.scriptURL, env.location.origin).pathname === workerPath ? registration : null;
  }
  async function prepare() {
    const registration = await env.navigator.serviceWorker.register(workerPath, {scope: '/', updateViaCache: 'none'});
    if (registration.active) return registration;
    const ready = await timeout(env.navigator.serviceWorker.ready);
    if (new URL(ready.active.scriptURL, env.location.origin).pathname !== workerPath)
      throw new Error(tText("common:unableToStartTheNotificationHandler"));
    return ready;
  }
  async function state() {
    const support = pushSupport(env);
    config = await api('/push');
    let registration = await findRegistration();
    if (config.configured && support.supported && env.Notification.permission !== 'denied')
      registration = await prepare();
    const subscription = await registration?.pushManager.getSubscription();
    preparedRegistration=registration;knownSubscription=subscription;
    const saved = subscription ? await api('/push/status', 'POST', {endpoint: subscription.endpoint,language:locale().split('-')[0]}) : null;
    return {
      ...support,
      configured: !!config.configured,
      permission: env.Notification?.permission || 'default',
      enabled: !!config.configured && !!saved?.subscribed && env.Notification?.permission === 'granted',
      hasSubscription: !!subscription,
    };
  }
  async function enable() {
    const support = pushSupport(env);
    if (!support.supported) throw new Error(pushSupportMessage(support.reason));
    if (!config?.configured) throw new Error(tText("common:notificationsForMessagesHaveNotYetBeenConfigured"));
    // Safari requires requestPermission directly inside the user's click handler,
    // before any network or service-worker await.
    const permission = env.Notification.permission === 'default'
      ? await env.Notification.requestPermission() : env.Notification.permission;
    if (permission !== 'granted') throw new Error(tText("common:enableNotificationsForTheDrgSiteInYour"));
    const registration = preparedRegistration;
    if(!registration?.active)throw new Error(tText("common:theNotificationHandlerIsNotYetReadyOpen"));
    let subscription = knownSubscription;
    let created = false;
    const key = applicationServerKey(config.public_key, env);
    const previousKey = subscription?.options?.applicationServerKey;
    if (previousKey && !sameBytes(previousKey, key)) {
      await api('/push/subscriptions', 'DELETE', {endpoint: subscription.endpoint});
      if (!(await subscription.unsubscribe())) throw new Error(tText("common:failedToUpdateNotificationSubscription"));
      subscription = null;
    }
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
      created = true;
    }
    try {
      await api('/push/subscriptions', 'POST', {
        ...subscription.toJSON(), language:locale().split('-')[0], device_name: deviceName(env.navigator),
      });
      knownSubscription=subscription;
    } catch (error) {
      if (created) await subscription.unsubscribe().catch(() => {});
      throw error;
    }
  }
  async function disable() {
    const registration = await findRegistration();
    const subscription = await registration?.pushManager.getSubscription();
    if (!subscription) return;
    let failure;
    try { await api('/push/subscriptions', 'DELETE', {endpoint: subscription.endpoint}); }
    catch (error) { failure = error; }
    if (!(await subscription.unsubscribe())) failure ||= new Error(tText("common:failedToDisableNotificationsOnThisDevice"));
    await closeNotifications(registration);
    if (failure) throw failure;
  }
  async function logout() {
    config = null;
    knownSubscription=null;preparedRegistration=null;
    // The logout API also revokes session-bound endpoints. Browser cleanup is
    // best effort, and a disconnected push service must not prevent logout.
    try {
      const registration = await timeout(findRegistration(), 3000);
      const subscription = await timeout(Promise.resolve(registration?.pushManager.getSubscription()), 3000);
      if (subscription) {
        try { await timeout(api('/push/subscriptions', 'DELETE', {endpoint: subscription.endpoint}), 3000); } catch {}
        try { await timeout(subscription.unsubscribe(), 3000); } catch {}
      }
      await closeNotifications(registration);
    } catch {}
  }
  async function test(){
    const registration=await findRegistration();
    const subscription=await registration?.pushManager.getSubscription();
    if(!subscription)throw new Error(tText("common:firstEnableNotificationsOnThisDevice"));
    return api('/push/test','POST',{endpoint:subscription.endpoint});
  }
  async function refreshWorker() {
    // Update an existing installation without asking permission or subscribing.
    try { await (await findRegistration())?.update(); } catch {}
  }
  async function syncLanguage() {
    const registration=await findRegistration();
    const subscription=await registration?.pushManager.getSubscription();
    if(subscription)await api('/push/status','POST',{endpoint:subscription.endpoint,language:locale().split('-')[0]});
  }
  return {state, enable, disable, logout, test, refreshWorker, syncLanguage};
}

function sameBytes(first, second) {
  const bytes = new Uint8Array(first);
  return bytes.length === second.length && bytes.every((value, index) => value === second[index]);
}
function deviceName(nav = {}) {
  if (/iPad/.test(nav.userAgent || '') || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1)) return 'iPad';
  if (/iPhone|iPod/.test(nav.userAgent || '')) return 'iPhone';
  if (/Android/.test(nav.userAgent || '')) return 'Android';
  if (/Mac/.test(nav.platform || '')) return 'Mac';
  if (/Win/.test(nav.platform || '')) return 'Windows';
  return tText("common:browser");
}
async function closeNotifications(registration) {
  try {
    const notifications = await registration?.getNotifications?.();
    for (const notification of notifications || []) notification.close();
  } catch {}
}
