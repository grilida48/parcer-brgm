export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
const configured = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
export const apiBase = configured.endsWith('/api') ? configured : `${configured}/api`;
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${apiBase}${path}`, { ...options, headers, credentials: 'include' });
  if (!response.ok) { const data = await response.json().catch(() => ({})); const detail = data.detail; throw new ApiError(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item: any) => `${item.loc?.slice(1).join('.')}: ${item.msg}`).join('; ') : `Ошибка запроса (${response.status})`, response.status); }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export async function download(path: string, filename: string) {
  const response = await fetch(`${apiBase}${path}`, { credentials: 'include' });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || 'Не удалось экспортировать данные'); }
  const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
