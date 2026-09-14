/* Authentication state.
 *
 * Owns the session token, the current user, and memberships. Registers
 * itself with the API client (token supply + 401 handling) so every
 * request authenticates consistently.
 *
 * The backend remains the authority: this context mirrors identity for
 * rendering only. Route guards redirect; permission checks gate API calls
 * server-side regardless of what the UI shows.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authApi } from "../api/auth";
import { configureApiClient, request } from "../api/client";
import type { MeResponse, UserResponse } from "../types/auth";
import { readToken, writeToken } from "./session";

export type AuthStatus =
  | "loading"
  | "bootstrap" // no users exist yet: first-organisation setup is open
  | "login" // users exist: credentials required
  | "authenticated";

export interface AuthContextValue {
  status: AuthStatus;
  token: string | null;
  user: UserResponse | null;
  memberships: MeResponse["memberships"];
  /** Sign in with email + password. Throws ApiError on failure. */
  login: (email: string, password: string) => Promise<void>;
  /** Create the first organisation + owner (bootstrap mode only). */
  bootstrap: (input: {
    organisation_name: string;
    slug: string;
    admin_email: string;
    password: string;
  }) => Promise<void>;
  /** Revoke the session server-side and clear local state. Never throws. */
  logout: () => Promise<void>;
  /** Refresh user + memberships from /auth/me. */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function probeMode(): Promise<"bootstrap" | "login"> {
  // No token: an unauthenticated read distinguishes first-run (open) from
  // locked-down (401) using existing contracts — no new endpoint needed.
  try {
    await request("/api/v1/entities", { auth: false });
    return "bootstrap";
  } catch {
    return "login";
  }
}

export function AuthProvider({ children }: { children: ReactNode }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [token, setToken] = useState<string | null>(() => readToken());
  const [me, setMe] = useState<MeResponse | null>(null);
  const tokenRef = useRef<string | null>(token);
  tokenRef.current = token;

  const clearSession = useCallback(
    (nextStatus: AuthStatus) => {
      writeToken(null);
      tokenRef.current = null;
      setToken(null);
      setMe(null);
      queryClient.clear();
      setStatus(nextStatus);
    },
    [queryClient],
  );

  // Wire the API client exactly once: token supply + single-shot 401 handling.
  useEffect(() => {
    configureApiClient({
      authProvider: {
        getToken: () => tokenRef.current,
        getOrganisationId: () => {
          // Organisation scope is read live from session state below via a
          // custom event-free approach: OrganisationProvider registers its
          // own getter through `setOrganisationReader`.
          return readOrganisationRef.current?.() ?? null;
        },
      },
      events: {
        onUnauthorized: () => {
          void probeMode().then((mode) => clearSession(mode));
        },
      },
    });
  }, [clearSession]);

  // Session restoration on mount.
  useEffect(() => {
    let cancelled = false;
    async function restore(): Promise<void> {
      const stored = readToken();
      if (!stored) {
        if (!cancelled) setStatus(await probeMode());
        return;
      }
      try {
        const profile = await authApi.me();
        if (cancelled) return;
        setMe(profile);
        setStatus("authenticated");
      } catch {
        if (cancelled) return;
        writeToken(null);
        setToken(null);
        setStatus(await probeMode());
      }
    }
    void restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<void> => {
    const session = await authApi.login({ email, password });
    writeToken(session.access_token);
    // Update the request-time token synchronously so the very next call
    // (me() below) authenticates without waiting for a re-render.
    tokenRef.current = session.access_token;
    setToken(session.access_token);
    const profile = await authApi.me();
    setMe(profile);
    setStatus("authenticated");
  }, []);

  const bootstrap = useCallback(
    async (input: {
      organisation_name: string;
      slug: string;
      admin_email: string;
      password: string;
    }): Promise<void> => {
      const created = await authApi.bootstrap(input);
      writeToken(created.token.access_token);
      tokenRef.current = created.token.access_token;
      setToken(created.token.access_token);
      const profile = await authApi.me();
      setMe(profile);
      setStatus("authenticated");
    },
    [],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await authApi.logout();
    } catch {
      // Logout is idempotent server-side; local cleanup always runs.
    } finally {
      const mode = await probeMode().catch((): "login" => "login");
      clearSession(mode);
    }
  }, [clearSession]);

  const refresh = useCallback(async (): Promise<void> => {
    const profile = await authApi.me();
    setMe(profile);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      token,
      user: me?.user ?? null,
      memberships: me?.memberships ?? [],
      login,
      bootstrap,
      logout,
      refresh,
    }),
    [status, token, me, login, bootstrap, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>.");
  return context;
}

/** Organisation-reader slot filled by OrganisationProvider (avoids a cycle). */
export const readOrganisationRef: { current: (() => string | null) | null } = {
  current: null,
};

/** Test/SSR escape hatch: point the client at an explicit base URL. */
export function configureApiBaseUrlForTests(baseUrl: string): void {
  configureApiClient({ baseUrl });
}
