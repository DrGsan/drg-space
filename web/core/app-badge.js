// An icon badge never requests notification permission. Unsupported devices and
// denied permissions must not interfere with opening or reading messages.
export function createAppBadge(env = globalThis) {
  let pending = Promise.resolve(), last;
  return {
    set(count, {force = false} = {}) {
      if (!Number.isSafeInteger(count) || count < 0) return pending;
      const nav = env.navigator;
      if (!nav?.setAppBadge || !nav?.clearAppBadge) return pending;
      if (!force && last === count) return pending;
      last = count;
      pending = pending.then(async () => {
        try {
          if (count) await nav.setAppBadge(count);
          else await nav.clearAppBadge();
        } catch { if (last === count) last = undefined; }
      });
      return pending;
    },
  };
}

export const appBadge = createAppBadge();
