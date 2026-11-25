// Only read requests may be replayed while private storage is locked.
export function storageBusy(error) {
  return error?.status === 503 &&
    ['storage_busy', 'operationWithDataIsInProgressRetryLater'].includes(error.data?.code);
}

function retryDelay(value) {
  if (!value) return 5000;
  const seconds = /^\d+$/.test(value);
  const ms = seconds ? Number(value) * 1000 : Date.parse(value) - Date.now();
  if (!Number.isFinite(ms)) return 5000;
  return ms > 10000 ? null : Math.max(1000, ms);
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', cancel);
      resolve();
    }, ms);
    signal?.addEventListener('abort', cancel, {once:true});
    if (signal?.aborted) cancel();
  });
}

export async function readStorage(api, path, signal) {
  for (let attempt = 0; ; attempt++) {
    signal?.throwIfAborted();
    try {
      return await api(path, 'GET', undefined, true, {}, {signal, timeoutMs:15000});
    } catch (error) {
      if (!storageBusy(error) || attempt >= 4) throw error;
      const delay = retryDelay(error.retryAfter);
      if (delay === null) throw error;
      await wait(delay, signal);
    }
  }
}
