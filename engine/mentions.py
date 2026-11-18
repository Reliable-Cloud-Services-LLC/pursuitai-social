"""LinkedIn mentions for award posts.

A company mention is sent only when exactly one LinkedIn organization has
a website whose registrable domain equals the awardee's SAM.gov entity
website. Zero matches, or more than one, and the post uses the plain legal
name. A stored UEI mapping is reused. An override or a block is never
replaced by a later automatic match.

People are suggested from the company website and are not mentioned until
the owner confirms them. LinkedIn's Posts API accepts a member mention
(`@[Name](urn:li:person:ID)`) in commentary authored by an organization.
The only API that turns a profile URL into that URN is People Typeahead,
which is restricted to select developers, requires
`r_organization_followers`, and returns only followers of the PursuitAI
Page. When that lookup is unavailable, a confirmed person is written as a
plain name. Unconfirmed people are left out. This module never requests a
LinkedIn profile page.

Docs, read 2026-11-18:

  Posts API mentions
    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-10
  little text
    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format?view=li-lms-2026-10
  Organization lookup by vanity name (email-domain finder removed in 202408)
    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/organization-lookup-api?view=li-lms-2026-10
  People Typeahead (restricted; followers only)
    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-atmention-search-api?view=li-lms-2026-10

    python engine/mentions.py set-org --uei UEI --company-url https://www.linkedin.com/company/example
    python engine/mentions.py set-org --uei UEI --urn urn:li:organization:1 --name "Exact LinkedIn Name"
    python engine/mentions.py block-org --uei UEI
    python engine/mentions.py confirm-person --uei UEI --name "Jane Doe" --title "CEO" --profile https://www.linkedin.com/in/example
    python engine/mentions.py confirm-person --uei UEI --name "Jane Doe" --title "CEO" --urn urn:li:person:abc
"""
import argparse
import datetime
import ipaddress
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import captions  # noqa: E402

STORE_NAME = os.path.join("content", "linkedin_mentions.json")
NOTE = ("UEI to LinkedIn organization, plus owner-confirmed people. "
        "Automatic matches never overwrite source override or blocked.")

# SAM.gov entity-information v3. entityURL is the website on the entity
# registration. Optional: the feed's recipient_website wins when present,
# so this key is not required.
SAM_ENTITIES = "https://api.sam.gov/entity-information/v3/entities"

_WEBSITE_KEYS = ("recipient_website", "entity_website", "sam_website", "entity_url")
_MULTI_SUFFIX = (
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "co.nz", "co.za", "com.br", "co.jp", "com.mx", "com.sg",
)
_PLATFORM_DOMAINS = {
    "linkedin.com", "facebook.com", "fb.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "google.com",
    "sam.gov", "usaspending.gov", "fpds.gov",
}
_SUFFIXES = {
    "inc", "llc", "corp", "co", "ltd", "lp", "llp", "pllc", "pc", "plc",
    "company", "corporation", "incorporated", "limited",
}
_GENERIC = _SUFFIXES | {
    "group", "solutions", "solution", "technologies", "technology", "tech",
    "federal", "systems", "system", "services", "service", "consulting",
    "consultants", "international", "associates", "partners", "partner",
    "holdings", "enterprises", "enterprise", "industries", "global",
    "national", "american", "usa", "the", "and", "of", "for",
}
_VANITY_DENY = {
    "admin", "signup", "login", "unavailable", "company", "school",
    "showcase", "feed", "jobs", "people", "about", "posts",
}
_LI_VANITY = re.compile(
    r"(?:https?:)?//(?:[\w.-]+\.)?linkedin\.com/"
    r"(?:company|school|showcase)/([A-Za-z0-9\-_%]+)",
    re.I)
_PROFILE = re.compile(
    r"https?://(?:[\w.-]+\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)/?",
    re.I)
_ORG_URN = re.compile(r"urn:li:organization:(\d+)\Z")
_PERSON_URN = re.compile(r"urn:li:person:[A-Za-z0-9_-]+\Z")
_UEI = re.compile(r"[A-Z0-9]{12}\Z")

_CEO = re.compile(
    r"chief executive officer|\bceo\b|"
    r"president\s*(?:&|and)\s*ceo|founder\s*(?:&|and)\s*ceo",
    re.I)
_GROWTH = re.compile(
    r"chief growth officer|\bcgo\b|business development|"
    r"head of growth|vp of growth|vice president of growth|"
    r"director of (?:growth|business development)|growth lead",
    re.I)
_PRESIDENT = re.compile(r"\bpresident\b", re.I)
_VICE = re.compile(r"\bvice\b", re.I)
_NAME_LINE = re.compile(
    r"[A-Z][a-z]+(?: [A-Z]\.)?(?: [A-Z][a-z]+){1,2}\Z")
_NAME_STOP = {
    "Inc", "Llc", "Corp", "The", "Our", "Team", "Leadership", "Board",
    "About", "Meet", "Contact", "Email", "Phone", "Read", "More", "Learn",
    "Click", "Home", "Services", "Solutions", "Federal", "Department",
    "United", "States", "Chief", "Executive", "Officer", "President",
    "Director", "Vice", "Growth", "Business", "Development", "Founder",
    "Head", "Company", "Group",
}


def _public_error(exc):
    """Exception text safe to print. The SAM key is a query parameter."""
    text = re.sub(r"(api_key=)[^&\s]+", r"\1redacted", str(exc))
    text = re.sub(r"(Bearer\s+)\S+", r"\1redacted", text)
    return text[:400]


class LookupFailed(RuntimeError):
    def __init__(self, message, fatal=False):
        super().__init__(message)
        self.fatal = fatal


class NoToken(LookupFailed):
    def __init__(self):
        super().__init__("no LinkedIn token", fatal=True)


def store_path(root=None):
    return os.path.join(root or ROOT, STORE_NAME)


def empty_store():
    return {"version": 1, "note": NOTE, "organizations": {}, "people": {}}


def load(root=None):
    path = store_path(root)
    if not os.path.exists(path):
        return empty_store()
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"[mentions] {path} is unreadable ({exc}); "
              f"not tagging from it and not overwriting it")
        store = empty_store()
        store["_corrupt"] = True
        return store
    if not isinstance(data, dict):
        store = empty_store()
        store["_corrupt"] = True
        return store
    orgs = data.get("organizations")
    people = data.get("people")
    return {
        "version": 1,
        "note": data.get("note") or NOTE,
        "organizations": orgs if isinstance(orgs, dict) else {},
        "people": people if isinstance(people, dict) else {},
    }


def save(root, store):
    """Write the store. Refuses to replace a file that did not parse."""
    if store.get("_corrupt"):
        print("[mentions] not writing over an unreadable store")
        return False
    path = store_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": 1,
        "note": store.get("note") or NOTE,
        "organizations": store.get("organizations") or {},
        "people": store.get("people") or {},
    }
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
    return True


def _snapshot(store):
    return json.dumps({
        "organizations": store.get("organizations") or {},
        "people": store.get("people") or {},
    }, sort_keys=True)


def uei_key(value):
    text = str(value or "").strip().upper()
    return text if _UEI.match(text) else ""


def _now(now=None):
    if now:
        return now
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def registrable_domain(value):
    """eTLD+1, lowercase, with a leading www removed.

    `https://www.akumen.com/about` and `akumen.com` both yield akumen.com.
    A subdomain yields the same registrable domain (`careers.akumen.com`).
    Empty when the host is not a domain.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    host = (urlparse(text).hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    labels = host.split(".")
    joined = ".".join(labels)
    suffix_labels = 1
    for suffix in _MULTI_SUFFIX:
        if joined == suffix or joined.endswith("." + suffix):
            suffix_labels = suffix.count(".") + 1
            break
    need = suffix_labels + 1
    if len(labels) < need:
        return ""
    return ".".join(labels[-need:])


def _http_url(value):
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    return text


def _url_allowed(url):
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower().strip(".")
    if (not host or host in {"localhost", "metadata.google.internal"}
            or host.endswith(".local") or host.endswith(".internal")):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


def website_for(award, sam_get=None):
    """(url, source) from the feed, else the optional SAM lookup.

    source is "feed", "sam", or "". No URL is not an error: the post
    still goes out, without a company mention.
    """
    award = award or {}
    for key in _WEBSITE_KEYS:
        url = _http_url(award.get(key))
        domain = registrable_domain(url)
        if url and domain and domain not in _PLATFORM_DOMAINS:
            return url, "feed"
    uei = uei_key(award.get("recipient_uei"))
    api_key = os.environ.get("SAM_API_KEY")
    if not uei or not api_key:
        return "", ""
    try:
        url = sam_entity_website(uei, api_key, get=sam_get)
    except Exception as exc:
        # The key rides on the query string. Do not print the exception:
        # requests includes the URL.
        print(f"[mentions] SAM lookup failed ({type(exc).__name__})")
        return "", ""
    domain = registrable_domain(url)
    if url and domain and domain not in _PLATFORM_DOMAINS:
        return url, "sam"
    return "", ""


def sam_entity_website(uei, api_key, get=None):
    """The entityURL on a SAM.gov registration, or ""."""
    import requests
    getter = get or requests.get
    response = getter(
        SAM_ENTITIES,
        params={"api_key": api_key, "ueiSAM": uei,
                "includeSections": "entityRegistration"},
        headers={"Accept": "application/json",
                 "User-Agent": "pursuitai-social-mentions"},
        timeout=20)
    if getattr(response, "status_code", None) != 200:
        return ""
    try:
        payload = response.json()
    except Exception:
        return ""
    return entity_url_from_sam(payload)


def entity_url_from_sam(payload):
    """First http(s) entityURL / entityWebsite in a SAM entity payload."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in ("entityurl", "entitywebsite") and isinstance(value, str):
                    found.append(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    for url in found:
        if _http_url(url):
            return url
    return ""


def _tokens(name):
    cleaned = (name or "").lower().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return [token for token in cleaned.split() if token]


def _strip_suffix(tokens):
    out = list(tokens)
    while out and out[-1] in _SUFFIXES:
        out.pop()
    if out and out[0] == "the":
        out = out[1:]
    return out


def vanity_candidates(name):
    """Vanity names to look up. Not a match by themselves.

    Each one is still accepted only when the organization's website
    domain equals the SAM domain. Generic single tokens (solutions,
    federal, inc) are not looked up.
    """
    tokens = _tokens(name)
    base = _strip_suffix(tokens)
    raws = []

    def add(parts):
        if not parts:
            return
        raws.append("-".join(parts))
        raws.append("".join(parts))

    add(tokens)
    if base != tokens:
        add(base)
    if (base and base[0] not in _GENERIC and len(base[0]) >= 4
            and len(base) > 1):
        raws.append(base[0])
    if len(base) >= 2 and base[0] not in _GENERIC:
        raws.append(base[0] + base[1])
        raws.append(base[0] + "-" + base[1])
    out = []
    seen = set()
    for item in raws:
        item = item.strip("-")
        if item in seen or item in _GENERIC or item in _VANITY_DENY:
            continue
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item):
            continue
        if not 3 <= len(item) <= 80:
            continue
        seen.add(item)
        out.append(item)
    return out[:8]


def vanities_in_html(html):
    """Company-page vanities linked from a page the company itself published."""
    found = []
    for match in _LI_VANITY.finditer(html or ""):
        vanity = match.group(1).lower().strip("-")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", vanity):
            continue
        if vanity in _VANITY_DENY or vanity in found:
            continue
        if not 2 <= len(vanity) <= 80:
            continue
        found.append(vanity)
    return found


def profile_vanity(url):
    match = _PROFILE.match((url or "").strip())
    if not match:
        return ""
    vanity = match.group(1).strip("-")
    if not re.fullmatch(r"[A-Za-z0-9\-_%]+", vanity) or "%" in vanity:
        return ""
    return vanity


def canonical_profile(url):
    vanity = profile_vanity(url)
    if not vanity:
        return ""
    return f"https://www.linkedin.com/in/{vanity}"


class _Visible(HTMLParser):
    """Visible text, with a marker in front of a name linked to /in/."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            return
        if tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr",
                   "section", "article"):
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            vanity = profile_vanity(href)
            if vanity:
                self.parts.append(f"\n[[in:{vanity}]] ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "li", "h1", "h2", "h3", "tr", "section"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible_lines(html):
    parser = _Visible()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    lines = []
    pending_profile = ""
    for raw in "".join(parser.parts).splitlines():
        line = raw.strip()
        profile = ""
        while line.startswith("[[in:"):
            end = line.find("]]")
            if end < 0:
                break
            profile = line[5:end]
            line = line[end + 2:].strip()
        line = re.sub(r"\s+", " ", line).strip()
        if profile:
            pending_profile = profile
        if not line:
            continue
        lines.append({"line": line, "profile": pending_profile})
        pending_profile = ""
    return lines


def _name_ok(name, company):
    parts = (name or "").split()
    if len(parts) < 2:
        return False
    if any(part in _NAME_STOP for part in parts):
        return False
    if company and name.casefold() in company.casefold():
        return False
    return True


def _clean_name(text):
    text = re.sub(r"[^A-Za-z.\s]", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .,-")
    if _NAME_LINE.match(text):
        return text
    return ""


def _same_line_name(line, pattern):
    stripped = pattern.sub(" ", line)
    return _clean_name(stripped)


def suggest_executives(pages, company_name=""):
    """Up to two unconfirmed people: one executive, one growth lead.

    A role with two different names is dropped. These are suggestions.
    Nothing here is a mention.
    """
    rows = []
    for page in pages or []:
        url = page.get("url") or ""
        for item in visible_lines(page.get("html") or ""):
            rows.append((item["line"], item.get("profile") or "", url))
    ceo = {}
    president = {}
    growth = {}

    def take(bucket, name, title, url, profile):
        current = bucket.get(name)
        if current is None:
            bucket[name] = {"name": name, "title": title, "source_url": url,
                            "profile_url": canonical_profile(
                                f"https://www.linkedin.com/in/{profile}")
                            if profile else ""}

    for index, (line, profile, url) in enumerate(rows):
        if len(line) > 90:
            continue
        ceo_match = _CEO.search(line)
        growth_match = _GROWTH.search(line)
        role_pattern = None
        bucket = None
        title = ""
        if ceo_match:
            role_pattern, bucket, title = _CEO, ceo, ceo_match.group(0)
        elif growth_match:
            role_pattern, bucket, title = _GROWTH, growth, growth_match.group(0)
        elif _PRESIDENT.search(line) and not _VICE.search(line):
            role_pattern, bucket, title = _PRESIDENT, president, "President"
        if bucket is None:
            continue
        name = _same_line_name(line, role_pattern)
        # "Vice President, Business Development" leaves the title words
        # behind. That is not a person; the name is on the next line.
        if name and not _name_ok(name, company_name):
            name = ""
        found_profile = profile
        if not name:
            for neighbor in (index - 1, index + 1):
                if not 0 <= neighbor < len(rows):
                    continue
                other, other_profile, other_url = rows[neighbor]
                if len(other) > 60:
                    continue
                candidate = _clean_name(other)
                if candidate and _name_ok(candidate, company_name):
                    name = candidate
                    url = other_url or url
                    found_profile = found_profile or other_profile
                    break
        if name and _name_ok(name, company_name):
            take(bucket, name, title, url, found_profile)
    chosen = []
    if len(ceo) == 1:
        chosen.append(("executive", next(iter(ceo.values()))))
    elif not ceo and len(president) == 1:
        chosen.append(("executive", next(iter(president.values()))))
    if len(growth) == 1:
        chosen.append(("growth", next(iter(growth.values()))))
    suggestions = []
    for role, row in chosen[:2]:
        item = {
            "name": row["name"],
            "title": row["title"],
            "role": role,
            "source_url": row["source_url"],
            "confirmed": False,
        }
        if row.get("profile_url"):
            item["profile_url"] = row["profile_url"]
        suggestions.append(item)
    return suggestions


def orgs_from_response(payload):
    """Non-admin organization elements that have an id and a localizedName."""
    if not isinstance(payload, dict):
        return []
    elements = payload.get("elements")
    if elements is None and payload.get("id") is not None:
        elements = [payload]
    out = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        try:
            org_id = int(element.get("id"))
        except (TypeError, ValueError):
            continue
        name = (element.get("localizedName") or "").strip()
        if not name or any(ch in name for ch in "[]\n\r"):
            continue
        out.append({
            "id": org_id,
            "organization_urn": f"urn:li:organization:{org_id}",
            "localized_name": name,
            "vanity_name": (element.get("vanityName") or "").strip(),
            "linkedin_website": (element.get("localizedWebsite") or "").strip(),
        })
    return out


def default_lookup(vanity):
    """GET /rest/organizations?q=vanityName. Raises NoToken or LookupFailed."""
    import requests
    import post_linkedin
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not token:
        raise NoToken()
    response = requests.get(
        f"{post_linkedin.API}/organizations",
        headers=post_linkedin._headers(token, json_body=False),
        params={"q": "vanityName", "vanityName": vanity},
        timeout=20)
    return _lookup_result(response)


def _lookup_result(response):
    code = getattr(response, "status_code", None)
    if code in (401, 403, 429) or (code is not None and code >= 500):
        raise LookupFailed(f"HTTP {code}", fatal=True)
    if code == 404 or (code is not None and code >= 400):
        return []
    try:
        payload = response.json()
    except Exception as exc:
        raise LookupFailed(f"non-JSON ({type(exc).__name__})", fatal=True) from exc
    return orgs_from_response(payload)


def default_person_lookup(profile_url):
    """Resolve a /in/ URL via People Typeahead, or return "".

    Empty means "do not mention": missing permission, the member does not
    follow the Page, or the URL is not a profile. Never raises into the
    poster. The endpoint is restricted and follower-only; see the module
    docstring.
    """
    import requests
    import post_linkedin
    vanity = profile_vanity(profile_url)
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    org_id = (os.environ.get("LINKEDIN_ORG_ID") or "").strip()
    if not vanity or not token or not org_id or not org_id.isdigit():
        return ""
    try:
        response = requests.get(
            f"{post_linkedin.API}/vanityUrl",
            headers=post_linkedin._headers(token, json_body=False),
            params={
                "q": "vanityUrlAsOrganization",
                "vanityUrl": f"https://www.linkedin.com/in/{vanity}",
                "organization": f"urn:li:organization:{org_id}",
            },
            timeout=20)
    except Exception as exc:
        print(f"[mentions] person lookup failed ({type(exc).__name__}: {_public_error(exc)})")
        return ""
    code = getattr(response, "status_code", None)
    if code in (401, 403, 404) or (code is not None and code >= 400):
        print(f"[mentions] person lookup HTTP {code} for /in/{vanity} "
              f"— confirmed name will be plain text")
        return ""
    try:
        elements = (response.json() or {}).get("elements") or []
    except Exception:
        return ""
    urns = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        member = element.get("member") or element.get("personUrn") or ""
        if _PERSON_URN.match(member):
            urns.append(member)
    if len(urns) == 1:
        return urns[0]
    return ""


def _plain(reason, **extra):
    row = {"status": "plain", "reason": reason, "source": ""}
    row.update(extra)
    return row


def _is_blocked(row):
    return isinstance(row, dict) and (
        row.get("source") == "blocked" or row.get("blocked") is True)


def _usable_match(row):
    if not isinstance(row, dict) or _is_blocked(row):
        return False
    name = (row.get("localized_name") or "").strip()
    urn = (row.get("organization_urn") or "").strip()
    return bool(name) and bool(_ORG_URN.match(urn))


def _from_stored(row, status):
    company = {
        "status": status,
        "reason": "blocked" if status == "blocked" else "",
        "source": row.get("source") or "",
        "organization_urn": row.get("organization_urn") or "",
        "localized_name": row.get("localized_name") or "",
        "vanity_name": row.get("vanity_name") or "",
        "linkedin_website": row.get("linkedin_website") or "",
        "sam_website": row.get("sam_website") or "",
        "sam_domain": row.get("sam_domain") or "",
        "verified_at": row.get("verified_at") or "",
        "note": row.get("note") or "",
        "reused": True,
        "checked": [],
    }
    return company


def _persist_row(company, award):
    return {
        "uei": uei_key(award.get("recipient_uei")),
        "recipient_name": award.get("recipient_name") or "",
        "organization_urn": company.get("organization_urn") or "",
        "localized_name": company.get("localized_name") or "",
        "vanity_name": company.get("vanity_name") or "",
        "linkedin_website": company.get("linkedin_website") or "",
        "sam_website": company.get("sam_website") or "",
        "sam_domain": company.get("sam_domain") or "",
        "source": "verified",
        "verified_at": company.get("verified_at") or "",
        "blocked": False,
        "note": "",
    }


def match_organization(award, *, lookup, pages=None, website=None,
                       sam_get=None, fetch=None, now=None):
    """The company mention decision for one award. Does not read the store.

    `lookup(vanity)` returns a list of org dicts, or raises NoToken /
    LookupFailed. `pages` is a list of {url, html}. Pass pages=[] to skip
    fetching. None fetches the company site when a website is known.
    """
    stamp = _now(now)
    if website is None:
        website, _source = website_for(award, sam_get=sam_get)
    if not website:
        return _plain("no_website")
    domain = registrable_domain(website)
    if not domain or domain in _PLATFORM_DOMAINS:
        return _plain("no_website")
    if pages is None:
        pages = collect_pages(website, fetch=fetch)
    vanities = []
    for page in pages or []:
        html = page.get("html") if isinstance(page, dict) else ""
        for vanity in vanities_in_html(html or ""):
            if vanity not in vanities:
                vanities.append(vanity)
    for vanity in vanity_candidates((award or {}).get("recipient_name") or ""):
        if vanity not in vanities:
            vanities.append(vanity)
    vanities = vanities[:8]
    if not vanities:
        return _plain("no_match", sam_domain=domain, sam_website=website,
                      checked=[])
    if lookup is None:
        lookup = default_lookup
    matches = []
    seen = set()
    errors = []
    checked = []
    for vanity in vanities:
        checked.append(vanity)
        try:
            found = lookup(vanity) or []
        except NoToken:
            return _plain("no_token", sam_domain=domain, sam_website=website,
                          checked=checked)
        except LookupFailed as exc:
            errors.append(str(exc))
            if exc.fatal:
                return _plain("lookup_failed", sam_domain=domain,
                              sam_website=website, checked=checked,
                              detail=str(exc))
            continue
        for org in found:
            org_domain = registrable_domain(org.get("linkedin_website") or "")
            if not org_domain or org_domain != domain:
                continue
            if org["id"] in seen:
                continue
            seen.add(org["id"])
            matches.append(org)
    if len(matches) == 1:
        org = matches[0]
        return {
            "status": "matched",
            "reason": "",
            "source": "verified",
            "organization_urn": org["organization_urn"],
            "localized_name": org["localized_name"],
            "vanity_name": org.get("vanity_name") or "",
            "linkedin_website": org.get("linkedin_website") or "",
            "sam_website": website,
            "sam_domain": domain,
            "verified_at": stamp,
            "note": "",
            "reused": False,
            "checked": checked,
        }
    if len(matches) > 1:
        return _plain("ambiguous", sam_domain=domain, sam_website=website,
                      checked=checked, matches=[
                          {"organization_urn": org["organization_urn"],
                           "localized_name": org["localized_name"],
                           "linkedin_website": org.get("linkedin_website") or ""}
                          for org in matches])
    if errors:
        return _plain("lookup_failed", sam_domain=domain, sam_website=website,
                      checked=checked, detail="; ".join(errors))
    return _plain("no_match", sam_domain=domain, sam_website=website,
                  checked=checked)


def _hrefs(html):
    return re.findall(r"""href\s*=\s*["']([^"'#]+)""", html or "", re.I)


def _fetch_page(url, fetch):
    if fetch is not None:
        page = fetch(url)
        if not page:
            return None
        if isinstance(page, dict):
            return page
        return None
    if not _url_allowed(url):
        return None
    import requests
    current = url
    for _hop in range(3):
        if not _url_allowed(current):
            return None
        try:
            response = requests.get(
                current, allow_redirects=False, timeout=8, stream=True,
                headers={"User-Agent": "pursuitai-social-mentions",
                         "Accept": "text/html"})
        except Exception:
            return None
        code = getattr(response, "status_code", None)
        if code in (301, 302, 303, 307, 308):
            location = (response.headers or {}).get("Location") or ""
            response.close()
            if not location:
                return None
            current = urljoin(current, location)
            continue
        if code != 200:
            response.close()
            return None
        chunks = []
        size = 0
        try:
            for chunk in response.iter_content(8192):
                if not chunk:
                    continue
                size += len(chunk)
                if size > 400_000:
                    break
                chunks.append(chunk)
        finally:
            response.close()
        html = b"".join(chunks).decode("utf-8", "replace")
        return {"url": current, "html": html}
    return None


def collect_pages(website, fetch=None):
    """The homepage and up to three leadership/about pages on the same domain.

    Failures are empty. A fetch error must not block the award post.
    """
    origin = _origin(website)
    domain = registrable_domain(website)
    if not origin or not domain:
        return []
    pages = []
    seen = set()

    def add(url):
        if len(pages) >= 4 or not url or url in seen:
            return
        if registrable_domain(url) != domain or not _url_allowed(url):
            return
        seen.add(url)
        page = _fetch_page(url, fetch)
        if page and page.get("html"):
            page["url"] = page.get("url") or url
            pages.append(page)

    add(website)
    if not pages:
        add(origin + "/")
    extras = []
    if pages:
        for href in _hrefs(pages[0].get("html") or ""):
            if re.search(r"leader|our-team|/team|about|management|executive",
                         href, re.I):
                extras.append(urljoin(pages[0]["url"], href))
    for path in ("/leadership", "/team", "/about", "/about-us"):
        extras.append(origin + path)
    for url in extras:
        add(url.split("#")[0])
    return pages


def _origin(website):
    parsed = urlparse(website or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.hostname}"


def _resolve_people(people, person_lookup):
    """Fill in person URNs for confirmed profiles. Never raises."""
    if person_lookup is None:
        person_lookup = default_person_lookup
    updated = []
    changed = False
    for person in people or []:
        if not isinstance(person, dict):
            continue
        person = dict(person)
        if (person.get("confirmed") and not person.get("person_urn")
                and person.get("profile_url")):
            try:
                urn = person_lookup(person["profile_url"]) or ""
            except Exception as exc:
                print(f"[mentions] person lookup skipped "
                      f"({type(exc).__name__}: {_public_error(exc)})")
                urn = ""
            if urn and _PERSON_URN.match(urn):
                person["person_urn"] = urn
                person["lookup_status"] = "resolved"
                changed = True
            elif person.get("profile_url"):
                person["lookup_status"] = person.get("lookup_status") or "unavailable"
        updated.append(person)
    return updated, changed


def review_text(company, executives, people, uei=""):
    """What the approval queue shows above the caption."""
    company = company or {}
    lines = []
    status = company.get("status")
    if status == "matched":
        lines.append(
            f"Company tag: {company.get('localized_name') or ''} "
            f"({company.get('organization_urn') or ''})")
        if company.get("source") == "override":
            lines.append(
                f"Owner override on {company.get('verified_at') or ''}: "
                f"mention text is the stored LinkedIn name "
                f"({company.get('organization_urn')}). "
                f"Website on that Page: "
                f"{company.get('linkedin_website') or 'not recorded'}."
                + (f" {company['note']}" if company.get("note") else ""))
        else:
            how = ("reused stored mapping" if company.get("reused")
                   else "looked up this run")
            lines.append(
                f"Verified: SAM domain {company.get('sam_domain') or ''} "
                f"matches LinkedIn website "
                f"{company.get('linkedin_website') or ''} "
                f"(vanity {company.get('vanity_name') or ''}, "
                f"source {company.get('source') or 'verified'}, {how}, "
                f"{company.get('verified_at') or ''}).")
    elif status == "blocked":
        lines.append("Company tag: blocked. The post uses the plain legal name."
                     + (f" {company.get('note')}" if company.get("note") else ""))
    else:
        reason = company.get("reason") or "no_match"
        lines.append(
            f"Company tag: none ({reason}). The post uses the plain legal name.")
        if reason == "ambiguous":
            bits = []
            for org in company.get("matches") or []:
                bits.append(
                    f"{org.get('organization_urn')} ({org.get('localized_name')})")
            if bits:
                lines.append(
                    "More than one LinkedIn page matches SAM domain "
                    f"{company.get('sam_domain')}: " + "; ".join(bits) + ".")
        elif company.get("sam_domain"):
            lines.append(f"SAM domain {company.get('sam_domain')}.")
        checked = company.get("checked") or []
        if checked:
            lines.append("Checked vanities: " + ", ".join(checked) + ".")
        if reason == "no_website":
            lines.append(
                "No SAM website on the award. The feed has no recipient_website "
                "and SAM_API_KEY is unset or returned nothing.")
        elif reason == "no_token":
            lines.append("LINKEDIN_ACCESS_TOKEN was not available, so no page was looked up.")
    confirmed = [person for person in people or []
                 if isinstance(person, dict) and person.get("confirmed")]
    if confirmed:
        lines.append("Confirmed people:")
        for person in captions._rank_people(confirmed):
            urn = person.get("person_urn") or ""
            how = urn if urn else "plain name (person URN unavailable)"
            lines.append(f"- {person.get('name')} — {how}")
    pending = []
    confirmed_names = {(person.get("name") or "").casefold() for person in confirmed}
    for person in executives or []:
        if (person.get("name") or "").casefold() in confirmed_names:
            continue
        pending.append(person)
    if pending:
        lines.append("Suggested, not tagged until you confirm:")
        for person in pending:
            where = person.get("source_url") or ""
            hint = ""
            if person.get("profile_url"):
                hint = f" profile {person['profile_url']}"
            lines.append(
                f"- {person.get('name')}, {person.get('title') or person.get('role')}"
                f" ({where}){hint}")
    if uei:
        lines.append(
            "Confirm a person on this branch before approving the run: "
            f"python engine/mentions.py confirm-person --uei {uei} "
            "--name \"NAME\" --title \"TITLE\" "
            "--profile https://www.linkedin.com/in/VANITY")
    return "\n".join(lines)


def _public_company(company):
    """The decision, without anything the queue does not need to keep."""
    keep = ("status", "reason", "source", "organization_urn", "localized_name",
            "vanity_name", "linkedin_website", "sam_website", "sam_domain",
            "verified_at", "note", "reused", "checked", "detail")
    out = {key: company.get(key) for key in keep if key in company}
    if company.get("matches"):
        out["matches"] = company["matches"]
    return out


def decorate(award, text, root, *, dry_run=False, lookup=None,
             person_lookup=None, pages=None, sam_get=None, fetch=None,
             now=None):
    """Caption plus the approval note. Never raises. Disk writes only when
    a new verified mapping or a resolved person URN should be kept, and
    never on a dry run.
    """
    try:
        return _decorate(award, text, root, dry_run=dry_run, lookup=lookup,
                         person_lookup=person_lookup, pages=pages,
                         sam_get=sam_get, fetch=fetch, now=now)
    except Exception as exc:
        print(f"[mentions] skipped ({type(exc).__name__}: {_public_error(exc)})")
        return plain_result(text, "mention step failed")


def plain_result(text, reason):
    company = _plain(reason)
    return {
        "text": text or "",
        "plain": text or "",
        "review": review_text(company, [], [], ""),
        "company": _public_company(company),
        "people": [],
        "executives": [],
    }


def _decorate(award, text, root, *, dry_run, lookup, person_lookup, pages,
              sam_get, fetch, now):
    store = load(root)
    before = _snapshot(store)
    uei = uei_key((award or {}).get("recipient_uei"))
    stored = (store.get("organizations") or {}).get(uei) if uei else None
    website = ""
    company = None
    if stored and _is_blocked(stored):
        company = _from_stored(stored, "blocked")
        website = stored.get("sam_website") or ""
    elif stored and _usable_match(stored) and stored.get("source") in (
            "override", "verified"):
        company = _from_stored(stored, "matched")
        website = stored.get("sam_website") or ""
    else:
        website, _source = website_for(award, sam_get=sam_get)
    # One fetch of the company site, shared by vanity discovery and the
    # executive suggestions. Pass pages=[] to skip it.
    if pages is None:
        pages = collect_pages(website, fetch=fetch) if website else []
    if company is None:
        company = match_organization(
            award, lookup=lookup, pages=pages, website=website,
            sam_get=sam_get, fetch=fetch, now=now)
        if (company.get("status") == "matched"
                and company.get("source") == "verified"
                and uei and not store.get("_corrupt")):
            store.setdefault("organizations", {})[uei] = _persist_row(
                company, award)
    executives = suggest_executives(pages, (award or {}).get("recipient_name") or "")
    people = []
    if uei:
        raw = (store.get("people") or {}).get(uei) or []
        people = [dict(person) for person in raw if isinstance(person, dict)]
        people, changed = _resolve_people(people, person_lookup)
        if changed and not store.get("_corrupt"):
            store.setdefault("people", {})[uei] = people
    if not dry_run and _snapshot(store) != before:
        save(root, store)
    rendered = captions.with_award_mentions(text or "", company, people, award or {})
    review = review_text(company, executives, people, uei)
    print(f"[mentions] {(award or {}).get('recipient_name') or uei or 'award'}: "
          f"{company.get('status')} {company.get('reason') or ''}".rstrip())
    return {
        "text": rendered,
        "plain": text or "",
        "review": review,
        "company": _public_company(company),
        "people": people,
        "executives": executives,
    }


def company_at_publish(post, store):
    """Store wins over the proposal: a block or a fix committed while the
    run waited on approval is what gets posted."""
    uei = uei_key((post or {}).get("recipient_uei"))
    prepared = ((post or {}).get("mention") or {}).get("company") or {}
    row = (store.get("organizations") or {}).get(uei) if uei else None
    if row and _is_blocked(row):
        return _from_stored(row, "blocked")
    if row and _usable_match(row):
        return _from_stored(row, "matched")
    if prepared.get("status") == "matched" and _usable_match(prepared):
        return prepared
    return prepared or _plain("no_match")


def finalize(post, root, *, person_lookup=None, now=None):
    """The commentary to publish, after the latest store is applied.

    Does not raise. A new person URN learned here is saved so the next
    award for this company does not look it up again.
    """
    try:
        return _finalize(post, root, person_lookup=person_lookup, now=now)
    except Exception as exc:
        print(f"[mentions] publish mentions skipped ({type(exc).__name__}: {_public_error(exc)})")
        plain = (post or {}).get("text_linkedin_plain") or captions.caption_without_mention_lead(
            (post or {}).get("text_linkedin") or "")
        return {"text": plain, "plain": plain}


def _finalize(post, root, *, person_lookup, now):
    plain = post.get("text_linkedin_plain") or captions.caption_without_mention_lead(
        post.get("text_linkedin") or "")
    store = load(root)
    before = _snapshot(store)
    uei = uei_key(post.get("recipient_uei"))
    company = company_at_publish(post, store)
    people = []
    if uei:
        raw = (store.get("people") or {}).get(uei) or []
        people = [dict(person) for person in raw if isinstance(person, dict)]
        people, changed = _resolve_people(people, person_lookup)
        if changed and not store.get("_corrupt"):
            store.setdefault("people", {})[uei] = people
            if _snapshot(store) != before:
                save(root, store)
    text = captions.with_award_mentions(plain, company, people, post)
    return {"text": text, "plain": plain, "company": company, "people": people}


def tagging_failure(exc):
    """True when a failed publish should be retried without mentions.

    A post LinkedIn already accepted is not retried: the id header was
    missing, or the failure is an upload, and a second call would
    duplicate the post or repeat a failure that is not the mention.
    """
    text = str(exc or "").lower()
    if "x-restli-id" in text:
        return False
    if any(word in text for word in ("image", "video", "upload", "processing")):
        return False
    return True


def _require_uei(value):
    key = uei_key(value)
    if not key:
        raise SystemExit("UEI must be 12 letters or digits")
    return key


def _require_store(root):
    store = load(root)
    if store.get("_corrupt"):
        raise SystemExit(f"{store_path(root)} is unreadable; not changing it")
    return store


def set_override(root, uei, *, urn, name, note="", vanity="", website="",
                 now=None):
    key = _require_uei(uei)
    if not _ORG_URN.match(urn or ""):
        raise SystemExit("urn must look like urn:li:organization:123")
    name = (name or "").strip()
    if not name or any(ch in name for ch in "[]\n\r"):
        raise SystemExit("name must be the Page's LinkedIn name, without brackets")
    store = _require_store(root)
    store.setdefault("organizations", {})[key] = {
        "uei": key,
        "organization_urn": urn,
        "localized_name": name,
        "vanity_name": vanity or "",
        "linkedin_website": website or "",
        "sam_website": "",
        "sam_domain": registrable_domain(website),
        "source": "override",
        "verified_at": _now(now),
        "blocked": False,
        "note": note or "",
    }
    save(root, store)
    print(f"[mentions] override {key} → {name} ({urn})")
    return store


def set_override_from_vanity(root, uei, company_url, *, lookup=None, note="",
                             now=None):
    match = _LI_VANITY.search(company_url or "")
    if not match:
        raise SystemExit("company URL must be a linkedin.com/company, /school, or /showcase link")
    vanity = match.group(1).lower().strip("-")
    lookup = lookup or default_lookup
    try:
        found = lookup(vanity) or []
    except LookupFailed as exc:
        raise SystemExit(f"LinkedIn lookup failed ({exc}). "
                         f"Pass --urn and --name instead.") from exc
    ids = {org["id"] for org in found}
    if len(ids) != 1:
        raise SystemExit(
            f"vanity {vanity} returned {len(ids)} organizations; not storing a guess")
    org = found[0]
    return set_override(
        root, uei, urn=org["organization_urn"], name=org["localized_name"],
        note=note, vanity=org.get("vanity_name") or vanity,
        website=org.get("linkedin_website") or "", now=now)


def block_organization(root, uei, *, note="", now=None):
    key = _require_uei(uei)
    store = _require_store(root)
    current = (store.get("organizations") or {}).get(key) or {}
    if not isinstance(current, dict):
        current = {}
    current = dict(current)
    current.update({
        "uei": key,
        "source": "blocked",
        "blocked": True,
        "verified_at": _now(now),
        "note": note or current.get("note") or "",
    })
    store.setdefault("organizations", {})[key] = current
    save(root, store)
    print(f"[mentions] blocked {key}")
    return store


def unblock_organization(root, uei):
    key = _require_uei(uei)
    store = _require_store(root)
    (store.get("organizations") or {}).pop(key, None)
    save(root, store)
    print(f"[mentions] removed the organization mapping for {key}")
    return store


def confirm_person(root, uei, *, name, title, profile="", urn="", role="",
                   now=None, person_lookup=None):
    key = _require_uei(uei)
    name = re.sub(r"\s+", " ", (name or "").strip())
    if len(name) < 3 or any(ch in name for ch in "[]\n\r"):
        raise SystemExit("name is required")
    urn = (urn or "").strip()
    if urn and not _PERSON_URN.match(urn):
        raise SystemExit("person urn must look like urn:li:person:abc")
    profile_url = ""
    if profile:
        profile_url = canonical_profile(profile)
        if not profile_url:
            raise SystemExit("profile must be a https://www.linkedin.com/in/… URL")
    if not urn and not profile_url:
        raise SystemExit("pass --profile or --urn")
    lookup_status = "supplied" if urn else ""
    if not urn and profile_url:
        lookup = person_lookup or default_person_lookup
        try:
            resolved = lookup(profile_url) or ""
        except Exception as exc:
            print(f"[mentions] person lookup skipped ({type(exc).__name__}: {_public_error(exc)})")
            resolved = ""
        if resolved and _PERSON_URN.match(resolved):
            urn = resolved
            lookup_status = "resolved"
        else:
            lookup_status = "unavailable"
            print("[mentions] no person URN — this confirmed name posts as plain text")
    if role not in ("executive", "growth", "other"):
        role = _role_from_title(title)
    store = _require_store(root)
    people = [dict(person) for person in (store.get("people") or {}).get(key) or []
              if isinstance(person, dict)]
    row = {
        "name": name,
        "linkedin_name": name,
        "title": (title or "").strip(),
        "role": role,
        "profile_url": profile_url,
        "person_urn": urn,
        "confirmed": True,
        "confirmed_at": _now(now),
        "source": "owner",
        "lookup_status": lookup_status,
    }
    replaced = False
    for index, person in enumerate(people):
        if (person.get("name") or "").casefold() == name.casefold():
            people[index] = row
            replaced = True
            break
    if not replaced:
        people.append(row)
    store.setdefault("people", {})[key] = people
    save(root, store)
    print(f"[mentions] confirmed {name} for {key}"
          + (f" ({urn})" if urn else " (plain name)"))
    return store


def _role_from_title(title):
    text = title or ""
    if _CEO.search(text) or (_PRESIDENT.search(text) and not _VICE.search(text)):
        return "executive"
    if _GROWTH.search(text):
        return "growth"
    return "other"


def forget_person(root, uei, name):
    key = _require_uei(uei)
    wanted = re.sub(r"\s+", " ", (name or "").strip()).casefold()
    if not wanted:
        raise SystemExit("name is required")
    store = _require_store(root)
    people = [person for person in (store.get("people") or {}).get(key) or []
              if isinstance(person, dict)
              and (person.get("name") or "").casefold() != wanted]
    store.setdefault("people", {})[key] = people
    save(root, store)
    print(f"[mentions] forgot {name} for {key}")
    return store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=ROOT)
    sub = parser.add_subparsers(dest="cmd", required=True)

    set_org = sub.add_parser("set-org", help="store an owner override")
    set_org.add_argument("--uei", required=True)
    set_org.add_argument("--urn")
    set_org.add_argument("--name")
    set_org.add_argument("--company-url")
    set_org.add_argument("--note", default="")

    block = sub.add_parser("block-org", help="never tag this UEI")
    block.add_argument("--uei", required=True)
    block.add_argument("--note", default="")

    unblock = sub.add_parser("unblock-org", help="drop the stored mapping")
    unblock.add_argument("--uei", required=True)

    confirm = sub.add_parser("confirm-person", help="tag this person on later posts")
    confirm.add_argument("--uei", required=True)
    confirm.add_argument("--name", required=True)
    confirm.add_argument("--title", default="")
    confirm.add_argument("--profile")
    confirm.add_argument("--urn")
    confirm.add_argument("--role", choices=("executive", "growth", "other"))

    forget = sub.add_parser("forget-person", help="stop tagging this person")
    forget.add_argument("--uei", required=True)
    forget.add_argument("--name", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "set-org":
        if args.company_url:
            set_override_from_vanity(args.root, args.uei, args.company_url,
                                     note=args.note)
        elif args.urn and args.name:
            set_override(args.root, args.uei, urn=args.urn, name=args.name,
                         note=args.note)
        else:
            raise SystemExit("pass --company-url, or both --urn and --name")
        return 0
    if args.cmd == "block-org":
        block_organization(args.root, args.uei, note=args.note)
        return 0
    if args.cmd == "unblock-org":
        unblock_organization(args.root, args.uei)
        return 0
    if args.cmd == "confirm-person":
        confirm_person(args.root, args.uei, name=args.name, title=args.title,
                       profile=args.profile or "", urn=args.urn or "",
                       role=args.role or "")
        return 0
    if args.cmd == "forget-person":
        forget_person(args.root, args.uei, args.name)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
