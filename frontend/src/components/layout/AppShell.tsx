import { useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3,
  Building2,
  CheckSquare,
  ChevronDown,
  Files,
  Menu,
  Network,
  Settings as SettingsIcon,
  Sparkles,
  Tags,
  X,
} from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { useOrganisation } from "../../auth/OrganisationContext";
import { Avatar } from "../ui/Avatar";
import { Dropdown } from "../ui/Dropdown";
import { Logo } from "./Logo";

const NAV_ITEMS = [
  { to: "/app/dashboard", label: "Dashboard", icon: BarChart3, end: false },
  { to: "/app/meetings", label: "Meetings", icon: Files, end: false },
  { to: "/app/entities", label: "Entities", icon: Tags, end: false },
  { to: "/app/intelligence", label: "Intelligence", icon: Network, end: false },
  { to: "/app/ask", label: "Ask", icon: Sparkles, end: false },
  { to: "/app/actions", label: "Actions", icon: CheckSquare, end: false },
  { to: "/app/settings", label: "Settings", icon: SettingsIcon, end: false },
] as const;

/* Authenticated application shell: responsive sidebar, header with
 * organisation switcher + user menu, and the main content region. */

export function AppShell(): React.JSX.Element {
  const { user, logout } = useAuth();
  const { organisations, current, role, select } = useOrganisation();
  const navigate = useNavigate();
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  function handleSelectOrganisation(organisationId: string): void {
    if (!select(organisationId)) return;
    // Query filters are tenant-scoped UI state. Keep the destination route,
    // but do not carry one organisation’s filters into another organisation.
    if (location.search) navigate(location.pathname, { replace: true });
  }

  async function handleLogout(): Promise<void> {
    await logout();
    navigate("/login", { replace: true });
  }

  const sidebar = (
    <nav className="tl-sidebar-nav" aria-label="Primary">
      <ul>
        {NAV_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/app/dashboard" ? true : undefined}
              onClick={() => setDrawerOpen(false)}
              className={({ isActive }) =>
                ["tl-nav-link", isActive ? "tl-nav-link-active" : ""].join(" ")
              }
            >
              <item.icon aria-hidden="true" />
              <span>{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );

  return (
    <div className="tl-shell">
      <a className="tl-skip-link" href="#tl-main">
        Skip to content
      </a>

      <header className="tl-header">
        <button
          type="button"
          className="tl-icon-btn tl-header-menu"
          aria-label={drawerOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={drawerOpen}
          onClick={() => setDrawerOpen((open) => !open)}
        >
          {drawerOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>

        <span className="tl-header-brand">
          <Logo />
        </span>

        <div className="tl-header-spacer" />

        <Dropdown
          label="Switch organisation"
          align="end"
          trigger={
            <span className="tl-org-trigger">
              <Building2 aria-hidden="true" />
              <span className="tl-org-name">{current?.organisation.name ?? "Select organisation"}</span>
              <ChevronDown aria-hidden="true" className="tl-org-chevron" />
            </span>
          }
          items={organisations
            .filter((m) => m.status === "ACTIVE")
            .map((m) => ({
              id: m.organisation.organisation_id,
              label: m.organisation.name,
              description: `${m.role} · ${m.organisation.slug}`,
            }))}
          onSelect={(id) => {
            handleSelectOrganisation(id);
          }}
        />

        <Dropdown
          label="Account"
          align="end"
          trigger={
            <span className="tl-user-trigger">
              <Avatar name={user?.email ?? "?"} size="sm" />
              <span className="tl-user-email">{user?.email}</span>
              <ChevronDown aria-hidden="true" className="tl-org-chevron" />
            </span>
          }
          items={[
            {
              id: "settings",
              label: "Settings",
              description: role ? `Signed in as ${role}` : undefined,
            },
            { id: "logout", label: "Sign out", danger: true },
          ]}
          onSelect={(id) => {
            if (id === "logout") void handleLogout();
            else navigate("/app/settings");
          }}
        />
      </header>

      <div className="tl-shell-body">
        <aside className="tl-sidebar" aria-label="Application">
          <div className="tl-sidebar-brand">
            <Logo />
          </div>
          {sidebar}
          <div className="tl-sidebar-foot">
            <p className="tl-meta">
              {current ? `${current.organisation.slug} · ${current.role}` : "No organisation"}
            </p>
          </div>
        </aside>

        {drawerOpen ? (
          <div className="tl-drawer-scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true" />
        ) : null}
        <aside
          className={["tl-drawer", drawerOpen ? "tl-drawer-open" : ""].join(" ")}
          aria-label="Mobile navigation"
          aria-hidden={!drawerOpen}
          inert={!drawerOpen}
        >
          <div className="tl-drawer-head">
            <Logo />
            <button
              type="button"
              className="tl-icon-btn"
              aria-label="Close navigation"
              onClick={() => setDrawerOpen(false)}
              tabIndex={drawerOpen ? 0 : -1}
            >
              <X aria-hidden="true" />
            </button>
          </div>
          {sidebar}
        </aside>

        <main id="tl-main" className="tl-main" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
