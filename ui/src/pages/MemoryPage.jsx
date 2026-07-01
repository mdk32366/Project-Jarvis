import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api.js";

// Inspect and correct what JARVIS knows about you: persona, standing
// preferences, and learned facts (which you can delete).
export default function MemoryPage() {
  const qc = useQueryClient();
  const persona = useQuery({ queryKey: ["persona"], queryFn: () => api.get("/memory/persona") });
  const prefs = useQuery({ queryKey: ["prefs"], queryFn: () => api.get("/memory/preferences") });
  const memories = useQuery({ queryKey: ["memories"], queryFn: () => api.get("/memory") });

  const [newFact, setNewFact] = useState("");

  const addFact = useMutation({
    mutationFn: () => api.post("/memory", { content: newFact, category: "manual" }),
    onSuccess: () => {
      setNewFact("");
      qc.invalidateQueries({ queryKey: ["memories"] });
    },
  });

  const delFact = useMutation({
    mutationFn: (id) => api.del(`/memory/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memories"] }),
  });

  return (
    <div className="memory">
      <h1>Memory</h1>
      <p className="hint">What JARVIS uses to think and act like you.</p>

      <div className="card">
        <h2>Persona</h2>
        <ul>
          {persona.data?.map((p) => (
            <li key={p.id}><span className="tag">{p.category}</span> {p.content}</li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>Standing preferences</h2>
        <ul>
          {prefs.data?.map((p) => (
            <li key={p.id}><strong>{p.key}:</strong> {p.value}</li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>Learned facts</h2>
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            if (newFact.trim()) addFact.mutate();
          }}
        >
          <input
            value={newFact}
            onChange={(e) => setNewFact(e.target.value)}
            placeholder="Add a fact JARVIS should remember…"
          />
          <button type="submit" disabled={addFact.isPending}>Add</button>
        </form>
        <ul>
          {memories.data?.map((m) => (
            <li key={m.id}>
              <span className="tag">{m.category}</span> {m.content}
              <button className="link-btn" onClick={() => delFact.mutate(m.id)}>delete</button>
            </li>
          ))}
          {memories.data?.length === 0 && <li className="hint">Nothing learned yet.</li>}
        </ul>
      </div>
    </div>
  );
}
