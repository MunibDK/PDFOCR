"""
vendor_110002.py
Vendor-specific extraction template for VENDOR CODE 110002.

Vendor identity (used for auto-detection, since "110002" itself is an
internal SAP vendor code and never appears verbatim on the vendor's own
PDF): the invoice is issued by "dormakaba Deutschland GmbH", DORMA Platz 1,
D-58256 Ennepetal, VAT ID DE126455151, IBAN DE59330700900431580000.
Any two of {issuer name, VAT ID, IBAN} matching is treated as a confirmed
vendor-110002 document.

Document type: commercial export invoice, one or more pages, one line-item
table that continues across page breaks. Repeated page header/footer
boilerplate is cropped out geometrically (not by text-pattern guessing),
using the y-position of the "Item ... Amount USD" column header and the
y-position of the small-font footer block ("dormakaba Deutschland GmbH
Gesellschaftsform Bankverbindung...") on every page.

Each line item is built from a variable-length metadata block followed by
the item row itself, then a "Conf. Pos." line (possibly continuing the
description), possibly further wrapped description lines, and finally an
"Item value....... <amount>" line that must match the item row's amount.

Metadata lines preceding an item (0 or 1 of each, in this order when present):
    Order number <sap order no>
    Delivery note <delivery note no>
    Your Order <customer po ref> of <DD.MM.YYYY>
    Tariff No.: <hs code>
    Country of origin: <ISO2>

None of these five fields are guaranteed present for every item -- some
items rely on the invoice header's order number instead (e.g. the very
first line item on the sample invoice has no per-item Order number/
Delivery note because it uses the header-level Order Number directly).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from pdf_core import clean_text

VENDOR_CODE = "110002"
VENDOR_NAME = "dormakaba Deutschland GmbH"

IDENTITY_MARKERS = [
    "dormakaba Deutschland GmbH",
    "DE126455151",          # Ust-IdNr without spaces
    "DE59330700900431580000",  # IBAN
]

# ---- regex patterns -------------------------------------------------------

RE_ORDER_NUMBER = re.compile(r"^Order number\s+(\S+)$")
RE_DELIVERY_NOTE = re.compile(r"^Delivery note\s+(\S+)$")
RE_YOUR_ORDER = re.compile(r"^Your Order\s+(\S+)\s+of\s+(\d{2}\.\d{2}\.\d{4})$")
RE_TARIFF = re.compile(r"^Tariff No\.:\s*(\S+)$")
RE_COUNTRY = re.compile(r"^Country of origin:\s*(\S+)$")
RE_CONF_POS = re.compile(r"^Conf\.\s*Pos\.\s*(\d+)\s*(.*)$")
RE_ITEM_VALUE = re.compile(r"^Item value\.+\s*([\d.,]+)$")
RE_TOTAL_ITEMS = re.compile(r"^Total items\s+([\d.,]+)$", re.MULTILINE)
RE_TABLE_HEADER_ECHO = re.compile(
    r"^Item Article Order\.qty PU Description Unit price USD Amount USD$"
)
# Known boundary artifact: our own table-region crop occasionally includes one
# pixel-row of the footer boilerplate (font height differs from body text just
# enough to sit on the crop edge). Harmless, but filtered so logs stay clean.
RE_FOOTER_ECHO = re.compile(
    r"^dormakaba Deutschland GmbH Gesellschaftsform Bankverbindung"
)

# Loose pattern: just "short number + something" at line start -- used only
# to detect item-row-*shaped* lines that failed the strict RE_ITEM_ROW match,
# so they can be surfaced as flagged partial records instead of silently
# dropped as unrecognized text.
RE_ITEM_ROW_LOOSE = re.compile(r"^\d{1,4}\s+\S+")

# Item row: <item_no> <article> <qty> <PU> <description...> <unit_price> <amount>
RE_ITEM_ROW = re.compile(
    r"^(?P<item_no>\d{1,4})\s+"
    r"(?P<article>\S+)\s+"
    r"(?P<qty>[\d.,]+)\s+"
    r"(?P<pu>[A-Z]{2,4})\s+"
    r"(?P<desc>.+?)\s+"
    r"(?P<unit_price>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
    r"(?P<amount>\d{1,3}(?:\.\d{3})*,\d{2})$"
)

# Header field patterns (page 1, above the table)
RE_INVOICE_NO = re.compile(r"Invoice No\.\s*(\d+)")
RE_INVOICE_DATE = re.compile(r"Invoice-date\s*(\d{2}\.\d{2}\.\d{4})")
RE_ORDER_NO_HDR = re.compile(r"Order Number\s*(\d+)")
RE_DELIVERY_NOTE_HDR = re.compile(r"Delivery-note No\.\s*(\d+)")
RE_YOUR_ORDER_NO_HDR = re.compile(r"Your order number\s*(\d+)")
RE_ORDER_DATE_HDR = re.compile(r"Order date\s*(\d{2}\.\d{2}\.\d{4})")
RE_CUSTOMER = re.compile(r"Customer\s+(\d+)")
RE_MARKETING_AREA = re.compile(r"Marketing area\s+(\S+)")


def eu_number_to_float(s: str) -> Optional[float]:
    """Convert '1.234,56' or '188,48' -> float. Returns None if unparseable."""
    if s is None:
        return None
    s = s.strip()
    if not re.match(r"^\d{1,3}(\.\d{3})*(,\d+)?$|^\d+(,\d+)?$", s):
        return None
    return float(s.replace(".", "").replace(",", "."))


def valid_date(s: str) -> bool:
    if not s:
        return False
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", s)
    if not m:
        return False
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return 1 <= d <= 31 and 1 <= mo <= 12 and 1900 <= y <= 2100


# ---- table region detection ------------------------------------------------

def get_table_bbox(page):
    """Return (header_bottom_y, footer_top_y) for the item-table region on
    this page, using visual layout rather than guessed text patterns."""
    words = page.extract_words()
    header_bottom = None
    footer_top = None
    for w in words:
        if w["text"] == "Item" and w["x0"] < 100:
            header_bottom = w["bottom"]
            break
    for w in words:
        if (
            w["text"] == "dormakaba"
            and w["height"] < 7
            and (header_bottom is None or w["top"] > header_bottom)
        ):
            footer_top = w["top"]
            break
    return header_bottom, footer_top


# ---- data model -------------------------------------------------------------

@dataclass
class LineItem:
    item_no: Optional[str] = None
    article: Optional[str] = None
    qty: Optional[float] = None
    qty_raw: Optional[str] = None
    pu: Optional[str] = None
    description: str = ""
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    item_value_amount: Optional[float] = None
    conf_pos: Optional[str] = None
    order_number: Optional[str] = None
    delivery_note: Optional[str] = None
    your_order_ref: Optional[str] = None
    your_order_date: Optional[str] = None
    tariff_no: Optional[str] = None
    country_of_origin: Optional[str] = None
    source_page: Optional[int] = None
    status: str = "OK"
    errors: list = field(default_factory=list)

    def flag(self, msg):
        self.status = "FLAGGED"
        self.errors.append(msg)


@dataclass
class InvoiceHeader:
    invoice_no: Optional[str] = None
    invoice_date: Optional[str] = None
    order_number: Optional[str] = None
    delivery_note_no: Optional[str] = None
    your_order_number: Optional[str] = None
    order_date: Optional[str] = None
    customer_no: Optional[str] = None
    marketing_area: Optional[str] = None
    currency: str = "USD"
    total_items_amount: Optional[float] = None
    total_net: Optional[float] = None
    total_amount: Optional[float] = None


def detect_vendor(full_text: str) -> bool:
    """Return True if this PDF matches the vendor-110002 signature."""
    hits = sum(1 for marker in IDENTITY_MARKERS if marker.replace(" ", "") in full_text.replace(" ", ""))
    return hits >= 2


def extract_header(pdf) -> InvoiceHeader:
    page1 = pdf.pages[0]
    hb, _ = get_table_bbox(page1)
    header_text = clean_text(page1, (0, 0, page1.width, hb)) if hb else clean_text(page1)

    def find(rx, text=header_text):
        m = rx.search(text)
        return m.group(1) if m else None

    hdr = InvoiceHeader(
        invoice_no=find(RE_INVOICE_NO),
        invoice_date=find(RE_INVOICE_DATE),
        order_number=find(RE_ORDER_NO_HDR),
        delivery_note_no=find(RE_DELIVERY_NOTE_HDR),
        your_order_number=find(RE_YOUR_ORDER_NO_HDR),
        order_date=find(RE_ORDER_DATE_HDR),
        customer_no=find(RE_CUSTOMER),
        marketing_area=find(RE_MARKETING_AREA),
    )

    # Totals appear on whichever page contains the last line item -- not
    # necessarily the physically last page (trailing pages can be pure
    # boilerplate/export declaration with no numbers at all). Scan pages
    # from the end until a "Total items" line is found.
    for page in reversed(pdf.pages):
        hb2, ft2 = get_table_bbox(page)
        if not (hb2 and ft2):
            continue
        tail_text = clean_text(page, (0, hb2, page.width, ft2))
        m = RE_TOTAL_ITEMS.search(tail_text)
        if not m:
            continue
        hdr.total_items_amount = eu_number_to_float(m.group(1))
        m2 = re.search(r"Total USD\s*\n?([\d.,]+)\s+([\d.,]+)", tail_text)
        if m2:
            hdr.total_net = eu_number_to_float(m2.group(1))
            hdr.total_amount = eu_number_to_float(m2.group(2))
        break
    return hdr


def extract_line_items(pdf, source_filename: str):
    """Walk every page's cropped item-table region and reconstruct line
    items with a small state machine, carrying wrapped description lines
    and per-item metadata blocks correctly across page breaks."""
    items: list[LineItem] = []
    pending = {
        "order_number": None,
        "delivery_note": None,
        "your_order_ref": None,
        "your_order_date": None,
        "tariff_no": None,
        "country_of_origin": None,
    }
    current: Optional[LineItem] = None
    unmatched_lines = []
    page_warnings = []

    for page_index, page in enumerate(pdf.pages, start=1):
        hb, ft = get_table_bbox(page)
        if hb is None:
            # Could not locate the column-header anchor at all: this page has
            # no recognizable item table (e.g. a pure cover/signature page).
            # Skip it -- but note the skip rather than silently dropping it.
            page_warnings.append(
                f"Page {page_index}: no item-table header anchor found; page skipped."
            )
            continue
        if ft is None:
            # Header anchor found but footer anchor missing (e.g. last page of
            # a truncated/synthetic file, or a footer style variant). Falling
            # back to the full page bottom is safer than silently dropping
            # every item on this page -- worst case we pick up a few extra
            # boilerplate lines, which the FSM below already tolerates.
            ft = page.height
            page_warnings.append(
                f"Page {page_index}: footer boundary not detected, used page "
                f"bottom as fallback (verify no boilerplate leaked into data)."
            )
        text = clean_text(page, (0, hb, page.width, ft))
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if RE_TABLE_HEADER_ECHO.match(line):
                continue  # our own crop occasionally re-includes this row
            if RE_FOOTER_ECHO.match(line):
                continue  # our own crop occasionally re-includes this row
            if RE_TOTAL_ITEMS.match(line):
                break  # end of item table reached

            m = RE_ORDER_NUMBER.match(line)
            if m:
                pending["order_number"] = m.group(1)
                continue
            m = RE_DELIVERY_NOTE.match(line)
            if m:
                pending["delivery_note"] = m.group(1)
                continue
            m = RE_YOUR_ORDER.match(line)
            if m:
                pending["your_order_ref"], pending["your_order_date"] = m.group(1), m.group(2)
                continue
            m = RE_TARIFF.match(line)
            if m:
                pending["tariff_no"] = m.group(1)
                continue
            m = RE_COUNTRY.match(line)
            if m:
                pending["country_of_origin"] = m.group(1)
                continue

            # A line that looks like it's trying to be an item row (starts
            # with a short numeric position, e.g. "10 ...") but doesn't fully
            # match the strict pattern -- almost always a missing/garbled
            # field (qty, PU, or price). Surface it as a flagged partial
            # record rather than silently dropping it into unmatched_lines.
            if RE_ITEM_ROW_LOOSE.match(line) and not RE_ITEM_ROW.match(line):
                partial = LineItem(source_page=page_index, **pending)
                pending = {k: None for k in pending}
                partial.description = line
                partial.flag(
                    "Item row did not match expected format (missing/garbled "
                    "quantity, unit of measure, unit price, or amount). Raw "
                    "line preserved in Description for manual review."
                )
                items.append(partial)
                continue

            m = RE_ITEM_ROW.match(line)
            if m:
                current = LineItem(
                    item_no=m.group("item_no"),
                    article=m.group("article"),
                    qty_raw=m.group("qty"),
                    qty=eu_number_to_float(m.group("qty")),
                    pu=m.group("pu"),
                    description=m.group("desc"),
                    unit_price=eu_number_to_float(m.group("unit_price")),
                    amount=eu_number_to_float(m.group("amount")),
                    source_page=page_index,
                    **pending,
                )
                pending = {k: None for k in pending}
                continue

            m = RE_CONF_POS.match(line)
            if m and current is not None:
                current.conf_pos = m.group(1)
                extra = m.group(2).strip()
                if extra:
                    current.description += " " + extra
                continue

            m = RE_ITEM_VALUE.match(line)
            if m and current is not None:
                current.item_value_amount = eu_number_to_float(m.group(1))
                items.append(current)
                current = None
                continue

            # Anything else while an item is open is a wrapped description line.
            if current is not None:
                current.description += " " + line
                continue

            unmatched_lines.append((page_index, line))
        else:
            continue
        break  # we hit RE_TOTAL_ITEMS -> stop scanning further pages' tables

    if current is not None:
        # Item never closed with an "Item value" line -> unreadable/incomplete.
        current.flag("Item block did not terminate with an 'Item value' line "
                      "(possible page-break corruption or truncated PDF).")
        items.append(current)

    return items, unmatched_lines, page_warnings


def validate_items(items: list[LineItem], source_pdf: str):
    seen_signatures = {}
    for it in items:
        if not it.article:
            it.flag("Missing Material/Item Code")
        if it.qty is None or it.qty <= 0:
            it.flag(f"Invalid or missing quantity: '{it.qty_raw}'")
        if it.unit_price is None:
            it.flag("Missing/unparseable unit price")
        if it.amount is None:
            it.flag("Missing/unparseable line amount")
        if it.your_order_date and not valid_date(it.your_order_date):
            it.flag(f"Invalid 'Your Order' date: '{it.your_order_date}'")

        # Cross-check unit_price * qty ~= amount
        if it.qty is not None and it.unit_price is not None and it.amount is not None:
            expected = round(it.qty * it.unit_price, 2)
            if abs(expected - it.amount) > 0.02:
                it.flag(
                    f"Qty*UnitPrice ({expected:.2f}) does not match Line Amount "
                    f"({it.amount:.2f})"
                )

        # Cross-check the item row's amount against the "Item value" line
        if it.amount is not None and it.item_value_amount is not None:
            if abs(it.amount - it.item_value_amount) > 0.01:
                it.flag(
                    f"Item row amount ({it.amount}) does not match "
                    f"'Item value' line ({it.item_value_amount})"
                )
        elif it.item_value_amount is None:
            it.flag("No matching 'Item value' confirmation line found")

        # Duplicate detection: identical item_no+article+qty+amount appearing twice
        sig = (it.item_no, it.article, it.qty, it.amount)
        if sig in seen_signatures:
            it.flag(f"Duplicate of item at row with same Item/Article/Qty/Amount "
                     f"(first seen: page {seen_signatures[sig]})")
        else:
            seen_signatures[sig] = it.source_page

    return items
