import { createContext, useContext, useState, useEffect } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Identity } from './api';

type Session = { token: string; identity: Identity };
const Context = createContext<{
  session: Session | null;
  connect: (session: Session) => void;
  disconnect: () => void;
} | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const client = useQueryClient();
  const disconnect = () => {
    setSession(null);
    client.clear();
  };
  useEffect(() => {
    window.addEventListener('forge:unauthorized', disconnect);
    return () => window.removeEventListener('forge:unauthorized', disconnect);
  });
  return (
    <Context.Provider
      value={{
        session,
        connect: (value) => {
          client.clear();
          setSession(value);
        },
        disconnect,
      }}
    >
      {children}
    </Context.Provider>
  );
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error('Session provider missing');
  return value;
}

export function useAccess() {
  const { session } = useSession();
  const admin = !!session?.identity.scopes.includes('admin');
  return {
    token: session?.token || '',
    admin,
    canRead: admin || !!session?.identity.scopes.includes('jobs:read'),
    canWrite: admin || !!session?.identity.scopes.includes('jobs:write'),
  };
}
