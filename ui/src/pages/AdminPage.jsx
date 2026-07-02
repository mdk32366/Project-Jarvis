import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api.js";

const EMPTY = { name: "", description: "", system_prompt: "", tools: [], enabled: true };

function AgentForm({ initial, tools, onSubmit, onCancel, submitting, submitLabel }) {
  const [form, setForm] = useState(initial);
  const toggleTool = (t) =>
    setForm((f) => ({
      ...f,
      tools: f.tools.includes(t) ? f.tools.filter((x) => x !== t) : [...f.tools, t],
    }));

  return (
    <form
      className="agent-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (form.name.trim()) onSubmit(form);
      }}
    >
      <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
        placeholder="e.g. scheduling" /></label>
      <label>Description<input value={form.description}
        onChange={(e) => setForm({ ...form, description: e.target.value })}
        placeholder="What this agent is for (shown to the delegator)" /></label>
      <label>System prompt<textarea rows={4} value={form.system_prompt}
        onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
        placeholder="How this specialist should behave" /></label>
      <div className="tools">
        <span className="hint">Tools</span>
        {tools?.length ? tools.map((t) => (
          <label key={t} className="tool-check">
            <input type="checkbox" checked={form.tools.includes(t)} onChange={() => toggleTool(t)} /> {t}
          </label>
        )) : <span className="hint">no tools available</span>}
      </div>
      <label className="tool-check">
        <input type="checkbox" checked={form.enabled}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Enabled
      </label>
      <div className="row">
        <button type="submit" disabled={submitting}>{submitLabel}</button>
        {onCancel && <button type="button" className="link-btn" onClick={onCancel}>cancel</button>}
      </div>
    </form>
  );
}

export default function AdminPage() {
  const qc = useQueryClient();
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api.get("/agents") });
  const tools = useQuery({ queryKey: ["agent-tools"], queryFn: () => api.get("/agents/tools") });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.get("/audit"), refetchInterval: 15000 });
  const calHealth = useQuery({ queryKey: ["cal-health"], queryFn: () => api.get("/calendar/health") });

  const [editingId, setEditingId] = useState(null);
  const [creating, setCreating] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["agents"] });
  const createAgent = useMutation({ mutationFn: (a) => api.post("/agents", a),
    onSuccess: () => { setCreating(false); invalidate(); } });
  const updateAgent = useMutation({ mutationFn: ({ id, ...a }) => api.put(`/agents/${id}`, a),
    onSuccess: () => { setEditingId(null); invalidate(); } });
  const deleteAgent = useMutation({ mutationFn: (id) => api.del(`/agents/${id}`), onSuccess: invalidate });

  const toolList = tools.data?.tools ?? [];

  return (
    <div className="admin">
      <h1>Admin</h1>
      <p className="hint">Configure specialist agents and review JARVIS's activity.</p>

      <div className="card">
        <div className="row space">
          <h2>Calendar status</h2>
          <button onClick={() => calHealth.refetch()} disabled={calHealth.isFetching}>
            {calHealth.isFetching ? "Checking…" : "Re-check"}
          </button>
        </div>
        {calHealth.isError && <p className="error">Could not reach calendar check.</p>}
        {calHealth.data && (
          <pre className="cal-result">{calHealth.data.result}</pre>
        )}
        <p className="hint">Raw output of the calendar tool — no AI paraphrasing.</p>
      </div>

      <div className="card">
        <div className="row space">
          <h2>Agents</h2>
          {!creating && <button onClick={() => setCreating(true)}>+ New agent</button>}
        </div>
        {creating && (
          <AgentForm initial={EMPTY} tools={toolList} submitting={createAgent.isPending}
            submitLabel="Create agent" onSubmit={(a) => createAgent.mutate(a)}
            onCancel={() => setCreating(false)} />
        )}
        {createAgent.isError && <p className="error">{String(createAgent.error.message)}</p>}

        <ul className="agent-list">
          {agents.data?.map((a) => (
            <li key={a.id} className="agent">
              {editingId === a.id ? (
                <AgentForm initial={a} tools={toolList} submitting={updateAgent.isPending}
                  submitLabel="Save" onSubmit={(f) => updateAgent.mutate({ id: a.id, ...f })}
                  onCancel={() => setEditingId(null)} />
              ) : (
                <div>
                  <div className="row space">
                    <strong>{a.name}{!a.enabled && <span className="tag"> disabled</span>}</strong>
                    <span>
                      <button className="link-btn" onClick={() => setEditingId(a.id)}>edit</button>
                      <button className="link-btn" onClick={() => deleteAgent.mutate(a.id)}>delete</button>
                    </span>
                  </div>
                  <div className="hint">{a.description}</div>
                  <div>{a.tools.length ? a.tools.map((t) => <span key={t} className="tag">{t}</span>) : <span className="hint">no tools</span>}</div>
                </div>
              )}
            </li>
          ))}
          {agents.data?.length === 0 && <li className="hint">No agents yet.</li>}
        </ul>
      </div>

      <div className="card">
        <h2>Activity log</h2>
        <p className="hint">Recent tool calls and delegations. Infra logs (uvicorn/worker) live in Fly — use <code>fly logs</code>.</p>
        <table className="audit">
          <thead><tr><th>Tool</th><th>Status</th><th>Channel</th><th>Actor</th><th>Result</th></tr></thead>
          <tbody>
            {audit.data?.map((r) => (
              <tr key={r.id}>
                <td>{r.tool}</td>
                <td><span className={`tag ${r.status}`}>{r.status}</span></td>
                <td>{r.channel}</td>
                <td>{r.actor}</td>
                <td className="truncate" title={r.result}>{r.result}</td>
              </tr>
            ))}
            {audit.data?.length === 0 && <tr><td colSpan={5} className="hint">No activity yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
