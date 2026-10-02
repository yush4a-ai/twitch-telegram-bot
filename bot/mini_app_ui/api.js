export class ApiError extends Error {
  constructor(status, code, data=null) { super(code); this.status = status; this.code = code; this.data=data; }
}

export function createApi(initData) {
  let user=null,storage;
  try{storage=window.localStorage;}catch{}
  const scopedStorage={
    getItem(key){try{return user?storage?.getItem(`ts-user:${user.id}:${key}`):null;}catch{return null;}},
    setItem(key,value){try{if(user)storage?.setItem(`ts-user:${user.id}:${key}`,value);}catch{}},
    removeItem(key){try{if(user)storage?.removeItem(`ts-user:${user.id}:${key}`);}catch{}},
  };
  async function post(path, fields = {}, options = {}) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      signal: options.signal,
      body: JSON.stringify({ ...fields, init_data: initData }),
    });
    const result = await response.json();
    if (!response.ok) throw new ApiError(response.status, result.error || 'request_failed', result);
    return result;
  }
  return {
    post,storage:scopedStorage,get user(){return user;},
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
