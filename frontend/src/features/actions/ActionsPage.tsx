import { Link } from "react-router-dom";
import { CheckSquare } from "lucide-react";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { Button } from "../../components/ui/Button";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState } from "../../components/feedback/States";

/* Action center shell (Stage 26 owns the build). Honest about scope:
 * per-entity actions already exist on entity pages; the cross-cutting
 * center is not built yet — no fake action items. */

export function ActionsPage(): React.JSX.Element {
  useDocumentTitle("Actions");
  return (
    <div className="tl-page">
      <PageHeader
        title="Actions"
        description="Every commitment made in your meetings, tracked to done."
      />
      <EmptyState
        title="The action center is coming next"
        body="Commitments are already extracted from every processed meeting. The dedicated center — owners, due dates, and follow-through across the organisation — ships in the next stage."
        icon={<CheckSquare aria-hidden="true" />}
        action={
          <Link to="/app/intelligence">
            <Button variant="secondary">Review intelligence meanwhile</Button>
          </Link>
        }
      />
    </div>
  );
}
