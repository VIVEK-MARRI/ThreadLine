import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { entitiesApi } from "../../api/entities";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { Badge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { PageHeader } from "../../components/layout/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";

interface RelationshipEdge {
  source_entity_id?: string;
  target_entity_id?: string;
  relationship_type?: string;
  strength?: number;
}

interface RelationshipGraph {
  entity_id: string;
  relationship_count: number;
  related_entity_ids: string[];
  relationships: RelationshipEdge[];
}

/* Entity workspace (foundation): real facts + real relationships.
 * Timeline, impacts, and graphs arrive in the next stage. */

export function EntityDetailPage(): React.JSX.Element {
  const { entityId = "" } = useParams();
  useDocumentTitle("Entity");
  const { organisationId } = useOrganisation();

  const detail = useQuery({
    queryKey: queryKeys.entity(organisationId ?? "", entityId),
    queryFn: () => entitiesApi.get(entityId),
    enabled: Boolean(organisationId && entityId),
  });

  const relationships = useQuery({
    queryKey: queryKeys.entitySection(organisationId ?? "", entityId, "relationships"),
    queryFn: () => entitiesApi.section<RelationshipGraph>(entityId, "relationships"),
    enabled: Boolean(organisationId && entityId && detail.isSuccess),
  });

  if (detail.isPending) return <LoadingState title="Loading entity" />;
  if (detail.isError) {
    return (
      <div className="tl-page">
        <PageHeader title="Entity" crumbs={[{ label: "Entities", to: "/app/entities" }]} />
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </div>
    );
  }

  const entity = detail.data;

  return (
    <div className="tl-page">
      <PageHeader
        title={entity.canonical_name}
        description={`${entity.entity_type} · tracked since ${new Date(entity.created_at).toLocaleDateString()}`}
        crumbs={[{ label: "Entities", to: "/app/entities" }, { label: entity.canonical_name }]}
      />
      <div className="tl-split">
        <Section title="Facts">
          <Card>
            <dl className="tl-facts">
              <div>
                <dt>Type</dt>
                <dd>
                  <Badge tone={entity.entity_type === "PERSON" ? "teal" : "info"}>
                    {entity.entity_type}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt>Also known as</dt>
                <dd>
                  {entity.aliases && entity.aliases.length > 0 ? (
                    <span className="tl-badge-row">
                      {entity.aliases.map((alias) => (
                        <Badge key={alias}>{alias}</Badge>
                      ))}
                    </span>
                  ) : (
                    <span className="tl-muted">No aliases recorded.</span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Entity ID</dt>
                <dd className="tl-mono">{entity.entity_id}</dd>
              </div>
            </dl>
          </Card>
        </Section>
        <Section
          title="Relationships"
          description="Deterministic links observed in your meetings — co-occurrence and explicit statements."
        >
          <Card>
            {relationships.isPending ? (
              <LoadingState title="Loading relationships" />
            ) : relationships.isError ? (
              <ErrorState error={relationships.error} onRetry={() => void relationships.refetch()} />
            ) : relationships.data.relationships.length === 0 ? (
              <EmptyState
                title="No relationships yet"
                body="Links appear once this entity is mentioned alongside others in processed meetings."
              />
            ) : (
              <ul className="tl-rel-list">
                {relationships.data.relationships.slice(0, 12).map((edge, index) => {
                  const other =
                    edge.source_entity_id === entity.entity_id
                      ? edge.target_entity_id
                      : edge.source_entity_id;
                  return (
                    <li key={`${edge.relationship_type}-${other}-${index}`}>
                      <Badge tone="neutral">{edge.relationship_type ?? "RELATED"}</Badge>
                      {other ? (
                        <Link className="tl-link-strong" to={`/app/entities/${encodeURIComponent(other)}`}>
                          {other}
                        </Link>
                      ) : (
                        <span className="tl-muted">Unknown entity</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </Section>
      </div>
    </div>
  );
}
