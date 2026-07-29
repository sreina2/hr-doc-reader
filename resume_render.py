import base64
import re
from pathlib import Path

# Fixed official brand asset - always used verbatim, never regenerated or approximated.
# Resolved relative to this file so it works regardless of the process's working
# directory (Streamlit's launcher runs with a different cwd than this package).
OFFICIAL_HEADER_PATH = Path(__file__).resolve().parent / "official-maven-header.png"
OFFICIAL_HEADER_PNG = (
    OFFICIAL_HEADER_PATH.read_bytes() if OFFICIAL_HEADER_PATH.exists() else None
)
# Real pixel dimensions of the asset above, used to compute its rendered aspect ratio.
OFFICIAL_HEADER_SIZE = (1190, 135)

# Maven's actual palette, sampled from the official header asset - used whenever a
# header mode below is active, to recolor section headers/rules/bands to match,
# rather than leaving them in a generic neutral style.
MAVEN_BAND_RGB = (223, 235, 224)
MAVEN_TEXT_RGB = (30, 45, 40)

# Three explicit, mutually exclusive header states - replaces the old single on/off
# toggle, which never actually controlled whether the logo rendered at all.
HEADER_MODE_NONE = "none"
HEADER_MODE_FIRST_PAGE = "first_page"
HEADER_MODE_EVERY_PAGE = "every_page"

HEADER_MODE_LABELS = {
    HEADER_MODE_NONE: "No formatting",
    HEADER_MODE_FIRST_PAGE: "First page Maven header only",
    HEADER_MODE_EVERY_PAGE: "Every page Maven header only",
}

_DATE_TRAIL_RE = re.compile(
    r"(?P<right>(\[REDACTED-DATE\]|Present)(\s*[-–—]\s*(\[REDACTED-DATE\]|Present))?)\s*$"
)


def split_trailing_date(line: str):
    """Best-effort split of a line like 'Title, Company [REDACTED-DATE] - Present'
    into (left, right) so the date portion can be rendered right-aligned, matching
    the original resume's left/right layout. Returns None if no trailing date found."""
    match = _DATE_TRAIL_RE.search(line)
    if not match:
        return None
    right = match.group("right").strip()
    left = line[: match.start()].rstrip(" -–—,")
    if not left:
        return None
    return left, right


def sections_to_html(sections: list, approximate_layout: bool = False, header_mode: str = HEADER_MODE_EVERY_PAGE) -> str:
    maven_styling = header_mode != HEADER_MODE_NONE
    if maven_styling and OFFICIAL_HEADER_PNG:
        encoded = base64.b64encode(OFFICIAL_HEADER_PNG).decode("ascii")
        band = f"<img src='data:image/png;base64,{encoded}' style='width:100%;display:block;margin-bottom:12px;' />"
    else:
        band = ""
    blocks = [band]
    if maven_styling:
        title_style = (
            f"font-weight:700;font-size:1.05em;background:rgb{MAVEN_BAND_RGB};"
            f"color:rgb{MAVEN_TEXT_RGB};border-bottom:2px solid rgb{MAVEN_TEXT_RGB};"
            f"padding:4px 8px;margin-top:10px;border-radius:3px;"
        )
    else:
        title_style = (
            "font-weight:700;font-size:1.05em;border-bottom:1.5px solid #999;"
            "padding-bottom:2px;margin-top:10px;"
        )
    for section in sections:
        block = [f"<div style='{title_style}'>{section['title']}</div>"]
        in_list = False
        for line in section["lines"]:
            split = split_trailing_date(line) if approximate_layout else None
            is_bullet = line.startswith("- ") and not split
            if is_bullet:
                if not in_list:
                    block.append("<ul style='margin:4px 0 8px 0;padding-left:20px;'>")
                    in_list = True
                block.append(f"<li style='margin-bottom:4px;'>{line[2:].strip()}</li>")
                continue
            if in_list:
                block.append("</ul>")
                in_list = False
            if split:
                left, right = split
                block.append(
                    "<div style='display:flex;justify-content:space-between;gap:12px;'>"
                    f"<span>{left}</span><span style='white-space:nowrap;color:#555;'>{right}</span></div>"
                )
            else:
                block.append(f"<div>{line}</div>")
        if in_list:
            block.append("</ul>")
        blocks.append("\n".join(block))
    return "\n".join(blocks)
