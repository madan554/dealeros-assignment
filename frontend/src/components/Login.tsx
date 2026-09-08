import { useState } from "react";
import { login, saveSession, type Session } from "../api";

export function Login({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [username, setUsername] = useState("alice");
  const [password, setPassword] = useState("demo-password");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await login(username, password);
      saveSession(session);
      onSignedIn(session);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>Reconciliation exceptions</h1>
      <p className="muted">
        Two demo users, one organisation each. Sign in as both to see that
        neither can reach the other's rows.
      </p>
      <label>
        Username
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <button type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      {error && <p className="error">{error}</p>}
      <table className="demo-users">
        <thead>
          <tr>
            <th>User</th>
            <th>Org</th>
            <th>Locations</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>alice</td>
            <td>ORG-A</td>
            <td>LOC-101, LOC-102, LOC-103</td>
          </tr>
          <tr>
            <td>bob</td>
            <td>ORG-B</td>
            <td>LOC-201, LOC-202</td>
          </tr>
        </tbody>
      </table>
    </form>
  );
}
