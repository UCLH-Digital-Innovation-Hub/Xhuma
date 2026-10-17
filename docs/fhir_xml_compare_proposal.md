# FHIR-vs-XML Comparison View (INT diagnostics)

**Status:** Option A **implemented** — see §6 "As implemented". Options B–D retained for context.
**Date:** 2026-10-15
**Goal:** A gated URL that shows the **FHIR bundle as received** from GP Connect alongside the
**C-CDA XML as sent to Epic**, either side-by-side on one scrollable page or as two toggleable
pages, for comparison. Always available in **INT**; hidden in **PRD** except for a deliberate,
temporary smoke test.

---

## 1. What the pipeline actually produces

The relevant path is the ITI-38 flow in
[`app/gpconnect.py`](../app/gpconnect.py) `_fetch_gpconnect_record`:

1. **FHIR bundle "as received"** — GP Connect returns `scr_bundle` (JSON), parsed into a
   `fhirclient` Bundle (`gpconnect.py:511-520`). **It is converted in-memory and then discarded —
   it is never persisted.**
2. **XML "as sent to Epic"** — `convert_bundle()`
   ([`app/ccda/fhir2ccda.py:22`](../app/ccda/fhir2ccda.py)) produces the C-CDA, which is
   base64/XOP-wrapped and cached in Redis (`gpconnect.py:601-606`):
   - `nhsno → doc_uuid`
   - `doc_uuid → xop` (the document Epic pulls via ITI-39)
   - `doc_patient:doc_uuid → nhsno`
   - TTL = `CCDA_EXPIRY_HOURS` (default 4h)

**Implications**

- `doc_uuid` is the natural join key between the two artifacts.
- The XML side already lives in Redis.
- The FHIR bundle side does **not** exist anywhere — this is the one real gap any comparison view
  must close.

The relay-diagnostics work ([`app/relay/hub.py:15`](../app/relay/hub.py)) deliberately logs **only
non-PHI metadata** and strips payloads, so it is not a source for the bundle/XML — but it is a good
reminder of how carefully PHI is handled in this codebase.

## 2. The toggle that already exists

Environment gating uses `ENV` (`os.getenv("ENV", "prod").lower()`), with observed values
`prod` / `int` / `dev` / `local`. There is a direct precedent to copy: a dev-only HTML viewer
`/_dev/audit` registered in [`app/main.py:374`](../app/main.py):

```python
if os.getenv("ENV", "prod").lower() in ("dev", "local"):
    @app.get("/_dev/audit", dependencies=[Depends(verify_api_key)])
```

A compare viewer should follow exactly this shape, adding `"int"` to the allow-list so it is always
on in INT, with a separate explicit flag for the rare PRD smoke test (see §4).

---

## 3. Options

### Option A — Redis-backed diagnostic pair + gated `/_diag/compare` viewer  ★ recommended

- **Capture:** in `_fetch_gpconnect_record`, when the diagnostic gate is on, add two more `setex`
  writes keyed off the existing `doc_uuid` — `diag:bundle:{doc_uuid}` (pretty FHIR JSON) and
  `diag:xml:{doc_uuid}` (pretty C-CDA) — with a **short, independent TTL** (e.g. a new
  `DIAG_TTL_MIN`, separate from the 4h document TTL).
- **View:** `/_diag/compare?nhsno=…` (resolves `nhsno → doc_uuid`) or `/_diag/compare/{doc_uuid}`,
  gated like `/_dev/audit`, rendering both panes from Redis.
- **Pros:** reuses existing cache + gating precedent; zero new infra; join key already exists; PHI
  self-expires.
- **Cons:** two extra Redis writes of PHI payloads (mitigated by the gate + short TTL).

### Option B — On-demand "dry-run" endpoint, nothing stored

- `/_diag/convert/{nhsno}` re-runs fetch + conversion live and returns bundle + XML in one response.
  No PHI at rest.
- **Pros:** nothing persisted; always fresh.
- **Cons:** re-hits GP Connect (extra load + audit events); shows a *fresh* conversion, not
  necessarily the exact cached document Epic actually retrieved — weaker "as sent" fidelity.

### Option C — Filesystem capture (extend the existing dev dump)

- Code already writes `{nhsno}.xml` in dev/local (`gpconnect.py:608`). Add a `{nhsno}.bundle.json`
  beside it and a tiny file-reader view.
- **Pros:** smallest change.
- **Cons:** INT runs stateless/multi-replica — files are ephemeral and land on whichever pod did the
  conversion. Fine for local, poor for INT. **Not recommended.**

### Option D — Postgres diagnostic artifact (alongside the audit store)

- Persist bundle+XML as a row linked to `doc_uuid`/audit event; viewer queries it like `/_dev/audit`.
- **Pros:** durable, browsable history.
- **Cons:** puts full PHI in the DB; needs schema + Alembic migration; heavier. Overkill unless
  retained history is wanted.

### Layout (orthogonal to A–D)

A single `?view=` parameter on the viewer:

- `split` — two `<pre>` columns, flexbox, one scrollable page (side by side);
- `tabs` — toggle between two full-width pages (two pages);
- `diff` — `difflib.HtmlDiff` unified view, for spotting what a field became.

---

## 4. Gating: INT vs PRD

- **Always on in INT:** add `"int"` to the `ENV` allow-list for both the route and the capture;
  keep `Depends(verify_api_key)`.
- **Exceptional PRD smoke test:** do **not** drive this via `ENV` (prod must stay locked). Use a
  separate, off-by-default flag — e.g. `DIAG_COMPARE_ENABLED=true` — that must be deliberately set
  and then unset, combined with the API-key dependency and a short TTL. This keeps "exceptional,
  temporary" as the literal semantics rather than a standing feature.

## 5. Recommendation

**Option A**, with the `?view=` layouts and the dual INT-env / PRD-flag gate. It leans entirely on
patterns already in the codebase (`doc_uuid`, Redis TTL, the `/_dev/audit` gate); the only genuinely
new behaviour is persisting the received FHIR bundle, which today is simply thrown away.

### PHI / safety notes

- Both artifacts contain full patient clinical data. The viewer must stay behind `verify_api_key`
  and the env/flag gate, mirroring `/_dev/audit`.
- Keep the diagnostic TTL short and independent of the 4h document TTL.
- The PRD flag should be off by default and documented as turn-on-for-the-test, turn-off-after.

---

## 6. As implemented (this PR)

Option A, built against `dev`.

**Gate — [`app/settings.py`](../app/settings.py)**

- `diag_compare_enabled()` — `True` in `dev`/`local`/`int`; in `prd` only when
  `DIAG_COMPARE_ENABLED` is truthy. Read live (not cached at import).
- `diag_ttl_minutes()` — reads `DIAG_TTL_MIN` (default 30; non-numeric/≤0 → 30).

| Variable | Default | Purpose |
| --- | --- | --- |
| `DIAG_COMPARE_ENABLED` | `false` | Opt-in switch for `prd` only (non-prod is always on). |
| `DIAG_TTL_MIN` | `30` | Minutes before the captured bundle/XML pair expires from Redis. |

**Capture — [`app/gpconnect.py`](../app/gpconnect.py)** `_fetch_gpconnect_record`

When the gate is on, after the existing CCDA cache write, two extra keys are written in their own
pipeline with a short TTL:

- `diag:bundle:{doc_uuid}` — the received FHIR bundle (`scr_bundle`), pretty JSON.
- `diag:xml:{doc_uuid}` — the serialised C-CDA (`xmltodict.unparse(xml_ccda, pretty=True)`).

The capture is wrapped so a diagnostics failure can never break the clinical response. The existing
`nhsno → doc_uuid` key is reused for NHS-number lookups; no new reverse key is added, and the viewer
degrades gracefully once the diag keys expire ahead of the 4h document.

**Viewer — [`app/main.py`](../app/main.py)** `GET /_diag/compare`

Registered only when `diag_compare_enabled()`; protected by `Depends(verify_api_key)` (`X-API-Key`).

- `?nhsno=<10 digits>` (resolved via the cached `nhsno → doc_uuid`) or `?doc_uuid=<id>`.
- `?view=split` (side-by-side, default), `?view=tabs` (two toggleable pages), `?view=diff`
  (`difflib.HtmlDiff` unified view).
- No params → a small lookup form. Missing/expired diagnostics and invalid NHS numbers return
  friendly messages. All payloads are HTML-escaped.

```bash
curl -s -H "X-API-Key: $API_KEY" "https://<host>/_diag/compare?nhsno=9690937278&view=split"
```

**Tests — [`app/tests/test_diag_compare.py`](../app/tests/test_diag_compare.py)**

Gate/TTL matrix, route registration per environment (absent in `prd`, present in `int` and in
`prd` with the flag), API-key enforcement, all three layouts, and the invalid/expired/empty paths.
Operator notes added to [`operators_manual.md`](operations/operators_manual.md) §7.5.
