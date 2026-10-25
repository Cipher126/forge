import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { duration, Empty, ErrorBox, JobTable } from './components';
import { ApiError } from './api';
import type { Job } from './api';

describe('measurements and error states', () => {
  it('distinguishes absent measurements from measured zero', () => {
    expect(duration(null)).toBe('—');
    expect(duration(0)).toBe('0 ms');
    expect(duration(120)).toBe('2.0 min');
  });
  it('shows empty data honestly', () => {
    render(<Empty title="No jobs here yet">Submit work to see activity.</Empty>);
    expect(screen.getByText('No jobs here yet')).toBeVisible();
  });
  it('explains throttling and retry instructions', () => {
    render(
      <ErrorBox
        error={new ApiError(429, 'rate_limit_exceeded', 'Quota reached', undefined, '15')}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Retry after 15 seconds');
  });
  it('renders untrusted job text as text, not HTML', () => {
    const job = {
      id: '123',
      type: '<img src=x onerror=alert(1)>',
      status: 'succeeded',
      priority: 'normal',
      attempt_count: 1,
      max_attempts: 3,
      created_at: new Date().toISOString(),
    } as Job;
    render(
      <MemoryRouter>
        <JobTable jobs={[job]} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText(job.type)).toBeVisible();
  });
});
