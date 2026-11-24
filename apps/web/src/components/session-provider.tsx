'use client';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { adapter, dataMode } from '@/lib/data';
import type { Login, User } from '@/lib/domain';
import { errorMessage } from '@/lib/api/errors';
import { draftStore } from '@/lib/draft-store';
import { onAuthenticationLost } from '@/lib/auth-events';

const PUBLIC_AUTH_PATHS = new Set(['/login', '/activate', '/recover', '/reset-password', '/staff/invitation']);
type Session = { status: 'loading' | 'signed-out' | 'authenticated' | 'unavailable'; user: User | null; error: string | null };
type Value = Session & { explicitLogout: boolean; login(input: Login): Promise<User>; logout(all?: boolean): Promise<void>; reload(): Promise<void>; saveProfile(name: string | null): Promise<void> };
const Context = createContext<Value | null>(null);
export function SessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isPublicAuth = pathname !== null && PUBLIC_AUTH_PATHS.has(pathname);
  const [session, setSession] = useState<Session>({ status: isPublicAuth ? 'signed-out' : 'loading', user: null, error: null });
  const [explicitLogout, setExplicitLogout] = useState(false);
  const generation = useRef(0);
  const hydratedRef = useRef(false);
  const install = useCallback((user: User | null) => {
    draftStore.bindActor(user?.id ?? null);
    setSession(user?.status === 'ACTIVE' ? { status: 'authenticated', user, error: null } : { status: 'signed-out', user: null, error: null });
  }, []);
  const reload = useCallback(async () => {
    hydratedRef.current = true;
    setExplicitLogout(false);
    const id = ++generation.current;
    setSession({ status: 'loading', user: null, error: null });
    try { const user = await adapter.identity.restore(); if (id === generation.current) install(user?.status === 'ACTIVE' ? user : null); }
    catch (error) { if (id === generation.current) { draftStore.bindActor(null); setSession({ status: 'unavailable', user: null, error: errorMessage(error) }); } }
  }, [install]);
  useEffect(() => {
    const unsubscribe = onAuthenticationLost(() => {
      generation.current++;
      hydratedRef.current = true;
      setExplicitLogout(false);
      install(null);
    });
    return () => { generation.current++; unsubscribe(); };
  }, [install]);
  useEffect(() => {
    if (hydratedRef.current) return;
    if (isPublicAuth) {
      setSession(prev => prev.status === 'authenticated' ? prev : { status: 'signed-out', user: null, error: null });
      return;
    }
    hydratedRef.current = true;
    void reload();
  }, [isPublicAuth, reload]);
  async function login(input: Login) {
    const id = ++generation.current;
    const user = await adapter.identity.login(input);
    if (id !== generation.current || user.status !== 'ACTIVE') throw new Error('Session changed');
    hydratedRef.current = true;
    setExplicitLogout(false);
    install(user); return user;
  }
  async function logout(all = false) {
    const id = ++generation.current;
    hydratedRef.current = true;
    // Invalidate draft writers before React unmounts their previous session.
    draftStore.bindActor(null);
    try { await (all ? adapter.identity.logoutAll() : adapter.identity.logout()); }
    finally {
      setExplicitLogout(true);
      if (id === generation.current) install(null);
    }
  }
  async function saveProfile(name: string | null) {
    const id = generation.current;
    const user = await adapter.identity.updateProfile(name);
    if (id === generation.current) install(user);
  }
  return <Context.Provider value={{ ...session, explicitLogout, login, logout, reload, saveProfile }}>
    {children}
    {dataMode === 'mock' && <aside className="preview-bar" role="status">Bản xem thử · dữ liệu mẫu · thay đổi không được gửi đến máy chủ</aside>}
  </Context.Provider>;
}
export function useSession() { const value = useContext(Context); if (!value) throw new Error('SessionProvider required'); return value; }
