"""Compliance reviewer — the last check before anything reaches an account.

Our audience is federal contracting BD and capture professionals at small
businesses. It is a small, reputation-dense market: one wrong factual claim
is not recoverable, and an implied endorsement or a win guarantee is worse
than a typo. Everything here BLOCKS. Nothing warns-and-continues.

Two guards, deliberately separate:

  is_publishable(topic)   — has this claim been traced to an implementation
                            or an authoritative external source? Only
                            VERIFIED publishes.
  check_claims(topic, caption)
                          — does the rendered text break a content rule?

The second exists because the first is not enough: a topic can be VERIFIED
on its central claim and still carry a sentence we cannot stand behind.

These rules are conservative pattern matching, not comprehension. They will
not catch a novel phrasing, and they are a floor under human review, never
a replacement for it — the approval gate is what actually protects us.
"""
import dataclasses
import re

# Only a traced claim reaches an audience.
PUBLISHABLE_STATUSES = frozenset({"VERIFIED"})

# Firms that must never appear in a post. Empty by default; populate when
# a customer agrees to be named, and get it in writing first.
CUSTOMER_NAMES: tuple = ()


@dataclasses.dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    excerpt: str

    def __str__(self):
        return f"[{self.rule}] {self.message} — {self.excerpt!r}"


AGENCIES = (r"SBA|GSA|DHS|DoD|DOD|NASA|VA|HHS|DOE|DOT|EPA|HUD|USACE|"
            r"Department of [A-Z][a-z]+(?: [A-Z][a-z]+)*|federal government|"
            r"the government")

# Things a federal agency legitimately approves that belong to the USER,
# not to us. "your SBA-approved MPA" is accurate and standard GovCon
# language; "PursuitAI is SBA-approved" is the claim we must never make.
# The rule cannot tell those apart from the hyphen alone, so the noun that
# follows decides.
USER_OWNED = (r"MPA|mentor[- ]prot[ée]g[ée] agreement|joint venture|JV|"
              r"agreement|certification|cert|status|subcontracting plan|"
              r"plan|contract|award|schedule|application|8\(a\) status")

# An endorsement is a RELATIONSHIP claim: approved by, partnered with,
# official. Naming an agency as a data source is not one, and the product
# is built on federal data — it has to be able to say so.
_ENDORSEMENT = [
    re.compile(rf"\b(?:endorsed|approved|certified|authorized|sanctioned)\s+by\s+"
               rf"(?:the\s+)?(?:{AGENCIES})\b", re.I),
    # negative lookahead: an agency-approved THING the user owns is fine
    re.compile(rf"\b(?:{AGENCIES})[- ](?:approved|endorsed|certified|authorized)\b"
               rf"(?!\s+(?:{USER_OWNED})\b)", re.I),
    re.compile(rf"\b(?:official|authorized)\s+(?:{AGENCIES})\s+"
               rf"(?:partner|vendor|supplier|provider)\b", re.I),
    re.compile(rf"\bin\s+partnership\s+with\s+(?:the\s+)?(?:{AGENCIES})\b", re.I),
    re.compile(r"\bgovernment[- ]approved\b", re.I),
]

# A promise about outcomes. "Win more. Guess less." is a slogan;
# "you will win more bids" is a guarantee.
_WIN_GUARANTEE = [
    re.compile(r"\bguarantee(?:d|s)?\b", re.I),
    re.compile(r"\byou\s+will\s+win\b", re.I),
    re.compile(r"\bensur(?:e|es|ing)\s+(?:you\s+)?win\b", re.I),
    re.compile(r"\bwin\s+(?:rate\s+)?guaranteed\b", re.I),
]

_SEAL = [
    re.compile(rf"\b(?:{AGENCIES})\s+(?:seal|logo|emblem|insignia|crest)\b", re.I),
    re.compile(rf"\b(?:seal|logo|emblem|insignia)\s+of\s+(?:the\s+)?(?:{AGENCIES})\b",
               re.I),
    re.compile(r"\bofficial\s+(?:seal|logo|emblem|insignia)\b", re.I),
]

# A customer reference is a named entity doing something for/with us.
_TESTIMONIAL = re.compile(
    r"\b([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3}\s+"
    r"(?:Inc\.?|LLC|LLP|Corp\.?|Corporation|Company|Technologies|Solutions|"
    r"Systems|Group|Partners|Federal))\b")
_TESTIMONIAL_VERB = re.compile(
    r"\b(?:increased|doubled|tripled|grew|won|saved|reduced|improved|"
    r"reported|says?|said|told us|credits?)\b", re.I)

# Superlatives about the competitive field. These cannot be verified
# against our code or any external source — nobody has audited every
# competitor — and one informed reader with a counterexample discredits
# the account. Positioning is fine; "no competitor has this" is not.
_COMPETITIVE = [
    re.compile(r"\bno\s+(?:other\s+)?(?:competitor|platform|tool|vendor|"
               r"product|company)\b", re.I),
    re.compile(r"\bno\s+one\s+else\b|\bnobody\s+else\b", re.I),
    re.compile(r"\bthe\s+only\s+(?:built-in|platform|tool|product|one)\b", re.I),
    re.compile(r"\beveryone\s+else'?s?\s+(?:tools?|platforms?|products?)\b", re.I),
    re.compile(r"\bfirst\s+and\s+only\b|\bindustry[- ]first\b", re.I),
]

# Digits that make a factual assertion. Regulatory citations and the
# platform's own trial terms are excluded: FAR 52.219-14 is an identifier,
# not a statistic, and "14-day trial" is a term of service.
_NUMERIC = re.compile(
    r"(?<![\w.-])"
    r"(?:\$\s?\d[\d,.]*\s?(?:[KMB]|million|billion|thousand)?"
    r"|\d[\d,]*\s?\+?\s?%"
    r"|\d[\d,]{2,}\+?"
    r"|\b\d+\s?[KMB]\b"
    r"|\b\d+\s+(?:dimensions?|signals?|agencies|agenc(?:y|ies)|GWACs?|"
    r"sources?|occupations?|holders?|months?|years?|seconds?|factors?)\b)",
    re.I)
_CITATION = re.compile(
    r"\b(?:FAR|DFARS|CFR|C\.F\.R\.|U\.S\.C\.|USC|NAICS|SIN|Part|Subpart)\s*"
    r"[\d§.\-()]+|\b\d+\s+CFR\b|\b13\s+CFR\b", re.I)
_OWN_TERMS = re.compile(r"\b14[- ]day\b|\bunder 2 minutes\b|\b24[- ]?h(?:ours?)?\b",
                        re.I)


def _blank(text, pattern):
    """Remove matches so they cannot also trip a later rule."""
    return pattern.sub(" ", text)


def _excerpt(text, match, width=60):
    start = max(0, match.start() - width // 3)
    return text[start:start + width].replace("\n", " ").strip()


def is_publishable(topic):
    """True only when the topic's claims have been traced to a source."""
    verification = (topic or {}).get("verification") or {}
    return verification.get("status") in PUBLISHABLE_STATUSES


# Fields rendered onto the card image. These were invisible to the gate
# for the whole project: check_claims only ever received caption text, so
# a claim living in `body` shipped on every card unexamined.
RENDERED_FIELDS = ("headline", "body", "stat")


def check_rendered(topic):
    """Violations in the text drawn onto the card itself."""
    out = []
    for field in RENDERED_FIELDS:
        for v in check_claims(topic, (topic or {}).get(field) or ""):
            out.append(Violation(v.rule, f"{field}: {v.message}", v.excerpt))
    return out


def check_claims(topic, caption):
    """Return a list of Violation. Empty means the caption may publish."""
    caption = caption or ""
    violations = []

    for pattern in _ENDORSEMENT:
        m = pattern.search(caption)
        if m:
            violations.append(Violation(
                "federal_endorsement",
                "implies a federal agency endorses, approves, or partners "
                "with us", _excerpt(caption, m)))
            break

    for pattern in _WIN_GUARANTEE:
        m = pattern.search(caption)
        if m:
            violations.append(Violation(
                "win_guarantee",
                "promises an outcome we cannot guarantee",
                _excerpt(caption, m)))
            break

    for pattern in _SEAL:
        m = pattern.search(caption)
        if m:
            violations.append(Violation(
                "agency_seal",
                "references an agency seal, logo, or insignia",
                _excerpt(caption, m)))
            break

    for pattern in _COMPETITIVE:
        m = pattern.search(caption)
        if m:
            violations.append(Violation(
                "competitive_claim",
                "asserts something about every competitor, which cannot be "
                "verified against any source", _excerpt(caption, m)))
            break

    for name in CUSTOMER_NAMES:
        idx = caption.lower().find(name.lower())
        if idx >= 0:
            violations.append(Violation(
                "customer_name", f"names a customer ({name})",
                caption[max(0, idx - 20):idx + 60].strip()))
            break
    else:
        m = _TESTIMONIAL.search(caption)
        if m and _TESTIMONIAL_VERB.search(caption):
            violations.append(Violation(
                "customer_name",
                "reads as a named customer reference; get written consent "
                "before naming any firm", _excerpt(caption, m)))

    # Numerics are only allowed once the topic's claims are sourced.
    if not is_publishable(topic):
        stripped = _blank(_blank(caption, _CITATION), _OWN_TERMS)
        m = _NUMERIC.search(stripped)
        if m:
            violations.append(Violation(
                "unsourced_numeric",
                "states a figure on a topic whose claims are not VERIFIED",
                _excerpt(stripped, m)))

    return violations


def assert_publishable(topic, captions_by_channel):
    """Raise ComplianceError if anything about this post may not ship."""
    if not is_publishable(topic):
        status = ((topic or {}).get("verification") or {}).get("status",
                                                              "MISSING")
        raise ComplianceError(
            f"topic {topic.get('id')!r} is {status}, not VERIFIED — its "
            f"claims have not been traced to a source")
    problems = [f"card {v}" for v in check_rendered(topic)]
    for channel, caption in captions_by_channel.items():
        for violation in check_claims(topic, caption):
            problems.append(f"{channel}: {violation}")
    if problems:
        raise ComplianceError("; ".join(problems))


class ComplianceError(Exception):
    """A post that must not be published."""
