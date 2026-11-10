import {locale,tText} from "./i18n.js";
import {abortable} from './request.js';
// Only CSRF is readable by JavaScript. JWT access and refresh stay HttpOnly.
export function csrfCookie() {
  const item = document.cookie.split("; ").find((part) => part.startsWith("drg_csrf="));
  return item ? decodeURIComponent(item.slice("drg_csrf=".length)) : "";
}

let refreshPending;
export function refreshSession() {
  if (!refreshPending) {
    const update = async (signal) => {
      // Another tab may already have rotated the shared cookies while we waited.
      const me = await fetch("/api/me", { credentials: "same-origin", signal, headers:{"Accept-Language":locale()} });
      if (me.ok) return true;
      const csrf = csrfCookie();
      if (!csrf) return false;
      const response = await fetch("/api/refresh", {
        method: "POST", credentials: "same-origin", signal,
        headers: { "X-CSRF-Token": csrf, "Accept-Language":locale() },
      });
      return response.ok;
    };
    // Web Locks serializes refresh across tabs on browsers with this API.
    refreshPending = abortable(signal=>navigator.locks
      ? navigator.locks.request('drg-auth-refresh',{signal},()=>update(signal))
      : update(signal),{timeoutMs:30000,message:tText("common:sessionCouldNotBeRefreshedInTimeRetry")}).finally(()=>{refreshPending=null;});
  }
  return refreshPending;
}
