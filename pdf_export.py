from io import BytesIO

from fpdf import FPDF
from PIL import Image

from resume_render import (
    HEADER_MODE_EVERY_PAGE,
    HEADER_MODE_FIRST_PAGE,
    HEADER_MODE_NONE,
    MAVEN_BAND_RGB,
    MAVEN_TEXT_RGB,
    OFFICIAL_HEADER_PNG,
    split_trailing_date,
)

_UNICODE_REPLACEMENTS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "•": "-",
    "…": "...",
}


def _sanitize(text: str) -> str:
    for src, dst in _UNICODE_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


class _BrandedPDF(FPDF):
    """Overriding header() makes fpdf2 draw the banner automatically on whichever
    pages header_mode calls for - every page, only the first, or none at all -
    rather than needing to redraw it manually at each call site."""

    header_mode = HEADER_MODE_EVERY_PAGE

    def header(self) -> None:
        if self.header_mode == HEADER_MODE_NONE:
            return
        if self.header_mode == HEADER_MODE_FIRST_PAGE and self.page_no() != 1:
            return
        if not OFFICIAL_HEADER_PNG:
            return
        img = Image.open(BytesIO(OFFICIAL_HEADER_PNG))
        width_mm = 190
        height_mm = width_mm * img.height / img.width
        self.image(BytesIO(OFFICIAL_HEADER_PNG), x=10, y=10, w=width_mm)
        self.set_xy(10, 10 + height_mm + 4)


def build_pdf(sections: list, approximate_layout: bool = False, header_mode: str = HEADER_MODE_EVERY_PAGE) -> bytes:
    maven_styling = header_mode != HEADER_MODE_NONE
    pdf = _BrandedPDF(format="A4")
    pdf.header_mode = header_mode
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    for section in sections:
        pdf.set_font("Helvetica", "B", 13)
        if maven_styling:
            pdf.set_fill_color(*MAVEN_BAND_RGB)
            pdf.set_text_color(*MAVEN_TEXT_RGB)
            pdf.set_x(15)
            pdf.cell(180, 8, _sanitize(section["title"]), ln=1, fill=True)
            pdf.set_draw_color(*MAVEN_TEXT_RGB)
        else:
            pdf.set_text_color(15, 15, 15)
            pdf.set_x(15)
            pdf.cell(0, 8, _sanitize(section["title"]), ln=1)
            pdf.set_draw_color(150, 150, 150)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(2)

        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(40, 40, 40)
        for line in section["lines"]:
            text = _sanitize(str(line))
            split = split_trailing_date(text) if approximate_layout else None
            if split:
                left, right = (_sanitize(part) for part in split)
                x_start, y_start = pdf.get_x(), pdf.get_y()
                right_width = 45
                left_width = 190 - right_width
                pdf.set_x(15)
                pdf.multi_cell(left_width, 6, left)
                y_after = pdf.get_y()
                pdf.set_xy(15 + left_width, y_start)
                pdf.cell(right_width, 6, right, align="R")
                pdf.set_xy(x_start, max(y_after, y_start + 6))
            elif text.startswith("- "):
                pdf.set_x(20)
                pdf.multi_cell(0, 6, "· " + text[2:].strip())
            else:
                pdf.set_x(15)
                pdf.multi_cell(0, 6, text)
        pdf.ln(4)

    return bytes(pdf.output())
