---
name: api-capability-probing
description: >
  Invoke the moment a third-party API call fails (any non-2xx), a 2xx write's
  effect never appears at the real sink (device, dashboard, CRM record), a
  feature behaves differently than its docs describe, or I am about to say a
  capability is "not supported", "impossible", "a platform limitation", or "a
  vendor bug". Also invoke when the user says they have seen a capability work
  that I claimed does not. Runs the probe protocol that separates "my request
  was wrong" from "the feature does not exist" — the two are indistinguishable
  from a single failed call. Encodes the 2026-08-05 Blooio session, where three
  capabilities were declared impossible and all three worked once called
  correctly.
---

# API Capability Probing

## The rule

A failed call is evidence about my request. It is not evidence about the
platform. `X works` is proven by one success; `X is impossible` is proven only
by exhausting the surface. Using one trial for both is what produces false
limitations.

Until the evidence bar below is met, the only permitted conclusion is
**"I haven't found a way to do this yet"** — never "it can't be done."

## What this costs and when to stop

Probing is cheap (minutes) compared to the alternative (a product redesigned
around a limitation that does not exist). But it is not unbounded. The honest
exit is a report, not a verdict: what I tried as a table, what I learned, and
the top 3 untried hypotheses. That exit is available at any time and is never a
failure — it hands the user a decision instead of a wrong fact.

## Protocol

Work top to bottom. Stop as soon as something works.

### 1. State the claim and its success signal

Write one line: `CLAIM UNDER TEST: <API> can <verb> <resource> such that <observable>.`

The observable must be the real sink — the title visible on the recipient's
device, the avatar rendered in the thread, the appointment in the CRM. Never an
HTTP status. A 200 that never applies is a failure; a 502 that applied is a
success.

### 2. Capture the raw failure before forming any opinion

Log verbatim: method, full path, content-type, body sent, response status, and
the **complete response body**. No paraphrase, no truncation.

Opinions formed before the evidence is captured are built on training-data
memory, which is the least reliable source available for a niche vendor API.

### 3. Enumerate the endpoint surface

Scan the whole doc set for the resource noun **and its synonyms** (group, chat,
thread, conversation). Build an inventory before probing further:

| method | path | required params | content-type | returns |

Include creation endpoints, sub-resource endpoints (`/{id}/icon`,
`/{id}/participants`, `/{id}/background`), and anything that merely mentions the
resource in prose.

I cannot claim a limitation of an object class while an endpoint that creates or
mutates that class sits uncalled. In the Blooio session the answer was a
documented endpoint that was never read and never called.

### 4. Mine error bodies — they are the schema oracle

Read the **body** of every non-2xx, not the status. Then actively provoke errors
to extract the contract: send an empty body, the wrong content-type, an unknown
field, a malformed id. Each 400/422 is a free spec excerpt.

The Blooio group-icon answer arrived as
`{"message":"Invalid form data. Use multipart/form-data."}` — the entire solution
to a problem already declared unsolvable, found by accident while probing.

Decode status classes as **routing instructions, not verdicts**:

| status | means | next action |
|---|---|---|
| 404 | wrong path or id | keep enumerating paths |
| 405 | path exists, wrong verb | try the other verbs |
| 415 | wrong content-type | multipart / form-urlencoded / json |
| 400, 422 | right endpoint, wrong shape | read the message, vary payload |
| 401, 403 | token scope | check permissions, not feature existence |
| 409 | resource state conflict | go to step 7 |
| 5xx | reached the feature; downstream choked on THIS object | evidence FOR the feature existing — go to step 7 |

### 5. Run a request-shape matrix, one axis at a time

- **content-type** — json, multipart/form-data, form-urlencoded
- **payload completeness** — full object with every writable field vs the single
  field I care about
- **field naming** — snake_case, camelCase, nested vs wrapped envelope
- **value encoding** — remote URL, base64, raw file part, vendor media id
- **identifier form** — internal id, GUID, raw handle, normalized E.164

"I retried" never means re-sending the same call. Log every cell.

### 6. Never conclude from a partial write

If an update returned 2xx but did not apply, re-issue it as a **full write**:
GET the resource, mutate one field, send every writable field back together.

Many APIs trigger downstream sync only on a complete-object write. This is
exactly the Blooio avatar case — an avatar-only PUT persisted in their database
and never reached the device; name + avatar + sharing sent together applied
immediately. A support ticket was drafted against a vendor for a caller-side bug.

### 7. Verify at the sink, then classify

| state | meaning | next |
|---|---|---|
| stored + applied | works | done |
| not stored | request shape wrong | step 5 |
| stored, NOT applied | sync condition unmet | step 6, then step 8 |

"Stored but not applied" looks most like a vendor bug and is most often
self-inflicted. Splitting it out is what keeps caller error and vendor defect
distinguishable.

### 8. Interrogate the resource's provenance

When an operation fails on one instance, **how that instance was created is a
prime suspect**. Documented create endpoint, implicit shortcut, imported, or
made in the vendor UI?

Dump the resource's own fields and look for type tells: service/protocol
markers, GUID prefixes, capability flags. Compare field-by-field against a
resource known to support the operation.

Blooio groups auto-created by messaging a comma-separated number list got a
service-agnostic `any;+;` chat GUID, so an iMessage-only feature 502'd. The
operation was fine. The object was the wrong kind of object.

### 9. Build a golden control

Create a fresh resource through the **most explicit documented create endpoint**
with every optional field supplied, then retry the failing operation against it.

If it succeeds, the capability exists and the original resource was in the wrong
state — fix provenance, not product. **No absence claim is admissible without a
golden control that also failed.**

### 10. Cross-check a known-good client

Does the vendor's own dashboard, SDK, Postman collection, or webhook sample do
this? If their UI can, open devtools and capture the exact network call — that is
ground truth for method, path, content-type, and field names, and it outranks
both prose docs and my memory.

### 11. Isolate flakiness

Repeat the failing call 3+ times, on a fresh resource, on a second account if
available, and after a 30-60s propagation wait with a re-GET. N=1 cannot
distinguish a permanent limitation from a transient, an async delay, or a cache.

## Evidence bar

### To say "not supported"

Either (a) an explicit doc statement that it is unsupported, or (b) **all** of:
complete endpoint inventory with every row probed; shape matrix exhausted;
golden control (step 9) also failed; minimal `curl` repro; 3+ retries across 2+
resources; no known-good client can do it.

### To say "vendor bug" or draft a support ticket

The exact doc line stating the promised behavior (quoted, with URL); the request
and response that contradict it; proof the full-object write also fails; proof
the golden control also fails; minimal `curl` repro; reproduction on 2+ distinct
resources. **If any item is missing, it is my bug until proven otherwise.**

A support ticket spends the user's credibility with a vendor. Default attribution
for an unexplained failure is the caller.

## When the user contradicts me

If the user says "I've seen it work," the investigation reopens immediately and
the question changes from *whether* to *how*. Reset to step 3 with the inventory
cleared, and ask what conditions produced their working case — which account,
which client, created how.

Their live-system experience outranks any inference I can draw from a partial
doc read. Never argue from memory against a reported observation.

## Composition with other rules

- **`live-system-testing` still governs writes a real person can see.** Read-only
  probes and writes to my own throwaway resources run freely. Probing that
  mutates a real prospect's or teammate's artifacts goes through that skill
  first. The rules compose; neither overrides the other.
- **`superpowers:systematic-debugging`** covers bugs in our code. This skill
  covers gaps in someone else's API. When a failure could be either, run this
  one first — it is cheaper to rule out my own request than to debug a system I
  do not control.

## Red flags — stop and run the protocol

| Thought | Reality |
|---|---|
| "That's just how Apple/the carrier/the platform works" | Name where I observed that. If it is in zero tool outputs, I invented it. |
| "The docs don't mention it, so it's not possible" | Absence of mention is not proof of absence. |
| "It 502'd — their device refuses this" | 5xx means I reached the feature. That is evidence FOR it existing. |
| "I should tell the user to open a support ticket" | Have I done a full-object write? A golden control? |
| "Let me redesign the flow around this limitation" | The limitation has not cleared the evidence bar. |
| "I already tried that" | Did I vary the axis, or re-send the same call? |
| "The user's waiting, I should just report the blocker" | Report it as "haven't found a way yet" with a table. Never as a verdict. |
