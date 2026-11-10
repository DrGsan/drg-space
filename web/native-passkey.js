import {translateStaticPage,locale} from './core/i18n.js';
import {mountPublicLanguage} from './modules/localization/index.js';
translateStaticPage();mountPublicLanguage(document.querySelector('main'));
import {tText} from "./core/i18n.js";
const button = document.querySelector('#continue');
const status = document.querySelector('#status');
document.querySelector('#server').textContent = location.host;
const flow = new URL(location.href).searchParams.get('flow');
const decode = value => Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));
const encode = value => btoa(String.fromCharCode(...new Uint8Array(value))).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
async function post(path, body) {
  const response = await fetch(path, {method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(30000),
    headers: {'Accept-Language':locale(),'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : tText("auth:failedToConfirmLogin"));
  return data;
}
if (!flow || !/^[A-Za-z0-9_-]{43}$/.test(flow)) {
  button.disabled = true;
  status.textContent = tText("auth:signInUsingPasskeyFromTheDrgApp");
} else button.onclick = async () => {
  button.disabled = true;
  status.textContent = tText("auth:confirmSignInOnYourDevice");
  try {
    if (!window.isSecureContext || !navigator.credentials?.get) throw new Error(tText("auth:aSecureWebsiteAndKeyAccessAreRequired"));
    const start = await post('/api/passkeys/options', {});
    const credential = await navigator.credentials.get({publicKey: {...start.options, challenge: decode(start.options.challenge)}});
    if (!credential) throw new Error(tText("auth:confirmationCancelled"));
    const proof = {id: credential.id, rawId: encode(credential.rawId), type: credential.type,
      response: {clientDataJSON: encode(credential.response.clientDataJSON),
        authenticatorData: encode(credential.response.authenticatorData), signature: encode(credential.response.signature),
        userHandle: credential.response.userHandle ? encode(credential.response.userHandle) : null},
      clientExtensionResults: credential.getClientExtensionResults?.() || {}};
    const result = await post('/api/passkeys/verify', {credential: proof, csrf: start.ceremony_csrf, native_flow: flow});
    const callback = new URL('drg-family://auth');
    callback.searchParams.set('code', result.native.code);
    callback.searchParams.set('state', result.native.state);
    status.textContent = tText("auth:returningToTheApp");
    location.replace(callback.href);
  } catch (error) {
    status.textContent = error.name === 'NotAllowedError' ? tText("auth:confirmationCancelledOrKeyNotFoundTryAgain") : error.message;
    button.disabled = false;
  }
};
