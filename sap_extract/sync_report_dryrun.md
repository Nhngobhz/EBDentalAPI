# SAP materials sync - DRY RUN - nothing was written

- Created: **0**
- Updated: **217**
- Unchanged: 7908
- New brands: 0
- New categories: 0
- Names read from FrgnName: 211
- **Delisted: 0** (hidden from the storefront)
- Re-listed: 0

## Skipped (2)

**No price on list 1 (1)** - `price` is required and must be > 0:
  - (Baot)BG26
**Not priced in USD (1)** - fix the currency in SAP, then re-run:
  - SSWFG1156 (currency `$`)

## Names read from FrgnName (211)

ItemName holds literal '?' for these - every non-ASCII character in that column was flattened when it was written, and nothing can undo it from here. FrgnName kept the real text, so it was used instead. Fixing ItemName in SAP is what makes this section go away.

- `(AL)8UA2M1` -> ថ្គាមលើ Acry Lux A2-M1
- `(AL)8UA2M3` -> ថ្គាមលើ Acry Lux A2-M3
- `(AL)8UA2M4` -> ថ្គាមលើ Acry Lux A2-M4
- `(AL)8UA2N1` -> ថ្គាមលើ Acry Lux A2-N1
- `(AL)8UA2N2` -> ថ្គាមលើ Acry Lux A2-N2
- `(AL)8UA2N3` -> ថ្គាមលើ Acry Lux A2-N3
- `(B)BMCR-33953` -> Blossom 3/8x1/2 10mm
- `(JL)15` -> JLក្រមួនថាសBIUEខៀវ InIay casting
- `(KM)041209R0` -> 4601.000 Endo Rescue Set ឈុតយកលីមបាក់
- `(KM)050078K0` -> ICT1.204… For Implant Komet
- `(KM)050079K0` -> ICT2.204… For Implant Komet
- `(KM)9750000` -> ថ្មសំអាត 9750 000 Cleaning Stone
- `(RTN)E-A3-20G` -> ស្ងោរ AcryC&B 20g A3 ENAMEL
- `(RTN)HIH4-1000G` -> ម្សៅអញ្ចាញស្ងោអ៊ីតាលីACRYPOL-Hi H4 /1000g
- `(RTN)HIH4500G` -> ម្សៅអញ្ចាញស្ងោអ៊ីតាលីACRYPOL -Hi H4 /500g
- `(S104)7181YL` -> កាណុងស៊ីលីកូន Spident 50pcs
- `(S29)314100` -> អាស៊ីត FineEtch 5mlx3syrings(spident)
- `(S30)1141BL` -> បិតបណ្តោះអាសន្ន Temp.it (ខៀវ) 3g x 3syrings
- `(S30)1141YL` -> បិតបណ្តោះអាសន្ន Temp.it (លឿង)3g x 3syrings
- `(S32)1142YL` -> បិតបណ្តោះអាសន្នរាវ Temp.it flowe
- _...191 more_

## Changed fields

- `(AL)8UA2M1`: product_name
- `(AL)8UA2M3`: product_name
- `(AL)8UA2M4`: product_name
- `(AL)8UA2N1`: product_name
- `(AL)8UA2N2`: product_name
- `(AL)8UA2N3`: product_name
- `(B)BMCR-33953`: product_name
- `(JL)15`: product_name
- `(KM)041209R0`: product_name
- `(KM)050078K0`: product_name
- `(KM)050079K0`: product_name
- `(KM)9750000`: product_name
- `(RTN)E-A3-20G`: product_name
- `(RTN)HIH4-1000G`: product_name
- `(RTN)HIH4500G`: product_name
- `(S104)7181YL`: product_name
- `(S29)314100`: product_name
- `(S30)1141BL`: product_name
- `(S30)1141YL`: product_name
- `(S32)1142YL`: product_name
- `(S33)1311100`: product_name
- `(S44)111100`: product_name
- `(S46)441100`: product_name
- `(S47)2111YL`: product_name
- `(S48)211300`: product_name
- `(S65)116100`: product_name
- `(S66)441130`: product_name
- `(ST)7015012`: product_name
- `(ST)7015013`: product_name
- `(ST)7015014`: product_name
- `(TDC)34650`: product_name
- `(VCS))0019`: product_name
- `(VCS)0016`: product_name
- `(WM)CP-002`: product_name
- `(WM)CRX-001`: product_name
- `(XH)0230`: product_name
- `(XH)0232`: product_name
- `(XH)0233`: product_name
- `(XH)0237`: product_name
- `(XH)0239`: product_name
- _...177 more_

