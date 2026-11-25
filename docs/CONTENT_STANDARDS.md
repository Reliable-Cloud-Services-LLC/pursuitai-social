# Content standards

Editorial guardrails for what goes in `content/calendar.json`. The README and
`OPERATIONS.md` cover *how* a post happens; this covers *what is allowed to be a
topic*.

## Security posture is a standard, not a feature

**Do not market security-by-design as an ad angle.** Secure handling of a
customer's files, tenancy isolation, encryption, access logging, and the absence
of risky endpoints are baseline standards we hold ourselves to — not selling
points to advertise.

Banned framings (topics and angles alike):

- "Encrypted per file / your own key" as the *headline* value of a topic.
- "The admin screen has no download button," "the endpoint does not exist,"
  "absence, not a 403," or any variant that advertises the *absence* of an
  insecure surface.
- "Role-gated access," "full audit log," "metadata-only by construction," or
  "built for firms that get security-reviewed" as the thing an ad leads on.

Why this is a standard, not a case-by-case call:

1. **Wrong reader.** The audience is federal BD and capture professionals, who
   measure us by whether we help them *win work*. An infra-architecture claim
   ("our admin file surface has no download endpoint") talks past them.
2. **Invites scrutiny you don't want.** In a small, reputation-dense market,
   advertising baseline hygiene makes buyers ask *why you're drawing attention
   to it* — and then asks you to prove it.
3. **Any gap becomes a liability.** The moment security is a marketing claim, a
   single caveat turns it into a misrepresentation. The retired `secure-docs`
   topic had already caused a near-miss: an over-claim ("platform staff see
   metadata, never contents") was caught in audit, because one shared KMS key
   means an operator with the right AWS access can decrypt out-of-band. The
   checkable application-layer claim was fine; the *marketing* of it was the risk.

If a prospect in a security review asks about file handling, answer it there,
grounded and precise. Do not put it on a billboard.

### What topics should sell instead

Outcomes the buyer is trying to produce: **win more, win faster, price to win,
find the right bids, stop guessing, manage the award.** Infrastructure and
security belong in the product and in security-review answers — not in the feed.

## History

- **2026-11-25** — Removed the `secure-docs` ("Secure File Storage") topic under
  this standard. It led on "the admin file surface has no download/preview
  endpoint — absence, not a 403," which is the exact framing this rule exists to
  prevent.
