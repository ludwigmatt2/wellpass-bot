# Reliability & Anti-Detection Plan

Status: 2026-06-14. Driven by 3h of Railway logs showing the bot re-authing every
~7s, frequent HTTP/2 connection terminations, long backoff stretches, an
intermittent crash, and 401 races.

---

## What the logs showed

| Symptom | Frequency | Meaning |
|---|---|---|
| `FLS token refreshed for user …` | every ~7s during active polling | Token re-fetched on nearly every poll → ~500 auth calls/hr/user |
| `ConnectionTerminated error_code:1/9` (h2 GOAWAY) + `Server disconnected` | constant; long stretches | Connection killed mid-poll; bot stuck in 10s backoff, not polling |
| `deque mutated during iteration` | ×2, next to error_code:9 | h2 stack throwing during a dying connection; aborts a whole poll cycle |
| `401 Unauthorized` on session fetch | a few, right after refreshes | token used as it expires; a 401 at spot-open = missed booking |
| `AVAIL […]` lines | **zero in 3h** | the availability instrumentation (commit 58cf227) is not running |

---

## A — Token re-auth storm. ✅ SHIPPED (2026-06-14)

Root cause: the proactive-refresh safety margin (`5 min`) was ≥ the FLS token's
real TTL, so `token_expires - margin > now` was never true → re-auth every poll.
Caching logic itself was correct (`update_token` persists, `get_user_by_id` reads back).

Fix in `wellpass/auth.py`: margin `5 min → 60 s` (`_TOKEN_REFRESH_MARGIN`), plus a
one-time `ttl=…s` log on every refresh so we can read the token's real lifetime
and tune the margin. Expect ~30× fewer auth calls.

**After deploy, verify:** the `ttl=…s` value in logs. If TTL is e.g. 300s, refreshes
drop to ~every 4 min. If TTL is < ~120s the margin may need to go even lower (or we
add on-401 retry instead of relying on the margin).

---

## B — Connection terminations (the likely real missed-booking cause)

### B0. Reconcile deployed env with repo  ← DO FIRST, it gates everything
The repo never enables HTTP/2 (`httpx.AsyncClient()` defaults to HTTP/1.1) and
`requirements.txt` has no `httpx[http2]`, yet logs show h2 GOAWAY frames. And the
`AVAIL` instrumentation from commit `58cf227` never logs. Conclusion: **Railway is
running different code/deps than the repo.**
- Confirm Railway's deployed commit == repo HEAD; redeploy if not.
- Pin deps exactly (freeze `httpx`, add/remove `h2` deliberately) so prod == repo.
- Add a one-shot log of `response.http_version` on the first successful call to learn
  whether prod is actually on h2 or h1.

### B1. Force HTTP/1.1 + a shared pooled client
Today every API call builds a **brand-new `httpx.AsyncClient`** (8 call sites in
`wellpass/api.py` + 2 in `auth.py`) → a fresh TLS handshake + connection per request,
massive churn, and (if h2 is active) repeated GOAWAY.
- Create one module-level `httpx.AsyncClient(http2=False, timeout=…, limits=Limits(
  max_keepalive_connections=…, keepalive_expiry=…))`, reuse it everywhere.
- Forcing HTTP/1.1 removes the GOAWAY/`error_code` class entirely and almost
  certainly kills the `deque mutated` crash (C) too, since that's the h2 stack.
- Keep the audit's 429/5xx backoff; add jittered retry on transient transport errors.
- **Risk:** low. **Effort:** ~1–2h. **Payoff:** high (stability = not missing spots).

### B2. On-401 refresh-and-retry
A 401 mid-poll currently just skips that watch for the tick. Wrap session fetch /
booking so a 401 forces an immediate token refresh and one retry. Removes the
expiry-race blind spot at the exact moment a spot opens. **Effort:** ~1h.

### Outcome of B
Stable, long-lived connections; no GOAWAY/backoff stretches; no h2 crash; no 401
gaps. This is the work most likely to fix the actual "spot opened, bot did nothing."

---

## C — Footprint reduction & anti-detection (rate-limit + bot-likeness)

Header masking is **already done** — the client sends iOS `User-Agent`
(`Wellpass/3058 CFNetwork/… Darwin/…`), `x-np-user-agent` with `devicePlatform=IOS`,
a device UID, app version, etc. (`wellpass/auth.py`, `wellpass/api.py`). So "imitate a
phone" at the HTTP layer is largely in place and won't move the needle much further.

The signals header-masking does **not** fix, ranked by how much they expose us:
1. **Request cadence** — a real phone does not poll a session every 5–7s, 24/7. This is
   the #1 tell. Fix with: **interval jitter** (5s ± random) and **window-scoped polling**
   (only poll hard in the last ~24–48h before a class, when cancellations actually
   happen — not for classes days out).
2. **IP / ASN** — Railway is a datacenter IP; the mobile API expects mobile/residential.
   egym can see this regardless of headers. Mitigation (optional, heavier): a residential
   or mobile proxy. Only worth it if we confirm IP-based blocking.
3. **TLS/HTTP-2 fingerprint** — httpx's TLS (JA3/JA4) and h2 SETTINGS fingerprint ≠ a real
   iPhone's CFNetwork. A WAF doing JA3 fingerprinting could reject us **even with perfect
   headers** — and this is a candidate explanation for the GOAWAY frames. Mitigation
   (heavy, last resort): a TLS-impersonating client (e.g. `curl_cffi` impersonating Safari/iOS).
   Forcing HTTP/1.1 (B1) also shrinks this fingerprint surface.

Also: **schedule-list polling** — one `get_schedule(gym, day)` call covers all watched
sessions at a gym for all users, vs one `get_session` per watch. Fewer calls → lower
footprint AND headroom to poll faster within limits. (Previously discussed; fits here.)

Priority within C: **jitter + window-scoped polling** (cheap, high value), then
schedule-list. IP/TLS impersonation only if we confirm fingerprint/IP blocking.

---

## D — Event-driven endgame: bsport email / waitlist trigger

The most human-like and lowest-footprint design, and the strongest answer to the
anti-bot concern: **don't poll Wellpass continuously at all — react to the bsport
signal, like a human does.**

Real-world flow (per Ludo): sign up on Basement's bsport site → bsport **emails** when a
spot frees → the spot then appears bookable in Wellpass for a few seconds.

Design:
1. A dedicated inbox receives the bsport waitlist emails (forward Ludo's, or use a
   purpose inbox subscribed on bsport). Gmail API/IMAP, or Gmail push (Pub/Sub) for
   near-instant.
2. On a "spot available" email → parse the class/time → immediately `get_session` +
   `book_class` in Wellpass for the matching watch.
3. Baseline Wellpass traffic ≈ 0; we only touch the API when there's genuinely
   something to grab. Nearly invisible to rate-limiting, and reacts at the source
   (often before a human even opens the email).

**Trade-off:** only works where bsport sends emails → **Basement only**, not arbitrary
Wellpass studios. So this is a high-value specialization for the contested case, not a
general replacement. Likely run **alongside** polling: event-trigger for Basement,
(reduced, window-scoped) polling for everything else.

**Effort:** medium-high (new inbox integration + email parsing). **Payoff:** highest for
Basement, and best stealth/speed profile of any option.

---

## Recommended sequence

1. **A** (done) → deploy, read `ttl=…s`, confirm auth storm is gone.
2. **B0** reconcile deploy/deps → makes prod == repo and turns on the `AVAIL`
   instrumentation we already built.
3. **B1 + B2** force HTTP/1.1, shared client, on-401 retry → kill the terminations,
   the crash, and the 401 gaps. Re-test; this likely fixes the missed bookings.
4. **C** jitter + window-scoped polling → lower footprint / bot-likeness.
5. **D** bsport email trigger for Basement → the endgame for contested slots.
6. IP/TLS impersonation only if B+C don't stop the connection terminations (i.e.
   if egym is actively fingerprint-blocking us).
