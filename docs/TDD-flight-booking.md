# TDD — Flight booking (Duffel, live)

**Status:** Ready to build
**Repo:** `mdk32366/Project-Jarvis`
**Prereq:** everything through `jarvis-websearch` deployed (240 tests green)
**Say "let's roll" and hand this to a fresh session.**

---

## 1. What we're building

JARVIS can already *search* flights (Duffel test mode) and *cannot* book. This
makes her able to book — for real, with a real card, behind the existing
confirmation gate.

**The scope is deliberately narrow: she can BUY A TICKET. She cannot do anything
else with money.** No hotels, no cars, no changes, no cancellations, no refunds.
One irreversible action, one gate, one cap.

---

## 2. The security argument, stated plainly

This is the first time JARVIS spends money in a system that also reads the open
internet. That combination is what makes this different from everything built so
far, and the design has to earn it.

### 2.1 The gate is the right mechanism, and it already exists

`send_email` and `create_event` are `gated=True`. Nothing executes until the user
hears a readback and says an explicit **"confirm"** — and on voice, `"ok"` and
`"yeah"` are deliberately **not** accepted (`orchestrator._VOCAB`). A webpage
cannot say "confirm" on the user's behalf. **That is a structural boundary, not a
prompt-level one**, and it is what stands between an injection and actual harm.

`book_flight` uses exactly this. Same `register_gated()` pattern as
`secretary.send_email`. No new mechanism.

### 2.2 What the gate does NOT protect against — and how we handle it

The gate stops **unauthorized execution**. It does not stop **authorized
execution of a manipulated action**.

The attack that survives the gate:

> A poisoned page influences which flight she considers "best." She reads back a
> plausible-sounding booking that is the *wrong* booking. The user says "confirm."
> The gate worked perfectly. Money leaves anyway.

**Three defences, in order of importance:**

**(a) The web is not in the booking loop at all.**

Duffel search results are a **structured API response** — carrier, times, fare,
`offer_id`. There is no prose for an injection to hide in.

> **HARD RULE: `book_flight` accepts ONLY a Duffel `offer_id` that JARVIS
> retrieved herself from `search_flights` in this same conversation. It never
> accepts a flight described in free text, and never one "found" on a web page.**

This is the single most important line in this document. It means the web-search
surface is simply **not connected** to the spending surface. Enforce it in code,
not in a prompt: hold the offers from the last `search_flights` call, and reject
any `offer_id` not in that set.

**(b) The readback names the money and the route.**

A manipulated booking has to survive the user *hearing it*. So the readback is
not "the flight we discussed" — it is:

> *"Readback: Alaska Airlines, Seattle to San Francisco, August fourth, departing
> seven oh four in the morning, one stop. Three hundred and seventeen dollars
> total. Confirm or cancel."*

Carrier, route, date, time, **total fare**. If any of those is wrong, the user
notices. That is a real check and it is doing real work.

**(c) The CARD is the cap. — DECIDED**

**A dedicated card with a ~$3,000 limit. That is the ceiling, and it is the right
one.** It bounds the cost of every failure nobody thought of, including mine.
Treat it as a first-class part of the design.

**No separate low fare cap in code.** The user's reasoning is correct and worth
recording: *fares are genuinely insane now, and a cap that is routinely wrong is
a cap that gets disabled — at which point you have no cap at all.* A control
people switch off is worse than no control, because it creates the illusion of
one.

**But DO wire the `notional` hook** — `Registry.register` already takes it, and
`finance.register_trading` uses it. Set `MAX_BOOKING_USD` at the **card limit**
(~$3,000), not below it:

```python
max_booking_usd: float = 3000.0     # = the card limit. NOT a second, tighter cap.
```

This is not a policy cap. It is a **sanity check**: if a booking comes back at
$30,000, something is badly wrong — a currency mix-up, a decimal error, a
manipulated offer — and it should be **refused outright**, not read back for
confirmation. There is no "confirm" for an obviously-broken number.

The card declines anything above the limit anyway. The code check exists so the
failure is a clear English message instead of a mysterious payment decline.

### 2.3 Second factor — DECIDED: yes, and it is required

**Booking requires a code, texted to `OWNER_PHONE`, spoken or typed back.**

This is the only control in the entire system that actually beats caller-ID
spoofing. It requires **possession of the phone**, not merely knowledge of its
number. Everything else — the whitelist, the readback, the gate — assumes the
caller is who they claim to be. This does not.

It is required because this is the first thing JARVIS does that **spends money**.
One extra turn on a call is a trivial price.

**Flow:**

```
JARVIS: "Readback: Alaska, Seattle to San Francisco, August fourth, seven oh
         four in the morning, one stop. Three hundred and seventeen dollars
         total. I've texted you a code — read it back to confirm."
   [SMS to OWNER_PHONE: "JARVIS booking code: 481 902"]
YOU:    "Four eight one nine zero two."
JARVIS: "Confirmed. Booking now."
```

**Implementation notes — get these right:**

- The code is generated at readback time, stored on the `PendingConfirmation`
  row, and **expires in 5 minutes**. A code that lives forever is a password.
- **Three attempts, then the pending confirmation is cancelled.** Not "try
  again" — cancelled. Unlimited retries turn a 6-digit code into a brute-force
  oracle.
- Compare with `hmac.compare_digest`. A plain `==` leaks it a digit at a time.
- **STT will mangle digits.** Normalize hard: strip spaces, punctuation, and
  spelled-out numbers ("four eight one" → `481`). Accept the digits in any
  grouping. Do NOT be clever about near-misses — a wrong code is a wrong code.
- The code goes **only** to `OWNER_PHONE`. Never read it aloud on the call —
  that would defeat the entire purpose.

> **Note the dependency:** this needs SMS working, which is currently blocked on
> A2P registration (in review). Until that clears, either gate booking behind
> A2P approval, or use a TOTP app instead (`pyotp`, one QR scan). **TOTP is
> arguably better anyway** — no carrier dependency, no A2P, and it can't be
> intercepted by a SIM swap. Consider building TOTP first and treating SMS as
> the fallback.

### 2.4 Voice CAN book — decided

Voice books, with the gate plus the second factor. The second factor is what
makes this defensible: a spoofed caller cannot produce the code.

---

## 3. Duffel live-mode prerequisites (do these first)

These are **not** code, and they gate everything:

1. **Activate the Duffel account.** Live tokens are not issued until Duffel
   reviews and activates. A single-user personal assistant is an unusual profile
   for them — be honest about what it is; they may have questions.
2. **Top up the Duffel balance.** The Flights API requires funds on account.
3. **Get the live token** (`duffel_live_...`).
4. **The card.** Duffel bills the balance; the card funds the balance. Use the
   dedicated low-limit card.

**Keep the test token working.** `DUFFEL_API_KEY` stays; add
`DUFFEL_LIVE_API_KEY` separately and a `BOOKING_ENABLED` flag. **Booking must be
testable against test mode**, and test mode is where every test in this document
runs. Never make the live key the only path.

---

## 4. Implementation

### 4.1 Search must retain offers

`travel._search_flights` currently formats offers into prose and throws the
structure away. Booking needs the `offer_id`s.

Add a `flight_offers` table (or reuse a short-lived cache keyed on
`thread_key`) holding, per offer: `offer_id`, `expires_at`, `total_amount`,
`total_currency`, `carrier`, `route`, `depart_at`, `arrive_at`, `segments`.

> **Duffel offers expire** — typically within ~30 minutes. A stale `offer_id` will
> be rejected by Duffel, and that is *good*: it fails closed. But JARVIS must say
> something useful when it happens ("that fare's expired, let me re-search"),
> not surface a raw API error.

### 4.2 `book_flight` — gated, top-level only

Follow `secretary.register_gated()` exactly:

```python
def register_gated(reg: Registry) -> None:
    """Top-level registry ONLY. The gate lives in orchestrator.run(); sub-agents
    call reg.execute() directly and bypass it entirely (run_agent hard-refuses
    gated tools). A booking tool in a sub-agent roster would spend money with no
    confirmation at all."""
    reg.register(
        {
            "name": "book_flight",
            "description": (
                "Book a flight the user has already been shown. Takes ONLY an "
                "offer_id from a search you performed in this conversation. This "
                "SPENDS REAL MONEY and is IRREVERSIBLE; the system will require "
                "the user's explicit confirmation."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "offer_id": {"type": "string",
                                 "description": "From search_flights. Never invent one."},
                },
                "required": ["offer_id"],
            },
        },
        _book_flight,
        gated=True,
        notional=_booking_notional,      # -> fare cap, exactly like trading
        summarize=_summarize_booking,    # -> the readback
    )
```

And register it in `build_registry`'s `include_delegate=True` branch, next to
`finance.register_trading(reg)` and `secretary.register_gated(reg)`.

**`_book_flight` must, in this order:**

1. Look up the `offer_id` **in the offers JARVIS retrieved herself**. Not found →
   refuse. *This is the rule from §2.2(a) and it is the whole ballgame.*
2. Check `BOOKING_ENABLED`, else refuse with a clear message.
3. Check the fare against `MAX_BOOKING_USD` → refuse if over (not gated: refused).
4. Fetch passenger details from `whoami` — name, DOB, frequent-flyer. **Do not ask
   the user to spell their own name on a phone call.**
5. `POST /air/orders` with the offer, passengers, and `payments: [{type: "balance"}]`.
6. On success: record a `Trip` row (the table already exists), and **email the
   confirmation** — spoken confirmation numbers are useless.
7. On failure: say what went wrong in English. `explain()` in `google_oauth.py` is
   the pattern to copy — an opaque API error buried in a log is a bug.

### 4.3 Passenger details

Duffel needs, at minimum: given name, family name, date of birth, gender, email,
phone. Some of that isn't in `whoami` yet.

Add to the owner profile:
```
OWNER_DOB=1970-01-01
OWNER_GENDER=m
OWNER_PASSPORT=            # only if international
```

**Do not make the user recite a date of birth on a phone call.** It should be
configured once, and simply known.

### 4.4 The loop this completes

The design was always this, and this closes it:

1. She **searches** (Duffel — structured, no web content)
2. She **reads back** the flight and the fare
3. The user says **"confirm"**
4. She **books**, and **emails** the confirmation
5. The airline's confirmation email arrives at JARVIS's inbox
6. `travel.record_trip_from_email` **captures the itinerary automatically**
7. *"When do I fly?"* just works

Steps 5–7 already exist and already work. This adds 1–4.

---

## 5. Tests that must exist

The security properties are worthless as claims. Prove them.

| Test | Property |
|---|---|
| `test_booking_refuses_an_offer_id_it_did_not_retrieve` | **The load-bearing one.** A flight "found on a web page" cannot be booked. Feed it a plausible offer_id that never came from `search_flights` → refused. |
| `test_booking_is_gated_and_creates_a_pending_confirmation` | No confirm, no booking. Assert `PendingConfirmation` exists and Duffel was never called. |
| `test_booking_is_not_in_any_sub_agent_roster` | Sub-agents bypass the gate. A gated tool in a roster spends money unconfirmed. |
| `test_booking_requires_the_second_factor` | **The one that beats spoofing.** Gate cleared with "confirm" but NO code → Duffel never called. |
| `test_a_wrong_code_three_times_CANCELS_the_booking` | Not "try again" — cancelled. Unlimited retries make a 6-digit code a brute-force oracle. |
| `test_an_expired_code_is_refused` | 5-minute TTL. A code that lives forever is a password. |
| `test_the_code_is_never_read_aloud_on_the_call` | Speaking it would defeat the entire purpose. |
| `test_spoken_digits_are_normalized` | STT mangles digits. "four eight one" and "481" and "4 8 1" all work. Near-misses do NOT. |
| `test_an_absurd_fare_is_REFUSED_not_gated` | $30,000 = something is broken. No "confirm" for an obviously-wrong number. |
| `test_the_readback_names_carrier_route_date_and_TOTAL_FARE` | A manipulated booking must not survive being heard. |
| `test_voice_will_not_accept_ok_to_confirm_a_booking` | `_VOCAB["voice"]` — conversational filler must never buy a plane ticket. |
| `test_an_expired_offer_fails_gracefully` | Duffel rejects stale offers. Say "that fare expired, let me re-search," not a raw 422. |
| `test_booking_disabled_by_default` | `BOOKING_ENABLED=false` until deliberately turned on. |
| `test_a_successful_booking_records_a_trip_and_emails_the_confirmation` | Spoken confirmation numbers are useless. |

**All tests run against Duffel test mode.** Never the live key.

---

## 6. Config

```python
# Booking (SPENDS REAL MONEY)
booking_enabled: bool = False          # off until deliberately enabled
duffel_live_api_key: str = ""          # separate from DUFFEL_API_KEY (test mode)

# = THE CARD LIMIT, not a tighter policy cap. A sanity check, not a budget:
# a $30,000 booking means something is broken (currency, decimal, manipulation)
# and must be REFUSED, not read back for confirmation.
max_booking_usd: float = 3000.0

# Second factor — REQUIRED for booking. The only control that beats caller-ID
# spoofing, because it needs POSSESSION of the phone, not knowledge of its number.
booking_second_factor: str = "totp"    # "totp" | "sms"
booking_code_ttl_seconds: int = 300    # a code that never expires is a password
booking_code_max_attempts: int = 3     # then CANCEL — not "try again"
totp_secret: str = ""                  # if totp: one QR scan, no carrier dependency
```

---

## 7. Things I would push back on, if asked

- **Do not let her book from a web-searched flight.** Ever. If the user asks for
  a fare they saw on Google Flights, she should re-search it *in Duffel* and book
  from her own offer. The rule in §2.2(a) is the difference between a system with
  a bounded attack surface and one without.
- **Do not add a tighter fare cap than the card limit.** It was considered and
  rejected on purpose: a cap that's routinely wrong gets switched off, and then
  there is no cap. The card IS the policy. `MAX_BOOKING_USD` is a sanity check at
  the card limit, not a budget below it.
- **Do not skip the second factor** because the gate exists. The gate assumes the
  caller is who they say they are. The second factor is the only thing that
  doesn't.
- **Do not add hotels/cars/changes "while we're in there."** Each is a new
  irreversible action with a different failure mode. One thing, done properly.
- **Do not let the readback be vague.** "The flight we discussed" is not a
  readback. It has to name the money.

---

## 8. Decisions (settled — do not re-litigate)

1. **Second factor: REQUIRED.** A code the user must produce. It is the only
   control that beats caller-ID spoofing, because it needs *possession* of the
   phone rather than knowledge of its number. One extra turn is a trivial price
   for the first thing that spends money. **Prefer TOTP over SMS** — no carrier
   dependency, no A2P blocker, immune to SIM swap. (§2.3)

2. **No separate fare cap. The CARD is the cap** (~$3,000). The reasoning is the
   user's and it is right: *fares are genuinely insane now, and a cap that is
   routinely wrong is a cap that gets switched off — at which point you have no
   cap at all.* `MAX_BOOKING_USD` is set AT the card limit as a **sanity check**
   (a $30k booking means something is broken), not below it as a budget. (§2.2c)

3. **Voice CAN book.** The gate plus the second factor makes it defensible: a
   spoofed caller cannot produce the code. (§2.4)

### Still open (small)

- **TOTP or SMS for the code?** TOTP is better on every axis and dodges the A2P
  blocker entirely. Recommend TOTP; ask before assuming.

---

*Everything else — the gate, the readback vocabulary, the sub-agent isolation,
the trip capture — already exists and already works. This is a small,
well-bounded addition on top of machinery that has been load-bearing for a week.
The care goes into §2.2(a): keep the open internet out of the loop that spends
money.*
