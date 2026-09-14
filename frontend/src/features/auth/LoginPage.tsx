import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { ApiError, userFacingMessage } from "../../types/api";
import { Alert } from "../../components/ui/Alert";
import { Button } from "../../components/ui/Button";
import { Field, Input } from "../../components/ui/Input";
import { Logo } from "../../components/layout/Logo";

/* Sign-in: email + password against the real backend. Failures share the
 * backend's generic message; field errors surface inline, never as toasts. */

export function LoginPage(): React.JSX.Element {
  useDocumentTitle("Sign in");
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/app/dashboard";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status === "authenticated") return <Navigate to={from} replace />;
  if (status === "loading") {
    return (
      <div className="tl-auth-wrap" role="status" aria-label="Checking session">
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
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (failure) {
      // The backend deliberately returns generic credential copy ("invalid
      // email or password") for 401s on this endpoint; show it rather than
      // the generic "session expired" message.
      setError(
        failure instanceof ApiError && failure.message
          ? failure.message
          : userFacingMessage(failure),
      );
      if (!(failure instanceof ApiError) || failure.kind !== "validation") {
        setPassword("");
      }
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
        <h1 className="tl-auth-title">Welcome back</h1>
        <p className="tl-body-secondary">Sign in to your ThreadLine organisation.</p>

        {status === "bootstrap" ? (
          <Alert tone="info" title="First time here?">
            This server has no users yet.{" "}
            <Link to="/setup">Create the first organisation</Link> to get started.
          </Alert>
        ) : null}

        {error ? (
          <Alert tone="danger" title="Couldn't sign you in">
            {error}
          </Alert>
        ) : null}

        <form onSubmit={onSubmit} noValidate={false} className="tl-auth-form">
          <Field label="Email" htmlFor="login-email" required>
            <Input
              id="login-email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@organisation.example"
            />
          </Field>
          <Field label="Password" htmlFor="login-password" required>
            <Input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Button type="submit" variant="primary" fullWidth loading={busy}>
            Sign in
          </Button>
        </form>
      </div>
      <p className="tl-auth-foot">
        ThreadLine keeps your organisation's memory private to your organisation.
      </p>
    </div>
  );
}
