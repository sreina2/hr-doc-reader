# HR Document Reader

Upload an HR document (PDF, DOCX, or TXT), choose a redaction level, review the
formatted result, download it as a PDF, then ask questions about it. Only the
redacted text is ever sent to the model for Q&A.

## Redaction levels

Pick exactly one before redacting — there's no default:

- **Level 1 — Personal info only**: redacts name, email, phone, home address,
  LinkedIn/personal URLs, and other direct personal identifiers. Dates, company
  names, job titles, descriptions, skills, and education institutions stay.
- **Level 2 — Personal info + de-identification**: everything in Level 1, plus
  all dates, plus a review pass for details specific enough to identify the
  person without their name (a distinctive tagline, a rare institution+role
  combination) — these are generalized where possible, or redacted with
  `[REDACTED]` if they can't be.
- **Level 3 — Work description only**: discards everything except the actual
  work performed. Names, companies, dates, education, and publication titles
  are all dropped; the remaining bullets are rewritten as standalone, anonymous
  descriptions of tasks/responsibilities/skills.

## How redaction works

1. **Regex pass** (no model call, runs first): emails, phone numbers, SSNs, UK
   National Insurance numbers, employee ID labels, and URLs/LinkedIn links are
   found and replaced by pattern matching, regardless of level.
2. **Claude pass**: the partially-redacted text is sent to Claude with a
   level-specific system prompt. Claude returns a structured JSON resume
   (section titles + lines) with the remaining redactions/rewrites applied
   directly, which is what renders as the preview, the PDF, and what's counted
   for the audit log.
3. Only the resulting redacted structure is stored in the session and used for
   the chat Q&A step — the original, unredacted text is discarded after
   redaction and never sent to the model again.
4. Each redaction run appends one line to `audit_log.jsonl` with the filename,
   timestamp, level, and counts per category. The actual redacted values are
   never logged.

Because Claude reproduces and restructures the document (needed for the
formatted output, and unavoidable for Level 3's rewriting), redaction accuracy
now depends on the model following the level's instructions rather than on
exact pattern matching alone — treat the preview as something to spot-check,
especially for Level 2's judgment calls about what counts as identifying.

The PDF export uses a plain text "Maven Partnership" header, not an actual
logo image — swap in your own asset in `pdf_export.py` if you have one. It's
built for internal use only; if you ever want to send this kind of output to
clients, route it through UpSlide instead so it stays within brand guidelines.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .env.example .env
# edit .env and add your ANTHROPIC_API_KEY
```

## Run

```bash
streamlit run app.py
```

Then open the local URL Streamlit prints (typically http://localhost:8501).

## Files

- `app.py` — Streamlit UI (upload, level selector, formatted preview, PDF
  download, chat)
- `parsers.py` — text extraction for PDF / DOCX / TXT
- `redactor.py` — regex pre-pass + per-level Claude-based structured redaction
- `pdf_export.py` — renders the redacted sections as a formatted PDF
- `qa.py` — Q&A over the redacted text only
- `audit.py` — counts-only audit logging
- `audit_log.jsonl` — created at runtime, one JSON line per processed document
