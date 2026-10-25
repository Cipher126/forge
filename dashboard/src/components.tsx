import type { ReactNode } from 'react';
import { AlertTriangle, ArrowUpRight, Inbox, LoaderCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ApiError } from './api';
import type { Job } from './api';
import { useDialog } from './dialog';

export const number = (value: number | undefined) =>
  value === undefined ? '—' : new Intl.NumberFormat().format(value);
export function duration(value: number | null | undefined) {
  if (value == null) return '—';
  if (value < 1) return `${(value * 1000).toFixed(0)} ms`;
  if (value < 60) return `${value.toFixed(2)} s`;
  return `${(value / 60).toFixed(1)} min`;
}
export function date(value: string | null | undefined) {
  return value
    ? new Date(value).toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : '—';
}
export function Status({ value }: { value: string }) {
  return (
    <span className={`status status-${value}`}>
      <span className="status-dot" />
      {value.replaceAll('_', ' ')}
    </span>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div role="alert" className="error-box">
      <AlertTriangle size={18} />
      <div>
        <strong>{error instanceof Error ? error.message : 'Something went wrong'}</strong>
        {error instanceof ApiError && (
          <>
            <small>
              {error.code}
              {error.retryAfter ? ` · Retry after ${error.retryAfter} seconds` : ''}
            </small>
            {error.details && <pre>{JSON.stringify(error.details, null, 2)}</pre>}
          </>
        )}
      </div>
    </div>
  );
}
export function Loading() {
  return (
    <div className="empty" role="status">
      <LoaderCircle className="spin" size={24} />
      Loading Forge data…
    </div>
  );
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Inbox size={28} />
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  );
}
export function Panel({
  title,
  eyebrow,
  action,
  children,
  className = '',
}: {
  title?: string;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <div>
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            <h2>{title}</h2>
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function PageHeading({
  label,
  title,
  description,
  action,
}: {
  label: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{label}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}
export function JobTable({ jobs }: { jobs: Job[] }) {
  if (!jobs.length)
    return <Empty title="No jobs here yet">Submit a job, or try a different filter.</Empty>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Job / handler</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Attempts</th>
            <th>Created</th>
            <th>
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td>
                <Link className="job-link" to={`/jobs/${job.id}`}>
                  {job.type}
                  <span className="mono">
                    {job.id.slice(0, 8)}…{job.id.slice(-6)}
                  </span>
                </Link>
              </td>
              <td>
                <Status value={job.status} />
              </td>
              <td>
                <span className={`priority priority-${job.priority}`}>{job.priority}</span>
              </td>
              <td className="mono">
                {job.attempt_count} / {job.max_attempts}
              </td>
              <td className="muted nowrap">{date(job.created_at)}</td>
              <td>
                <Link aria-label={`Open job ${job.id}`} to={`/jobs/${job.id}`}>
                  <ArrowUpRight size={17} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Confirm({
  title,
  description,
  onConfirm,
  onClose,
  pending,
  error,
}: {
  title: string;
  description: string;
  onConfirm: () => void;
  onClose: () => void;
  pending: boolean;
  error?: unknown;
}) {
  const dialog = useDialog(true, () => {
    if (!pending) onClose();
  });
  return (
    <div className="modal-shade" onClick={onClose}>
      <section
        ref={dialog}
        className="modal panel"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="confirm-title">{title}</h2>
        <p>{description}</p>
        <ErrorBox error={error} />
        <div className="button-row">
          <button autoFocus onClick={onClose} disabled={pending}>
            Keep as is
          </button>
          <button className="danger" onClick={onConfirm} disabled={pending}>
            {pending ? 'Working…' : 'Confirm'}
          </button>
        </div>
      </section>
    </div>
  );
}
