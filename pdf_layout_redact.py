import fitz

from redactor import (
    RedactionCounts,
    _apply_regex_redactions,
    find_contract_spans,
    find_identity_block,
    find_level2_spans,
    find_personal_spans,
    find_regex_spans,
)
from resume_render import (
    HEADER_MODE_EVERY_PAGE,
    HEADER_MODE_FIRST_PAGE,
    HEADER_MODE_NONE,
    OFFICIAL_HEADER_PNG,
    OFFICIAL_HEADER_SIZE,
)

# Generous fixed floor for the header clearance shift - covers a name plus a
# multi-line contact/tagline block on its own, with no reliance on per-document
# detection. Identity-block detection can only ever push the shift higher than
# this, never lower, so a detection miss still leaves a safe default in place.
# This only ever adds blank vertical space above the (already-redacted) content
# by shifting the whole page down - it never paints over or erases content,
# since content that needed redacting was already tagged in place by
# _redact_and_brand before this runs.
MIN_HEADER_SHIFT_PT = 130

TOKEN_TO_FIELD = {
    "[REDACTED-NAME]": "names",
    "[REDACTED-ADDRESS]": "addresses",
    "[REDACTED-EMAIL]": "emails",
    "[REDACTED-PHONE]": "phones",
    "[REDACTED-URL]": "urls",
    "[REDACTED-ID]": "ids",
    "[REDACTED-DATE]": "dates",
    "[REDACTED-LOCATION]": "locations",
    "[REDACTED-COMPENSATION]": "compensation",
}


def _group_wrapped_rects(rects: list) -> list:
    """Group rects that are vertically adjacent (consecutive wrapped lines of the same
    logical match) so a single occurrence doesn't get its replacement text repeated once
    per physical line. Rects far apart vertically are genuinely separate occurrences."""
    if not rects:
        return []
    ordered = sorted(rects, key=lambda r: (round(r.y0, 1), r.x0))
    groups = [[ordered[0]]]
    for rect in ordered[1:]:
        prev = groups[-1][-1]
        gap = rect.y0 - prev.y1
        if -2 <= gap <= prev.height * 1.5:
            groups[-1].append(rect)
        else:
            groups.append([rect])
    return groups


def _available_extra_height(page, rect, max_extra: float = 48.0) -> float:
    """How far a replacement box can safely extend below its original rect before
    reaching the next line of content on the page, so a replacement that needs more
    room than the span it replaces (e.g. a multi-part university tier label swapped
    in for a short date, or a free-text generalization) never grows into and erases
    whatever comes after it."""
    window = fitz.Rect(0, rect.y1 + 1, page.rect.width, rect.y1 + 1 + max_extra)
    blocks = page.get_text("blocks", clip=window)
    if not blocks:
        return max_extra
    nearest_top = min(b[1] for b in blocks)
    return max(0.0, nearest_top - rect.y1 - 2)


def _available_extra_width(page, rect, max_extra: float) -> float:
    """How far a replacement box can safely extend rightward from its original rect
    before reaching the next content on the same line (e.g. the degree text right
    after an institution name, or a date column further along the row), so growing
    to fit longer text never grows into and covers that content."""
    if max_extra <= 0:
        return 0.0
    window = fitz.Rect(rect.x1 + 1, rect.y0, rect.x1 + 1 + max_extra, rect.y1)
    blocks = page.get_text("blocks", clip=window)
    if not blocks:
        return max_extra
    nearest_left = min(b[0] for b in blocks)
    return max(0.0, nearest_left - rect.x1 - 2)


def _draw_fitted_text(page, rect, text: str, fill=(0.82, 0.82, 0.82), right_margin: float = 36.0) -> None:
    """Draw `text` into a box grown from rect's position as needed to fit it - width
    first (up to whatever blank space precedes the next content on the same line),
    then height (up to whatever blank space precedes the next content below),
    shrinking the font only as a last resort. Used as a fallback for free-text
    replacements too long to fit inline at the standard size (e.g. a multi-part
    university tier label, or a Level 2 generalization), so one is always fully
    drawn rather than silently dropped for not fitting the original span's rect, and
    never grows into and covers other content in the process. Tries the smallest box
    first and grows only as far as PyMuPDF's own layout reports is actually needed,
    so the result hugs the replacement instead of leaving a big empty-looking filled
    block."""
    max_extra_width = max(0.0, page.rect.width - right_margin - rect.x1)
    max_width = rect.width + _available_extra_width(page, rect, max_extra_width)
    max_height = max(rect.height, rect.height + _available_extra_height(page, rect))
    height_steps = sorted({rect.height, rect.height * 2, rect.height * 3, max_height})
    height_steps = [h for h in height_steps if h <= max_height] or [max_height]

    for fontsize in (8, 7, 6, 5, 4.5, 4):
        for height in height_steps:
            box = fitz.Rect(rect.x0, rect.y0, rect.x0 + max_width, rect.y0 + height)
            page.draw_rect(box, color=None, fill=fill)
            if page.insert_textbox(box, text, fontsize=fontsize, fontname="helv") >= 0:
                return
    # Smallest font, largest box already drawn above as the last attempt - whatever
    # fits there is the final result, so text is never simply left undrawn.


def _identity_block_bottom(page, identity_texts: list) -> float:
    """How far down page 1 the candidate's own name/contact block extends, found
    from the exact spans already identified for redaction - not guessed - so the
    white-out region is sized to this specific document, not a fixed assumption."""
    max_y = 0.0
    for span_text in identity_texts:
        if not span_text.strip():
            continue
        for rect in page.search_for(span_text):
            if rect.y0 < 200:
                max_y = max(max_y, rect.y1)
    return max_y


def _apply_header_mode(
    doc: "fitz.Document", identity_block_bottom: float, header_mode: str
) -> "fitz.Document":
    """Apply header_mode to the redacted document. Every span that needed redacting
    (identity block included) has already been replaced with its visible [REDACTED-X]
    tag by _redact_and_brand before this runs - this function only ever repositions
    page content to make room for the fixed Maven header image, it never paints over
    or blanks any region, since doing so would erase legitimate tags/text (e.g. the
    Summary section) rather than actually redacting anything:
    - none: no logo, no shift - page is left exactly as already redacted.
    - first_page: page 1 is shifted down and branded; every other page is left
      completely untouched (original layout, no shift, no image).
    - every_page: every page is shifted down and branded.
    The image is never extracted from the source document - always this one
    stored asset."""
    if header_mode == HEADER_MODE_NONE or not OFFICIAL_HEADER_PNG:
        return doc

    page0 = doc[0]
    width = page0.rect.width
    header_height = width * OFFICIAL_HEADER_SIZE[1] / OFFICIAL_HEADER_SIZE[0]
    page1_shift = max(MIN_HEADER_SHIFT_PT, header_height, identity_block_bottom + 4)

    new_doc = fitz.open()
    for i in range(len(doc)):
        page = doc[i]
        if header_mode == HEADER_MODE_FIRST_PAGE and i > 0:
            new_doc.insert_pdf(doc, from_page=i, to_page=i)
            continue
        shift = page1_shift if i == 0 else header_height
        new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height + shift)
        new_page.show_pdf_page(
            fitz.Rect(0, shift, page.rect.width, page.rect.height + shift), doc, i
        )
        new_page.insert_image(fitz.Rect(0, 0, width, header_height), stream=OFFICIAL_HEADER_PNG)

    doc.close()
    return new_doc


def _redact_and_brand(
    doc: "fitz.Document",
    unique_spans: list,
    identity_block_texts: list,
    header_mode: str = HEADER_MODE_EVERY_PAGE,
) -> tuple:
    """Apply span-based redactions across every page, then apply header_mode. Shared
    by the resume and contract/letter true-layout pipelines.
    Returns (redacted_pdf_bytes, RedactionCounts, redacted_text, page_preview_pngs)."""
    identity_bottom = _identity_block_bottom(doc[0], identity_block_texts)

    counts = RedactionCounts()
    for page in doc:
        deferred = []
        for span_text, replacement in unique_spans:
            if not span_text.strip():
                continue
            rects = page.search_for(span_text)
            if not rects:
                continue
            field = TOKEN_TO_FIELD.get(replacement, "other")
            is_free_text = not (replacement.startswith("[REDACTED-") and replacement.endswith("]"))
            for group in _group_wrapped_rects(rects):
                combined = group[0]
                for r in group[1:]:
                    combined |= r
                fits_inline = (
                    not is_free_text
                    or fitz.get_text_length(replacement, fontname="helv", fontsize=8)
                    <= combined.width
                )
                if fits_inline:
                    for i, rect in enumerate(group):
                        page.add_redact_annot(
                            rect,
                            text=replacement if i == 0 else "",
                            fontsize=8,
                            fill=(0.82, 0.82, 0.82),
                        )
                else:
                    for rect in group:
                        page.add_redact_annot(rect, fill=(0.82, 0.82, 0.82))
                    deferred.append((combined, replacement))
                setattr(counts, field, getattr(counts, field) + 1)
        page.apply_redactions()
        for rect, replacement in deferred:
            _draw_fitted_text(page, rect, replacement)

    doc = _apply_header_mode(doc, identity_bottom, header_mode)

    redacted_text = "\n".join(page.get_text() for page in doc)
    previews = [page.get_pixmap(dpi=150).tobytes("png") for page in doc]
    out_bytes = doc.tobytes()
    doc.close()

    return out_bytes, counts, redacted_text, previews


def redact_pdf_in_place(
    pdf_bytes: bytes, client, model: str, level: int = 1, header_mode: str = HEADER_MODE_EVERY_PAGE
) -> tuple:
    """Redact sensitive spans directly on the original PDF pages, leaving every
    untouched region (fonts, body layout, bullets, right-aligned dates) byte-for-byte
    as designed. Level 1 replaces spans with fixed tokens; Level 2 additionally
    redacts dates, workplace locations, and generalizes identifying details in place,
    using the same page geometry. header_mode controls the fixed official Maven
    header image's placement, regardless of what was there in the source.
    Returns (redacted_pdf_bytes, RedactionCounts, redacted_text, page_preview_pngs)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    pre_redacted = _apply_regex_redactions(full_text)

    entity_spans = (
        find_level2_spans(client, pre_redacted, model)
        if level == 2
        else find_personal_spans(client, pre_redacted, model)
    )
    identity_block_texts = find_identity_block(client, pre_redacted, model)
    identity_spans = [(s, "[REDACTED-NAME]") for s in identity_block_texts]
    spans = find_regex_spans(full_text) + entity_spans + identity_spans
    unique_spans = sorted(set(spans), key=lambda s: len(s[0]), reverse=True)

    return _redact_and_brand(doc, unique_spans, identity_block_texts, header_mode)


def redact_contract_pdf_in_place(
    pdf_bytes: bytes, client, model: str, header_mode: str = HEADER_MODE_EVERY_PAGE
) -> tuple:
    """Redact all identifying information (all parties, addresses, dates, signatures,
    contact info, reference numbers, compensation figures) directly on the original
    contract/letter pages, preserving the rest of the document's layout exactly.
    header_mode controls the fixed official Maven header image's placement.
    Returns (redacted_pdf_bytes, RedactionCounts, redacted_text, page_preview_pngs)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text() for page in doc)
    pre_redacted = _apply_regex_redactions(full_text)

    entity_spans = find_contract_spans(client, pre_redacted, model)
    identity_block_texts = find_identity_block(client, pre_redacted, model)
    identity_spans = [(s, "[REDACTED-NAME]") for s in identity_block_texts]
    spans = find_regex_spans(full_text) + entity_spans + identity_spans
    unique_spans = sorted(set(spans), key=lambda s: len(s[0]), reverse=True)

    return _redact_and_brand(doc, unique_spans, identity_block_texts, header_mode)
