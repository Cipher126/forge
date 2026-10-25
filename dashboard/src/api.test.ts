import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, parseEvents, queryString, request } from './api';

afterEach(() => vi.unstubAllGlobals());
describe('authenticated requests', () => {
  it('sends the bearer key only in a header, without cookies or response caching', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response('{"items":[]}', { headers: { 'Content-Type': 'application/json' } }),
      );
    vi.stubGlobal('fetch', fetcher);
    await request('private-test-key', '/v1/jobs');
    const [url, options] = fetcher.mock.calls[0];
    expect(url).toBe('/api/v1/jobs');
    expect(options.headers.Authorization).toBe('Bearer private-test-key');
    expect(options.cache).toBe('no-store');
    expect(options.credentials).toBe('omit');
  });
  it('preserves retry metadata and structured validation details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'submission_capacity_exceeded',
              message: 'Try again',
              details: { limit: 6 },
            },
          }),
          { status: 503, headers: { 'Retry-After': '2' } },
        ),
      ),
    );
    await expect(request('key', '/v1/jobs')).rejects.toMatchObject({
      status: 503,
      code: 'submission_capacity_exceeded',
      retryAfter: '2',
      details: { limit: 6 },
    });
  });
  it('disconnects a revoked session on 401', async () => {
    const listener = vi.fn();
    window.addEventListener('forge:unauthorized', listener);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 401 })));
    await expect(request('revoked', '/v1/jobs')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener('forge:unauthorized', listener);
  });
  it('does not treat a successful HTML proxy fallback as API data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>wrong origin</html>')));
    await expect(request('key', '/v1/jobs')).rejects.toMatchObject({ code: 'invalid_response' });
  });
});
describe('resumable events', () => {
  it('retains a partial frame and emits each completed transition', () => {
    const deliver = vi.fn();
    const first = 'id: 15\nevent: job.transition\ndata: {"id":15,"job_id":"job-1",';
    const rest = parseEvents(first, deliver);
    expect(deliver).not.toHaveBeenCalled();
    expect(parseEvents(rest + '"to_status":"running"}\n\n', deliver)).toBe('');
    expect(deliver).toHaveBeenCalledWith(expect.objectContaining({ id: 15, to_status: 'running' }));
  });
  it('ignores heartbeat comments and malformed or unrelated events', () => {
    const deliver = vi.fn();
    parseEvents(
      ': keep-alive\n\nevent: other\ndata: {}\n\nevent: job.transition\ndata: not-json\n\n',
      deliver,
    );
    expect(deliver).not.toHaveBeenCalled();
  });
  it('handles CRLF and multiple complete frames', () => {
    const deliver = vi.fn();
    const frame = (id: number) =>
      `id: ${id}\r\nevent: job.transition\r\ndata: {"id":${id},"job_id":"j","to_status":"succeeded"}\r\n\r\n`;
    parseEvents(frame(1) + frame(2), deliver);
    expect(deliver).toHaveBeenCalledTimes(2);
  });
});
it('encodes user filters and omits empty query parameters', () => {
  expect(queryString({ type: 'a&b', status: '', cursor: undefined, limit: 25 })).toBe(
    '?type=a%26b&limit=25',
  );
});
