/**
 * The agent system prompt was editable but invisible.
 *
 * SYMPTOM (owner, 2026-08-02): "I never found an element of the user interface
 * where I can see each prompt for each agent and then update it."
 *
 * MECHANISM: the capability existed the whole time — Admin → Agents → `edit`
 * opens a form containing a System prompt textarea, and `PUT /api/agents/{id}`
 * persists it. But the COLLAPSED row rendered name, description and tools and
 * never the prompt. Nothing on the surface indicated a prompt existed, so the
 * only way to discover it was to click `edit` (a text-styled link sitting next
 * to `delete`) on the off-chance. A capability you cannot see is one you do not
 * have.
 *
 * This matters more than cosmetics here: `seed_agents` deliberately never
 * overwrites `system_prompt`, so the Admin UI is the ONLY route by which a
 * prompt change reaches production. An invisible control on the only path is a
 * gap in the path.
 *
 * These tests are the regression: they must be RED against the old row.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminPage from "../pages/AdminPage.jsx";
import { api } from "../lib/api.js";

const SECRETARY_PROMPT =
  "You are JARVIS's secretary. Draft emails with draft_email and return the FULL " +
  "draft. PLANNING SESSIONS are for thinking something through, not for producing " +
  "a document on request.";

const AGENTS = [
  { id: 1, name: "secretary", description: "Email, tasks, projects.",
    system_prompt: SECRETARY_PROMPT, tools: ["draft_email", "add_task"], enabled: true },
  { id: 2, name: "netstatus", description: "LAN status.",
    system_prompt: "", tools: ["get_node_status"], enabled: true },
];

function renderAdmin() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AdminPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "get").mockImplementation((path) => {
    if (path === "/agents") return Promise.resolve(AGENTS);
    if (path === "/agents/tools") return Promise.resolve(["draft_email", "add_task", "get_node_status"]);
    return Promise.resolve([]);
  });
});

describe("agent system prompts are visible without entering edit mode", () => {
  it("shows the prompt text on the collapsed agent row", async () => {
    renderAdmin();

    // The prompt is reachable without clicking `edit` — that is the whole point.
    const disclosure = await screen.findByText(/System prompt \(\d+ chars\)/);
    expect(disclosure).toBeTruthy();
    expect(screen.getByText(SECRETARY_PROMPT)).toBeTruthy();
  });

  it("states the prompt length so a truncated-looking row is not mistaken for the whole prompt", async () => {
    renderAdmin();
    const disclosure = await screen.findByText(
      new RegExp(`System prompt \\(${SECRETARY_PROMPT.length} chars\\)`),
    );
    expect(disclosure).toBeTruthy();
  });

  it("says so explicitly when an agent has no prompt, rather than showing nothing", async () => {
    renderAdmin();
    // Absence must be STATED. A blank space is indistinguishable from a control
    // that isn't rendered — which is the bug this test exists for.
    expect(await screen.findByText(/No system prompt set/)).toBeTruthy();
  });

  it("still offers edit, so seeing the prompt and changing it are one flow", async () => {
    renderAdmin();
    await screen.findByText(/System prompt \(\d+ chars\)/);

    const edits = screen.getAllByRole("button", { name: /^edit$/ });
    await userEvent.click(edits[0]);

    // The edit form's textarea is prefilled with the prompt you were just reading.
    const box = await screen.findByPlaceholderText(/How this specialist should behave/);
    expect(box.value).toBe(SECRETARY_PROMPT);
  });
});
