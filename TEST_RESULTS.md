# Test Results -- Vendor 110002 Extraction Module

## 1. Real invoice: `3209526595_Container_3.pdf` (dormakaba Deutschland GmbH, 15 pages)

| Check | Result |
|---|---|
| Line items extracted | 36 / 36 (every item number 10-780 present) |
| Records flagged | 0 |
| Sum of extracted Line Amounts | 244,359.97 |
| Printed "Total items" on invoice | 244,359.97 |
| **Match** | **Exact, to the cent** |
| Header fields (Invoice No., Invoice date, Customer, Order Number, Delivery-note No., Your order number, Order date, Marketing area) | All correctly extracted from page 1 |
| Multi-line wrapped descriptions (e.g. item 10, item 110/500, item 780) | Correctly reconstructed across line wraps and page breaks |
| Items with missing optional fields (no Order number/Delivery note, e.g. items 690, 720) | Correctly left blank, no fabricated values |
| Numbers vulnerable to the phantom-space bug (e.g. "300", "reveal depths") | Verified correct after the space-width fix; would have been silently corrupted otherwise |

## 2. Synthetic / derived edge cases

| Test file | Purpose | Result |
|---|---|---|
| `single_page.pdf` | Single-page PDF | 2 records extracted, 0 flagged |
| `single_page_duplicate.pdf` | Identical file under a different name | 2 records, identical to above -- confirms deterministic output |
| `multi_page_3.pdf` | Multi-page (3 pages) with a table spanning page breaks | 7 records, 0 flagged |
| `full_15_pages.pdf` | Full multi-page invoice (all 15 pages) | 36 records, matches Section 1 exactly |
| `corrupted.pdf` | Truncated/invalid PDF bytes | Correctly fails with a clear error message in the log; 0 rows fabricated |
| `not_a_real_pdf.pdf` | Plain text file renamed to `.pdf` | Correctly fails with a clear error message; 0 rows fabricated |
| `scanned_page1.pdf` | Rasterized page, no text layer (simulated scan) | OCR fallback correctly triggered; vendor identity confirmed from OCR text; per-line extraction correctly declined with an explicit "manual review required" note rather than guessing |
| `synthetic_missing_values.pdf` | One item row with a missing quantity field; one item with an invalid date (`45.13.2026`) | Malformed row surfaced as a **flagged** record with the raw text preserved and every parseable field left blank (not guessed); invalid date correctly flagged on the otherwise-valid second item |

## 3. Robustness fix discovered during testing

The synthetic-missing-values test also exposed a real gap: when the
footer-boundary anchor can't be found on a page (as happens on that
minimal synthetic PDF, which has no footer at all), the original code
silently skipped the entire page with zero rows and zero warning. This
was fixed to fall back to the page bottom and log an explicit warning,
so a genuine vendor-110002 page with an unusual footer would never
silently lose its line items without at least a log entry pointing at it.

## 4. Full run log

See `extraction_log_test_suite.txt` (all 8 test files run together) and
`extraction_log_real_invoice.txt` (the real invoice run alone) for the
complete, unedited log output backing the table above.

## 5. What was not tested

- A genuine scanned vendor-110002 PDF from a real fax/scan (only a
  synthetic rasterization was available) -- OCR text quality on a real
  scan may differ.
- A second real vendor-110002 invoice with a materially different page
  layout (only one real sample PDF was provided). The regexes are
  written from the one real layout available; a second sample should be
  run through before treating the template as fully locked for
  production, per the original scope note about needing "several PDFs
  from vendor 110002."
