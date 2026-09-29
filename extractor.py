"""Sends invoice / stocktake photos or PDFs to Claude and returns structured extractions."""

import base64
import os
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel

import cost_tracker
import storage

MODEL = "claude-sonnet-5-5"

MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "pdf": "application/pdf",
}

INVOICE_SYSTEM_PROMPT = """You are helping a club storeman digitise supplier invoices.

Read the attached invoice image or PDF carefully and extract its details.

Rules you must follow exactly:
- Never invent or guess a number. If you cannot read a value confidently
  (blurry, cut off, handwritten and unclear, or genuinely absent), leave
  that field as null and set "check" to true on that line item with a
  short plain-English "check_reason" explaining what's wrong.
- Only report a line item's numbers if you can actually see them on the
  invoice. Do not calculate a missing unit_price or line_total by
  guessing - leave it null and flag it instead.
- "department" is your best judgement of whether this invoice is mostly
  bar stock, kitchen/catering stock, other (cleaning, office, etc.), or
  a mix of these. Use "unclear" if you genuinely can't tell.
- Dates should be returned as YYYY-MM-DD when you can read them clearly,
  otherwise null.
- Keep "description" to just the product name (e.g. "Chicken Nuggets
  Tempura Breast"). Drop the supplier's internal product code (e.g.
  "CNT6KG"), pack-size multipliers (e.g. "6 x 1kg"), and phrases like
  "(Ctn Only)" - that information already belongs in "qty" and "unit".
  Still read qty/unit/prices exactly as printed; only the description
  text should be shortened.
- Use extraction_notes for anything else worth flagging that doesn't fit
  a specific field (e.g. "invoice appears to be page 1 of 2").
"""

STOCKTAKE_SYSTEM_PROMPT = """You are helping a club storeman digitise a physical stocktake
count sheet - a list of stock items with a counted quantity or weight written
next to each one, often by hand.

Read the attached photo or PDF carefully and extract every item and its
counted amount.

Rules you must follow exactly:
- Never invent or guess a count. If a counted quantity is missing, crossed
  out, illegible, or you genuinely can't tell what was written, leave
  counted_qty as null and set "check" to true with a short plain-English
  "check_reason".
- Some items on club stocktake sheets are unlabelled or the handwriting is
  unclear - if you can't confidently read the item description itself,
  still include the row with your best guess at the description, but set
  "check" to true and explain why in check_reason.
- "department" is your best judgement for the whole sheet: bar,
  kitchen/catering, other, mixed, or unclear if you can't tell.
- Keep "description" to just the product name, dropping any internal
  product code or pack-size multiplier already captured by "unit" -
  matching the wording used on the product's own supplier invoices
  where possible, since that's what price/value matching is based on.
- Use extraction_notes for anything worth flagging that doesn't fit a
  specific field (e.g. "sheet appears to be page 2 of 3").
"""


class LineItem(BaseModel):
    description: str
    qty: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    check: bool = False
    check_reason: Optional[str] = None


class InvoiceExtraction(BaseModel):
    supplier: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    department: Literal["bar", "kitchen", "other", "mixed", "unclear"] = "unclear"
    gst: Optional[float] = None
    invoice_total: Optional[float] = None
    line_items: List[LineItem] = []
    extraction_notes: Optional[str] = None


class StocktakeItem(BaseModel):
    description: str
    counted_qty: Optional[float] = None
    unit: Optional[str] = None
    check: bool = False
    check_reason: Optional[str] = None


class StocktakeExtraction(BaseModel):
    department: Literal["bar", "kitchen", "other", "mixed", "unclear"] = "unclear"
    items: List[StocktakeItem] = []
    extraction_notes: Optional[str] = None


class CostCapExceeded(Exception):
    pass


def _sniff_media_type(file_bytes: bytes) -> Optional[str]:
    """Detects the real file type from its bytes - filenames/extensions can lie
    (e.g. a phone photo saved as .png that's actually JPEG-encoded)."""
    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if file_bytes.startswith(b"%PDF"):
        return "application/pdf"
    return None


def _extract(file_bytes: bytes, filename: str, kind: str, system_prompt: str, output_model, instruction: str):
    if cost_tracker.is_over_cap():
        raise CostCapExceeded(
            f"Monthly API cost cap (${cost_tracker.get_monthly_cap():.2f}) reached. "
            "Raise MONTHLY_COST_CAP_USD in .env to continue this month, or wait "
            "until next month."
        )

    media_type = _sniff_media_type(file_bytes)
    if media_type is None:
        ext = filename.rsplit(".", 1)[-1].lower()
        media_type = MEDIA_TYPES.get(ext)
    if media_type is None:
        raise ValueError(f"Unsupported or unrecognised file type: {filename}")

    encoded = base64.standard_b64encode(file_bytes).decode("utf-8")
    block_type = "document" if media_type == "application/pdf" else "image"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=8000,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": block_type,
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            output_format=output_model,
        )
    except Exception:
        storage.log_api_call(kind, filename, 0, 0, 0.0, success=False)
        raise

    cost = cost_tracker.record_usage(response.usage.input_tokens, response.usage.output_tokens)
    storage.log_api_call(kind, filename, response.usage.input_tokens, response.usage.output_tokens, cost)

    return response.parsed_output, cost


def extract_invoice(file_bytes: bytes, filename: str) -> tuple[InvoiceExtraction, float]:
    """Extracts invoice data from an image or PDF's raw bytes.

    Returns (extraction, cost_of_this_call_usd). Raises CostCapExceeded if
    the monthly spend cap has already been reached.
    """
    return _extract(
        file_bytes, filename, "invoice", INVOICE_SYSTEM_PROMPT, InvoiceExtraction, "Extract this invoice's details."
    )


def extract_stocktake(file_bytes: bytes, filename: str) -> tuple[StocktakeExtraction, float]:
    """Extracts counted items from a photographed/PDF stocktake sheet.

    Returns (extraction, cost_of_this_call_usd). Raises CostCapExceeded if
    the monthly spend cap has already been reached.
    """
    return _extract(
        file_bytes,
        filename,
        "stocktake",
        STOCKTAKE_SYSTEM_PROMPT,
        StocktakeExtraction,
        "Extract every counted item on this stocktake sheet.",
    )
