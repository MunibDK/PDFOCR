"""
engine.py
Vendor-agnostic orchestration: given one or more PDF paths, figure out which
vendor template applies, run it, run OCR fallback if the PDF has no
extractable text layer, validate results, and return a flat list of row
dicts ready for Excel/CSV export plus a structured log of what happened.

Adding a new vendor later = write vendors/vendor_XXXXXX.py exposing
detect_vendor(text)->bool, extract_header(pdf)->obj, extract_line_items(pdf,
filename)->(items, unmatched), validate_items(items, filename)->items, and
register it in VENDOR_TEMPLATES below. No other file needs to change.
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor_templates"))

from pdf_core import open_pdf, clean_text
import vendor_110002

VENDOR_TEMPLATES = {
    "110002": vendor_110002,
}

OUTPUT_COLUMNS = [
    "Vendor Code", "Vendor Name", "Document Number", "Document Date",
    "SAP Order Number", "Delivery Note Number", "Customer Order Number (Header)",
    "Item No", "Conf Pos", "Material/Item Code", "Description",
    "Quantity", "Unit of Measure", "Unit Price", "Line Amount", "Currency",
    "Your Order Ref (Line)", "Your Order Date (Line)", "Tariff No",
    "Country of Origin", "Total Items Amount (Doc)", "Total Amount (Doc)",
    "Source PDF", "Source Page", "Extraction Status", "Error Details",
]


def _try_ocr_text(pdf_path: str) -> str:
    """Best-effort OCR fallback for scanned/no-text PDFs. Requires
    pytesseract + Tesseract OCR + pdf2image/poppler to be installed;
    degrades gracefully (returns '') if unavailable so the rest of the
    pipeline can still flag the file as unreadable rather than crashing."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        return ""
    try:
        text_parts = []
        images = convert_from_path(pdf_path, dpi=300)
        for img in images:
            text_parts.append(pytesseract.image_to_string(img))
        return "\n".join(text_parts)
    except Exception:
        return ""


def process_pdf(pdf_path: str, forced_vendor: str = "110002"):
    """Returns (rows: list[dict], log_entry: dict)"""
    filename = os.path.basename(pdf_path)
    log_entry = {
        "filename": filename,
        "vendor_code": forced_vendor,
        "extraction_datetime": dt.datetime.now().isoformat(timespec="seconds"),
        "records_extracted": 0,
        "validation_issues": 0,
        "processing_errors": [],
        "ocr_used": False,
    }
    rows = []

    template = VENDOR_TEMPLATES.get(forced_vendor)
    if template is None:
        log_entry["processing_errors"].append(f"No template registered for vendor {forced_vendor}")
        return rows, log_entry

    try:
        with open_pdf(pdf_path) as pdf:
            if len(pdf.pages) == 0:
                raise ValueError("PDF has zero pages")

            # Check whether page 1 actually has a text layer.
            probe_text = clean_text(pdf.pages[0])
            if len(probe_text.strip()) < 20:
                log_entry["ocr_used"] = True
                ocr_text = _try_ocr_text(pdf_path)
                if not ocr_text.strip():
                    log_entry["processing_errors"].append(
                        "No text layer detected and OCR unavailable/failed. "
                        "PDF may be scanned or corrupted. No rows extracted."
                    )
                    return rows, log_entry
                if not template.detect_vendor(ocr_text):
                    log_entry["processing_errors"].append(
                        "OCR text did not match vendor 110002 signature. Skipped."
                    )
                    return rows, log_entry
                log_entry["processing_errors"].append(
                    "OCR fallback used; per-line extraction from OCR text is not "
                    "implemented in this vendor-110002 module (text-based template "
                    "only). Manual review required for this file."
                )
                return rows, log_entry

            full_text_sample = "\n".join(
                clean_text(p) for p in pdf.pages[: min(3, len(pdf.pages))]
            )
            if not template.detect_vendor(full_text_sample):
                log_entry["processing_errors"].append(
                    "Vendor signature (issuer name / VAT ID / IBAN) did not match "
                    "vendor 110002. File skipped -- route to correct vendor template."
                )
                return rows, log_entry

            header = template.extract_header(pdf)
            items, unmatched, page_warnings = template.extract_line_items(pdf, filename)
            items = template.validate_items(items, filename)

            if page_warnings:
                log_entry["processing_errors"].extend(page_warnings)

            if unmatched:
                log_entry["processing_errors"].append(
                    f"{len(unmatched)} unrecognized line(s) in item-table region "
                    f"(first: page {unmatched[0][0]}: '{unmatched[0][1][:80]}')"
                )

            for it in items:
                if it.status != "OK":
                    log_entry["validation_issues"] += 1
                rows.append({
                    "Vendor Code": template.VENDOR_CODE,
                    "Vendor Name": template.VENDOR_NAME,
                    "Document Number": header.invoice_no,
                    "Document Date": header.invoice_date,
                    "SAP Order Number": it.order_number or header.order_number,
                    "Delivery Note Number": it.delivery_note or header.delivery_note_no,
                    "Customer Order Number (Header)": header.your_order_number,
                    "Item No": it.item_no,
                    "Conf Pos": it.conf_pos,
                    "Material/Item Code": it.article,
                    "Description": it.description.strip(),
                    "Quantity": it.qty,
                    "Unit of Measure": it.pu,
                    "Unit Price": it.unit_price,
                    "Line Amount": it.amount,
                    "Currency": header.currency,
                    "Your Order Ref (Line)": it.your_order_ref,
                    "Your Order Date (Line)": it.your_order_date,
                    "Tariff No": it.tariff_no,
                    "Country of Origin": it.country_of_origin,
                    "Total Items Amount (Doc)": header.total_items_amount,
                    "Total Amount (Doc)": header.total_amount,
                    "Source PDF": filename,
                    "Source Page": it.source_page,
                    "Extraction Status": it.status,
                    "Error Details": "; ".join(it.errors),
                })

            # Document-level reconciliation: sum of lines vs printed total
            if header.total_items_amount is not None:
                line_sum = round(sum(r["Line Amount"] for r in rows if r["Line Amount"] is not None), 2)
                if abs(line_sum - header.total_items_amount) > 0.02:
                    log_entry["processing_errors"].append(
                        f"Document total mismatch: sum of extracted lines = {line_sum}, "
                        f"printed 'Total items' = {header.total_items_amount}"
                    )

            log_entry["records_extracted"] = len(rows)

    except Exception as e:
        log_entry["processing_errors"].append(
            f"Unhandled error while reading/parsing this PDF: {type(e).__name__}: {e}. "
            f"File is likely corrupted, password-protected, or not a valid PDF."
        )

    return rows, log_entry


def process_many(pdf_paths: list[str], forced_vendor: str = "110002"):
    all_rows = []
    all_logs = []
    for path in pdf_paths:
        rows, log_entry = process_pdf(path, forced_vendor=forced_vendor)
        all_rows.extend(rows)
        all_logs.append(log_entry)
    return all_rows, all_logs
