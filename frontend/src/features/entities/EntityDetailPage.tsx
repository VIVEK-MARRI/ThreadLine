import { Link, useParams } from "react-router-dom";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { ApiError } from "../../types/api";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { formatEntityDay } from "./entitiesFormat";
import { useEntityDetail } from "./useEntities";
import {
  EntityAttentionActionsSection,
  EntityChangesSection,
  EntityCurrentStateSection,
  EntityDependencySection,
  EntityImpactSection,
  EntityMeetingsSection,
  EntityMemorySection,
  EntityMetadataRail,
  EntityRiskSection,
  EntityTimelineSection,
} from "./EntityDetailSections";
import "./entities.css";

/* Entity Intelligence Workspace detail: the organisation’s living memory
 * for one canonical entity. Detail, history, risks, dependencies, impact,
 * meetings, changes, memory, and actions load in parallel and fail
 * independently. Only backend-provided facts are shown.
 */

function DetailSkeleton(): React.JSX.Element {
  return (
    <div className="tl-page">
      <PageHeader title="Entity" description="Loading organisational memory…" />
      <div className="tl-entity-layout">
        <div className="tl-entity-main">
          <Skeleton lines={4} label="Loading current state" />
          <Skeleton lines={6} label="Loading timeline" />
          <Skeleton lines={4} label="Loading risk signals" />
          <Skeleton lines={5} label="Loading dependencies" />
          <Skeleton lines={4} label="Loading potential impact" />
          <Skeleton lines={4} label="Loading related meetings" />
          <Skeleton lines={4} label="Loading entity changes" />
          <Skeleton lines={5} label="Loading memory and evidence" />
          <Skeleton lines={4} label="Loading attention and actions" />
        </div>
        <div className="tl-entity-rail">
          <Skeleton lines={5} label="Loading key context" />
        </div>
      </div>
    </div>
  );
}

export function EntityDetailPage(): React.JSX.Element {
  const { entityId = "" } = useParams();
  const detail = useEntityDetail(entityId);
  useDocumentTitle(detail.data?.canonical_name ?? "Entity");

  if (detail.isLoading) return <DetailSkeleton />;
  if (detail.isError) {
    const unavailable =
      detail.error instanceof ApiError &&
      (detail.error.kind === "not-found" || detail.error.kind === "forbidden");
    return (
      <div className="tl-page">
        <PageHeader title="Entity unavailable" crumbs={[{ label: "Entities", to: "/app/entities" }]} />
        {unavailable ? (
          <EmptyState
            title="This entity isn't available"
            body="It may not exist, or it may belong to another organisation."
            action={
              <Link className="tl-btn tl-btn-secondary" to="/app/entities">
                Back to entities
              </Link>
            }
          />
        ) : (
          <ErrorState
            title="Couldn't load this entity"
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
      </div>
    );
  }

  const entity = detail.data;
  if (!entity) {
    return (
      <div className="tl-page">
        <PageHeader title="Entity unavailable" crumbs={[{ label: "Entities", to: "/app/entities" }]} />
        <ErrorState
          title="Couldn't load this entity"
          error={new Error("The entity response was empty.")}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  }
  const trackedSince = formatEntityDay(entity.created_at) ?? "Date not provided";

  return (
    <div className="tl-page">
      <PageHeader
        title={entity.canonical_name}
        description={`${entity.entity_type} · tracked since ${trackedSince}`}
        crumbs={[{ label: "Entities", to: "/app/entities" }, { label: entity.canonical_name }]}
      />
      <div className="tl-entity-layout">
        <div className="tl-entity-main">
          <EntityCurrentStateSection entityId={entity.entity_id} />
          <EntityTimelineSection entityId={entity.entity_id} />
          <EntityRiskSection entityId={entity.entity_id} />
          <EntityDependencySection entityId={entity.entity_id} />
          <EntityImpactSection entityId={entity.entity_id} />
          <EntityMeetingsSection entityId={entity.entity_id} />
          <EntityChangesSection entityId={entity.entity_id} />
          <EntityMemorySection entityId={entity.entity_id} />
          <EntityAttentionActionsSection entityId={entity.entity_id} />
        </div>
        <EntityMetadataRail entity={entity} entityId={entity.entity_id} />
      </div>
    </div>
  );
}
