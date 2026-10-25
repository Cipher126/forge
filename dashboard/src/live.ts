import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { parseEvents } from './api';
import type { JobEvent } from './api';

export function useLiveEvents(token: string, enabled: boolean) {
  const client = useQueryClient();
  const [status, setStatus] = useState('Connecting');
  const [events, setEvents] = useState<JobEvent[]>([]);
  useEffect(() => {
    if (!enabled) return;
    setEvents([]);
    const controller = new AbortController();
    let cursor: number | undefined;
    let failures = 0;
    let dirty = false;
    let wake: ReturnType<typeof setTimeout> | undefined;
    const refresh = setInterval(() => {
      if (dirty && !document.hidden) {
        dirty = false;
        void client.invalidateQueries({
          predicate: (query) => !['identity', 'handlers'].includes(String(query.queryKey[0])),
        });
      }
    }, 15000);
    async function stream() {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(
            `/api/v1/admin/operations/events${cursor === undefined ? '' : `?after=${cursor}`}`,
            {
              headers: { Authorization: `Bearer ${token}` },
              credentials: 'omit',
              cache: 'no-store',
              signal: controller.signal,
            },
          );
          if (response.status === 401) {
            window.dispatchEvent(new Event('forge:unauthorized'));
            return;
          }
          if (
            !response.ok ||
            !response.body ||
            !response.headers.get('Content-Type')?.includes('text/event-stream')
          )
            throw new Error('Stream unavailable');
          setStatus('Live');
          failures = 0;
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          try {
            while (!controller.signal.aborted) {
              const { value, done } = await reader.read();
              if (done) break;
              buffer = parseEvents(buffer + decoder.decode(value, { stream: true }), (event) => {
                if (cursor !== undefined && event.id <= cursor) return;
                cursor = event.id;
                dirty = true;
                setEvents((items) => [event, ...items].slice(0, 20));
              });
              if (buffer.length > 1024 * 1024) throw new Error('Stream frame too large');
            }
          } finally {
            await reader.cancel().catch(() => {});
            reader.releaseLock();
          }
        } catch {
          /* Fall back to the query polling interval during disconnection. */
        }
        if (controller.signal.aborted) return;
        setStatus('Polling · reconnecting');
        failures++;
        await new Promise<void>((resolve) => {
          const finish = () => {
            controller.signal.removeEventListener('abort', finish);
            resolve();
          };
          wake = setTimeout(finish, Math.min(30000, 1000 * 2 ** Math.min(failures, 5)));
          controller.signal.addEventListener('abort', finish, { once: true });
        });
      }
    }
    void stream();
    return () => {
      controller.abort();
      clearInterval(refresh);
      clearTimeout(wake);
    };
  }, [token, enabled, client]);
  return { status: enabled ? status : 'Polling', events };
}
