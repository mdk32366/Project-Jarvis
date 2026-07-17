import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api.js";

// Status page: signed-in identity + live API/DB health.
export default function DashboardPage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get("/health", { auth: false }),
  });

  const me = useQuery({ queryKey: ["me"], queryFn: () => api.me() });

  return (
    <div className="card">
      <h1>Status</h1>
      <p>Signed in as <strong>{me.data?.username ?? "…"}</strong>.</p>

      <h2>API health</h2>
      {health.isLoading && <p>Checking…</p>}
      {health.isError && <p className="error">Could not reach API.</p>}
      {health.data && (
        <ul>
          <li>Status: {health.data.status}</li>
          <li>Environment: {health.data.environment}</li>
          <li>Database: {health.data.database}</li>
        </ul>
      )}
    </div>
  );
}
