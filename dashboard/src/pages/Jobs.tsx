import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowLeft, ArrowRight, Check, Copy, Plus, RotateCcw, Search, XCircle } from 'lucide-react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryString, request } from '../api';
import type { Job, Schema } from '../api';
import { useAccess } from '../session';
import {
  Confirm,
  date,
  Empty,
  ErrorBox,
  JobTable,
  JsonView,
  Loading,
  PageHeading,
  Panel,
  Status,
} from '../components';

const statuses = [
  'scheduled',
  'queued',
  'running',
  'retry_scheduled',
  'cancellation_requested',
  'succeeded',
  'failed',
  'dead_lettered',
  'cancelled',
];
const terminal = new Set(['succeeded', 'failed', 'dead_lettered', 'cancelled']);

export function Jobs() {
  const { token, canWrite } = useAccess();
  const [params, setParams] = useSearchParams();
  const [lookup, setLookup] = useState('');
  const navigate = useNavigate();
  const filter = {
    status: params.get('status') || '',
    priority: params.get('priority') || '',
    type: params.get('type') || '',
    cursor: params.get('cursor') || '',
  };
  const jobs = useQuery({
    queryKey: ['jobs', filter],
    queryFn: () =>
      request<Schema<'JobListResponse'>>(token, `/v1/jobs${queryString({ ...filter, limit: 25 })}`),
  });
  const handlers = useQuery({
    queryKey: ['handlers'],
    queryFn: () => request<Schema<'HandlerListResponse'>>(token, '/v1/handlers'),
    staleTime: 60000,
    refetchInterval: false,
  });
  const setFilter = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    next.delete('cursor');
    value ? next.set(name, value) : next.delete(name);
    setParams(next);
  };
  return (
    <>
      <PageHeading
        label="EXECUTION / JOBS"
        title="Every job has a story."
        description="Inspect durable state, attempts, and the history behind every outcome."
        action={
          canWrite && (
            <Link className="button primary" to="/jobs/new">
              <Plus size={16} />
              Submit job
            </Link>
          )
        }
      />
      <Panel>
        <div className="filter-bar">
          <label>
            Status
            <select
              aria-label="Filter status"
              value={filter.status}
              onChange={(event) => setFilter('status', event.target.value)}
            >
              <option value="">All statuses</option>
              {statuses.map((status) => (
                <option key={status} value={status}>
                  {status.replaceAll('_', ' ')}
                </option>
              ))}
            </select>
          </label>
          <label>
            Priority
            <select
              aria-label="Filter priority"
              value={filter.priority}
              onChange={(event) => setFilter('priority', event.target.value)}
            >
              <option value="">All priorities</option>
              {['high', 'normal', 'low'].map((priority) => (
                <option key={priority}>{priority}</option>
              ))}
            </select>
          </label>
          <label>
            Handler
            <select
              aria-label="Filter handler"
              value={filter.type}
              onChange={(event) => setFilter('type', event.target.value)}
            >
              <option value="">All handlers</option>
              {[...new Set(handlers.data?.items.map((handler) => handler.job_type))].map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          </label>
          <button className="text-button" onClick={() => setParams({})}>
            Reset
          </button>
          <form
            className="lookup"
            onSubmit={(event) => {
              event.preventDefault();
              if (/^[0-9a-f-]{36}$/i.test(lookup.trim())) navigate(`/jobs/${lookup.trim()}`);
            }}
          >
            <label className="sr-only" htmlFor="job-lookup">
              Find job by UUID
            </label>
            <input
              id="job-lookup"
              value={lookup}
              placeholder="Find by full job UUID"
              pattern="[0-9a-fA-F-]{36}"
              onChange={(event) => setLookup(event.target.value)}
              required
            />
            <button aria-label="Find job">
              <Search size={17} />
            </button>
          </form>
        </div>
        <ErrorBox error={jobs.error || handlers.error} />
        {jobs.isPending ? <Loading /> : <JobTable jobs={jobs.data?.items || []} />}
        <div className="pagination">
          <span>{jobs.data?.items.length || 0} jobs on this page · newest first</span>
          <div className="button-row">
            <button disabled={!filter.cursor} onClick={() => setFilter('cursor', '')}>
              First page
            </button>
            <button
              disabled={!jobs.data?.next_cursor || jobs.isFetching}
              onClick={() => {
                const next = new URLSearchParams(params);
                next.set('cursor', jobs.data!.next_cursor!);
                setParams(next);
              }}
            >
              Next page
              <ArrowRight size={14} />
            </button>
          </div>
        </div>
      </Panel>
    </>
  );
}

export function SubmitJob() {
  const { token, canRead } = useAccess();
  const navigate = useNavigate();
  const client = useQueryClient();
  const handlers = useQuery({
    queryKey: ['handlers'],
    queryFn: () => request<Schema<'HandlerListResponse'>>(token, '/v1/handlers'),
    enabled: canRead,
    refetchInterval: false,
  });
  const [type, setType] = useState('demo.generate_report');
  const [payload, setPayload] = useState(
    '{\n  "report_id": "my-first-report",\n  "format": "json"\n}',
  );
  const [priority, setPriority] = useState<'high' | 'normal' | 'low'>('normal');
  const [schedule, setSchedule] = useState('');
  const [maxAttempts, setAttempts] = useState(3);
  const [idempotency, setIdempotency] = useState<string>(() => crypto.randomUUID());
  const [localError, setLocalError] = useState<unknown>();
  const [accepted, setAccepted] = useState<Job | null>(null);
  const failedBody = useRef<string | null>(null);
  const mutation = useMutation({
    mutationFn: (body: Schema<'SubmitJobRequest'>) =>
      request<Job>(token, '/v1/jobs', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotency },
        body: JSON.stringify(body),
      }),
    onSuccess: (job) => {
      failedBody.current = null;
      void client.invalidateQueries();
      canRead ? navigate(`/jobs/${job.id}`) : setAccepted(job);
    },
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    setLocalError(undefined);
    try {
      const parsed: unknown = JSON.parse(payload);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw new Error('Payload must be a JSON object.');
      const body: Schema<'SubmitJobRequest'> = {
        type,
        payload: parsed as Record<string, unknown>,
        priority,
        max_attempts: maxAttempts,
        run_at: schedule ? new Date(schedule).toISOString() : null,
      };
      const serialized = JSON.stringify(body);
      if (failedBody.current && failedBody.current !== serialized)
        throw new Error(
          'This key was used for a different request. Restore the previous body to retry, or choose “New submission” to generate a new key.',
        );
      failedBody.current = serialized;
      mutation.mutate(body);
    } catch (error) {
      setLocalError(error);
    }
  }
  const active = handlers.data?.items.filter((handler) => handler.active) || [];
  const selected = active.find((handler) => handler.job_type === type);
  return (
    <>
      <PageHeading
        label="EXECUTION / NEW JOB"
        title="Give Forge work."
        description="Choose a registered handler. Forge validates and stores the job before accepting it."
      />
      <div className="form-layout">
        <Panel title="Job configuration">
          <form className="stack-form" onSubmit={submit}>
            <label>
              Handler type
              {canRead ? (
                <select
                  value={type}
                  onChange={(event) => {
                    setType(event.target.value);
                    setPayload(
                      event.target.value === 'demo.wait'
                        ? '{\n  "task_id": "example-wait",\n  "duration_seconds": 10\n}'
                        : '{\n  "report_id": "my-first-report",\n  "format": "json"\n}',
                    );
                  }}
                >
                  <option value="demo.generate_report">demo.generate_report</option>
                  {active
                    .filter((handler) => handler.job_type !== 'demo.generate_report')
                    .map((handler) => (
                      <option key={handler.job_type}>{handler.job_type}</option>
                    ))}
                </select>
              ) : (
                <input value={type} onChange={(event) => setType(event.target.value)} required />
              )}
            </label>
            <label>
              Payload (JSON)
              <textarea
                className="code-input"
                value={payload}
                onChange={(event) => setPayload(event.target.value)}
                rows={9}
                spellCheck={false}
                required
              />
            </label>
            <div className="field-grid">
              <label>
                Priority
                <select
                  value={priority}
                  onChange={(event) => setPriority(event.target.value as typeof priority)}
                >
                  <option value="high">High</option>
                  <option value="normal">Normal</option>
                  <option value="low">Low</option>
                </select>
              </label>
              <label>
                Maximum attempts
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={maxAttempts}
                  onChange={(event) => setAttempts(Number(event.target.value))}
                  required
                />
              </label>
            </div>
            <label>
              Schedule for (optional, your local time)
              <input
                type="datetime-local"
                value={schedule}
                onChange={(event) => setSchedule(event.target.value)}
              />
            </label>
            <label>
              Idempotency key
              <input
                className="mono"
                value={idempotency}
                onChange={(event) => {
                  if (!failedBody.current) setIdempotency(event.target.value);
                }}
                readOnly={!!failedBody.current}
                required
                maxLength={255}
              />
            </label>
            <small>
              Retries preserve this key and the exact request body. For a different job, start a new
              submission.
            </small>
            <ErrorBox error={localError || mutation.error || handlers.error} />
            {accepted && (
              <div role="status" className="success-box">
                Accepted job <code>{accepted.id}</code>. This key has write access only; a read key
                is needed to inspect its result.
              </div>
            )}
            <div className="button-row">
              <button className="primary" disabled={mutation.isPending || !!accepted}>
                {mutation.isPending ? 'Submitting…' : 'Submit job'}
                <ArrowRight size={16} />
              </button>
              <button
                type="button"
                disabled={mutation.isPending}
                onClick={() => {
                  failedBody.current = null;
                  setIdempotency(crypto.randomUUID());
                  setAccepted(null);
                  mutation.reset();
                  setLocalError(undefined);
                }}
              >
                New submission
              </button>
            </div>
          </form>
        </Panel>
        <div>
          <Panel title="What happens next" eyebrow="THE EXECUTION PATH">
            <ol className="instruction-list">
              <li>
                <strong>Validate & commit</strong>
                <p>Job, idempotency record and outbox event commit together in PostgreSQL.</p>
              </li>
              <li>
                <strong>Deliver & claim</strong>
                <p>Redis delivers the message. A database lease grants attempt ownership.</p>
              </li>
              <li>
                <strong>Execute & persist</strong>
                <p>
                  Forge stores the outcome. Retries and recovery remain visible in the timeline.
                </p>
              </li>
            </ol>
          </Panel>
          {selected && (
            <Panel title="Handler payload schema">
              <JsonView value={selected.payload_schema} />
            </Panel>
          )}
          <p className="help-text">
            demo.generate_report returns deterministic JSON and a suggested path. It does not create
            an actual report file.
          </p>
        </div>
      </div>
    </>
  );
}

export function JobDetail() {
  const { jobId = '' } = useParams();
  const { token, canWrite, admin } = useAccess();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState<'cancel' | 'replay' | null>(null);
  const [copied, setCopied] = useState(false);
  const replayKey = useRef(crypto.randomUUID());
  useEffect(() => {
    replayKey.current = crypto.randomUUID();
    setConfirm(null);
  }, [jobId]);
  const jobQuery = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => request<Job>(token, `/v1/jobs/${jobId}`),
  });
  const attempts = useQuery({
    queryKey: ['attempts', jobId],
    queryFn: () => request<Schema<'JobAttemptListResponse'>>(token, `/v1/jobs/${jobId}/attempts`),
  });
  const transitions = useQuery({
    queryKey: ['transitions', jobId],
    queryFn: () =>
      request<Schema<'JobTransitionListResponse'>>(token, `/v1/jobs/${jobId}/transitions`),
  });
  const job = jobQuery.data;
  const result = useQuery({
    queryKey: ['result', jobId],
    queryFn: () => request<Schema<'JobResultResponse'>>(token, `/v1/jobs/${jobId}/result`),
    enabled: job?.status === 'succeeded',
    refetchInterval: false,
  });
  const mutation = useMutation({
    mutationFn: (action: 'cancel' | 'replay') =>
      request<Job>(token, `/v1/jobs/${jobId}/${action}`, {
        method: 'POST',
        ...(action === 'replay' ? { headers: { 'Idempotency-Key': replayKey.current } } : {}),
      }),
    onSuccess: (updated, action) => {
      setConfirm(null);
      void client.invalidateQueries();
      if (action === 'replay') navigate(`/jobs/${updated.id}`);
    },
  });
  if (jobQuery.isPending) return <Loading />;
  if (!job) return <ErrorBox error={jobQuery.error || new Error('Job unavailable')} />;
  return (
    <>
      <Link to="/jobs" className="back-link">
        <ArrowLeft size={15} />
        All jobs
      </Link>
      <PageHeading
        label="EXECUTION / JOB DETAILS"
        title={job.type}
        description={`Handler version ${job.handler_version} · accepted ${date(job.created_at)}`}
        action={
          <div className="button-row">
            <Status value={job.status} />
            {canWrite && !terminal.has(job.status) && job.status !== 'cancellation_requested' && (
              <button onClick={() => setConfirm('cancel')}>
                <XCircle size={15} />
                Cancel job
              </button>
            )}
            {admin && job.status === 'dead_lettered' && (
              <button className="primary" onClick={() => setConfirm('replay')}>
                <RotateCcw size={15} />
                Replay job
              </button>
            )}
          </div>
        }
      />
      <div className="job-id-line">
        <code>{job.id}</code>
        <button
          className="icon-button"
          aria-label="Copy job ID"
          onClick={() => {
            void navigator.clipboard
              .writeText(job.id)
              .then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              })
              .catch(() => setCopied(false));
          }}
        >
          {copied ? <Check size={15} /> : <Copy size={15} />}
        </button>
      </div>
      <ErrorBox error={jobQuery.error || attempts.error || transitions.error || mutation.error} />
      <div className="details-summary">
        <div>
          <span>Priority</span>
          <strong className={`priority-${job.priority}`}>{job.priority}</strong>
        </div>
        <div>
          <span>Attempts</span>
          <strong>
            {job.attempt_count} / {job.max_attempts}
          </strong>
        </div>
        <div>
          <span>Scheduled for</span>
          <strong>{date(job.run_at)}</strong>
        </div>
        <div>
          <span>First started</span>
          <strong>{date(job.started_at)}</strong>
        </div>
        <div>
          <span>Finished</span>
          <strong>{date(job.finished_at)}</strong>
        </div>
      </div>
      {job.replayed_from_job_id && (
        <p className="help-text">
          Replayed from{' '}
          <Link to={`/jobs/${job.replayed_from_job_id}`}>{job.replayed_from_job_id}</Link>. The
          original job remains unchanged.
        </p>
      )}
      <div className="overview-grid">
        <Panel title="Transition timeline" eyebrow="DURABLE HISTORY">
          {transitions.isPending ? (
            <Loading />
          ) : transitions.data?.items.length ? (
            <ol className="timeline">
              {transitions.data.items.map((transition) => (
                <li key={transition.id}>
                  <span className="timeline-dot" />
                  <div>
                    <div className="timeline-top">
                      <Status value={transition.to_status} />
                      <time>{date(transition.created_at)}</time>
                    </div>
                    <strong>{transition.reason.replaceAll('_', ' ')}</strong>
                    <small>
                      {transition.actor_type}
                      {transition.actor_id ? ` · ${transition.actor_id}` : ''}
                    </small>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <Empty title="No transitions found" />
          )}
        </Panel>
        <div>
          <Panel
            title="Payload"
            action={
              <span className="tiny-badge">
                {job.payload_stored_externally ? 'EXTERNAL OBJECT' : 'INLINE JSON'}
              </span>
            }
          >
            {job.payload ? (
              <JsonView value={job.payload} />
            ) : (
              <p className="panel-note">
                The payload is stored externally. Its object reference is retained by Forge; the
                result endpoint retrieves external results.
              </p>
            )}
          </Panel>
          <Panel
            title="Result"
            action={job.status === 'succeeded' ? <Status value="succeeded" /> : undefined}
          >
            {result.isFetching ? (
              <Loading />
            ) : result.data ? (
              <JsonView value={result.data.result} />
            ) : (
              <Empty title="No stored result yet">
                A result becomes available after successful execution.
              </Empty>
            )}
            <ErrorBox error={result.error} />
          </Panel>
        </div>
      </div>
      <Panel title="Execution attempts" eyebrow="OWNERSHIP & RECOVERY">
        {attempts.data?.items.length ? (
          <div className="attempt-list">
            {attempts.data.items.map((attempt) => (
              <article key={attempt.id}>
                <div className="attempt-header">
                  <strong>Attempt {attempt.attempt_number}</strong>
                  <Status value={attempt.status} />
                  <span className="muted">{date(attempt.started_at)}</span>
                </div>
                <p className="mono worker-id">{attempt.worker_id}</p>
                {attempt.error_code && (
                  <div className="attempt-error">
                    <strong>{attempt.error_code}</strong>
                    <p>{attempt.error_message}</p>
                    <small>
                      {attempt.failure_kind} · {attempt.retryable ? 'Retryable' : 'Not retryable'}
                    </small>
                  </div>
                )}
                <small className="muted">
                  Finished {date(attempt.finished_at)} · lease expires{' '}
                  {date(attempt.lease_expires_at)}
                </small>
              </article>
            ))}
          </div>
        ) : (
          <Empty title="No attempts yet">
            A worker will create an attempt when it claims this job.
          </Empty>
        )}
      </Panel>
      {confirm && (
        <Confirm
          title={confirm === 'cancel' ? 'Cancel this job?' : 'Replay this dead-lettered job?'}
          description={
            confirm === 'cancel'
              ? 'Queued jobs cancel immediately. Running cancellation is cooperative and cannot undo side effects already performed.'
              : 'Forge creates a new linked job. Verify downstream effects before replaying; repeated execution can repeat external work.'
          }
          pending={mutation.isPending}
          error={mutation.error}
          onClose={() => {
            if (!mutation.isPending) setConfirm(null);
          }}
          onConfirm={() => mutation.mutate(confirm)}
        />
      )}
    </>
  );
}
