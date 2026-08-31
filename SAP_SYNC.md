# How the SAP catalogue sync works

SAP Business One is the authority for the **item master** - which products exist,
what they are called, what they cost and whether they are still offered. This
Postgres is the authority for **everything about presenting them** - photographs,
descriptions, badges, promotions, discounts.

The sync is the one-way bridge between the two. It never writes to SAP, and it
never overwrites anything SAP does not know about.

```
SAP B1 company database              this repo                        store Postgres
EBDS_PRO_DB_LIVE (MSSQL)
  OITM  item master                scripts/sap_db_pull.py            products
  OITB  item groups        --->      one read-only SELECT     --->     (+ brands,
  ITM1  price list 1                 gzip+base64 -> JSON               categories)
  OITW  stock per warehouse
                                   scripts/sap_sync.py
                                     upsert + delist          --->    activity log
                                     writes a run report
```

Two scripts, usable separately:

| Script | What it does | Writes |
| --- | --- | --- |
| `scripts/sap_db_pull.py` | Runs the SELECT, saves `sap_extract/<name>.json` + `.csv` + a data-quality `_report.md` | files only |
| `scripts/sap_sync.py` | Reads an extract (live, or `--from-file`) and upserts it into `products` | Postgres |

`sap_sync` calls the same query builder and transport as `sap_db_pull`, so a
scheduled run needs no file on disk - the pull script exists for looking at the
data, not as a step the sync depends on.

---

## 1. Two catalogues, two sections

`CATALOGUES` in [scripts/sap_db_pull.py](scripts/sap_db_pull.py) is the single
definition both scripts read:

| Catalogue | SAP item groups (`OITB.ItmsGrpCod`) | Lands in `products.section` |
| --- | --- | --- |
| `materials` | 101 Materials, 106 Lab Material | `materials` |
| `spare-parts` | 103 Spare Part | `spare_parts` |

Groups 100 Office supply, 102 Services and 104 Equipment are deliberately **not**
pulled. Office supply and Services are not sold online; Equipment overlaps the
hand-curated machinery catalogue, and importing it would create a second,
SAP-shaped copy of products that already have photographs here.

**Why spare parts are their own section and not `machinery`,** even though that is
the shop they are sold in: the sync delists everything in the section it owns that
is missing from the extract. A spare-parts run that owned `machinery` would delist
all 110 hand-curated machines on its first pass, none of which SAP has ever heard
of. One section per catalogue makes each run's authority exactly the rows it is
actually the authority for. The Flask shell still shows two shops - see
`site_section.SECTIONS` and `Product.section`.

## 2. What the sync owns

Overwritten on every run:

> `product_name`, `list_price`, `price`, `uom`, `brand_id`, `category_id`,
> `stock_qty`, `stock_synced_at`, `section`

Never written, at all:

> `product_image`, the `product_images` gallery, `description`, `badge`,
> `is_purchasable`, `discount`, `discount_type`

That split is the whole design. Photos are uploaded here and exist nowhere in SAP,
so a sync that wrote `product_image` would erase every picture anyone had added,
the moment it ran. The owned-field list is written out explicitly in the code
rather than inferred from "whatever the extract happens to contain", so a new
column in the extract cannot quietly start overwriting local data.

## 3. Field mapping

| Postgres | SAP | Notes |
| --- | --- | --- |
| `product_code` | `OITM.ItemCode` | the match key, unique on both sides |
| `product_name` | `OITM.ItemName` | falls back to `FrgnName` on damaged rows - see 5 |
| `list_price` | `ITM1.Price` where `PriceList = 1` | list 1 "Normal Sale Price" is the only list with any rows |
| `price` | *derived* | `list_price` minus the discount held **here** - see 4 |
| `uom` | `OITM.InvntryUom` | |
| `brand_id` | UDF `OITM.U_Brand` | get-or-create, case-insensitive; `Unbranded` when empty |
| `category_id` | UDF `OITM.U_Sub_Group` | get-or-create, case-insensitive; nullable |
| `stock_qty` | `OITM.OnHand` | stored, **not displayed** - it is an opening balance, not a count |
| `stock_synced_at` | - | stamped every run, even when nothing changed |
| `delisted_at` | `validFor` / `frozenFor` / absence | see 6 |

Brand and category come from **user-defined fields**, not from SAP's own tables:
`OMRC` (manufacturers) holds one row, "- No Manufacturer -", and all 64 `QryGroup`
item properties are unused, so there is no web-publish flag to honour either.

`OITM.OnHand` is the total across warehouses; the extract confirms it equals the
sum of the per-warehouse `OITW` rows, so there is nothing to add up. The stock
figure is stored but never shown: 8,115 of 8,127 items sit at exactly 300,000,
which is a go-live opening balance rather than a count.

## 4. Prices

SAP's price lands on `list_price`, **never directly on `price`**. `price` is then
recomputed from the discount staff have set here:

```
discount <= 0          ->  price = list_price
discount_type=percent  ->  price = list_price * (100 - discount) / 100
discount_type=cash     ->  price = list_price - discount
```

So SAP repricing an item keeps any promotion running on top of it instead of
silently cancelling it. `_price_for()` in the sync is the deliberate mirror of
`_derive_list_price()` in [app/routers/products.py](app/routers/products.py) - if
the two ever disagree, a synced product shows a discount that does not match the
gap between its own two prices. A stale cash discount larger than the new list
price would produce a price of zero or less, which the schema forbids; the
discount is dropped and the list price used instead.

JSON is parsed with `parse_float=Decimal`. Letting prices become binary floats
first is how `15.00` becomes `14.999999999999998` on a `Numeric` column.

## 5. Names

`OITM.ItemName` has lost every non-ASCII character it ever held - each flattened
to a literal `?` (211 of 8,127 material rows, e.g. `????????? 4.0cm K30290`). The
question marks are really in the column; no reader can recover the original text.
`OITM.FrgnName` was written through a Unicode-safe path and kept the Khmer, so it
is read wherever `ItemName` contains a `?`. Both hold the same product's name,
which makes the swap a repair rather than a change of meaning.

Rows `FrgnName` cannot save are stored as they stand and listed in the run report,
because only a person editing SAP can fix them. Zero-width spaces are stripped on
the way in - they are invisible, but one sitting inside `5/1<U+200B>6` stops the
row ever matching a search for `5/16`.

Names are otherwise left exactly as SAP has them, including the 3% that carry
stray CSV quote marks. SAP is the authority, and cleaning them in transit would
make the website disagree with the printed quote and the SAP client.

## 6. Delisting

`sap_sync` used to only ever create and update, which meant an item withdrawn in
SAP kept selling on the site forever. Now, per run and **strictly within the one
section it owns**:

- absent from the extract, or `validFor = 'N'`, or `frozenFor = 'Y'`
  -> `delisted_at` is stamped;
- present again -> `delisted_at` is cleared automatically.

Delisted, never deleted: `order_items.product_id` is `ON DELETE SET NULL` and
images/manuals cascade, so deleting would blank past order lines. Public reads
exclude delisted rows; `include_delisted=true` is for the admin table.

**The safety rail.** If a single run wants to delist more than
`--max-delist-ratio` (default `0.10`) of the section, it aborts with exit 1 and
writes nothing. A partial extract, a timeout or the wrong `--groups` would
otherwise make "everything SAP no longer offers" mean the whole catalogue, and one
unattended run would empty the storefront. Real withdrawals come in ones and tens.
Verified: a truncated 500-row extract refused to delist 94%. Pass
`--max-delist-ratio 1.0` if a mass withdrawal really is correct.

Note that **validity is decided before price**. A valid item that happens to have
no price is a data gap, not a withdrawal - it is skipped for the upsert but still
counts as offered, so it is never hidden, and an already-hidden one comes back.

## 7. What gets skipped

| Reason | Behaviour |
| --- | --- |
| No price on list 1, or price <= 0 | skipped and named in the report; `price` is required and must be > 0 |
| Currency is not `USD` | skipped and named. SAP has a currency literally called `$`, separate from `USD`, with one item in it. Probably a mis-picked dropdown - but "probably" is not good enough for money on a live storefront. One field to fix in SAP, then it syncs normally |
| The code already belongs to a product in **another** section | reported, never converted. A hand-curated machinery product carries photos, a description and a staff-set price; moving it into a synced section would hand all of that to SAP. A collision means the code was reused, not that the product moved |

## 8. Brands and categories

`NameCache` does get-or-create matched **case-insensitively**. SAP compares these
under a case-insensitive collation, so `Woodpecker` and `WOODPECKER` are one brand
there; `brands.brand_name` is UNIQUE under Postgres' case-*sensitive* default, so
importing both would create two - and nineteen real brands are spelled both ways.
Whichever spelling is seen first wins. Case-insensitive matching also correctly
reuses the existing machinery brands rather than duplicating them.

`products.brand_id` is NOT NULL and roughly a fifth of SAP items have no
`U_Brand`, so those get the `Unbranded` brand - an existing real brand would be a
lie, and skipping them would drop 1,600 sellable products off the site. Category
is nullable, so an item with no `U_Sub_Group` simply has none.

## 9. Reports and the change log

Every run writes `sap_extract/<catalogue>_sync_report.md` and prints it. Counts
first, then the specific rows a person needs to look at: what was delisted and
why, what was skipped, which names had to come from `FrgnName`, and - for
updates - **which fields actually moved**. "Updated 8,127 products" says nothing;
a list of the forty whose price changed is worth reading.

The sync also writes the normal activity log, attributed to
`("system", None, "SAP sync (<catalogue>)")`, so a surprise price change traces to
a run of this script instead of appearing to come from nowhere. Two things make
that work, and both are easy to lose:

- `import app.core.activity` at the top of the sync has **no reference below it
  and must stay**. That module registers the `before_flush`/`after_flush`
  listeners at import time; a standalone script that does not import it writes no
  history at all, silently. (`scripts/seed_catalog.py` deliberately does not
  import it - seeding an empty dev database is not a change anyone made.)
- `stock_synced_at` is in `IGNORED_FIELDS`. Stamping it every run would otherwise
  file 8,000 "updated product" rows a night and bury the real changes.

Each catalogue runs in its own session and transaction, so a spare-parts run that
trips the delisting rail cannot roll back a materials run that already succeeded,
and each report describes what was really written. One timestamp is taken for the
whole invocation, so `stock_synced_at` and any `delisted_at` agree across
catalogues instead of differing by however long the first query took.

## 10. Running it

Dry run first - it does the entire run and rolls back, which is how you check a
run before trusting it:

```bash
python -m scripts.sap_sync                                  # dry run, both catalogues
python -m scripts.sap_sync --apply                          # write, both
python -m scripts.sap_sync --catalogue spare-parts --apply  # one catalogue
python -m scripts.sap_sync --catalogue materials --from-file sap_extract/materials.json --apply
python -m scripts.sap_sync --transport local --apply        # on the server
```

`--from-file` requires an explicit `--catalogue`: a file on disk does not say
which section its rows belong in, and guessing would write a whole catalogue into
the wrong one.

To look at the data rather than sync it:

```bash
python -m scripts.sap_db_pull                          # materials -> sap_extract/materials.json|.csv|_report.md
python -m scripts.sap_db_pull --catalogue spare-parts  # -> spare_parts.*
python -m scripts.sap_db_pull --groups all             # ad-hoc -> items.*
```

### Transports

Three, because there are three real situations:

| `--transport` | How | When |
| --- | --- | --- |
| `local` | `sqlcmd -S localhost -E` | **production** - store-api is deployed on the same server as SQL Server, so nothing crosses a network |
| `ssh` | `scp` the query over, then `sqlcmd` through `ssh ebserver` | **the default from a dev machine.** Needs no credential of its own and no change to the server |
| `odbc` | `pyodbc` straight over the LAN, using `SAP_DB_DSN` | preferred once a read-only SQL login exists. The server is already in mixed mode, so this needs no change to SQL Server - just the login, which is a production security change and has not been made |

`auto` picks `odbc` when `SAP_DB_DSN` is set and `ssh` otherwise - deliberately
never `local`, since guessing wrong there means querying whatever SQL Server
happens to be installed on the machine you are sitting at.

Windows authentication does **not** cross from a dev machine: the two boxes share
no domain and SQL Server answers "the login is from an untrusted domain". Port
1433 being open is not the issue.

Environment: `SAP_SSH_HOST` (default `ebserver`), `SAP_COMPANY_DB` (default
`EBDS_PRO_DB_LIVE`), `SAP_DB_DSN` (unset by default).

### The transport trap worth knowing

`sqlcmd` is a *display* tool: it chops a long column into 2033-character lines and
pads each one out to the column width, so its output needs both re-joining and
right-stripping. Right-stripping raw JSON silently deletes real spaces wherever a
chunk boundary lands on one inside an item name - about 80 times in a 6 MB
extract, with no error anywhere. So the query hands back `COMPRESS()` + base64
instead: base64 contains no spaces, which makes stripping exactly as lossless as
it looks, and the gzip step shrinks a ~6 MB UTF-16 document to something worth
sending down an SSH pipe. (`-y 0` is mutually exclusive with both `-W` and `-h -1`,
hence the manual header trimming.)

The query also asks SQL Server for `FOR JSON` rather than delimited columns: item
names carry commas, quotes and parentheses, and every delimiter that could
separate them also appears inside them.

## 11. Scheduling in production

- [scripts/sap_sync_scheduled.cmd](scripts/sap_sync_scheduled.cmd) - one run:
  `cd E:\Website\store-api`, then `venv\Scripts\python.exe -m scripts.sap_sync
  --transport local --apply`, appending to `E:\Website\logs\sap_sync_<date>.log`
  and propagating the exit code so Task Scheduler's "Last Run Result" is the truth.
- [scripts/install_sap_sync_task.ps1](scripts/install_sap_sync_task.ps1) -
  registers it daily at 02:30 as an account that can both read the SAP company
  database through Windows auth and write to the store Postgres (Administrator has
  both). Run it elevated, on the server; it prompts for the password rather than
  storing one.

A scheduled task rather than an NSSM service (`ebdental-api` / `ebdental-web`)
because this is a job that runs and exits - a service wrapper would either restart
it in a loop or sit "stopped" looking like a fault. Nightly rather than hourly
because SAP's item master changes a handful of times a week, while every run
rewrites ~8,000 rows and files change-log entries for whatever moved.

**Status: written, not installed.** Installing it is a production change and needs
a go-ahead.

## 12. Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Refusing to delist N of M products` | the safety rail (6). Check the extract is complete before overriding it |
| `the login is from an untrusted domain` | Windows auth from a dev machine. Use `--transport ssh`, or set up `SAP_DB_DSN` |
| `extract is empty - refusing to run` | the query returned no rows - wrong `--groups`, wrong database, or a failed connection |
| A name reads `?????` in the report | SAP's `ItemName` really holds that and `FrgnName` could not save it. Fix it in SAP |
| Sync ran but the activity log is empty | the `import app.core.activity` line was dropped (9) |
| An item vanished from the storefront | it was delisted. Check the report, then `validFor` / `frozenFor` in SAP |
| Category dropdown in the admin form is missing options | `MAX_PAGE_SIZE = 500` is a hard server cap and there are 850+ categories - use `client.get_all()` (see `EB Web Project/store_api.py`) |

## 13. If you change this

- Keep `_price_for()` in step with `_derive_list_price()` in
  [app/routers/products.py](app/routers/products.py).
- Keep the owned-field list explicit. Never add a presentational column to it.
- `CATALOGUES` must stay the single source for group -> section: a pull and a sync
  that disagreed about which groups make up a catalogue would delist the
  difference between them.
- `sap_db_pull` must never grow an `INSERT` or `UPDATE`. B1 maintains its own
  document numbering and audit tables, so writing to a company database from
  outside the client is how documents get corrupted.
