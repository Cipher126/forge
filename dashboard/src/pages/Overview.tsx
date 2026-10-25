import { useState } from 'react';
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  CheckCheck,
  CircleDot,
  Cpu,
  Plus,
  Timer,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { request } from '../api';
import type { JobEvent, Schema } from '../api';
import { useAccess } from '../session';
import {
  date,
  duration,
  Empty,
  ErrorBox,
  JobTable,
  Loading,
  number,
  PageHeading,
  Panel,
  Status,
} from '../components';

export function Overview({ live }: { live: { status: string; events: JobEvent[] } }) {
  const { token } = useAccess();
  const [windowMinutes, setWindow] = useState(60);
  const summary = useQuery({
    queryKey: ['summary'],
    queryFn: () => request<Schema<'OperationsSummary'>>(token, '/v1/admin/operations/summary'),
  });
  const performance = useQuery({
    queryKey: ['performance', windowMinutes],
    queryFn: () =>
      request<Schema<'PerformanceStatistics'>>(
        token,
        `/v1/admin/operations/performance?window_minutes=${windowMinutes}`,
      ),
  });
  const jobs = useQuery({
    queryKey: ['jobs', 'recent'],
    queryFn: () => request<Schema<'JobListResponse'>>(token, '/v1/jobs?limit=6'),
  });
  const s = summary.data;
  const p = performance.data;
  const stats = [
    {
      label: 'Outstanding work',
      value: number(s?.outstanding_jobs),
      note: 'Accepted, not yet terminal',
      icon: ArrowDownToLine,
      accent: true,
    },
    {
      label: 'Completed in window',
      value: number(p?.completed_jobs),
      note: p ? `${p.throughput_per_minute.toFixed(1)} completions / min` : 'Terminal outcomes',
      icon: CheckCheck,
    },
    {
      label: 'Active workers',
      value: number(s?.active_workers),
      note: 'Recent heartbeat detected',
      icon: Cpu,
    },
    {
      label: 'Success rate',
      value: p?.success_rate == null ? '—' : `${(p.success_rate * 100).toFixed(1)}%`,
      note: 'Successful / all terminal outcomes',
      icon: CircleDot,
    },
  ];
  const chart = ['high', 'normal', 'low'].map((priority) => ({
    priority,
    messages: s?.queues.queued_by_priority[priority] || 0,
  }));
  return (
    <>
      <PageHeading
        label="OPERATIONS / OVERVIEW"
        title="The control room."
        description="A clear view of the work moving through Forge."
        action={
          <div className="button-row">
            <span className="live-tag">
              <span className="health-dot ok" />
              {live.status}
            </span>
            <Link className="button primary" to="/jobs/new">
              <Plus size={16} />
              Submit job
            </Link>
          </div>
        }
      />
      <div className="context-row">
        <span>
          <span className="health-dot ok" />
          Connected to live API <span className="muted">/ updated {date(s?.generated_at)}</span>
        </span>
        <label className="inline-label">
          Performance window
          <select value={windowMinutes} onChange={(event) => setWindow(Number(event.target.value))}>
            <option value={15}>Last 15 minutes</option>
            <option value={60}>Last hour</option>
            <option value={1440}>Last 24 hours</option>
            <option value={10080}>Last 7 days</option>
          </select>
        </label>
      </div>
      <ErrorBox error={summary.error || performance.error || jobs.error} />
      <div className="stats-grid">
        {stats.map(({ label, value, note, icon: Icon, accent }) => (
          <section className={`stat-card ${accent ? 'accent' : ''}`} key={label}>
            <div className="stat-label">
              {label}
              <Icon size={17} />
            </div>
            <strong>{value}</strong>
            <small>{note}</small>
          </section>
        ))}
      </div>
      <div className="overview-grid">
        <Panel
          title="Queue distribution"
          eyebrow="DELIVERY / RIGHT NOW"
          action={
            <Link className="subtle-link" to="/workers">
              Inspect queues
              <ArrowUpRight size={15} />
            </Link>
          }
        >
          <div className="chart-caption">
            <span className="legend-square" />
            Redis queued messages
            <span className="muted">{number(s?.queues.queued_total)} total</span>
          </div>
          <div className="chart-wrap">
            {summary.isPending ? (
              <Loading />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={chart}
                  barSize={46}
                  margin={{ left: -25, right: 12, top: 12, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="#e8ebea" />
                  <XAxis
                    dataKey="priority"
                    tickLine={false}
                    axisLine={false}
                    tick={{ fill: '#697570', fontSize: 12 }}
                  />
                  <YAxis
                    allowDecimals={false}
                    tickLine={false}
                    axisLine={false}
                    tick={{ fill: '#8a9490', fontSize: 11 }}
                  />
                  <Tooltip
                    cursor={{ fill: '#eff4ee' }}
                    contentStyle={{ border: '1px solid #dde4de', borderRadius: 10, fontSize: 12 }}
                  />
                  <Bar dataKey="messages" name="Messages" fill="#263b2f" radius={[5, 5, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="panel-note">
            Message counts can include repeat delivery. PostgreSQL tracks the durable outstanding
            jobs.
          </div>
        </Panel>
        <Panel
          title="Time in the system"
          eyebrow="LATENCY / P95"
          action={<Timer size={18} className="muted" />}
        >
          <div className="latency-list">
            {[
              ['Queue wait', p?.queue_wait, 'First start − latest enqueue'],
              ['Execution', p?.execution_time, 'Finish − first start'],
              ['End to end', p?.end_to_end_time, 'Finish − creation'],
              ['Schedule lag', p?.scheduling_lag, 'Promotion − scheduled time'],
            ].map(([label, distribution, explanation]) => {
              const d = distribution as Schema<'DurationDistribution'> | undefined;
              return (
                <div className="latency-row" key={String(label)}>
                  <div>
                    <strong>{String(label)}</strong>
                    <small>{String(explanation)}</small>
                  </div>
                  <div>
                    <b>{duration(d?.p95_seconds)}</b>
                    <small>{number(d?.sample_count)} samples</small>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="panel-note">
            No samples means no measurement. Execution can include retries and recovery.
          </div>
        </Panel>
      </div>
      <Panel
        title="Recent jobs"
        eyebrow="EXECUTION LOG"
        action={
          <Link className="subtle-link" to="/jobs">
            All jobs
            <ArrowRight size={16} />
          </Link>
        }
      >
        {jobs.isPending ? <Loading /> : <JobTable jobs={jobs.data?.items || []} />}
      </Panel>
      <div className="overview-grid bottom-grid">
        <Panel
          title="Live transitions"
          eyebrow="DURABLE HISTORY"
          action={<span className="tiny-badge">LATEST 20</span>}
        >
          {live.events.length ? (
            <ol className="event-list">
              {live.events.slice(0, 6).map((event) => (
                <li key={event.id}>
                  <span className="event-node" />
                  <div>
                    <Link to={`/jobs/${event.job_id}`} className="mono">
                      {event.job_id.slice(0, 8)}…{event.job_id.slice(-6)}
                    </Link>
                    <small>
                      {event.reason.replaceAll('_', ' ')} · {date(event.created_at)}
                    </small>
                  </div>
                  <Status value={event.to_status} />
                </li>
              ))}
            </ol>
          ) : (
            <Empty title="Listening for the next transition">
              New job activity appears here while connected. Existing history is available inside
              each job.
            </Empty>
          )}
        </Panel>
        <section className="dark-panel">
          <div className="eyebrow">BUILT FOR THE UNEXPECTED</div>
          <h2>
            Work survives.
            <br />
            History explains.
          </h2>
          <p>
            A worker can disappear. An attempt can retry.
            <br />
            PostgreSQL keeps the record of what happened.
          </p>
          <Link to="/jobs">
            Follow a job
            <ArrowUpRight size={17} />
          </Link>
          <div className="dark-grid" aria-hidden="true" />
        </section>
      </div>
    </>
  );
}
