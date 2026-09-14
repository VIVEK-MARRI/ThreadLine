import { lazy, Suspense } from "react";
import {
  createBrowserRouter,
  createMemoryRouter,
  Navigate,
  Outlet,
  type RouteObject,
} from "react-router-dom";
import { RequireAuth, RequireOrganisation } from "../auth/guards";
import { AppShell } from "../components/layout/AppShell";
import { LoadingState } from "../components/feedback/States";
import { LoginPage } from "../features/auth/LoginPage";
import { SetupPage } from "../features/auth/SetupPage";
import { SelectOrganisationPage } from "../features/auth/SelectOrganisationPage";

/* Feature routes are lazy: the shell + auth code loads first, each section
 * loads on demand. Page shells are stable contracts for future stages. */
const DashboardPage = lazy(() =>
  import("../features/dashboard/DashboardPage").then((m) => ({ default: m.DashboardPage })),
);
const MeetingsPage = lazy(() =>
  import("../features/meetings/MeetingsPage").then((m) => ({ default: m.MeetingsPage })),
);
const MeetingDetailPage = lazy(() =>
  import("../features/meetings/MeetingDetailPage").then((m) => ({ default: m.MeetingDetailPage })),
);
const EntitiesPage = lazy(() =>
  import("../features/entities/EntitiesPage").then((m) => ({ default: m.EntitiesPage })),
);
const EntityDetailPage = lazy(() =>
  import("../features/entities/EntityDetailPage").then((m) => ({ default: m.EntityDetailPage })),
);
const IntelligencePage = lazy(() =>
  import("../features/intelligence/IntelligencePage").then((m) => ({ default: m.IntelligencePage })),
);
const AskPage = lazy(() =>
  import("../features/ask/AskPage").then((m) => ({ default: m.AskPage })),
);
const ActionsPage = lazy(() =>
  import("../features/actions/ActionsPage").then((m) => ({ default: m.ActionsPage })),
);
const SettingsPage = lazy(() =>
  import("../features/settings/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

function LazyOutlet(): React.JSX.Element {
  return (
    <Suspense fallback={<LoadingState title="Loading section" />}>
      <Outlet />
    </Suspense>
  );
}

function WithOrganisation(): React.JSX.Element {
  return (
    <RequireOrganisation>
      <LazyOutlet />
    </RequireOrganisation>
  );
}

export const appRoutes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  { path: "/setup", element: <SetupPage /> },
  {
    path: "/app",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/app/dashboard" replace /> },
      {
        path: "select-organisation",
        element: (
          <RequireAuth>
            <SelectOrganisationPage />
          </RequireAuth>
        ),
      },
      {
        element: <WithOrganisation />,
        children: [
          { path: "dashboard", element: <DashboardPage /> },
          { path: "meetings", element: <MeetingsPage /> },
          { path: "meetings/:meetingId", element: <MeetingDetailPage /> },
          { path: "entities", element: <EntitiesPage /> },
          { path: "entities/:entityId", element: <EntityDetailPage /> },
          { path: "intelligence", element: <IntelligencePage /> },
          { path: "ask", element: <AskPage /> },
          { path: "actions", element: <ActionsPage /> },
        ],
      },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
  { path: "/", element: <Navigate to="/app/dashboard" replace /> },
  { path: "*", element: <Navigate to="/app/dashboard" replace /> },
];

export const router = createBrowserRouter(appRoutes);

/** Memory router with the same routes, for tests. */
export function createTestRouter(initialPath: string): ReturnType<typeof createMemoryRouter> {
  return createMemoryRouter(appRoutes, { initialEntries: [initialPath] });
}

/** Route catalogue for tests: every path must resolve without errors. */
export const ROUTE_PATHS = [
  "/login",
  "/setup",
  "/app",
  "/app/dashboard",
  "/app/meetings",
  "/app/meetings/abc",
  "/app/entities",
  "/app/entities/abc",
  "/app/intelligence",
  "/app/ask",
  "/app/actions",
  "/app/settings",
  "/app/select-organisation",
];
