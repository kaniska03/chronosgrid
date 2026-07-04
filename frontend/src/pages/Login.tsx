import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";

const DEMO_EMAIL = "demo@chronosgrid.dev";
const DEMO_PASSWORD = "Demo@1234";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const doLogin = async (e: string, p: string) => {
    setBusy(true);
    setError(null);
    try {
      await login(e, p);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-100 to-brand-50 p-4">
      <div className="card w-full max-w-md p-8">
        <h1 className="text-center text-2xl font-bold text-brand-700">⏱ ChronosGrid</h1>
        <p className="mt-1 text-center text-sm text-slate-500">
          Multi-tenant distributed job scheduler
        </p>
        <form className="mt-6 space-y-4"
          onSubmit={(e) => { e.preventDefault(); void doLogin(email, password); }}>
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" type="email" required className="input" value={email}
              onChange={(e) => setEmail(e.target.value)} placeholder="you@company.dev" />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input id="password" type="password" required className="input" value={password}
              onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
          </div>
          {error && <div className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</div>}
          <button type="submit" disabled={busy} className="btn-primary w-full justify-center">
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="mt-6 border-t border-slate-100 pt-4">
          <button
            data-testid="demo-login"
            disabled={busy}
            onClick={() => {
              setEmail(DEMO_EMAIL);
              setPassword(DEMO_PASSWORD);
              void doLogin(DEMO_EMAIL, DEMO_PASSWORD);
            }}
            className="btn-secondary w-full justify-center">
            🚀 Login as Demo User
          </button>
          <p className="mt-2 text-center text-xs text-slate-400">
            Demo credentials: {DEMO_EMAIL} / {DEMO_PASSWORD}
          </p>
        </div>
      </div>
    </div>
  );
}
