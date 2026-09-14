import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Tags } from "lucide-react";
import { entitiesApi } from "../../api/entities";
import { queryKeys } from "../../api/keys";
import { useOrganisation } from "../../auth/OrganisationContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import type { EntityType } from "../../types/entities";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, Section } from "../../components/ui/Card";
import { Field, Input, Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { Table } from "../../components/ui/Table";
import { Tabs } from "../../components/ui/Tabs";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "PERSON", label: "People" },
  { id: "ISSUE", label: "Issues" },
] as const;

/* Entities: real registry list with type filter + real creation.
 * Resolution quality (ambiguous/unresolved) arrives with the workspace. */

export function EntitiesPage(): React.JSX.Element {
  useDocumentTitle("Entities");
  const { organisationId } = useOrganisation();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [filter, setFilter] = useState<string>("all");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<EntityType>("ISSUE");
  const [error, setError] = useState<string | null>(null);

  const entityType = filter === "all" ? null : (filter as EntityType);
  const list = useQuery({
    queryKey: queryKeys.entities(organisationId ?? "", entityType),
    queryFn: () => entitiesApi.list(entityType),
    enabled: Boolean(organisationId),
  });

  const create = useMutation({
    mutationFn: () => entitiesApi.create({ entity_type: kind, canonical_name: name.trim() }),
    onSuccess: () => {
      setName("");
      setError(null);
      toast({ title: "Entity recorded" });
      void queryClient.invalidateQueries({ queryKey: queryKeys.entities(organisationId ?? "") });
    },
    onError: (failure) => setError(userFacingMessage(failure)),
  });

  function onCreate(event: FormEvent): void {
    event.preventDefault();
    setError(null);
    create.mutate();
  }

  return (
    <div className="tl-page">
      <PageHeader
        title="Entities"
        description="The people and issues ThreadLine tracks across your meetings. Names resolve conservatively — nothing merges silently."
      />
      <Tabs
        label="Filter entities by type"
        items={FILTERS.map((item) => ({ ...item }))}
        value={filter}
        onChange={setFilter}
      />
      {list.isPending ? (
        <LoadingState title="Loading entities" />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.length === 0 ? (
        <EmptyState
          title={filter === "all" ? "No entities yet" : `No ${filter.toLowerCase()}s yet`}
          body="Entities appear as meetings are processed, or record one below to start tracking it."
          icon={<Tags aria-hidden="true" />}
        />
      ) : (
        <Card padded={false}>
          <Table
            caption="Canonical entities"
            columns={[
              {
                key: "name",
                header: "Name",
                render: (row) => (
                  <Link className="tl-link-strong" to={`/app/entities/${encodeURIComponent(row.entity_id)}`}>
                    {row.canonical_name}
                  </Link>
                ),
              },
              {
                key: "type",
                header: "Type",
                render: (row) => (
                  <Badge tone={row.entity_type === "PERSON" ? "teal" : "info"}>{row.entity_type}</Badge>
                ),
              },
              {
                key: "aliases",
                header: "Also known as",
                render: (row) =>
                  row.aliases && row.aliases.length > 0 ? (
                    <span className="tl-muted">{row.aliases.join(", ")}</span>
                  ) : (
                    <span className="tl-muted">—</span>
                  ),
              },
            ]}
            rows={list.data}
            keyOf={(row) => row.entity_id}
          />
        </Card>
      )}
      <Section title="Record an entity" description="Track something new explicitly. Duplicates return the existing record.">
        <Card>
          {error ? (
            <Alert tone="danger" title="Couldn't record the entity">
              {error}
            </Alert>
          ) : null}
          <form onSubmit={onCreate} className="tl-form-inline">
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
              <Select id="entity-type" value={kind} onChange={(event) => setKind(event.target.value as EntityType)}>
                <option value="ISSUE">Issue</option>
                <option value="PERSON">Person</option>
              </Select>
            </Field>
            <div className="tl-form-inline-action">
              <Button type="submit" variant="primary" loading={create.isPending}>
                Record
              </Button>
            </div>
          </form>
        </Card>
      </Section>
    </div>
  );
}
