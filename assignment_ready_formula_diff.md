# Assignment-Ready Sheet Formula Diff

Compared:

- Old sheet: `1YF408L3VZkw-7M17rCnMrsLCZ0KuKGGKipSyqHtYPeU`
- New sheet: `1d2HSRMB_6gCPLcEnXOMM2sBgsgWuy7Vb1UVTqRN_FPw`

## Summary

- Old formula cells: `8012`
- New formula cells: `8319`
- Net increase: `+307`
- New tabs in the new sheet: `Job Request`, `Upload`
- The new tabs do not contain formulas.
- No formulas changed at the same tab/cell location.

## Extra Formulas

The extra formulas are on the `Mode` tab.

| Column | Header | Rows | Formula pattern | Count |
| --- | --- | --- | --- | --- |
| `AJ` | `Start Date` | `2-580` | `=XLOOKUP(AC2,Jobs!B:B,Jobs!F:F,0)` | `579` |
| `AK` | `End Date` | `2-580` | `=XLOOKUP(AC2,Jobs!B:B,Jobs!G:G,0)` | `579` |
| `AL` | `Shift Start Date` | `2-580` | `=TEXT(I2,"mm/dd/yyyy")` | `579` |

These are the assignment-readiness formulas: they pull job start/end dates from `Jobs` using `Available Jobs` in column `AC`, and normalize the shift date from column `I`.

## Removed Formula Coverage From Old Sheet

The new sheet also removes some formulas that existed in the older copy:

- `Mode!AG2:AG1000` no longer has the old `CAN Details` lookup formula.
- `Mode!AF581:AF1000` no longer has the old `Existing Jobs` lookup formula.
- `Mode!X354:X356`, `Mode!X377`, `Mode!X503:X507`, and `Mode!X536` no longer have the old `Perfect AID` formula.
- `Mode!AB326` no longer has the old `CAN ID` lookup formula.

That is why there are `1737` newly added formula cells but only a `+307` net formula-cell increase.

## Filters In New Sheet

The new sheet has basic filters on:

- `Mode!A1:AM1000`
- `Raw Data!A1:U1028`
- `Open & Closed!A1:BE14275`
- `Jobs!A1:Q130`
- `Open Active!A1:BG1276`
- `Fixed Rate!A1:Q1275`

No saved filter views were returned by the Sheets API.
