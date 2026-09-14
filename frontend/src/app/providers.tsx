import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../auth/AuthContext";
import { OrganisationProvider } from "../auth/OrganisationContext";
import { ToastProvider } from "../components/ui/Toast";

/* One QueryClient for the app lifetime. Tenant data is namespaced by
 * organisation in every key (see api/keys); signing out or switching
 * organisations clears the cache outright.
 *
 * Defaults: single retry (fail visibly, don't hammer), no refetch on
 * window focus (screens refetch explicitly where freshness matters).
 */
export function AppProviders({ children }: { children: ReactNode }): React.JSX.Element {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
            staleTime: 15_000,
          },
          mutations: {
            retry: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <OrganisationProvider>
          <ToastProvider>{children}</ToastProvider>
        </OrganisationProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
