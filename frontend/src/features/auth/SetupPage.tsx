import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { userFacingMessage } from "../../types/api";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/Input";
import { Logo } from "../../components/layout/Logo";

/* First-run setup: creates the first organisation + owner. Reachable only
 * while the backend reports bootstrap mode; afterwards it redirects away. */

export function SetupPage(): React.JSX.Element {
  useDocumentTitle("Set up ThreadLine");
  const { status, bootstrap } = useAuth();
  const navigate = useNavigate();

  const [organisationName, setOrganisationName] = useState("");
  const [slug, setSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status === "authenticated") return <Navigate to="/app/dashboard" replace />;
  if (status === "login") return <Navigate to="/login" replace />;
  if (status === "loading") {
    return (
      <div className="tl-auth-wrap" role="status" aria-label="Checking server state">
        <span className="tl-spinner tl-spinner-lg" aria-hidden="true" />
      </div>
    );
  }

  async function onSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await bootstrap({
        organisation_name: organisationName.trim(),
        slug: slug.trim().toLowerCase(),
        admin_email: email.trim(),
        password,
      });
      navigate("/app/dashboard", { replace: true });
    } catch (failure) {
      setError(userFacingMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tl-auth-wrap">
      <div className="tl-auth-card">
        <div className="tl-auth-brand">
          <Logo />
        </div>
        <h1 className="tl-auth-title">Set up ThreadLine</h1>
        <p className="tl-body-secondary">
          Create your organisation and its first owner. There is no default
          password — you choose the credential.
        </p>

        {error ? (
          <Alert tone="danger" title="Couldn't finish setup">
            {error}
          </Alert>
        ) : null}

        <form onSubmit={onSubmit} className="tl-auth-form">
          <Field label="Organisation name" htmlFor="setup-org" required>
            <Input
              id="setup-org"
              required
              value={organisationName}
              onChange={(event) => setOrganisationName(event.target.value)}
              placeholder="Acme Corporation"
            />
          </Field>
          <Field
            label="Organisation handle"
            htmlFor="setup-slug"
            hint="Lowercase letters, numbers, dashes. Used in your workspace address."
            required
          >
            <Input
              id="setup-slug"
              required
              pattern="[a-z0-9-_]+"
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              placeholder="acme"
            />
          </Field>
          <Field label="Owner email" htmlFor="setup-email" required>
            <Input
              id="setup-email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@organisation.example"
            />
          </Field>
          <Field
            label="Owner password"
            htmlFor="setup-password"
            hint="At least 8 characters. Stored as a salted hash, never in plain text."
            required
          >
            <Input
              id="setup-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Button type="submit" variant="primary" fullWidth loading={busy}>
            Create organisation
          </Button>
        </form>
      </div>
      <p className="tl-auth-foot">
        Already set up? <Link to="/login">Sign in</Link>
      </p>
    </div>
  );
}
