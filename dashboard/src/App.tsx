import { lazy, Suspense, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Box,
  ChevronRight,
  CircleHelp,
  Command,
  Cpu,
  Flame,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  Plus,
  ShieldCheck,
  Workflow,
  X,
} from 'lucide-react';
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';
import type { Identity, Schema } from './api';
import { useAccess, useSession } from './session';
import { useLiveEvents } from './live';
import { ErrorBox, Loading } from './components';
const Overview = lazy(() =>
  import('./pages/Overview').then((module) => ({ default: module.Overview })),
);
const Jobs = lazy(() => import('./pages/Jobs').then((module) => ({ default: module.Jobs })));
const SubmitJob = lazy(() =>
  import('./pages/Jobs').then((module) => ({ default: module.SubmitJob })),
);
const JobDetail = lazy(() =>
  import('./pages/Jobs').then((module) => ({ default: module.JobDetail })),
);
const Workers = lazy(() => import('./pages/Admin').then((module) => ({ default: module.Workers })));
const Handlers = lazy(() =>
  import('./pages/Admin').then((module) => ({ default: module.Handlers })),
);
const Keys = lazy(() => import('./pages/Admin').then((module) => ({ default: module.Keys })));
const Audit = lazy(() => import('./pages/Admin').then((module) => ({ default: module.Audit })));

function Brand() {
  return (
    <Link to="/" className="brand">
      <span className="brand-mark">
        <Flame size={23} fill="currentColor" />
      </span>
      <span>
        forge<span className="brand-period">.</span>
      </span>
    </Link>
  );
}

function Connect() {
  const { connect } = useSession();
  const [credential, setCredential] = useState('');
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setPending(true);
    try {
      const token = credential.trim();
      const identity = await request<Identity>(token, '/v1/auth/me');
      connect({ token, identity });
      setCredential('');
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <main className="connect-page">
      <div className="connect-art">
        <Brand />
        <div className="connect-copy">
          <span className="eyebrow">THE EXECUTION LAYER</span>
          <h1>
            Every job.
            <br />
            Accounted for.
          </h1>
          <p>
            Durable by design. Observable at every step.
            <br />
            Your distributed work, in one place.
          </p>
          <div className="pipeline">
            <span>
              <Box />
              Accept
            </span>
            <ChevronRight />
            <span>
              <Workflow />
              Dispatch
            </span>
            <ChevronRight />
            <span>
              <Cpu />
              Execute
            </span>
            <ChevronRight />
            <span>
              <ShieldCheck />
              Persist
            </span>
          </div>
        </div>
        <p className="connect-foot">
          PostgreSQL durability · Redis delivery · At-least-once execution
        </p>
      </div>
      <div className="connect-form-wrap">
        <form className="connect-form" onSubmit={submit}>
          <span className="section-icon">
            <Command size={24} />
          </span>
          <span className="eyebrow">FORGE CONTROL ROOM</span>
          <h2>Connect to your workspace</h2>
          <p>Use a scoped API key from your Forge installation.</p>
          <label htmlFor="credential">API key</label>
          <input
            id="credential"
            name="credential"
            type="password"
            placeholder="forge_live_…"
            value={credential}
            onChange={(event) => setCredential(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            required
            maxLength={512}
          />
          <small>
            <ShieldCheck size={14} />
            Your key stays in memory. Reloading disconnects this session.
          </small>
          <ErrorBox error={error} />
          <button className="primary" disabled={pending || !credential.trim()}>
            {pending ? 'Connecting…' : 'Connect to Forge'}
            <ArrowRight size={17} />
          </button>
          <details>
            <summary>Where do I get a key?</summary>
            <p>
              On a trusted Forge host, run <code>forge key create --scope admin</code>.
              Administrators can issue scoped keys in API keys. A read key can inspect owned jobs;
              an administrator key unlocks operations.
            </p>
          </details>
          <a className="subtle-link" href="/api/docs" target="_blank" rel="noreferrer">
            API reference
            <ArrowUpRight size={14} />
          </a>
        </form>
      </div>
    </main>
  );
}

function Guard({ allow, children }: { allow: boolean; children: ReactNode }) {
  return allow ? (
    children
  ) : (
    <div className="empty">
      <ShieldCheck />
      <h2>This view requires a different scope</h2>
      <p>
        Your key cannot access this operation. Ask an administrator for an appropriate scoped key.
      </p>
      <Link to="/">Back to overview</Link>
    </div>
  );
}

export function App() {
  const { session, disconnect } = useSession();
  const { token, admin, canRead, canWrite } = useAccess();
  const location = useLocation();
  const [menu, setMenu] = useState(false);
  const live = useLiveEvents(token, !!session && admin);
  const readiness = useQuery({
    queryKey: ['readiness'],
    queryFn: () => request<Schema<'ReadinessResponse'>>(token, '/ready'),
    enabled: !!session,
    refetchInterval: 30000,
  });
  if (!session) return <Connect />;
  const entries = [
    { path: '/', label: 'Overview', icon: LayoutDashboard, show: true },
    { path: '/jobs', label: 'Jobs', icon: ListChecks, show: canRead },
    { path: '/workers', label: 'Workers & queues', icon: Cpu, show: admin },
    { path: '/handlers', label: 'Handlers', icon: Workflow, show: canRead },
    { path: '/keys', label: 'API keys', icon: KeyRound, show: admin },
    { path: '/audit', label: 'Audit history', icon: ShieldCheck, show: admin },
  ];
  const label =
    entries.find((entry) => entry.path !== '/' && location.pathname.startsWith(entry.path))
      ?.label || 'Overview';
  return (
    <div className="app-shell">
      <aside className={`sidebar ${menu ? 'is-open' : ''}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="mobile-only icon-button"
            aria-label="Close navigation"
            onClick={() => setMenu(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="workspace">
          <span className="workspace-avatar">F</span>
          <div>
            Forge workspace<small>Operations console</small>
          </div>
          <span className="workspace-indicator" />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {entries
            .filter((entry) => entry.show)
            .map(({ path, label: text, icon: Icon }) => (
              <NavLink key={path} to={path} end={path === '/'} onClick={() => setMenu(false)}>
                <Icon size={18} />
                {text}
              </NavLink>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="guarantee">
            <Activity size={17} />
            <strong>Work that survives.</strong>
            <p>
              Accepted jobs are durable.
              <br />
              Follow the history to see why.
            </p>
          </div>
          <a href="/api/docs" target="_blank" rel="noreferrer">
            <CircleHelp size={17} />
            API reference
            <ArrowUpRight size={14} />
          </a>
          <div className="identity">
            <span className="identity-avatar">
              {session.identity.name.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <strong title={session.identity.name}>{session.identity.name}</strong>
              <small>{admin ? 'Administrator' : session.identity.scopes.join(' · ')}</small>
            </div>
            <button
              className="icon-button"
              aria-label="Disconnect"
              title="Disconnect"
              onClick={disconnect}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      {menu && <div className="menu-backdrop" onClick={() => setMenu(false)} />}
      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-only icon-button"
              aria-label="Open navigation"
              onClick={() => setMenu(true)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{label}</strong>
          </div>
          <div className="topbar-status">
            <span className={`health-dot ${readiness.data?.status === 'ready' ? 'ok' : 'warn'}`} />
            <span>
              {readiness.isError
                ? 'Dependency unavailable'
                : readiness.data?.status === 'ready'
                  ? 'System ready'
                  : 'Checking system'}
            </span>
            <span className="topbar-divider" />
            <span className="mono">v0.1</span>
          </div>
        </header>
        <main className="page-content">
          {readiness.isError && <ErrorBox error={readiness.error} />}
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route
                path="/"
                element={
                  admin ? (
                    <Overview live={live} />
                  ) : (
                    <div>
                      <div className="page-heading">
                        <div>
                          <span className="eyebrow">YOUR WORKSPACE</span>
                          <h1>Welcome to Forge.</h1>
                          <p>Access is limited to the scopes on your key.</p>
                        </div>
                      </div>
                      <div className="button-row">
                        {canRead && (
                          <Link className="button primary" to="/jobs">
                            View your jobs
                            <ArrowRight size={16} />
                          </Link>
                        )}
                        {canWrite && (
                          <Link className="button" to="/jobs/new">
                            <Plus size={16} />
                            Submit a job
                          </Link>
                        )}
                      </div>
                    </div>
                  )
                }
              />
              <Route
                path="/jobs"
                element={
                  <Guard allow={canRead}>
                    <Jobs />
                  </Guard>
                }
              />
              <Route
                path="/jobs/new"
                element={
                  <Guard allow={canWrite}>
                    <SubmitJob />
                  </Guard>
                }
              />
              <Route
                path="/jobs/:jobId"
                element={
                  <Guard allow={canRead}>
                    <JobDetail />
                  </Guard>
                }
              />
              <Route
                path="/workers"
                element={
                  <Guard allow={admin}>
                    <Workers />
                  </Guard>
                }
              />
              <Route
                path="/handlers"
                element={
                  <Guard allow={canRead}>
                    <Handlers />
                  </Guard>
                }
              />
              <Route
                path="/keys"
                element={
                  <Guard allow={admin}>
                    <Keys />
                  </Guard>
                }
              />
              <Route
                path="/audit"
                element={
                  <Guard allow={admin}>
                    <Audit />
                  </Guard>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
        <footer className="page-footer">
          <span>
            <span className="health-dot ok" />
            Forge · Distributed job processing
          </span>
          <span>Durability first. Every transition visible.</span>
        </footer>
      </div>
    </div>
  );
}
