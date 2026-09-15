import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { organisationsApi } from "../../api/organisations";
import { jobsApi } from "../../api/intelligence";
import { queryKeys } from "../../api/keys";
import { useAuth } from "../../auth/AuthContext";
import { useOrganisation } from "../../auth/OrganisationContext";
import {
  canGrantOwner,
  canManageMembers,
  canManageOrganisation,
  canViewDiagnostics,
} from "../../auth/permissions";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import type { Role } from "../../types/auth";
import { Alert } from "../../components/ui/Alert";
import { Avatar } from "../../components/ui/Avatar";
import { Badge, StatusBadge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, Section } from "../../components/ui/Card";
import { ConfirmationDialog } from "../../components/ui/Dialog";
import { Field, Input, Select } from "../../components/ui/Input";
import { PageHeader } from "../../components/layout/PageHeader";
import { Table } from "../../components/ui/Table";
import { EmptyState, ErrorState, LoadingState } from "../../components/feedback/States";
import { useToast } from "../../components/ui/Toast";

/* Settings: identity, organisation, membership, and system status.
 * Everything here is real backend state; affordances hide by role, but the
 * backend enforces every mutation regardless. */

function MembersPanel({
  organisationId,
  role,
  currentUserId,
}: {
  organisationId: string;
  role: Role | null;
  currentUserId: string | null;
}): React.JSX.Element {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState<string | null>(null);
  const [removing, setRemoving] = useState<{ userId: string; email: string | null } | null>(null);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<Role>("MEMBER");
  const [memberPassword, setMemberPassword] = useState("");

  const members = useQuery({
    queryKey: queryKeys.members(organisationId),
    queryFn: () => organisationsApi.listMembers(organisationId),
    enabled: canManageMembers(role),
  });

  function invalidateMembers(): void {
    void queryClient.invalidateQueries({ queryKey: queryKeys.members(organisationId) });
  }

  const addMember = useMutation({
    mutationFn: () =>
      organisationsApi.addMember(organisationId, {
        email: memberEmail.trim(),
        role: memberRole,
        password: memberPassword || null,
      }),
    onSuccess: () => {
      setMemberEmail("");
      setMemberPassword("");
      setMemberRole("MEMBER");
      setFailure(null);
      invalidateMembers();
      toast({ title: "Member added" });
    },
    onError: (error) => setFailure(userFacingMessage(error)),
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => organisationsApi.removeMember(organisationId, userId),
    onSuccess: () => {
      setRemoving(null);
      invalidateMembers();
      toast({ title: "Member removed", body: "Their access ended immediately." });
    },
    onError: (error) => {
      setRemoving(null);
      setFailure(userFacingMessage(error));
    },
  });

  return (
    <>
      {failure ? (
        <Alert tone="danger" title="Couldn't save">
          {failure}
        </Alert>
      ) : null}
      <Section
        title="Members"
        description="Owners and admins manage membership. Removal ends access immediately."
      >
        <Card padded={false}>
          {members.isPending ? (
            <div className="tl-card-pad">
              <LoadingState title="Loading members" />
            </div>
          ) : members.isError ? (
            <div className="tl-card-pad">
              <ErrorState error={members.error} onRetry={() => void members.refetch()} />
            </div>
          ) : members.data.length === 0 ? (
            <div className="tl-card-pad">
              <EmptyState title="No members" body="This organisation has no active members." />
            </div>
          ) : (
            <Table
              caption="Organisation members"
              columns={[
                {
                  key: "email",
                  header: "Email",
                  render: (row) => <span className="tl-link-strong">{row.email ?? row.user_id}</span>,
                },
                {
                  key: "role",
                  header: "Role",
                  render: (row) => <Badge tone={row.role === "OWNER" ? "warning" : row.role === "ADMIN" ? "info" : "neutral"}>{row.role}</Badge>,
                },
                {
                  key: "status",
                  header: "Status",
                  render: (row) => (
                    <StatusBadge tone={row.status === "ACTIVE" ? "success" : "neutral"} label={row.status} />
                  ),
                },
                {
                  key: "actions",
                  header: "Actions",
                  align: "right",
                  render: (row) =>
                    row.user_id === currentUserId ? (
                      <span className="tl-muted">You</span>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setRemoving({ userId: row.user_id, email: row.email })}
                      >
                        Remove
                      </Button>
                    ),
                },
              ]}
              rows={members.data}
              keyOf={(row) => row.user_id}
            />
          )}
        </Card>
        <Card>
          <h3 className="tl-card-title">Invite a member</h3>
          <form
            className="tl-form-grid"
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              if (memberEmail.trim()) addMember.mutate();
            }}
          >
            <Field label="Email" htmlFor="member-email" required>
              <Input
                id="member-email"
                type="email"
                required
                value={memberEmail}
                onChange={(event) => setMemberEmail(event.target.value)}
                placeholder="teammate@organisation.example"
              />
            </Field>
            <Field label="Role" htmlFor="member-role">
              <Select
                id="member-role"
                value={memberRole}
                onChange={(event) => setMemberRole(event.target.value as Role)}
              >
                <option value="MEMBER">Member</option>
                <option value="ADMIN">Admin</option>
                {canGrantOwner(role) ? <option value="OWNER">Owner</option> : null}
              </Select>
            </Field>
            <Field
              label="Password (new accounts)"
              htmlFor="member-password"
              hint="Required only when this email has no ThreadLine account yet."
            >
              <Input
                id="member-password"
                type="password"
                autoComplete="new-password"
                value={memberPassword}
                onChange={(event) => setMemberPassword(event.target.value)}
              />
            </Field>
            <div>
              <Button type="submit" variant="primary" loading={addMember.isPending}>
                Add member
              </Button>
            </div>
          </form>
        </Card>
      </Section>
      {removing ? (
        <ConfirmationDialog
          title="Remove this member?"
          body={`${removing.email ?? removing.userId} will lose access to this organisation immediately. Their account elsewhere is unaffected.`}
          confirmLabel="Remove member"
          danger
          busy={removeMember.isPending}
          onConfirm={() => removeMember.mutate(removing.userId)}
          onCancel={() => setRemoving(null)}
        />
      ) : null}
    </>
  );
}

function DiagnosticsPanel({ organisationId }: { organisationId: string }): React.JSX.Element {
  const diagnostics = useQuery({
    queryKey: queryKeys.diagnostics(organisationId),
    queryFn: () => jobsApi.diagnostics(),
    enabled: true,
  });

  return (
    <Section title="System status" description="Live backend diagnostics for this workspace.">
      <Card>
        {diagnostics.isPending ? (
          <LoadingState title="Loading diagnostics" />
        ) : diagnostics.isError ? (
          <ErrorState error={diagnostics.error} onRetry={() => void diagnostics.refetch()} />
        ) : (
          <dl className="tl-facts">
            <div>
              <dt>Source backend</dt>
              <dd className="tl-mono">{diagnostics.data.source_backend}</dd>
            </div>
            <div>
              <dt>Jobs succeeded</dt>
              <dd className="tl-numeric">{diagnostics.data.background_job_counts.SUCCEEDED ?? 0}</dd>
            </div>
            <div>
              <dt>Jobs pending</dt>
              <dd className="tl-numeric">{diagnostics.data.background_job_counts.PENDING ?? 0}</dd>
            </div>
            <div>
              <dt>Jobs failed</dt>
              <dd className="tl-numeric">{diagnostics.data.background_job_counts.FAILED ?? 0}</dd>
            </div>
          </dl>
        )}
      </Card>
    </Section>
  );
}

export function SettingsPage(): React.JSX.Element {
  useDocumentTitle("Settings");
  const { user, logout } = useAuth();
  const { current, organisationId, role, organisations } = useOrganisation();
  const navigate = useNavigate();
  const { toast } = useToast();

  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const [newName, setNewName] = useState("");

  const rename = useMutation({
    mutationFn: () => organisationsApi.rename(organisationId ?? "", { name: newName.trim() }),
    onSuccess: () => {
      setNewName("");
      setNotice("Organisation renamed.");
      toast({ title: "Organisation renamed" });
    },
    onError: (error) => setFailure(userFacingMessage(error)),
  });

  async function signOut(): Promise<void> {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="tl-page">
      <PageHeader title="Settings" description="Your identity, organisation, and workspace status." />

      {notice ? (
        <Alert tone="success" title="Done">
          {notice}
        </Alert>
      ) : null}
      {failure ? (
        <Alert tone="danger" title="Couldn't save">
          {failure}
        </Alert>
      ) : null}

      <div className="tl-split">
        <Section title="Profile">
          <Card>
            <div className="tl-profile">
              <Avatar name={user?.email ?? "?"} size="lg" />
              <dl className="tl-facts">
                <div>
                  <dt>Email</dt>
                  <dd>{user?.email}</dd>
                </div>
                <div>
                  <dt>Role in {current?.organisation.name ?? "—"}</dt>
                  <dd>{role ? <Badge tone="teal">{role}</Badge> : <span className="tl-muted">—</span>}</dd>
                </div>
                <div>
                  <dt>Organisations</dt>
                  <dd className="tl-numeric">{organisations.filter((m) => m.status === "ACTIVE").length}</dd>
                </div>
              </dl>
            </div>
            <div className="tl-card-foot">
              <Button variant="secondary" onClick={() => void signOut()}>
                Sign out
              </Button>
            </div>
          </Card>
        </Section>

        <Section title="Organisation" description="The workspace whose data fills every screen.">
          <Card>
            <dl className="tl-facts">
              <div>
                <dt>Name</dt>
                <dd>{current?.organisation.name}</dd>
              </div>
              <div>
                <dt>Handle</dt>
                <dd className="tl-mono">{current?.organisation.slug}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>
                  <StatusBadge tone="success" label={current?.organisation.status ?? "—"} />
                </dd>
              </div>
            </dl>
            {canManageOrganisation(role) ? (
              <form
                className="tl-form-inline"
                onSubmit={(event: FormEvent) => {
                  event.preventDefault();
                  if (newName.trim()) rename.mutate();
                }}
              >
                <Field label="Rename organisation" htmlFor="org-rename">
                  <Input
                    id="org-rename"
                    value={newName}
                    onChange={(event) => setNewName(event.target.value)}
                    placeholder={current?.organisation.name}
                  />
                </Field>
                <div className="tl-form-inline-action">
                  <Button type="submit" variant="secondary" loading={rename.isPending}>
                    Rename
                  </Button>
                </div>
              </form>
            ) : null}
          </Card>
        </Section>
      </div>

      {organisationId && canManageMembers(role) ? (
        <MembersPanel organisationId={organisationId} role={role} currentUserId={user?.user_id ?? null} />
      ) : null}

      {organisationId && canViewDiagnostics(role) ? (
        <DiagnosticsPanel organisationId={organisationId} />
      ) : null}
    </div>
  );
}
