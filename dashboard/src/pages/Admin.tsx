import { useState } from 'react';
import type { FormEvent } from 'react';
import { Check, Copy, Cpu, KeyRound, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryString, request } from '../api';
import type { Identity, Schema } from '../api';
import { useAccess, useSession } from '../session';
import { useDialog } from '../dialog';
import {
  Confirm,
  date,
  Empty,
  ErrorBox,
  JsonView,
  Loading,
  number,
  PageHeading,
  Panel,
  Status,
} from '../components';

export function Workers() {
  const { token } = useAccess();
  const workers = useQuery({
    queryKey: ['workers'],
    queryFn: () =>
      request<Schema<'WorkerHealthListResponse'>>(token, '/v1/admin/operations/workers'),
  });
  const queues = useQuery({
    queryKey: ['queues'],
    queryFn: () => request<Schema<'QueueStatistics'>>(token, '/v1/admin/operations/queues'),
  });
  return (
    <>
      <PageHeading
        label="INFRASTRUCTURE / WORKERS & QUEUES"
        title="Behind every execution."
        description="Worker heartbeats, active attempt ownership, and delivery queues."
      />
      <ErrorBox error={workers.error || queues.error} />
      <div className="stats-grid three">
        {['high', 'normal', 'low'].map((priority) => (
          <div className="stat-card" key={priority}>
            <div className="stat-label">
              {priority} priority queue
              <LayersIcon />
            </div>
            <strong>{number(queues.data?.queued_by_priority[priority])}</strong>
            <small>Redis messages waiting for delivery</small>
          </div>
        ))}
      </div>
      <Panel
        title="Worker health"
        eyebrow="LIVE HEARTBEATS"
        action={<span className="tiny-badge">{workers.data?.items.length || 0} DETECTED</span>}
      >
        {workers.isPending ? (
          <Loading />
        ) : workers.data?.items.length ? (
          <div className="worker-grid">
            {workers.data.items.map((worker) => (
              <article className="worker-card" key={worker.worker_id}>
                <div className="worker-card-top">
                  <span className="section-icon">
                    <Cpu size={20} />
                  </span>
                  <Status value={worker.status} />
                </div>
                <h3 className="mono worker-id">{worker.worker_id}</h3>
                <dl>
                  <div>
                    <dt>Active attempts</dt>
                    <dd>{worker.active_attempts}</dd>
                  </div>
                  <div>
                    <dt>Reserved deliveries</dt>
                    <dd>{worker.reserved_messages}</dd>
                  </div>
                  <div>
                    <dt>Heartbeat expires in</dt>
                    <dd>{worker.heartbeat_ttl_seconds}s</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        ) : (
          <Empty title="No recent worker heartbeats">
            Check that workers are running and can reach Redis. Lost workers disappear after
            heartbeat expiry; attempt leases govern recovery.
          </Empty>
        )}
      </Panel>
      <div className="info-strip">
        <ShieldCheck size={20} />
        <p>
          <strong>A heartbeat shows presence. A lease grants ownership.</strong> Workers reserve
          deliveries in Redis, then claim attempts in PostgreSQL. Redis message counts can differ
          from the number of durable jobs.
        </p>
      </div>
      <Panel title="Queue accounting">
        <dl className="accounting">
          <div>
            <dt>Durable outstanding jobs</dt>
            <dd>{number(queues.data?.durable_outstanding)}</dd>
          </div>
          <div>
            <dt>Queued delivery messages</dt>
            <dd>{number(queues.data?.queued_total)}</dd>
          </div>
          <div>
            <dt>Reserved delivery messages</dt>
            <dd>{number(queues.data?.processing_total)}</dd>
          </div>
        </dl>
      </Panel>
    </>
  );
}
function LayersIcon() {
  return <span className="queue-glyph">≋</span>;
}

export function Handlers() {
  const { token } = useAccess();
  const handlers = useQuery({
    queryKey: ['handlers'],
    queryFn: () => request<Schema<'HandlerListResponse'>>(token, '/v1/handlers'),
    refetchInterval: false,
  });
  return (
    <>
      <PageHeading
        label="EXECUTION / HANDLER REGISTRY"
        title="Known work. Explicit versions."
        description="Only deployed, registered handlers can execute. Payloads are validated against these schemas."
      />
      <ErrorBox error={handlers.error} />
      {handlers.isPending ? (
        <Loading />
      ) : !handlers.data?.items.length ? (
        <Empty title="No handlers registered" />
      ) : (
        <div className="handler-grid">
          {handlers.data.items.map((handler) => (
            <Panel
              key={`${handler.job_type}:${handler.version}`}
              title={handler.job_type}
              action={
                <span className={`tiny-badge ${handler.active ? 'green' : ''}`}>
                  {handler.active ? 'ACTIVE' : 'RETAINED'}
                </span>
              }
            >
              <div className="handler-meta">
                <span>
                  Version <b>{handler.version}</b>
                </span>
                <span>
                  Mode <b>{handler.execution_mode}</b>
                </span>
                <span>
                  Timeout <b>{handler.timeout_seconds}s</b>
                </span>
                <span>
                  Attempts <b>{handler.default_max_attempts}</b>
                </span>
              </div>
              <details>
                <summary>Payload JSON schema</summary>
                <JsonView value={handler.payload_schema} />
              </details>
              {handler.job_type === 'demo.generate_report' && (
                <p className="panel-note">
                  Demonstration handler: returns JSON and a suggested report path, not a generated
                  file.
                </p>
              )}
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}

export function Keys() {
  const { token } = useAccess();
  const { session, disconnect } = useSession();
  const client = useQueryClient();
  const [cursor, setCursor] = useState('');
  const [creating, setCreating] = useState(false);
  const [revoke, setRevoke] = useState<Identity | null>(null);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState(['jobs:read', 'jobs:write']);
  const [expires, setExpires] = useState('');
  const [rate, setRate] = useState(1000);
  const [copied, setCopied] = useState(false);
  const keys = useQuery({
    queryKey: ['keys', cursor],
    queryFn: () =>
      request<Schema<'ApiKeyListResponse'>>(
        token,
        `/v1/admin/api-keys${queryString({ cursor, limit: 25 })}`,
      ),
  });
  const create = useMutation({
    mutationFn: () =>
      request<Schema<'CreateApiKeyResponse'>>(token, '/v1/admin/api-keys', {
        method: 'POST',
        body: JSON.stringify({
          name,
          scopes,
          expires_at: expires ? new Date(expires).toISOString() : null,
          rate_limit_requests: rate,
          rate_limit_window_s: 60,
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['keys'] });
    },
  });
  const revocation = useMutation({
    mutationFn: (key: Identity) =>
      request<Identity>(token, `/v1/admin/api-keys/${key.id}/revoke`, { method: 'POST' }),
    onSuccess: (key) => {
      setRevoke(null);
      void client.invalidateQueries({ queryKey: ['keys'] });
      if (key.id === session?.identity.id) disconnect();
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }
  function close() {
    if (!create.isPending) {
      setCreating(false);
      create.reset();
      setCopied(false);
      setName('');
    }
  }
  const dialog = useDialog(creating, close);
  return (
    <>
      <PageHeading
        label="ACCESS / API KEYS"
        title="The right key. The right scope."
        description="Issue credentials for clients and operators. Forge stores a digest, never the raw key."
        action={
          <button className="primary" onClick={() => setCreating(true)}>
            <Plus size={16} />
            Create key
          </button>
        }
      />
      <ErrorBox error={keys.error || revocation.error} />
      <Panel title="Workspace credentials" eyebrow="ACCESS CONTROL">
        {keys.isPending ? (
          <Loading />
        ) : keys.data?.items.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Key name</th>
                  <th>Scopes</th>
                  <th>Status</th>
                  <th>Quota / minute</th>
                  <th>Expires</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {keys.data.items.map((key) => (
                  <tr key={key.id}>
                    <td>
                      <strong>{key.name}</strong>
                      <small className="mono">
                        {key.public_id}
                        {key.id === session?.identity.id ? ' · current session' : ''}
                      </small>
                    </td>
                    <td>
                      <div className="scope-list">
                        {key.scopes.map((scope) => (
                          <code key={scope}>{scope}</code>
                        ))}
                      </div>
                    </td>
                    <td>
                      <Status
                        value={
                          !key.enabled
                            ? 'revoked'
                            : key.expires_at && new Date(key.expires_at) <= new Date()
                              ? 'expired'
                              : 'active'
                        }
                      />
                    </td>
                    <td className="mono">
                      {key.rate_limit_requests} / {key.rate_limit_window_s}s
                    </td>
                    <td>{date(key.expires_at)}</td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`Revoke ${key.name}`}
                        disabled={!key.enabled}
                        onClick={() => setRevoke(key)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No API keys found" />
        )}
        <div className="pagination">
          <span>Credentials are shown without their secrets.</span>
          <div className="button-row">
            <button disabled={!cursor} onClick={() => setCursor('')}>
              First page
            </button>
            <button
              disabled={!keys.data?.next_cursor}
              onClick={() => setCursor(keys.data!.next_cursor!)}
            >
              Next page
            </button>
          </div>
        </div>
      </Panel>
      {creating && (
        <div className="modal-shade">
          <section
            ref={dialog}
            className="modal panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="key-title"
          >
            <span className="section-icon">
              <KeyRound size={20} />
            </span>
            <h2 id="key-title">{create.data ? 'Save your new key' : 'Create an API key'}</h2>
            {create.data ? (
              <>
                <p>This credential is shown once. Store it securely before closing.</p>
                <pre className="secret-value" data-testid="new-api-key">
                  {create.data.api_key}
                </pre>
                <div className="button-row">
                  <button
                    onClick={() => {
                      void navigator.clipboard
                        .writeText(create.data!.api_key)
                        .then(() => setCopied(true))
                        .catch(() => setCopied(false));
                    }}
                  >
                    {copied ? <Check size={15} /> : <Copy size={15} />}Copy key
                  </button>
                  <button className="primary" onClick={close}>
                    I saved it
                  </button>
                </div>
              </>
            ) : (
              <form className="stack-form" onSubmit={submit}>
                <label>
                  Name
                  <input
                    autoFocus
                    value={name}
                    maxLength={100}
                    onChange={(event) => setName(event.target.value)}
                    required
                    placeholder="e.g. reporting-service"
                  />
                </label>
                <fieldset>
                  <legend>Scopes</legend>
                  {['jobs:read', 'jobs:write', 'admin'].map((scope) => (
                    <label className="checkbox" key={scope}>
                      <input
                        type="checkbox"
                        checked={scopes.includes(scope)}
                        onChange={(event) =>
                          setScopes(
                            event.target.checked
                              ? [...scopes, scope]
                              : scopes.filter((value) => value !== scope),
                          )
                        }
                      />
                      {scope}
                    </label>
                  ))}
                </fieldset>
                {scopes.includes('admin') && (
                  <small>
                    Admin grants access to all jobs, operational data and credential management.
                  </small>
                )}
                <label>
                  Requests per 60 seconds
                  <input
                    type="number"
                    min={1}
                    max={1000000}
                    value={rate}
                    onChange={(event) => setRate(Number(event.target.value))}
                    required
                  />
                </label>
                <label>
                  Expires at (optional, your local time)
                  <input
                    type="datetime-local"
                    value={expires}
                    onChange={(event) => setExpires(event.target.value)}
                  />
                </label>
                <ErrorBox error={create.error} />
                <div className="button-row">
                  <button className="primary" disabled={create.isPending || !scopes.length}>
                    {create.isPending ? 'Creating…' : 'Create key'}
                  </button>
                  <button type="button" onClick={close} disabled={create.isPending}>
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </section>
        </div>
      )}
      {revoke && (
        <Confirm
          title={`Revoke ${revoke.name}?`}
          description={
            revoke.id === session?.identity.id
              ? 'This is your current key. Revoking it disconnects this session and every client using it.'
              : 'Clients using this key will lose access. Existing accepted jobs remain in Forge.'
          }
          pending={revocation.isPending}
          error={revocation.error}
          onClose={() => {
            if (!revocation.isPending) setRevoke(null);
          }}
          onConfirm={() => revocation.mutate(revoke)}
        />
      )}
    </>
  );
}

export function Audit() {
  const { token } = useAccess();
  const [cursor, setCursor] = useState<number | undefined>();
  const [action, setAction] = useState('');
  const audit = useQuery({
    queryKey: ['audit', cursor, action],
    queryFn: () =>
      request<Schema<'AuditEventListResponse'>>(
        token,
        `/v1/admin/audit-events${queryString({ cursor, action, limit: 25 })}`,
      ),
  });
  return (
    <>
      <PageHeading
        label="ACCESS / AUDIT HISTORY"
        title="Actions leave a record."
        description="A durable account of sensitive operations, their actors, and affected resources."
      />
      <Panel>
        <div className="filter-bar">
          <label>
            Action
            <select
              value={action}
              onChange={(event) => {
                setAction(event.target.value);
                setCursor(undefined);
              }}
            >
              <option value="">All actions</option>
              {[
                'api_key.created',
                'api_key.revoked',
                'job.cancelled',
                'job.cancellation_requested',
                'job.replayed',
              ].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>
        <ErrorBox error={audit.error} />
        {audit.isPending ? (
          <Loading />
        ) : audit.data?.items.length ? (
          <div className="audit-list">
            {audit.data.items.map((event) => (
              <article key={event.id}>
                <span className="audit-icon">
                  <ShieldCheck size={17} />
                </span>
                <div>
                  <div className="audit-top">
                    <strong>{event.action}</strong>
                    <time>{date(event.created_at)}</time>
                  </div>
                  <p>
                    <code>{event.actor_public_id}</code> → {event.resource_type}{' '}
                    <code>{event.resource_id}</code>
                  </p>
                  <details>
                    <summary>Details</summary>
                    <JsonView value={event.details} />
                  </details>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty title="No audit events in this view">
            Key administration and job control actions appear here.
          </Empty>
        )}
        <div className="pagination">
          <span>Newest events first</span>
          <div className="button-row">
            <button disabled={cursor === undefined} onClick={() => setCursor(undefined)}>
              First page
            </button>
            <button
              disabled={audit.data?.next_cursor == null}
              onClick={() => setCursor(audit.data!.next_cursor!)}
            >
              Next page
            </button>
          </div>
        </div>
      </Panel>
    </>
  );
}
