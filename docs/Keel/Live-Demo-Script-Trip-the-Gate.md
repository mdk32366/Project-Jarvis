# Live Demo Script — "Trip the Gate"
### The five-minute demonstration that makes the whole discipline real

*This is the facilitator's run-sheet for the one live moment in the session: showing a failing test get BLOCKED from production, then a passing one get SHIPPED. It's Principle 6 — "prove the safety net by tripping it" — done live. Nothing else in the session convinces like watching it happen.*

*Read this whole sheet once before the session. Do the PRE-FLIGHT before the room arrives. The live part is ~5 minutes.*

---

## What this demo proves (say this to yourself before you start)

You are not teaching them to code. You are showing them one thing: **a broken build physically cannot reach production, and you can SEE it be stopped.** That's the entire payload. If they walk away believing the gate is real because they watched it work, the demo succeeded.

---

## PRE-FLIGHT — do ALL of this before anyone is watching

> The #1 rule of live demos: the audience sees a five-minute miracle; you did the setup in private. Never build the scaffold live.

- [ ] **A throwaway demo repo already exists and is already deployed once, working.** Not your real project. A tiny repo — call it `gate-demo` — with the CI test→deploy gate already set up and PROVEN working at least once. You are demonstrating the gate, not building it live.
- [ ] **The gate is confirmed working TODAY.** Run the whole demo start-to-finish once, this morning, on the same laptop and network you'll use. A demo that worked last week is not a demo that works now. (Principle 6 applies to your demo too.)
- [ ] **Two commits are prepared in your head (or on a sticky note):**
   - The **breaking** change: a one-line edit that makes a test fail.
   - The **fix**: the one-line edit that makes it pass again.
- [ ] **Browser tab open and logged in** to the repo's GitHub Actions page (the tab that shows the check running). Zoom the browser to 150%+ so the back row can read it.
- [ ] **Terminal open**, font size cranked up (18–24pt), in the demo repo folder, on a clean `main`.
- [ ] **Network tested in the actual room** if you can get in early. CI needs the internet.
- [ ] **Fallback ready:** screenshots of each step saved in a folder, OR the last slide of the deck's "prove the gate" walkthrough queued up. If the live run stalls, you narrate the screenshots and lose nothing. (See FAILURE RECOVERY below.)

---

## THE LIVE SEQUENCE — ~5 minutes

### Frame it (30 seconds) — before you touch anything
> Say: *"I'm going to try to ship broken code to production on purpose. Watch what stops me. This is the safety net every one of your projects will have — and the only way to trust it is to see it catch something."*

Let that land. The promise "I'm going to break it on purpose" is what makes them lean in.

---

### MOVE 1 — Break it on purpose (45 sec)
- [ ] In the terminal (or editor on screen), make the **breaking** change. Keep it visible and obvious — e.g. change a test's expected value so it's now wrong, or break the thing the test checks.
- [ ] Commit it on a branch and push:
   ```
   git checkout -b break-it
   git commit -am "break a test on purpose"
   git push -u origin break-it
   ```
> Say: *"That's broken now. A test will fail. Watch — the system is about to refuse it."*

**Audience sees:** you typing a clearly-labeled "break" and pushing it.

---

### MOVE 2 — Watch it get BLOCKED (60–90 sec) — the payoff moment
- [ ] Switch to the browser tab (GitHub → the branch or its Pull Request → Actions/Checks).
- [ ] The test job runs. Wait for it. **Do not fill the silence with apology** — narrate the tension:
> Say: *"It's running the tests right now. If they fail, the deploy will not happen. There it goes…"*
- [ ] The check turns **RED / X**. The deploy step is **skipped**.
> Say, pointing at the red X: *"There it is. The tests failed, so the deploy never ran. That broken code is physically unable to reach production. I could not ship it even though I tried."*

**Audience sees:** a red failure, a skipped deploy. **This is the whole demo.** Pause here. Let them look at it.

---

### MOVE 3 — Fix it (30 sec)
- [ ] Make the **fix** — revert the breaking line so the test passes again.
   ```
   git commit -am "fix the test"
   git push
   ```
> Say: *"Now I fix it. Same path — but this time the tests will pass."*

---

### MOVE 4 — Watch it SHIP (60–90 sec)
- [ ] Back to the browser. The check runs again.
- [ ] It turns **GREEN / ✓**, and this time the **deploy step runs.**
> Say: *"Green. Tests passed. And NOW — only now — it deploys. The gate let the good build through, on its own, with no command from me."*

**Audience sees:** green check, deploy runs. The contrast with the red is the lesson.

---

### LAND IT (30 sec)
> Say: *"That's the whole idea. I don't have to remember to check. I don't have to be careful. The system will not let broken work reach the thing I depend on — and I watched it prove that. A safety net you've never seen catch anything is just a hope. Now you've seen it. That's the difference."*

Then advance to the next slide. Don't linger past the point.

---

## FAILURE RECOVERY — if the live run stalls

You rehearsed, so this is unlikely. But if CI hangs, the network drops, or anything spins too long:

- [ ] **Do not troubleshoot on stage.** Never debug in front of the room — it's the one thing that kills the mood.
- [ ] **Cut to the screenshots / the deck walkthrough immediately.** Say: *"The internet's being slow — here's exactly what it does, from my run this morning,"* and narrate the saved screenshots step by step. The teaching is identical; you just lose the live thrill.
- [ ] Move on. Never let a stalled demo eat more than 30 seconds before you switch to the fallback.

> This is itself a Principle-6 lesson you can name out loud: *"I have a fallback because I tested this and planned for it to fail. That's the same discipline we're teaching."*

---

## DEBRIEF PROMPTS — turn the demo into their insight

After it lands, pull the lesson out of THEM (works better than telling):
- *"What would have happened, in the old way, if I'd shipped that broken code?"* (→ it reaches production, breaks, you find out when it fails.)
- *"How much did that gate cost me to set up?"* (→ once, on day one — then it works forever.)
- *"Why did I break it on purpose instead of just trusting it?"* (→ because a net you've never seen catch anything is a hope, not a guarantee. Principle 6, in their own mouths.)

---

## THE ONE-LINE VERSION (if you need to run it from memory)

**Break a test on a branch → push → show the red X and skipped deploy → fix it → push → show the green check and the deploy running.** Narrate the contrast: *blocked when broken, shipped when clean, and I watched it happen.*

---

## ADAPTING THIS

- **Not on GitHub?** The shape is identical on any platform with a CI gate (GitLab, etc.) — "push broken → watch it blocked → push fixed → watch it ship." Only the button names change.
- **No demo repo yet?** Building `gate-demo` is itself a perfect walk-through of the Day-One Checklist Steps 8–11. You could even build it WITH the room as the hands-on portion, then trip the gate on the thing you just built together — the strongest version of this demo, if you have the time and the room's ready for it.
- **Purely on slides?** The deck's "prove the gate" walkthrough (the 4-step slide) carries the same content. Use this script's narration lines as your talk track over that slide.
