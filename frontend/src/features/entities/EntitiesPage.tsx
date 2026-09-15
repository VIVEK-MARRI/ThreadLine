import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useOrganisation } from "../../auth/OrganisationContext";
import { canRecordEntity } from "../../auth/permissions";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import type { EntityType } from "../../types/entities";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { StatusBadge } from "../../components/ui/Badge";
import { Card, Section } from "../../components/ui/Card";
import { Field, Input, Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { Table } from "../../components/ui/Table";
import { Tabs } from "../../components/ui/Tabs";
import { EmptyState, ErrorState, Skeleton } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";
import { attentionLevelTone } from "../../components/ui/status";
import { useEntityDirectoryRows, useRecordEntity } from "./useEntities";
import {
  ENTITY_DIRECTORY_SORT_OPTIONS,
  ENTITY_DIRECTORY_STATE_OPTIONS,
  applyEntityDirectoryFilters,
  attentionLevelLabel,
  normalizeEntityQuery,
  parseEntityDirectorySort,
  parseEntityDirectoryState,
  parseEntityDirectoryType,
  temporalStateLabel,
  temporalStateTone,
  type EntityDirectoryTypeFilter,
} from "./entitiesFormat";
import "./entities.css";

/* Entity directory: the canonical registry joined to the organisation
 * portfolio. Backend supports type filtering; search, state, and sort are
 * transparent presentation controls over the loaded tenant-scoped directory.
 */

const SEARCH_DEBOUNCE_MS = 180;

export function EntitiesPage(): React.JSX.Element {
  useDocumentTitle("Entities");
  const { role } = useOrganisation();
  const { toast } = useToast();
  const [params, setParams] = useSearchParams();

  const type = parseEntityDirectoryType(params.get("type"));
  const state = parseEntityDirectoryState(params.get("state"));
  const sort = parseEntityDirectorySort(params.get("sort"));
  const urlQuery = params.get("q") ?? "";
  const [draftQuery, setDraftQuery] = useState(urlQuery);

  const entityType: EntityType | null = type === "ALL" ? null : type;
  const directory = useEntityDirectoryRows(entityType);
  const filters = useMemo(
    () => ({ query: normalizeEntityQuery(urlQuery), type, state, sort }),
    [urlQuery, type, state, sort],
  );
  const visible = useMemo(
    () => applyEntityDirectoryFilters(directory.rows, filters),
    [directory.rows, filters],
  );
  const filtersActive = urlQuery !== "" || type !== "ALL" || state !== "ALL" || sort !== "attention";

  const [name, setName] = useState("");
  const [kind, setKind] = useState<EntityType>("ISSUE");
  const [formError, setFormError] = useState<string | null>(null);
  const record = useRecordEntity();

  const updateParam = useCallback(
    (filterName: string, value: string) => {
      const next = new URLSearchParams(params);
      if (value) next.set(filterName, value);
      else next.delete(filterName);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  useEffect(() => {
    setDraftQuery(urlQuery);
  }, [urlQuery]);

  useEffect(() => {
    const trimmed = draftQuery.trim();
    if (trimmed === urlQuery) return;
    const timer = window.setTimeout(() => {
      updateParam("q", trimmed);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [draftQuery, urlQuery, updateParam]);

  function onCreate(event: FormEvent): void {
    event.preventDefault();
    const canonicalName = name.trim();
    if (!canonicalName) {
      setFormError("Add an entity name before recording it.");
      return;
    }
    setFormError(null);
    record.mutate(
      { entity_type: kind, canonical_name: canonicalName },
      {
        onSuccess: () => {
          setName("");
          toast({ title: "Entity recorded" });
        },
        onError: (failure) => setFormError(userFacingMessage(failure)),
      },
    );
  }

  function renderRows(): React.JSX.Element {
    if (directory.listPending) {
      return (
        <div className="tl-entity-loading-block">
          <Skeleton lines={6} label="Loading entities" />
        </div>
      );
    }
    if (directory.listError) {
      return (
        <ErrorState
          title="Couldn't load entities"
          error={directory.listError}
          onRetry={() => directory.refetchList()}
        />
      );
    }
    if (directory.rows.length === 0) {
      return (
        <EmptyState
          title="No entities yet"
          body="ThreadLine records entities when meetings are processed, or you can record one below to start tracking it."
        />
      );
    }
    return (
      <>
        {directory.portfolioError ? (
          <Alert tone="warning" title="Organisation intelligence is temporarily unavailable">
            The directory below shows registry facts only. Current state and attention will return
            when the intelligence snapshot loads again.
          </Alert>
        ) : null}
        <p className="tl-entity-result-count" role="status">
          {`Showing ${visible.length} of ${directory.rows.length} ${
            directory.rows.length === 1 ? "entity" : "entities"
          } · intelligence assessed for ${directory.assessedCount}.`}
          {` Search narrows this loaded directory; type filters use the entity registry.`}
        </p>
        <Table<(typeof visible)[number]>
          caption={`Entities, ${type === "ALL" ? "all types" : type.toLowerCase()} · ${sort}`}
          columns={[
            {
              key: "entity",
              header: "Entity",
              render: (row) => (
                <span>
                  <Link
                    className="tl-entity-row-title"
                    to={`/app/entities/${encodeURIComponent(row.entity.entity_id)}`}
                  >
                    {row.entity.canonical_name}
                  </Link>
                  <span className="tl-entity-row-meta">
                    {row.entity.entity_type}
                    {row.entity.aliases && row.entity.aliases.length > 0
                      ? ` · also known as ${row.entity.aliases.join(", ")}`
                      : ""}
                  </span>
                </span>
              ),
            },
            {
              key: "state",
              header: "Current state",
              render: (row) =>
                row.summary ? (
                  <StatusBadge
                    tone={temporalStateTone(row.summary.current_state)}
                    label={temporalStateLabel(row.summary.current_state)}
                  />
                ) : (
                  <span className="tl-entity-cell-muted">No assessed signals</span>
                ),
            },
            {
              key: "attention",
              header: "Attention",
              render: (row) =>
                row.summary?.attention_level ? (
                  <StatusBadge
                    tone={attentionLevelTone(row.summary.attention_level)}
                    label={`${attentionLevelLabel(row.summary.attention_level)} · ${
                      row.summary.attention_score
                    }`}
                  />
                ) : (
                  <span className="tl-entity-cell-muted">No attention signals</span>
                ),
            },
            {
              key: "evidence",
              header: "Evidence trail",
              render: (row) =>
                row.summary ? (
                  <span className="tl-entity-cell-muted">
                    {`${row.summary.observation_count} observations · ${row.summary.action_count} actions · ${row.summary.impact_count} impacts`}
                  </span>
                ) : (
                  <span className="tl-entity-cell-muted">Registry record only</span>
                ),
            },
          ]}
          rows={visible}
          keyOf={(row) => row.entity.entity_id}
          empty={
            <EmptyState
              title="No loaded entities match these filters."
              body="Clear the search or filters to see the loaded entity directory again."
              action={
                <Button variant="secondary" onClick={() => setParams({}, { replace: true })}>
                  Clear search and filters
                </Button>
              }
            />
          }
        />
      </>
    );
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Entities"
        description="The organisation’s living memory: canonical entities joined to current state, attention, and evidence."
        crumbs={[{ label: "Entities" }]}
      />
      <Tabs
        label="Filter entities by type"
        items={[
          { id: "ALL", label: "All" },
          { id: "PERSON", label: "People" },
          { id: "ISSUE", label: "Issues" },
        ]}
        value={type}
        onChange={(id) =>
          updateParam("type", (id as EntityDirectoryTypeFilter) === "ALL" ? "" : id)
        }
      />
      <Section
        title="Entity directory"
        description="The registry and intelligence snapshot load together. Type is filtered by the backend."
      >
        <form
          role="search"
          aria-label="Find entities"
          className="tl-entity-toolbar"
          onSubmit={(event) => event.preventDefault()}
        >
          <Field
            label="Search entities"
            htmlFor="entity-search"
            hint="Matches canonical names, aliases, and IDs in the loaded directory."
          >
            <Input
              id="entity-search"
              type="search"
              value={draftQuery}
              onChange={(event) => setDraftQuery(event.target.value)}
              placeholder="Search name, alias, or ID"
            />
          </Field>
          <Field label="Current state" htmlFor="entity-state">
            <Select
              id="entity-state"
              value={state}
              onChange={(event) =>
                updateParam("state", event.target.value === "ALL" ? "" : event.target.value)
              }
            >
              {ENTITY_DIRECTORY_STATE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Sort" htmlFor="entity-sort">
            <Select
              id="entity-sort"
              value={sort}
              onChange={(event) =>
                updateParam("sort", event.target.value === "attention" ? "" : event.target.value)
              }
            >
              {ENTITY_DIRECTORY_SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <div className="tl-entity-toolbar-actions">
            <Button
              type="button"
              variant="secondary"
              disabled={!filtersActive}
              onClick={() => {
                setParams({}, { replace: true });
                setDraftQuery("");
              }}
            >
              Clear
            </Button>
          </div>
        </form>
        {renderRows()}
      </Section>
      {canRecordEntity(role) ? (
        <Section
          title="Record an entity"
          description="Track something new explicitly. Duplicates return the existing record."
        >
          <Card>
            <form onSubmit={onCreate} className="tl-form-inline" aria-label="Record entity">
              <Field label="Name" htmlFor="entity-name" required>
                <Input
                  id="entity-name"
                  required
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Payment API instability"
                />
              </Field>
              <Field label="Type" htmlFor="entity-type" required>
                <Select
                  id="entity-type"
                  value={kind}
                  onChange={(event) => setKind(event.target.value as EntityType)}
                >
                  <option value="ISSUE">Issue</option>
                  <option value="PERSON">Person</option>
                </Select>
              </Field>
              <div className="tl-form-inline-action">
                <Button type="submit" variant="primary" loading={record.isPending}>
                  Record
                </Button>
              </div>
            </form>
            {formError ? (
              <Alert tone="danger" title="Couldn't record the entity">
                {formError}
              </Alert>
            ) : null}
          </Card>
        </Section>
      ) : null}
    </div>
  );
}
