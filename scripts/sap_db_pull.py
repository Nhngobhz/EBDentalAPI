"""
Read-only extract of a catalogue - materials or spare parts - from the SAP
Business One company database, straight over SQL.

Why SQL and not Service Layer (scripts/sap_discover.py): Service Layer needs a B1
user, burns a licence slot for the length of the run, and pages 6,800 items twenty
at a time. The company database sits on the same machine the app is deployed to,
and every field this extract wants is one join away. Nothing here writes.

READ-ONLY BY CONSTRUCTION. The only statement issued is the SELECT below. B1
maintains its own document numbering and audit tables, so writing to a company
database from outside the client is how documents get corrupted - this script must
never grow an INSERT/UPDATE, and the sync that consumes its output writes only to
our own Postgres.

Transport
---------
SSH is how this reaches the database from a developer machine, not a property of
the data - see TRANSPORTS below for the three ways in and when each applies. The
short version:

  - In production nothing crosses a network at all: store-api is deployed on the
    same server as SQL Server, so `--transport local` talks to localhost.
  - From a developer machine, Windows authentication does not work (the two boxes
    share no domain, and SQL Server answers "the login is from an untrusted
    domain"). With no SQL login in place, SSH is what is left - hence the default.
  - Given a read-only SQL login in SAP_DB_DSN, `--transport odbc` connects straight
    over the LAN from anywhere, and no SSH is involved. The server is already in
    mixed mode, so that needs no change to SQL Server itself.

Usage:
    python -m scripts.sap_db_pull                          # materials (101 + 106)
    python -m scripts.sap_db_pull --catalogue spare-parts  # group 103
    python -m scripts.sap_db_pull --groups all             # ad-hoc, -> items.json
    python -m scripts.sap_db_pull --transport local        # on the server itself
"""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# The item groups (OITB.ItmsGrpCod) as they stand in EBDS_PRO_DB_LIVE. Hard-coded
# only as a default and for the --groups help text; the query reads whatever it is
# given, so a new group in SAP needs no change here.
GROUP_NAMES = {
    100: "Office supply",
    101: "Materials",
    102: "Services",
    103: "Spare Part",
    104: "Equipment",
    106: "Lab Material",
}
# The catalogues this integration pulls, each a set of SAP item groups and the
# `products.section` they land in. Named here rather than in sap_sync.py because
# this is the module that already knows what an item group is, and both scripts
# have to agree: the sync's delisting hides everything in a section that is absent
# from the extract, so a pull and a sync that disagreed about which groups make up
# a catalogue would hide the difference between them.
#
# 100 Office supply / 102 Services / 104 Equipment are deliberately absent. Office
# supply and Services are not things this company sells online (17 of the Services
# rows have no price at all), and Equipment overlaps the hand-curated machinery
# catalogue - importing it would create a second, SAP-shaped copy of products that
# already exist here with photographs and descriptions.
CATALOGUES = {
    "materials": {
        "groups": [101, 106],
        "section": "materials",
        "label": "Materials",
    },
    "spare-parts": {
        "groups": [103],
        "section": "spare_parts",
        "label": "Spare parts",
    },
}

# OPLN.ListNum of "Normal Sale Price". It is the only price list in this company
# database with any rows in it - the other nine exist but are empty, so picking a
# different one would silently produce a catalogue priced at nothing.
PRICE_LIST = 1

SSH_HOST = os.getenv("SAP_SSH_HOST", "ebserver")
COMPANY_DB = os.getenv("SAP_COMPANY_DB", "EBDS_PRO_DB_LIVE")

# A full ODBC connection string, e.g.
#   DRIVER={ODBC Driver 17 for SQL Server};SERVER=192.168.0.113;DATABASE=EBDS_PRO_DB_LIVE;UID=eb_web_ro;PWD=...
# Set it and the "odbc" transport talks to SQL Server directly, from anywhere on
# the LAN. Left unset by default because it is the only transport of the three
# that needs a credential to exist first.
SAP_DB_DSN = os.getenv("SAP_DB_DSN")


def build_query(group_codes: list[int]) -> str:
    """One SELECT, returned as JSON by SQL Server itself.

    FOR JSON rather than delimited columns because item names carry commas, quotes
    and parentheses ("Impression Trays - Perforated With Retention (Pair)"), and
    every delimiter that could separate them also appears inside them. Letting the
    server do the encoding removes that whole class of parsing bug.

    Every text column is RTRIM'd here rather than in Python: these are CHAR columns
    padded with spaces, and the transport below strips trailing whitespace per line
    anyway - trimming at the source means the two agree.
    """
    in_list = ",".join(str(c) for c in group_codes)
    return f"""
SET NOCOUNT ON;
DECLARE @json nvarchar(max) = (SELECT
    RTRIM(i.ItemCode)                        AS code,
    RTRIM(i.ItemName)                        AS name,
    NULLIF(RTRIM(i.FrgnName), '')            AS foreign_name,
    i.ItmsGrpCod                             AS group_code,
    RTRIM(g.ItmsGrpNam)                      AS group_name,
    NULLIF(RTRIM(i.U_Brand), '')             AS brand,
    NULLIF(RTRIM(i.U_Sub_Group), '')         AS subgroup,
    NULLIF(RTRIM(i.U_Series), '')            AS series,
    NULLIF(RTRIM(i.U_ItemWarranty), '')      AS warranty,
    NULLIF(RTRIM(i.InvntryUom), '')          AS uom,
    NULLIF(RTRIM(i.SalUnitMsr), '')          AS sales_uom,
    CAST(p.Price AS decimal(19, 4))          AS price,
    RTRIM(p.Currency)                        AS currency,
    CASE WHEN i.validFor  = 'Y' THEN 1 ELSE 0 END AS valid,
    CASE WHEN i.frozenFor = 'Y' THEN 1 ELSE 0 END AS frozen,
    CASE WHEN i.SellItem  = 'Y' THEN 1 ELSE 0 END AS sellable,
    CASE WHEN i.InvntItem = 'Y' THEN 1 ELSE 0 END AS stocked,
    CAST(i.OnHand     AS decimal(19, 4))     AS on_hand,
    CAST(i.IsCommited AS decimal(19, 4))     AS committed,
    CAST(i.OnOrder    AS decimal(19, 4))     AS on_order,
    CONVERT(varchar(10), i.CreateDate, 23)   AS created,
    CONVERT(varchar(10), i.UpdateDate, 23)   AS updated,
    (
        SELECT RTRIM(t.WhsCode)                     AS warehouse,
               CAST(t.OnHand AS decimal(19, 4))     AS on_hand,
               CAST(t.IsCommited AS decimal(19, 4)) AS committed
        FROM OITW t
        WHERE t.ItemCode = i.ItemCode
        ORDER BY t.WhsCode
        FOR JSON PATH
    )                                        AS stock
FROM OITM i
LEFT JOIN OITB g ON g.ItmsGrpCod = i.ItmsGrpCod
LEFT JOIN ITM1 p ON p.ItemCode = i.ItemCode AND p.PriceList = {PRICE_LIST}
WHERE i.ItmsGrpCod IN ({in_list})
ORDER BY i.ItemCode
FOR JSON PATH);

-- Gzip it, then base64 it, and hand back the text. See _run_sql: this is what makes
-- the result survive sqlcmd's line chunking intact.
DECLARE @gz varbinary(max) = COMPRESS(@json);
SELECT CAST(N'' AS xml).value('xs:base64Binary(sql:variable("@gz"))', 'varchar(max)') AS payload;
""".strip()


def _decode(b64: str) -> str:
    """Undo the gzip+base64 wrapper the query applies. See _run_sql_sqlcmd."""
    if not b64:
        return ""
    # COMPRESS() emits gzip, and the value was UTF-16 before it was compressed
    # (nvarchar -> varbinary keeps the encoding), so it decodes back the same way.
    return gzip.decompress(base64.b64decode(b64)).decode("utf-16-le")


def _run_sql_odbc(sql: str) -> str:
    """Straight to SQL Server over the network, using SAP_DB_DSN.

    The transport to prefer once a login exists: no SSH, no temp file, no display
    layer to work around - pyodbc hands back the column as a Python string. Needs a
    SQL login because Windows authentication does not cross machines here (the two
    boxes share no domain, and SQL Server rejects it with "the login is from an
    untrusted domain"). The server is in mixed mode, so such a login is allowed.
    """
    import pyodbc  # imported here so the other transports don't require it

    with pyodbc.connect(SAP_DB_DSN, autocommit=True) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        # The query ends in a SELECT, but SET NOCOUNT/DECLARE ahead of it can leave
        # non-row results in front of it depending on driver version.
        while True:
            row = cursor.fetchone() if cursor.description else None
            if row is not None:
                return _decode(row[0] or "")
            if not cursor.nextset():
                return ""


def _run_sql_sqlcmd(sql: str, *, remote: bool) -> str:
    """Run one query on the company database and return its JSON payload.

    The awkward part is getting a multi-megabyte value out of sqlcmd intact. sqlcmd
    is a *display* tool: it chops a long column into 2033-character lines and pads
    each one out to the column width, so the raw output needs both re-joining and
    right-stripping. Right-stripping raw JSON is not safe - a chunk boundary lands
    mid-value roughly a thousand times in a catalogue this size, and every boundary
    that happens to fall on a space inside an item name would silently delete that
    space ("Diamond Bur" -> "DiamondBur"), with nothing anywhere reporting an error.

    So the query hands back gzip-then-base64 instead. Base64 contains no spaces at
    all, which makes stripping and re-joining exactly as lossless as they look, and
    the gzip step shrinks a ~6 MB UTF-16 document to something worth sending over
    an SSH pipe. -y 0 keeps sqlcmd from truncating the column at its default width.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as fh:
        fh.write(sql)
        local_sql = fh.name
    try:
        # -y 0 is mutually exclusive with both -W (trim) and -h -1 (suppress the
        # header), so this does its own trimming and drops the header below.
        if remote:
            script_path = "C:\\Windows\\Temp\\eb_sap_pull.sql"
            subprocess.run(
                ["scp", "-q", "-o", "BatchMode=yes", local_sql,
                 f"{SSH_HOST}:C:/Windows/Temp/eb_sap_pull.sql"],
                check=True,
            )
            argv = [
                "ssh", "-o", "BatchMode=yes", SSH_HOST,
                f"sqlcmd -S localhost -E -d {COMPANY_DB} -I -y 0 -i {script_path}",
            ]
        else:
            # On the server itself, where the database is local and Windows auth
            # works because it never leaves the machine.
            argv = [
                "sqlcmd", "-S", "localhost", "-E", "-d", COMPANY_DB,
                "-I", "-y", "0", "-i", local_sql,
            ]
        proc = subprocess.run(
            argv, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    finally:
        os.unlink(local_sql)

    # Everything up to and including sqlcmd's "-----" rule is the column header.
    # Keying on the rule rather than on a line count is deliberate: base64's
    # alphabet has no hyphen in it, so no line of data can ever be mistaken for it.
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if set(line) == {"-"}:
            lines = lines[index + 1:]
            break
    return _decode("".join(lines))


# How the query reaches SQL Server. Three transports because there are three real
# situations, not because the choice is interesting:
#
#   odbc   - direct over the LAN. Needs a SQL login (SAP_DB_DSN). Preferred once
#            one exists; the only one that works from a machine with neither SSH
#            access nor the database on it.
#   local  - sqlcmd against localhost. What production uses: store-api is deployed
#            on the same server as SQL Server, so there is nothing to cross.
#   ssh    - sqlcmd on the server, driven over SSH. Needs no credential of its own
#            and no change to the server, which is why it is the default today.
TRANSPORTS = ("auto", "odbc", "local", "ssh")


def _run_sql(sql: str, transport: str = "auto") -> str:
    """Run the query using the requested transport.

    "auto" prefers a direct connection when a DSN is configured and otherwise falls
    back to SSH - deliberately never to "local", because guessing wrong there means
    running the query against whatever SQL Server happens to be installed on the
    machine you are sitting at rather than the one holding the company database.
    """
    if transport == "auto":
        transport = "odbc" if SAP_DB_DSN else "ssh"
    if transport == "odbc":
        if not SAP_DB_DSN:
            raise RuntimeError("SAP_DB_DSN is not set - cannot use the odbc transport.")
        return _run_sql_odbc(sql)
    return _run_sql_sqlcmd(sql, remote=transport == "ssh")


def case_collisions(values: list[str]) -> dict[str, list[str]]:
    """Groups of spellings that differ only in case ("Woodpecker"/"WOODPECKER").

    Worth its own function because it is the one flaw in this data that an importer
    cannot shrug off. SAP compares these under a case-insensitive collation, so to
    SAP they are one brand; `brands.brand_name` is UNIQUE under Postgres' default
    case-*sensitive* collation, so to us they are two. Import them as they come and
    the storefront's brand filter grows a second, half-populated entry for sixteen
    real brands - and merging them afterwards means repointing products by hand.
    """
    by_fold: dict[str, set[str]] = {}
    for value in values:
        by_fold.setdefault(value.casefold(), set()).add(value)
    return {fold: sorted(spellings) for fold, spellings in by_fold.items() if len(spellings) > 1}


def summarise(rows: list[dict], group_codes: list[int], label: str = "catalogue") -> str:
    labels = ", ".join("{} {}".format(c, GROUP_NAMES.get(c, "?")) for c in group_codes)
    L: list[str] = [f"# SAP {label} extract", ""]
    L.append(f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    L.append(f"- Company DB: `{COMPANY_DB}` (read-only)")
    L.append(f"- Item groups: {labels}")
    L.append(f"- Rows: **{len(rows)}**")
    L.append("")

    priced = [r for r in rows if r.get("price")]
    L.append("## Price")
    L.append("")
    L.append(f"- Priced on list {PRICE_LIST}: {len(priced)} of {len(rows)}")
    if priced:
        amounts = sorted(float(r["price"]) for r in priced)
        L.append(f"- Range: ${amounts[0]:,.2f} - ${amounts[-1]:,.2f}")
    non_usd = [r for r in rows if r.get("currency") and r["currency"] != "USD"]
    if non_usd:
        codes = ", ".join(r["code"] for r in non_usd[:5])
        L.append(f"- **Not USD: {len(non_usd)}** ({codes})")
    L.append("")

    L.append("## Brand (U_Brand)")
    L.append("")
    brands = Counter(r["brand"] for r in rows if r.get("brand"))
    L.append(f"- Items with a brand: {sum(brands.values())} of {len(rows)}")
    L.append(f"- Distinct brands: {len(brands)}")
    L.append(
        f"- **Without a brand: {len(rows) - sum(brands.values())}** "
        "(products.brand_id is NOT NULL, so these need a fallback)"
    )
    brand_dupes = case_collisions(list(brands))
    if brand_dupes:
        L.append(
            f"- **Case collisions: {len(brand_dupes)}** - match brands case-insensitively on "
            "import, or each of these becomes two brands:"
        )
        for spellings in list(brand_dupes.values())[:20]:
            L.append(f"  - {' / '.join(spellings)}")
    L.append("")

    # Left in SAP rather than cleaned on the way through, deliberately: SAP is the
    # authority for the item master, so stripping the quotes here would make the
    # website disagree with the quote, the invoice and the SAP client - all of
    # which read the same field. The fix belongs upstream; this just names it.
    mangled = [
        r for r in rows
        if r.get("name") and (r["name"].lstrip().startswith('"') or '""' in r["name"])
    ]
    if mangled:
        L.append("## Item names carrying stray quote marks")
        L.append("")
        L.append(
            f"**{len(mangled)} of {len(rows)} ({len(mangled) / len(rows):.0%})** start with `\"` or "
            "contain a doubled `\"\"` - the signature of text pasted in from a CSV or "
            "spreadsheet with its escaping intact. Verified against OITM directly: this "
            "is what SAP holds, not damage from the extract."
        )
        L.append("")
        L.extend(f"  - `{r['code']}` - {r['name'][:60]}" for r in mangled[:10])
        if len(mangled) > 10:
            L.append(f"  - _...{len(mangled) - 10} more_")
        L.append("")
        L.append("Worth fixing in SAP: these names reach the storefront, the printed "
                 "quote and the invoice exactly as they are here.")
        L.append("")

    L.append("## Sub-group (U_Sub_Group)")
    L.append("")
    subs = Counter(r["subgroup"] for r in rows if r.get("subgroup"))
    L.append(f"- Items with a sub-group: {sum(subs.values())} of {len(rows)}")
    L.append(f"- Distinct sub-groups: {len(subs)}")
    sub_dupes = case_collisions(list(subs))
    if sub_dupes:
        L.append(f"- **Case collisions: {len(sub_dupes)}** - "
                 + "; ".join(" / ".join(s) for s in list(sub_dupes.values())[:10]))
    L.append("")

    # The reason this section exists: the numbers below decide whether stock can be
    # shown on the storefront at all, and they are the first thing to re-check once
    # SAP's opening balances are replaced by a real count.
    L.append("## Stock")
    L.append("")
    per_value = Counter(str(r.get("on_hand")) for r in rows)
    top_value, top_count = per_value.most_common(1)[0]
    L.append(f"- Most common on-hand figure: `{top_value}` on {top_count} of {len(rows)} items")
    if top_count > len(rows) * 0.9:
        L.append("")
        L.append(
            f"> **{top_count / len(rows):.0%} of items carry the identical quantity.** That is an "
            "opening balance, not a stock count - do not publish it as availability."
        )
    L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only SAP catalogue extract.")
    parser.add_argument(
        "--catalogue",
        choices=sorted(CATALOGUES),
        help="Pull a named catalogue - decides both the item groups and the output "
             "filename. Default: materials. "
             + "; ".join(f"{k}={c['groups']}" for k, c in sorted(CATALOGUES.items())),
    )
    parser.add_argument(
        "--groups",
        help="Comma-separated OITB group codes, or 'all', for an ad-hoc pull that is "
             "not one of the catalogues above - writes items.json rather than "
             "overwriting a catalogue's extract. "
        + "; ".join(f"{c}={n}" for c, n in sorted(GROUP_NAMES.items())),
    )
    parser.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default="auto",
        help="How to reach SQL Server. auto: direct if SAP_DB_DSN is set, else ssh. "
             "local: sqlcmd against localhost, for running on the server itself.",
    )
    parser.add_argument("--out", default="sap_extract", help="Output directory")
    args = parser.parse_args()

    if args.catalogue and args.groups:
        print("Pass --catalogue or --groups, not both.", file=sys.stderr)
        sys.exit(1)

    # The output basename tracks WHAT was pulled, so two catalogues can sit side by
    # side in one extract directory instead of overwriting each other. Materials
    # keeps the name it had when it was the only catalogue there is, which is what
    # lets the saved extracts and sap_sync's --from-file examples keep resolving.
    if args.groups:
        basename, label = "items", "catalogue"
        if args.groups.strip().lower() == "all":
            group_codes = sorted(GROUP_NAMES)
        else:
            try:
                group_codes = [int(c) for c in args.groups.split(",") if c.strip()]
            except ValueError:
                print(
                    f"--groups must be comma-separated numbers or 'all', got {args.groups!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
    else:
        catalogue = CATALOGUES[args.catalogue or "materials"]
        group_codes = catalogue["groups"]
        basename = (args.catalogue or "materials").replace("-", "_")
        label = catalogue["label"].lower()
    if not group_codes:
        print("No item groups selected.", file=sys.stderr)
        sys.exit(1)

    via = "SAP_DB_DSN" if (args.transport == "odbc" or (args.transport == "auto" and SAP_DB_DSN))         else ("localhost" if args.transport == "local" else SSH_HOST)
    print(f"Reading {COMPANY_DB} via {via} (groups {group_codes})...", flush=True)
    try:
        payload = _run_sql(build_query(group_codes), args.transport)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[:500]
        print(f"Query failed: {detail}", file=sys.stderr)
        sys.exit(1)
    if not payload:
        print("Query returned nothing - no items in those groups?", file=sys.stderr)
        sys.exit(1)

    rows = json.loads(payload)
    # The nested warehouse array arrives as a JSON *string*: a subquery's FOR JSON
    # is a value, not a document, so it needs a second parse to become a list.
    for row in rows:
        raw = row.get("stock")
        row["stock"] = json.loads(raw) if isinstance(raw, str) else (raw or [])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / f"{basename}.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # A flat CSV alongside the JSON, because the first thing anyone does with a
    # catalogue extract is open it in Excel and sort by price. Warehouse stock is
    # flattened to one column per warehouse for the same reason. utf-8-sig: Excel
    # reads a BOM-less UTF-8 CSV as the system codepage and mangles every name.
    # The column list has to be the union across every row, not row[0]'s keys:
    # FOR JSON omits a key entirely when its value is NULL, so the first item
    # having no U_Series is enough to drop `series` from the whole file.
    warehouses = sorted({s["warehouse"] for r in rows for s in r["stock"]})
    fields: list[str] = []
    for row in rows:
        fields.extend(f for f in row if f != "stock" and f not in fields)
    fields += [f"stock_{w}" for w in warehouses]
    with (out_dir / f"{basename}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = {k: v for k, v in row.items() if k != "stock"}
            by_whs = {s["warehouse"]: s["on_hand"] for s in row["stock"]}
            flat.update({f"stock_{w}": by_whs.get(w) for w in warehouses})
            writer.writerow(flat)

    report_name = f"{basename}_report.md"
    (out_dir / report_name).write_text(
        summarise(rows, group_codes, label), encoding="utf-8"
    )

    print(f"\n{len(rows)} items -> {out_dir / f'{basename}.json'}, "
          f"{out_dir / f'{basename}.csv'}")
    print(f"Summary: {out_dir / report_name}")


if __name__ == "__main__":
    main()
