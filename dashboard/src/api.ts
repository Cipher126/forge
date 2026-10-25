import type { components } from './generated/api';

export type Schema<T extends keyof components['schemas']> = components['schemas'][T];
export type Job = Schema<'JobResponse'>;
export type Identity = Schema<'ApiKeyResponse'>;
export type JobEvent = {
  id: number;
  job_id: string;
  to_status: string;
  from_status: string | null;
  reason: string;
  created_at: string;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public retryAfter?: string | null,
  ) {
    super(message);
  }
}

export async function request<T>(token: string, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    cache: 'no-store',
    credentials: 'omit',
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
      Authorization: `Bearer ${token}`,
    },
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('forge:unauthorized'));
    const error = data?.error;
    throw new ApiError(
      response.status,
      error?.code || `http_${response.status}`,
      error?.message || `Request failed (${response.status})`,
      error?.details || data?.detail,
      response.headers.get('Retry-After'),
    );
  }
  if (!data || typeof data !== 'object')
    throw new ApiError(
      502,
      'invalid_response',
      'The API returned an unexpected response. Check the proxy connection.',
    );
  return data as T;
}

export function queryString(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value));
  });
  return params.size ? `?${params}` : '';
}

/** Parse chunk boundaries, comments and multi-line SSE data without putting keys in URLs. */
export function parseEvents(buffer: string, deliver: (event: JobEvent) => void): string {
  const normalized = buffer.replace(/\r\n/g, '\n');
  const records = normalized.split('\n\n');
  const rest = records.pop() || '';
  for (const record of records) {
    const lines = record.split('\n');
    if (!lines.some((line) => line === 'event: job.transition')) continue;
    const data = lines
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');
    try {
      const event = JSON.parse(data) as JobEvent;
      if (
        Number.isSafeInteger(event.id) &&
        typeof event.job_id === 'string' &&
        typeof event.to_status === 'string'
      )
        deliver(event);
    } catch {
      /* Ignore a malformed event; polling remains authoritative. */
    }
  }
  return rest;
}
