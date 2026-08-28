# SAP materials extract

- Generated: 2026-08-27T09:42:51+00:00
- Company DB: `EBDS_PRO_DB_LIVE` (read-only)
- Item groups: 101 Materials, 106 Lab Material
- Rows: **8127**

## Price

- Priced on list 1: 8126 of 8127
- Range: $0.01 - $4,890.00
- **Not USD: 1** (SSWFG1156)

## Brand (U_Brand)

- Items with a brand: 6456 of 8127
- Distinct brands: 191
- **Without a brand: 1671** (products.brand_id is NOT NULL, so these need a fallback)
- **Case collisions: 19** - match brands case-insensitively on import, or each of these becomes two brands:
  - PAKISTAN / Pakistan
  - HELVEMED / Helvemed
  - MEYARN / Meyarn
  - PASCAL / Pascal
  - SPIDENT / Spident
  - TISEN / Tisen
  - WOODPECKER / Woodpecker
  - YOUJOY / Youjoy
  - SHOFU / Shofu
  - SEPTODONT / Septodont
  - ITALIAN / Italian
  - SUPER ROCK / Super Rock
  - KENSONA / Kensona
  - DENTEX / Dentex
  - MASTER-DENT / Master-Dent
  - MYOBRACE / Myobrace
  - NOVOCOL / Novocol
  - DEDECO / Dedeco
  - DENFIL / Denfil

## Item names carrying stray quote marks

**241 of 8127 (3%)** start with `"` or contain a doubled `""` - the signature of text pasted in from a CSV or spreadsheet with its escaping intact. Verified against OITM directly: this is what SAP holds, not damage from the extract.

  - `(CR)662110` - "Root Elevator Apexo mm2
  - `(CR)662130` - "Root Elevator Apexomm2
  - `(CR)664810` - "Root elevator kopp mm3
  - `(CR)664820` - "Root elevator kopp mm3
  - `(CR)664830` - "Root elevator kopp mm4
  - `(CR)664840` - "Root elevator kopp mm4
  - `(CR)664850` - "Root ellevator koppmm4
  - `(CR)711821` - "DistalEnd CUTTERS N.69 Long SS & NiTi wires ø max 0
  - `(CR)711921` - "DistalEnd CUTTERS N.69 SS & NiTi wires ø max 0
  - `(CR)712021` - "Pin and Ligature CUTTERS N.89 TC ø max 0
  - _...231 more_

Worth fixing in SAP: these names reach the storefront, the printed quote and the invoice exactly as they are here.

## Sub-group (U_Sub_Group)

- Items with a sub-group: 7995 of 8127
- Distinct sub-groups: 835
- **Case collisions: 11** - Impression Tray / Impression tray; ARTICULATOR / Articulator; MX Cutter / Mx Cutter; Box / box; Diamond Strip / Diamond strip; Retraction Cord / Retraction cord; Cement Capsule / Cement capsule; Tire Nerfs / tire Nerfs; Set One / Set one; Resin Tooth Set / Resin tooth Set

## Stock

- Most common on-hand figure: `300000.0` on 8115 of 8127 items

> **100% of items carry the identical quantity.** That is an opening balance, not a stock count - do not publish it as availability.

