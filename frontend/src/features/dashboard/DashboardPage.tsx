import { Link } from "react-router-dom";
import { ArrowRight, Files, Sparkles, Users } from "lucide-react";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { PageHeader } from "../../components/layout/PageHeader";
import { Card } from "../../components/ui/Card";
import { canManageMembers } from "../../auth/permissions";

/* Dashboard (foundation): orientation, not metrics. Real next steps only —
 * no invented statistics, no activity feeds, no charts of nothing. */

const STEPS = [
  {
    to: "/app/meetings",
    icon: Files,
    title: "Ingest a meeting",
    body: "Bring a transcript in. ThreadLine extracts facts, people, and commitments.",
  },
  {
    to: "/app/ask",
    icon: Sparkles,
    title: "Ask about your organisation",
    body: "Questions are answered with cited evidence from your own meetings.",
  },
  {
    to: "/app/settings",
    icon: Users,
    title: "Invite your team",
    body: "Owners and admins add members so the whole organisation is heard.",
    adminOnly: true,
  },
] as const;

export function DashboardPage(): React.JSX.Element {
  useDocumentTitle("Dashboard");
  const { current, role } = useOrganisation();
  const orgName = current?.organisation.name ?? "your organisation";

  return (
    <div className="tl-page">
      <PageHeader
        title={`Good to see you — here's ${orgName}`}
        description="ThreadLine turns your meetings into shared organisational memory: who said what, what was decided, and what needs attention."
      />
      <div className="tl-step-grid">
        {STEPS.filter((step) => !("adminOnly" in step && step.adminOnly && !canManageMembers(role))).map(
          (step) => (
            <Link key={step.to} to={step.to} className="tl-step-card">
              <span className="tl-step-icon" aria-hidden="true">
                <step.icon />
              </span>
              <span className="tl-step-title">{step.title}</span>
              <span className="tl-body-secondary">{step.body}</span>
              <span className="tl-step-go">
                Open <ArrowRight aria-hidden="true" />
              </span>
            </Link>
          ),
        )}
      </div>
      <Card>
        <h2 className="tl-section-title">How ThreadLine works</h2>
        <ol className="tl-how-list">
          <li>
            <strong>Meetings in.</strong> Transcripts become durable, revisioned source truth.
          </li>
          <li>
            <strong>Understanding out.</strong> People, issues, dependencies, and risks are
            resolved deterministically — never guessed.
          </li>
          <li>
            <strong>Memory that answers.</strong> Every answer cites the meeting it came from.
          </li>
        </ol>
        <p className="tl-body-secondary">
          Everything on this page is scoped to {orgName}. Switching organisations in the
          header switches the entire workspace — nothing leaks across.
        </p>
      </Card>
    </div>
  );
}
