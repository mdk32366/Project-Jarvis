"""The prompt review ledger — agent × tool × disposition.

WHAT DEFECT THIS EXISTS FOR. `seed_agents` reconciles tool ROSTERS and
deliberately never overwrites `system_prompt` (an admin who tunes prose should
keep their wording). The consequence nobody was watching: **every new tool
reaches production and none of the prose explaining it does.** Nothing goes red.
The agent simply doesn't offer capabilities it has, which is indistinguishable
from not having them — the unwatched-instrument shape exactly, a known defect
class with a documented remedy and no alarm. See `docs/design-note-prompt-drift.md`.

WHY A LEDGER RATHER THAN SEVEN CURATED LISTS. A per-agent "these tools need
guidance" list catches the gaps we already know about; it cannot catch the ones
we haven't thought of, and **the defect is silent GROWTH, not any particular
missing sentence**. The ledger fails when a roster grows past what has been
reviewed — so a tool added next year trips it as surely as one added today. The
curated list still exists, but as a BYPRODUCT of review (the `guided` entries)
rather than a second artifact to maintain.

WHY A CODE FILE AND NOT A TABLE.

  * It is version-controlled, so a disposition is reviewable in the PR that adds
    the tool — and the PR adding a tool to a roster naturally touches this file
    right next to it.
  * A table would need a migration, an admin surface, and a seeding path — and
    would put the record of a REVIEW DECISION somewhere a deploy could reconcile
    it away. That is the original defect wearing a new hat.

WHAT THIS GUARD CANNOT DO — §4, and it is not a small caveat.

**The guard reads the SEED, not production.** Rosters are reconciled from seed,
so the seed is the right trigger: roster growth is visible there. But prompts are
NOT reconciled, so the *fix* is partly outside CI — editing
`DEFAULT_AGENTS[...].system` turns the test green while production keeps its old
prompt.

  1. **Green CI does not mean production is correct.** It means the seed was
     reviewed. The DB write is a separate, owner-authorised act.
  2. **A prompt edited in production but not in seed is invisible to all three
     guards.** That is the inverse drift. Named as a known gap; not built for
     without a case.

  3. **The guidance guard's green can be earned trivially.** It passes when a
     `guided` tool's NAME appears in the prompt — so a prompt that merely LISTS
     tool names satisfies it while carrying no guidance at all. That is exactly
     the manifest failure the curated-list design was rejected to avoid, and the
     pass condition can be met without a single sentence of judgment.

     Same family as `design-note-unwatched-instruments.md` §2.7: a guard whose
     green can be reached by coincidence rather than by the property it is named
     for. It cannot be mechanised — *"is this real guidance"* is a judgment, not
     a property of the repo — so it is recorded rather than solved.

     Found by the thing it describes: `set_project_status` was marked `guided`
     while the prompt carried the parking rule ("requires a reason, ideally a
     resumption condition") and never named the tool. Guidance present, tool
     unattached — the agent reads the rule with nothing to bind it to. The fix
     was to name the tool, not to downgrade the disposition: the judgment was
     real, the wiring was missing. The inverse — a name with no rule behind it —
     is what this note is about, and nothing catches it.

LIMIT 1 IS NOW CLOSED (2026-08-03). `check_prompt_guidance` reads the LIVE agent
rows and asserts the same rule this ledger asserts against the seed: every
`guided` tool's name present in the production prompt, fault
`prompt_missing_guidance`, agents named in the detail. It goes **amber** — never
down, a prompt gap is not a system fault — and it is safe against the travel
precedent because it judges **naming, not wording**, so production can hold the
truer prose without being flagged as drift.

The caveat above still holds for what CI alone tells you; what changed is that
the gap is now MONITORED rather than merely documented. Limits 2 and 3 remain
open by construction: nothing catches a prompt edited in production but not in
seed, and nothing can tell real guidance from a bare list of tool names.

DISPOSITIONS — exactly two, and the distinction is about the SCHEMA:

  `GUIDED`          the schema leaves a real decision unmade — when to reach for
                    it unprompted, which of two similar-looking options is right,
                    or what not to promise. The prompt must mention it.
  `SELF_DESCRIBING` the schema carries everything the agent needs. The roster
                    alone is sufficient and a prompt line would be noise.

A tool can be `GUIDED` on one agent and `SELF_DESCRIBING` on another: `whoami`
carries a real rule for the secretary (never ask the user for their own address)
and is incidental for the archivist. The disposition is a property of the pair.
"""

from __future__ import annotations

GUIDED = "guided"
SELF_DESCRIBING = "self-describing"

# agent -> tool -> (disposition, reason)
#
# The reason is required on GUIDED entries and costs nothing: it turns this from
# a list of strings into a record of judgment, so a later reader can disagree
# with the call rather than merely observe it.
LEDGER: dict[str, dict[str, tuple[str, str]]] = {
    "archivist": {
        "remember_fact":   (GUIDED, "what counts as DURABLE is a judgment; and it must never "
                                    "contradict the AUTHORITATIVE block"),
        "recall_facts":    (GUIDED, "one of three recall-shaped tools; choosing between them "
                                    "is the decision the schema cannot make"),
        "recall":          (GUIDED, "merges facts and episodes — the prompt has to say when "
                                    "that is wanted over recall_facts"),
        "recall_episodes": (GUIDED, "episodes carry VERBATIM quotes; summaries are "
                                    "interpretation, and the difference is the point"),
        "forget_fact":     (GUIDED, "use it whenever the user corrects something — learned "
                                    "memory is inferred and is sometimes simply wrong"),
        "forget_episode":  (GUIDED, "same correction right as forget_fact"),
        "audit_memory":    (SELF_DESCRIBING, ""),
        "whoami":          (SELF_DESCRIBING, ""),
    },
    "finance": {
        # Read-only price/portfolio lookups. The prompt's load-bearing sentence
        # is "you cannot place trades", which is about the ABSENT tool, not these.
        "get_stock_price": (SELF_DESCRIBING, ""),
        "get_portfolio":   (SELF_DESCRIBING, ""),
    },
    "infra": {
        "fleet_health":    (SELF_DESCRIBING, ""),
        "fleet_spend":     (GUIDED, "the run-rate is an ESTIMATE, not a bill — reporting it "
                                    "as exact is the failure the prompt guards"),
    },
    "navigator": {
        "get_traffic":     (GUIDED, "defaults to where the user currently is, from the phone "
                                    "— so 'how long to work' just works"),
        "find_place":      (GUIDED, "same current-position default as get_traffic"),
        "where_am_i":      (SELF_DESCRIBING, ""),
        "whoami":          (GUIDED, "call it for home/work addresses rather than asking"),
        "check_location_freshness": (GUIDED, "report the LAYER it names and guess no cause "
                                             "beyond it"),
        "location_ping_log": (GUIDED, "nothing in the schema says when to reach for it "
                                      "unprompted — 'has my phone been reporting?'"),
    },
    "netstatus": {
        # All three currently return honest "[not configured]" notices; the
        # prompt maps each to its subject so the agent picks the right one.
        "get_node_status":   (GUIDED, "Proxmox hosts specifically — one of three "
                                      "similar-sounding status tools"),
        "get_service_health": (GUIDED, "Kuma reachability specifically"),
        "tailscale_status":  (GUIDED, "tailnet devices and KEY EXPIRY, which is the part a "
                                      "reader would not guess from the name"),
    },
    "researcher": {
        "web_search":      (GUIDED, "search whenever the answer could have changed since "
                                    "training; a couple of well-aimed searches, not many"),
        "fetch_page":      (GUIDED, "ONLY when the full text of one page is truly needed, and "
                                    "never on video URLs — it burns a call and cannot extract"),
    },
    "scheduling": {
        "calendar_lookup": (SELF_DESCRIBING, ""),
    },
    "secretary": {
        # ── genuinely guided ────────────────────────────────────────────────
        "draft_email":     (GUIDED, "returns a DRAFT; the orchestrator sends it behind the "
                                    "gate. She must never say email cannot be sent"),
        "add_task":        (GUIDED, "a discrete action with a due date is a TASK, not a "
                                    "project — the boundary is the decision"),
        "watch_for":       (GUIDED, "differs from call_me_back: fires WHEN a condition "
                                    "becomes true, not after a delay"),
        "call_me_back":    (GUIDED, "rings unconditionally after a delay — the other half of "
                                    "the watch_for distinction"),
        "whoami":          (GUIDED, "NEVER ask the user for their own email address"),
        "lookup_contact":  (GUIDED, "never ask twice for someone else's address"),
        "save_contact":    (GUIDED, "save it once they tell you, so it is not asked again"),
        "capture_idea":    (GUIDED, "keep the user's own framing, not a summary"),
        "project_status":  (GUIDED, "'where am I on X' — and a project is a multi-session arc, "
                                    "not a task"),
        "complete_milestone": (GUIDED, "if the title is ambiguous it asks; pass the question "
                                       "on rather than picking one"),
        "set_project_status": (GUIDED, "parking requires a reason, ideally a resumption "
                                       "condition"),
        "start_planning":  (GUIDED, "a planning session is a CONVERSATION, not a request for "
                                    "a document"),
        "add_planning_note": (GUIDED, "capture their OWN words; a tidied summary loses the "
                                      "thing being captured"),
        "next_planning_question": (GUIDED, "ask ONE at a time; a session that feels like a "
                                           "form gets abandoned"),
        "planning_status": (GUIDED, "the review surface for an open session"),
        "abandon_planning": (GUIDED, "terminal, and the reason is required"),
        "propose_milestone_date": (GUIDED, "a floated date is a PROPOSAL and sets no baseline"),
        "ratify_plan":     (GUIDED, "the only routine path that writes a baseline, and only on "
                                    "explicit agreement — never on her own"),
        "replan":          (GUIDED, "needs the reason the user gives; the reason is the point"),
        "reset_baseline":  (GUIDED, "RARE — for a plan that genuinely changed, not one that "
                                    "slipped; replan is almost always right"),
        "project_timeline": (GUIDED, "report the day counts as facts; never say they are "
                                     "behind"),
        "flag_risk":       (GUIDED, "offer it when they mention something that could go "
                                    "wrong, rather than waiting to be asked"),
        "break_assumption": (GUIDED, "surfaces once rather than nagging"),
        "resolve_risk":    (GUIDED, "realized and retired are DIFFERENT outcomes; collapsing "
                                    "them understates what went wrong"),
        "list_plan_risks": (GUIDED, "gives the ids the other risk tools need"),
        # ── self-describing CRUD ────────────────────────────────────────────
        # Most of the secretary's 47 are exactly this. A prompt enumerating them
        # all would be a manifest, not guidance — and a manifest is what a reader
        # skims past on the way to the part that matters.
        "list_watches":       (SELF_DESCRIBING, ""),
        "cancel_watch":       (SELF_DESCRIBING, ""),
        "list_tasks":         (SELF_DESCRIBING, ""),
        "complete_task":      (SELF_DESCRIBING, ""),
        "cancel_task":        (SELF_DESCRIBING, ""),
        "list_ideas":         (SELF_DESCRIBING, ""),
        "get_idea":           (SELF_DESCRIBING, ""),
        "create_project":     (SELF_DESCRIBING, ""),
        "promote_idea":       (SELF_DESCRIBING, ""),
        "list_projects":      (SELF_DESCRIBING, ""),
        "add_milestone":      (SELF_DESCRIBING, ""),
        "drop_milestone":     (SELF_DESCRIBING, ""),
        "attach_document":    (SELF_DESCRIBING, ""),
        "supersede_document": (SELF_DESCRIBING, ""),
        "list_contacts":      (SELF_DESCRIBING, ""),
        "sync_google_contacts": (SELF_DESCRIBING, ""),
        "google_status":      (SELF_DESCRIBING, ""),
        "pending_callbacks":  (SELF_DESCRIBING, ""),
        "cancel_callback":    (SELF_DESCRIBING, ""),
        "create_google_doc":  (SELF_DESCRIBING, ""),
        "create_google_sheet": (SELF_DESCRIBING, ""),
        "append_to_google_doc": (SELF_DESCRIBING, ""),
    },
    "travel": {
        "list_trips":      (GUIDED, "trips are learned from confirmation emails — she holds "
                                    "no airline credentials and cannot log in"),
        "search_flights":  (GUIDED, "research only; booking is currently OFF and she must not "
                                    "imply one is under way"),
        "whoami":          (GUIDED, "home airport and frequent-flyer numbers rather than "
                                    "asking"),
        "create_google_doc":  (SELF_DESCRIBING, ""),
        "create_google_sheet": (SELF_DESCRIBING, ""),
    },
}


def guided_tools(agent: str) -> list[str]:
    """Tools on this agent whose prompt must mention them."""
    return sorted(t for t, (d, _) in LEDGER.get(agent, {}).items() if d == GUIDED)


def reviewed_tools(agent: str) -> set[str]:
    return set(LEDGER.get(agent, {}))
