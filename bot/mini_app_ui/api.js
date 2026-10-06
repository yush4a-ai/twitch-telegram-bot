export class ApiError extends Error {
  constructor(status, code, data=null) { super(code); this.status = status; this.code = code; this.data=data; }
}

export function createApi(initData, {onAuthExpired=null, timeoutMs=10000}={}) {
  let user=null,storage,authExpired=false;
  try{storage=window.localStorage;}catch{}
  const scopedStorage={
    getItem(key){try{return user?storage?.getItem(`ts-user:${user.id}:${key}`):null;}catch{return null;}},
    setItem(key,value){try{if(user)storage?.setItem(`ts-user:${user.id}:${key}`,value);}catch{}},
    removeItem(key){try{if(user)storage?.removeItem(`ts-user:${user.id}:${key}`);}catch{}},
  };
  async function post(path, fields = {}, options = {}) {
    // Единый таймаут: без него запрос на плохой сети не завершается, и экран
    // навсегда остаётся в состоянии «Загружаем…».
    const limit = Number.isFinite(options.timeout) ? options.timeout : timeoutMs;
    const controller = new AbortController();
    const external = options.signal;
    const relay = () => controller.abort();
    if (external) {
      if (external.aborted) controller.abort();
      else external.addEventListener('abort', relay, {once: true});
    }
    const timer = setTimeout(() => controller.abort(), limit);
    try {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
        body: JSON.stringify({ ...fields, init_data: initData }),
      });
      let result;
      try { result = await response.json(); }
      catch { result = { error: 'invalid_response' }; }
      // Подпись Telegram живёт 10 минут: и «нет подписи» (401), и «подпись не
      // прошла проверку» (403 с error=unauthorized) означают одно — приложению
      // нужно перезагрузиться за свежей подписью. Иначе человек после долгой
      // оплаты видел «нет связи» вместо выданного доступа.
      const authExpiredResponse = response.status === 401
        || (response.status === 403 && result && result.error === 'unauthorized');
      if (authExpiredResponse) {
        if (!authExpired && typeof onAuthExpired === 'function') {
          authExpired = true;
          onAuthExpired();
        }
        throw new ApiError(response.status, result.error || 'unauthorized', result);
      }
      if (!response.ok) throw new ApiError(response.status, result.error || 'request_failed', result);
      return result;
    } catch (cause) {
      // Таймаут, обрыв связи и ответ сервера — разные причины с разными
      // текстами и разным решением о повторе: их различает таксономия сбоев.
      if (cause instanceof ApiError) throw cause;
      if (cause && cause.name === 'AbortError') {
        throw new ApiError(0, external?.aborted ? 'aborted' : 'timeout');
      }
      throw new ApiError(0, 'network');
    } finally {
      clearTimeout(timer);
      if (external) external.removeEventListener('abort', relay);
    }
  }
  return {
    post,storage:scopedStorage,get user(){return user;},
    get authExpired(){return authExpired;},
    bindIdentity(identity){
      if(!identity||!Number.isSafeInteger(identity.id)||identity.id<=0)throw new TypeError('Verified server identity required');
      try{
        const previous=storage?.getItem('ts-app-last-user');
        if(previous&&previous!==String(identity.id)){const prefix=`ts-user:${previous}:`;for(const key of Object.keys(storage||{}))if(key.startsWith(prefix))storage.removeItem(key);}
        for(const key of Object.keys(storage||{}))if(key==='ts-app-search-draft'||key==='ts-streamer-connect-intent'||key==='ts-streamer-community-intent'||key.startsWith('ts-streamer-post-draft:')||key.startsWith('ts-streamer-template-'))storage.removeItem(key);
        storage?.setItem('ts-app-last-user',String(identity.id));
      }catch{}
      user=Object.freeze({...identity});
    },
  };
}
