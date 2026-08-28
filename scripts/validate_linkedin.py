#!/usr/bin/env python3
"""LinkedIn Page-posting pre-flight.

Run this BEFORE trusting the automation, and after every re-authorization.
It verifies each link in the chain without publishing anything:

  1. Token is valid and identifies a member
  2. That member holds a role on the target Page (posting fails without it)
  3. The organization URN resolves
  4. (--upload) uploads a REAL image and waits for AVAILABLE, proving the
     half of the flow that actually breaks — but creates no post

The reason this matters more here than on the other channels: LinkedIn
access tokens last 60 days and programmatic refresh is limited to select
partners, so this credential dies on a schedule. A chain that worked last
month says nothing about today.

Usage:
  export LINKEDIN_ACCESS_TOKEN=... LINKEDIN_ORG_ID=...
  python scripts/validate_linkedin.py              # checks 1-3
  python scripts/validate_linkedin.py --upload     # checks 1-4

Exit code 0 = ready to post.
"""
import argparse
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import post_linkedin  # noqa: E402

# Mirrors validate_ig's 7-day gate. A token good for three more days passes
# every check here and still cannot survive the week — reporting that as
# "ready" is how the channel dies quietly on a Tuesday.
MIN_DAYS = 7


def fail(msg):
    print(f"  ✗ {msg}")
    sys.exit(1)


def ok(msg):
    print(f"  ✓ {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload", action="store_true",
                    help="also upload a real image (never posted) to prove "
                         "the half of the flow that actually breaks")
    ap.add_argument("--discover", action="store_true",
                    help="print LINKEDIN_ORG_ID and LINKEDIN_TOKEN_EXPIRES_AT "
                         "for the current token, ready to paste as secrets")
    ap.add_argument("--ttl-seconds", type=int, default=None,
                    help="token TTL from the portal's Token Details, used "
                         "with --discover to compute the expiry timestamp")
    ap.add_argument("--file", metavar="REPO_REL_PATH",
                    help="image to upload with --upload; defaults to the "
                         "newest LinkedIn card")
    args = ap.parse_args()

    tok = os.environ.get("LINKEDIN_ACCESS_TOKEN") or fail(
        "LINKEDIN_ACCESS_TOKEN not set")
    if args.discover:
        return discover(tok, args.ttl_seconds)
    org_id = os.environ.get("LINKEDIN_ORG_ID") or fail(
        "LINKEDIN_ORG_ID not set")
    org_urn = f"urn:li:organization:{org_id}"
    headers = post_linkedin._headers(tok, json_body=False)

    print(f"[0] pinned API version {post_linkedin.LINKEDIN_VERSION}")
    print("[1] token validity")
    r = requests.get("https://api.linkedin.com/v2/userinfo",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    if r.status_code != 200:
        fail(f"token rejected ({r.status_code}): {r.text[:200]}")
    ok(f"token valid — member: {(r.json() or {}).get('name', 'unknown')}")

    print("[2] Page role")
    # Posting as an organization requires an ADMINISTRATOR (or DSC) role on
    # that Page. Without it the token still looks fine and the POST fails
    # with a 403 at publish time, which is the worst moment to find out.
    r = requests.get(
        "https://api.linkedin.com/rest/organizationAcls"
        "?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
        headers=headers, timeout=30)
    if r.status_code != 200:
        print(f"    (could not read organizationAcls: {r.status_code} "
              f"{r.text[:160]})")
        print("    NOT fatal on its own, but if the upload below 403s, this "
              "is why — grant the member an ADMINISTRATOR role on the Page.")
    else:
        orgs = _orgs_in(r.json())
        if org_urn in orgs:
            ok(f"member administers {org_urn}")
        else:
            fail(f"member does NOT administer {org_urn}. Roles found: "
                 f"{orgs or 'none'}. Posting will 403.")

    print("[3] token lifetime")
    days = post_linkedin.token_days_left()
    if days is None:
        print("    LINKEDIN_TOKEN_EXPIRES_AT not set — cannot warn before "
              "this token dies. Set it when you mint the token (epoch "
              "seconds: now + expires_in).")
    elif days < MIN_DAYS:
        fail(f"token expires in {days} day(s). It will not survive the "
             f"posting schedule — re-authorize before relying on it.")
    else:
        ok(f"token has {days} days left")

    if args.upload:
        print("[4] image upload (real, but NOTHING is posted)")
        rel = args.file or _newest_card()
        if not rel:
            fail("no LinkedIn card to upload — render one, or pass --file")
        ok(f"target: {rel}")
        try:
            urn = post_linkedin.upload_image(rel, tok, org_urn)
        except post_linkedin.LinkedInError as e:
            fail(str(e))
        ok(f"uploaded and AVAILABLE: {urn} — no post was created")

    print("\nAll checks passed — the LinkedIn chain is ready. 🚀")


# LinkedIn's own samples for this endpoint disagree with each other: the
# roleAssignee example returns the URN under "organization", the paginated
# example under "organizationTarget". Reading only one silently finds
# nothing and reports "member does NOT administer" against a Page they do.
_ORG_FIELDS = ("organization", "organizationTarget")


def _orgs_in(payload):
    out = []
    for e in (payload or {}).get("elements", []):
        for field in _ORG_FIELDS:
            if e.get(field):
                out.append(e[field])
                break
    return out


def _newest_card():
    d = os.path.join(ROOT, "assets", "linkedin")
    if not os.path.isdir(d):
        return None
    imgs = [f for f in os.listdir(d)
            if f.lower().endswith(post_linkedin.ALLOWED_SUFFIXES)]
    if not imgs:
        return None
    newest = max(imgs, key=lambda f: os.path.getmtime(os.path.join(d, f)))
    return os.path.join("assets", "linkedin", newest)




# Documented default: "all access tokens are issued with a 60-day lifespan".
# Used only when the real TTL is not supplied — see the warning below.
DEFAULT_TTL_SECONDS = 60 * 86400


def discover(tok, ttl_seconds=None):
    """Print the two derived secrets for a freshly minted token.

    LINKEDIN_ORG_ID is asked of the API rather than read off a Page URL, so
    it cannot be the id of a Page the token cannot actually post to.
    """
    import time
    headers = post_linkedin._headers(tok, json_body=False)
    r = requests.get(
        "https://api.linkedin.com/rest/organizationAcls"
        "?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED",
        headers=headers, timeout=30)
    if r.status_code != 200:
        fail(f"could not read organizationAcls ({r.status_code}): "
             f"{r.text[:200]}\n"
             f"       This needs the rw_organization_admin scope on the "
             f"token — regenerate it with that scope ticked.")
    orgs = _orgs_in(r.json())
    if not orgs:
        fail("this member administers no Pages. Posting as an organization "
             "needs an ADMINISTRATOR role — grant it on the Page, then mint "
             "a fresh token.")

    print("\n# Paste these as repository secrets:\n")
    for urn in orgs:
        print(f"LINKEDIN_ORG_ID={urn.rsplit(':', 1)[-1]}"
              + (f"    # {urn}" if len(orgs) > 1 else ""))
    if len(orgs) > 1:
        print("# NB more than one Page — pick the PursuitAI one.")

    ttl = ttl_seconds or DEFAULT_TTL_SECONDS
    print(f"LINKEDIN_TOKEN_EXPIRES_AT={int(time.time()) + ttl}")
    if ttl_seconds is None:
        print(f"\n# ^ assumes the documented {DEFAULT_TTL_SECONDS // 86400}-day "
              f"lifespan, NOT this token's actual TTL. The portal shows the\n"
              f"# real value under Token Details; re-run with --ttl-seconds N "
              f"to use it. Being wrong here\n"
              f"# only shifts when the expiry alarm fires, but it shifts it "
              f"in the unhelpful direction if the\n"
              f"# token was already partly used.")


# Entry point LAST, deliberately. Python executes a module top to bottom, so
# a __main__ guard placed mid-file calls main() before anything below it is
# defined — which is exactly how discover() became a NameError the moment it
# was appended past the guard. test_entry_point_reaches_argument_parsing
# runs the script for real so that cannot recur silently.
if __name__ == "__main__":
    main()
