export class ApiError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}

export function createApi(initData) {
  async function post(path, fields = {}) {
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      credentials: 'same-origin',
      body: JSON.stringify({ ...fields, init_data: initData }),
    });
    const result = await response.json();
    if (!response.ok) throw new ApiError(response.status, result.error || 'request_failed');
    return result;
  }
  return { post };
}
