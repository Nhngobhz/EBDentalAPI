"""
Phase 0 of the SAP Business One integration: ask a company database what it holds,
and write the answer down.

The materials half of the website does not exist yet - SAP is where its catalogue
comes from. So this report is not a reconciliation of two existing datasets; it IS
the specification for what the materials storefront will have to display. How many
items, what the category and brand trees look like, which price list carries the
number a customer should see. Everything after this phase is designed against it.

SAFE TO RUN AGAINST ANY COMPANY DATABASE, INCLUDING PRODUCTION. Every probe is a
GET. The only POSTs are /Login, /Logout, and CompanyService_GetCompanyInfo - the
last is an OData function import that reads company configuration and writes
nothing (Service Layer just exposes it as a POST). There is deliberately no code
path here that can create, update or delete business data.

Usage:
    python -m scripts.sap_discover --company-db EBDS_PRO_DB_LIVE --username manager
    (run from the project root with the virtualenv activated; you will be prompted
     for the password unless --password or SAP_PASSWORD is set)

Connect by HOSTNAME, not IP. https://192.168.0.113:50000 returns a bare Apache 403,
while https://QPLUS365SERVER:50000 reaches Service Layer properly - and the SAP
certificate's SAN covers the hostname, not that address, so IP access could never
pass verification anyway.

Every probe is independent and fault-tolerant: Service Layer's entity set varies by
version and patch level, so one 404 records itself in the report and the run
continues rather than losing the other twenty answers.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Service Layer pages at 20 rows by default and says nothing about it. Asking for a
# bigger page is the difference between reporting a catalogue and reporting its
# first screenful - the easiest way to be confidently wrong here.
PAGE_SIZE = 200

# A cap so a huge item master cannot turn discovery into a ten-minute crawl. Counts
# in the report stay exact regardless: they come from $inlinecount, not from len()
# of what was actually fetched.
MAX_ROWS = 5000

# B1 gives every item 64 built-in yes/no "properties". They are the cheapest way to
# mark which items belong on the website - no UDF, no metadata change, no Service
# Layer restart, and they can be set in bulk from the item master list. Discovering
# which ones are already in use is how we find out whether such a marker exists.
QRY_GROUPS = [f"QryGroup{i}" for i in range(1, 65)]


class ServiceLayerError(Exception):
    """A Service Layer call that failed in a way worth reporting rather than raising
    through - the caller records it against the probe and carries on."""


class ServiceLayer:
    """Minimal Service Layer session.

    Deliberately NOT the client the app will eventually use: this one is standalone
    so it can probe a company database before any integration config exists, and so
    it can be pointed at a database that is not the one the app is configured for.
    """

    def __init__(self, base_url: str, company_db: str, username: str, password: str, verify: bool):
        self.base_url = base_url.rstrip("/")
        self.company_db = company_db
        self._username = username
        self._password = password
        self.version: str | None = None
        self._client = httpx.Client(verify=verify, timeout=60)

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc_info):
        self.logout()
        return False

    def login(self) -> None:
        resp = self._client.post(
            f"{self.base_url}/Login",
            json={
                "CompanyDB": self.company_db,
                "UserName": self._username,
                "Password": self._password,
            },
        )
        if resp.status_code != 200:
            raise ServiceLayerError(_explain_login_failure(resp))
        # The login response carries the B1 version - the cheapest way to get it,
        # and it needs no extra authorisation.
        self.version = resp.json().get("Version")

    def logout(self) -> None:
        """Best effort. A leaked session expires on its own, but it holds a licence
        slot until it does, so returning it promptly is good manners."""
        try:
            self._client.post(f"{self.base_url}/Logout")
        except Exception:
            pass
        finally:
            self._client.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        resp = self._client.get(
            f"{self.base_url}/{path.lstrip('/')}",
            params=params or {},
            headers={"Prefer": f"odata.maxpagesize={PAGE_SIZE}"},
        )
        if resp.status_code != 200:
            raise ServiceLayerError(_explain_error(resp))
        return resp.json()

    def post_function(self, path: str) -> dict:
        """For OData function imports that read configuration but are exposed as
        POST (CompanyService_GetCompanyInfo). Sends an empty body; writes nothing."""
        resp = self._client.post(f"{self.base_url}/{path.lstrip('/')}", json={})
        if resp.status_code != 200:
            raise ServiceLayerError(_explain_error(resp))
        return resp.json()

    def count(self, path: str, params: dict | None = None) -> int | None:
        """Exact row count via $inlinecount, independent of what gets fetched.
        None when the entity does not support it, rather than a wrong number."""
        merged = dict(params or {})
        merged["$inlinecount"] = "allpages"
        merged["$top"] = "1"
        try:
            body = self.get(path, merged)
        except ServiceLayerError:
            return None
        raw = body.get("odata.count", body.get("@odata.count"))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def get_all(self, path: str, params: dict | None = None, max_rows: int = MAX_ROWS) -> list[dict]:
        """Follow odata.nextLink until the rows run out or max_rows is reached."""
        rows: list[dict] = []
        body = self.get(path, params)
        while True:
            rows.extend(body.get("value", []))
            next_link = body.get("odata.nextLink") or body.get("@odata.nextLink")
            if not next_link or len(rows) >= max_rows:
                break
            # nextLink is relative to the service root and carries its own query
            # string, so it is passed through untouched.
            body = self.get(next_link)
        return rows[:max_rows]


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------

def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        if not text:
            return "<empty response>"
        # Apache's error pages are HTML, and dumping the markup buries the one line
        # that matters. This is the most common first-run failure, so it gets the
        # readable treatment: the <title> is the whole message.
        if text.lstrip().lower().startswith(("<!doctype", "<html")):
            match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
            return f"{match.group(1).strip()} (HTML error page)" if match else "HTML error page"
        return text[:300]
    err = body.get("error") or {}
    message = err.get("message")
    if isinstance(message, dict):
        message = message.get("value")
    code = err.get("code")
    return f"[{code}] {message}" if code is not None else str(body)[:300]


def _explain_error(resp: httpx.Response) -> str:
    return f"HTTP {resp.status_code}: {_error_text(resp)}"


def _explain_login_failure(resp: httpx.Response) -> str:
    """Login failures are the ones most worth explaining in English - nearly every
    first run trips over one of these three."""
    detail = _error_text(resp)
    lowered = detail.lower()
    if "-311" in detail or "bound database" in lowered:
        hint = (
            " -- Service Layer could not resolve that CompanyDB. Check the spelling, "
            "and that the company is registered in the SLD (port 40000)."
        )
    elif resp.status_code == 401:
        hint = " -- wrong username/password, or the account is locked in SAP."
    elif resp.status_code == 403:
        hint = (
            " -- Apache refused this before Service Layer saw it. That is what "
            "connecting by IP instead of hostname looks like; try the server name."
        )
    else:
        hint = ""
    return f"Login failed (HTTP {resp.status_code}): {detail}{hint}"


def _probe(results: dict, name: str, fn) -> None:
    """Run one probe, recording either its value or why it failed. A missing entity
    set is information, not a reason to lose the other twenty answers."""
    try:
        results[name] = {"ok": True, "data": fn()}
        print(f"  ok    {name}", flush=True)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ServiceLayerError) else f"{type(exc).__name__}: {exc}"
        results[name] = {"ok": False, "error": detail}
        print(f"  FAIL  {name}: {detail}", flush=True)


# ---------------------------------------------------------------------------
# The probes
# ---------------------------------------------------------------------------

def collect(sl: ServiceLayer) -> dict:
    results: dict = {}

    _probe(results, "company_info", lambda: sl.post_function("CompanyService_GetCompanyInfo"))
    _probe(results, "currencies", lambda: sl.get_all("Currencies"))

    _probe(results, "item_count", lambda: sl.count("Items"))
    _probe(
        results,
        "items",
        lambda: sl.get_all(
            "Items",
            {
                "$select": ",".join(
                    [
                        "ItemCode",
                        "ItemName",
                        "ItemsGroupCode",
                        "Manufacturer",
                        "InventoryUOM",
                        "SalesUnit",
                        "Valid",
                        "Frozen",
                        "ItemType",
                        "InventoryItem",
                        "SalesItem",
                        "UpdateDate",
                    ]
                    + QRY_GROUPS
                )
            },
        ),
    )
    # A couple of complete rows, no $select, so the report shows every field this
    # install actually exposes - including UDFs, which is how we learn their names.
    _probe(results, "item_sample_full", lambda: sl.get_all("Items", {"$top": "3"}, max_rows=3))

    _probe(results, "item_groups", lambda: sl.get_all("ItemGroups"))
    _probe(results, "manufacturers", lambda: sl.get_all("Manufacturers"))
    _probe(results, "price_lists", lambda: sl.get_all("PriceLists"))
    _probe(results, "uoms", lambda: sl.get_all("UnitOfMeasurements"))
    _probe(results, "uom_groups", lambda: sl.get_all("UnitOfMeasurementGroups"))
    _probe(results, "warehouses", lambda: sl.get_all("Warehouses"))
    _probe(results, "tax_codes", lambda: sl.get_all("SalesTaxCodes"))

    _probe(results, "business_partner_count", lambda: sl.count("BusinessPartners"))
    _probe(
        results,
        "customer_count",
        lambda: sl.count("BusinessPartners", {"$filter": "CardType eq 'cCustomer'"}),
    )

    # UDFs on the tables this integration will touch. Their absence is the expected
    # result today - the idempotency field for order posting has to be created.
    tables = ["OITM", "ORDR", "OQUT", "OINV", "OCRD"]
    udf_filter = " or ".join(f"TableName eq '{t}'" for t in tables)
    _probe(results, "user_fields", lambda: sl.get_all("UserFieldsMD", {"$filter": udf_filter}))

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _rows(results: dict, name: str) -> list[dict]:
    entry = results.get(name) or {}
    return entry.get("data") or [] if entry.get("ok") else []


def _value(results: dict, name: str):
    entry = results.get(name) or {}
    return entry.get("data") if entry.get("ok") else None


def _table(headers: list[str], rows: list[list], limit: int = 60) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows[:limit]:
        out.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    if len(rows) > limit:
        out.append(f"| _...{len(rows) - limit} more_ |" + " |" * (len(headers) - 1))
    return out


def build_report(results: dict, meta: dict) -> str:
    L: list[str] = []
    L.append("# SAP Business One - discovery report")
    L.append("")
    L.append(f"- Generated: {meta['generated_at']}")
    L.append(f"- Server: `{meta['base_url']}`")
    L.append(f"- Company DB: `{meta['company_db']}`")
    L.append(f"- B1 version (from Login): `{meta.get('version') or 'unknown'}`")
    L.append("")

    failed = [n for n, e in results.items() if not e.get("ok")]
    if failed:
        L.append(f"> {len(failed)} probe(s) failed: {', '.join(failed)}. See the JSON for details.")
        L.append("")

    info = _value(results, "company_info") or {}
    if info:
        L.append("## Company")
        L.append("")
        for key in ("CompanyName", "LocalCurrency", "SystemCurrency", "Version", "DBServerType"):
            if info.get(key) is not None:
                L.append(f"- **{key}**: {info[key]}")
        local, system = info.get("LocalCurrency"), info.get("SystemCurrency")
        if local and local != "USD":
            L.append("")
            L.append(
                f"> Local currency is `{local}`, not USD. Every price on the website is in "
                "dollars, so documents will post in a foreign currency - and SAP **rejects** "
                "a document dated on a day with no exchange rate loaded. Confirm who "
                "maintains the daily rate before order posting goes live."
            )
        L.append("")

    # --- Items ---
    items = _rows(results, "items")
    count = _value(results, "item_count")
    L.append("## Items")
    L.append("")
    L.append(f"- Exact count: **{count if count is not None else 'unknown'}**")
    L.append(f"- Rows fetched for analysis: {len(items)}")
    if count and len(items) < count:
        L.append(f"- (capped at MAX_ROWS={MAX_ROWS}; distributions below cover the fetched rows)")
    if items:
        sellable = sum(1 for i in items if i.get("Valid") == "tYES" and i.get("Frozen") != "tYES")
        L.append(f"- Valid and not frozen: **{sellable}** of {len(items)} fetched")
        no_uom = sum(1 for i in items if not (i.get("InventoryUOM") or i.get("SalesUnit")))
        L.append(f"- Missing any unit of measure: {no_uom}")
        no_mfr = sum(1 for i in items if not i.get("Manufacturer") or i.get("Manufacturer") == -1)
        L.append(
            f"- Missing a manufacturer: {no_mfr} "
            "(these need the fallback Brand, since `products.brand_id` is NOT NULL)"
        )
    L.append("")

    # --- Item properties: the website-publish marker ---
    if items:
        L.append("### Item properties (QryGroup1..64)")
        L.append("")
        L.append(
            "B1's built-in per-item flags. If one of these already marks the web-facing "
            "items, it is the cheapest possible selection rule - no UDF, no metadata "
            "change, no Service Layer restart."
        )
        L.append("")
        used = [(g, sum(1 for i in items if i.get(g) == "tYES")) for g in QRY_GROUPS]
        used = [(g, n) for g, n in used if n]
        if used:
            L.extend(_table(["Property", "Items set"], [[g, n] for g, n in sorted(used, key=lambda x: -x[1])]))
        else:
            L.append("_None in use._ A property will need to be chosen and populated, or the "
                     "selection rule based on item group instead.")
        L.append("")

    # --- Groups / manufacturers ---
    groups = _rows(results, "item_groups")
    if groups:
        by_group = Counter(i.get("ItemsGroupCode") for i in items)
        L.append("### Item groups -> web Category")
        L.append("")
        L.extend(
            _table(
                ["Code", "Group name", "Items"],
                sorted(
                    ([g.get("Number"), g.get("GroupName"), by_group.get(g.get("Number"), 0)] for g in groups),
                    key=lambda r: -r[2],
                ),
            )
        )
        L.append("")

    mfrs = _rows(results, "manufacturers")
    if mfrs:
        by_mfr = Counter(i.get("Manufacturer") for i in items)
        L.append("### Manufacturers -> web Brand")
        L.append("")
        L.extend(
            _table(
                ["Code", "Manufacturer", "Items"],
                sorted(
                    ([m.get("Code"), m.get("ManufacturerName"), by_mfr.get(m.get("Code"), 0)] for m in mfrs),
                    key=lambda r: -r[2],
                ),
            )
        )
        L.append("")

    # --- Price lists ---
    price_lists = _rows(results, "price_lists")
    if price_lists:
        L.append("## Price lists")
        L.append("")
        L.append(
            "Which of these carries the number a customer should see is a business "
            "decision, not a discoverable fact - but the row counts below narrow it down."
        )
        L.append("")
        L.extend(
            _table(
                ["No.", "Name", "Base list", "Factor"],
                [
                    [p.get("PriceListNo"), p.get("PriceListName"), p.get("BasePriceList"), p.get("Factor")]
                    for p in price_lists
                ],
            )
        )
        L.append("")

    # --- Reference data ---
    for key, title, cols in (
        ("warehouses", "Warehouses", [("WarehouseCode", "Code"), ("WarehouseName", "Name")]),
        ("tax_codes", "Tax codes", [("Code", "Code"), ("Name", "Name"), ("Rate", "Rate")]),
        ("uom_groups", "UoM groups", [("Code", "Code"), ("Name", "Name")]),
        ("currencies", "Currencies", [("Code", "Code"), ("Name", "Name")]),
    ):
        rows = _rows(results, key)
        if rows:
            L.append(f"## {title}")
            L.append("")
            L.extend(_table([c[1] for c in cols], [[r.get(c[0]) for c in cols] for r in rows]))
            L.append("")

    # --- UDFs ---
    udfs = _rows(results, "user_fields")
    L.append("## User-defined fields")
    L.append("")
    if udfs:
        L.extend(
            _table(
                ["Table", "Name", "Description", "Type"],
                [[u.get("TableName"), u.get("Name"), u.get("Description"), u.get("Type")] for u in udfs],
            )
        )
    else:
        L.append("_None on the tables this integration touches._")
    L.append("")
    L.append(
        "Order posting will need an idempotency field (e.g. `U_EBWebOrder` on ORDR/OQUT/"
        "OINV) so a retry cannot create a duplicate document. Creating it needs "
        "User-Defined Fields Management **plus a Service Layer restart** to refresh "
        "metadata - schedule that rather than discover it."
    )
    L.append("")

    bp = _value(results, "business_partner_count")
    cust = _value(results, "customer_count")
    if bp is not None or cust is not None:
        L.append("## Business partners")
        L.append("")
        L.append(f"- Total: {bp if bp is not None else 'unknown'}")
        L.append(f"- Customers: {cust if cust is not None else 'unknown'}")
        L.append("")

    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------

def main() -> None:
    # Load store-api/.env first, so SAP_USERNAME / SAP_PASSWORD can live there
    # alongside every other secret this project holds, rather than being typed on a
    # command line (where they land in shell history). Must happen before the parser
    # is built, since the argument defaults read os.getenv at construction time.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Read-only discovery of a SAP Business One company database."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("SAP_BASE_URL", "https://QPLUS365SERVER:50000/b1s/v1"),
        help="Service Layer base URL. Use the HOSTNAME, not an IP.",
    )
    parser.add_argument("--company-db", default=os.getenv("SAP_COMPANY_DB"))
    parser.add_argument("--username", default=os.getenv("SAP_USERNAME"))
    parser.add_argument("--password", default=os.getenv("SAP_PASSWORD"))
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip TLS verification. SAP ships a self-signed certificate, so this is "
             "needed until it is trusted locally - and only ever safe on a LAN you own.",
    )
    parser.add_argument("--out", default="sap_discovery", help="Output directory")
    args = parser.parse_args()

    company_db = args.company_db or input("Company DB: ").strip()
    username = args.username or input("SAP username: ").strip()
    password = args.password or getpass.getpass("SAP password: ")
    if not (company_db and username and password):
        print("Company DB, username and password are all required.", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to {args.base_url} (company {company_db})...", flush=True)
    try:
        sl = ServiceLayer(args.base_url, company_db, username, password, verify=not args.no_verify)
        sl.login()
    except ServiceLayerError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except httpx.HTTPError as exc:
        print(f"Could not reach Service Layer: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "If this is a certificate error, retry with --no-verify, or trust the SAP "
            "certificate locally. If it is a connection error, check you used the "
            "hostname rather than an IP address.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Connected. B1 version {sl.version}. Running probes:", flush=True)
    try:
        results = collect(sl)
    finally:
        sl.logout()

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "company_db": company_db,
        "version": sl.version,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # No credentials are recorded anywhere in these files - only what was read.
    (out_dir / "discovery.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2, default=str), encoding="utf-8"
    )
    report_path = out_dir / "report.md"
    report_path.write_text(build_report(results, meta), encoding="utf-8")

    failed = sum(1 for e in results.values() if not e.get("ok"))
    print(f"\nWrote {report_path} and {out_dir / 'discovery.json'}")
    if failed:
        print(f"{failed} probe(s) failed - the report names them.")


if __name__ == "__main__":
    main()
