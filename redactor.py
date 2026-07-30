import json
import re
from dataclasses import dataclass
from enum import IntEnum

from company_university_lookup import lookup_university_labels

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Broad candidate match for phone-like digit groups; filtered by digit count below
# to avoid flagging dates, page numbers, currency amounts, etc.
PHONE_CANDIDATE_RE = re.compile(r"(?<!\w)(\+?\(?\d[\d\s().-]{6,17}\d)(?!\w)")

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# UK National Insurance number format
NI_NUMBER_RE = re.compile(
    r"\b[A-CEGHJ-PR-TW-Z]{1}[A-CEGHJ-NPR-TW-Z]{1}\d{6}[A-D]\b", re.IGNORECASE
)

EMPLOYEE_ID_RE = re.compile(
    r"\b(?:Employee|Emp|Staff)\s*(?:ID|No\.?|Number)\s*[:#-]?\s*[A-Za-z0-9-]{4,12}\b",
    re.IGNORECASE,
)

URL_RE = re.compile(
    r"(?:https?://\S+"
    r"|www\.\S+"
    r"|(?:github|gitlab|linkedin|behance|dribbble|medium|stackoverflow)\.com/\S+"
    r"|\b[A-Za-z0-9][\w-]*\.(?:dev|io|me)\b(?:/\S*)?)",
    re.IGNORECASE,
)


class RedactionLevel(IntEnum):
    PERSONAL_INFO = 1
    DEIDENTIFIED = 2
    WORK_ONLY = 3


class ContractLevel(IntEnum):
    FULL_REDACTION = 1
    SUMMARIZED_TAKEAWAYS = 2


LEVEL_1_SYSTEM_PROMPT = """You are formatting a resume/CV/reference document into clean \
structured sections while redacting direct personal identifiers.

Redact ONLY the following, replacing each occurrence with the exact token shown:
- The full name, first name alone, last name alone, or initial-plus-name combination of \
ANY person mentioned anywhere in the document - the subject, a reference, a manager, a \
co-author, anyone at all named, with no exceptions (e.g. "Sabah Reina", "Sarah", \
"Mark R.") -> [REDACTED-NAME]. A person's name used as part of a company's own name (e.g. \
"Smith & Associates", "Johnson Consulting") is a company name, not a personal name - leave \
it exactly as written, per the company-name rule below.
- Email addresses -> [REDACTED-EMAIL]
- Phone numbers -> [REDACTED-PHONE]
- Any home/mailing address, for anyone mentioned (not just the subject) - including a \
partial fragment on its own, such as a unit/apartment number (e.g. "Apt 2B") -> \
[REDACTED-ADDRESS]
- LinkedIn profiles, personal websites, or other personal URLs -> [REDACTED-URL]
- Any other direct personal identifier (e.g. a personal ID number) -> [REDACTED-ID]

Do NOT redact or alter: dates, company names, job titles, job descriptions, skills, \
education institution names, or any other content. Reproduce all non-redacted content \
faithfully and in full, without summarizing or shortening it - you are only reorganizing \
it into sections and applying the redactions above.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"sections": [{"title": "Summary", "lines": ["paragraph text", "- bullet text"]}]}

Use standard resume section titles that fit the content (e.g. Summary, Experience, \
Education, Skills, Certifications). Only include a section if the source document has \
content for it. Prefix bullet-point lines with "- ". Preserve the original ordering of \
entries as they appear in the source."""

LEVEL_2_SYSTEM_PROMPT = """You are formatting a resume/CV/reference document into clean \
structured sections while de-identifying the subject as thoroughly as possible.

Redact all of the following, replacing each occurrence with the exact token shown:
- The full name, first name alone, last name alone, or initial-plus-name combination of \
ANY person mentioned anywhere in the document - the subject, a reference, a manager, a \
co-author, anyone at all named, with no exceptions (e.g. "Sabah Reina", "Sarah", \
"Mark R.") -> [REDACTED-NAME]. A person's name used as part of a company's own name (e.g. \
"Smith & Associates", "Johnson Consulting") is a company name, not a personal name - it may \
stay per the company-name allowance below.
- Email addresses -> [REDACTED-EMAIL]
- Phone numbers -> [REDACTED-PHONE]
- Any home/mailing address, for anyone mentioned (not just the subject) - including a \
partial fragment on its own, such as a unit/apartment number (e.g. "Apt 2B") -> \
[REDACTED-ADDRESS]
- LinkedIn profiles, personal websites, or other personal URLs -> [REDACTED-URL]
- Any other direct personal identifier -> [REDACTED-ID]
- Every non-education date (employment dates, publication dates, any specific year or \
date range) -> [REDACTED-DATE]
- Every education entry's institution/school name - do NOT write it as plain text \
anywhere in the entry; in its place write the exact marker \
[[EDU:Institution Name Exactly As Shown]], using the institution's name precisely as \
it appears in the source inside the marker (this marker is resolved deterministically \
afterward to either a short reference label or nothing at all - never write the \
institution's real name anywhere else in the output)
- Every education entry's graduation/attendance date (including a start-end range if \
shown, e.g. "Sept 2022 - Dec 2023") -> [REDACTED-DATE], same as other dates
- Every workplace or institution location (a city/state/country shown next to a job or \
education entry, e.g. "New York, NY") -> [REDACTED-LOCATION]

Beyond that, review the remaining content for anything so specific that it could identify \
the person even without their name - e.g. a distinctive personal tagline, an unusually \
specific singular achievement, a rare combination of institution and role that narrows \
down who this is, or a verbatim publication/paper title with enough detail (journal, \
volume/issue, co-author names) that it could be searched online and matched straight back \
to the person. When you find such a detail:
- If it can be generalized while keeping its meaning (e.g. "VP of Engineering at a \
12-person fintech startup founded in 2019" -> "VP of Engineering at an early-stage fintech \
startup", or a full paper title/citation -> "co-authored a peer-reviewed study on [general \
topic]"), generalize it in place.
- If it can't be generalized without losing all meaning, replace just that detail with \
[REDACTED].

Company names and job descriptions may stay as long as they aren't themselves the \
uniquely-identifying detail. Do not summarize or shorten content beyond what's needed for \
de-identification - you are reorganizing it into sections and applying redactions/\
generalizations only.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"sections": [{"title": "Summary", "lines": ["paragraph text", "- bullet text"]}]}

Use standard resume section titles that fit the content. Only include a section if the \
source document has content for it. Prefix bullet-point lines with "- "."""

LEVEL_3_SYSTEM_PROMPT = """You are extracting an anonymous work-description profile from a \
resume/CV/reference document.

Discard entirely: every person's name mentioned anywhere in the document (the subject, a \
reference, a manager, a co-author, anyone at all named - including a first name alone, a \
last name alone, or an initial-plus-name combination, with no exceptions), every home/\
mailing address or address fragment (including a partial fragment on its own, such as a \
unit/apartment number), all company/organization names, all dates, all other personal \
contact information, education institution names, and publication titles.

Keep only descriptions of the actual work performed: tasks, responsibilities, and skills \
used. Rewrite each one as a standalone, anonymous bullet point that reads naturally without \
any of the discarded context - do not leave placeholders in this output, just omit the \
identifying context and keep the substance of what the work was.

Before the work description bullets, also write a "Summarized Important Takeaways" section: \
a brief two-line summary of the candidate's overall profile, written the way a recruiter \
would skim it - what kind of role/discipline this person works in and their general level \
of experience/impact. It must not reintroduce any name, date, company, or other detail that \
is being discarded - keep it as general as the work description bullets themselves.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"sections": [
  {"title": "Summarized Important Takeaways", "lines": ["line 1 of the summary", "line 2 of the summary"]},
  {"title": "Work Description", "lines": ["- bullet text", "- bullet text"]}
]}"""

LEVEL_PROMPTS = {
    RedactionLevel.PERSONAL_INFO: LEVEL_1_SYSTEM_PROMPT,
    RedactionLevel.DEIDENTIFIED: LEVEL_2_SYSTEM_PROMPT,
    RedactionLevel.WORK_ONLY: LEVEL_3_SYSTEM_PROMPT,
}

CONTRACT_LEVEL1_SYSTEM_PROMPT = """You are formatting a contract, agreement, or letter into \
clean structured sections while redacting all identifying information.

Redact ALL of the following, with NO exceptions, replacing each occurrence with the exact \
token shown:
- The full name, first name alone, last name alone, or initial-plus-name combination of ANY \
person mentioned anywhere in this document, in any capacity - not only defined Parties or \
signatories, but also anyone referenced, mentioned in passing, named as an example, a \
witness, a guarantor, a beneficiary, or in any other role, with no exceptions -> \
[REDACTED-NAME]
- The name of ANY company or organization mentioned anywhere in this document, in any \
capacity - a party, a signatory, an employer named in passing, a counterparty, anyone's \
current or former employer mentioned as background, or any other company referenced for any \
reason, with no exceptions - it is NEVER shown plain and NEVER given a label or \
description in its place, always redacted outright (e.g. "Susquehanna", "Atlas", \
"Citadel") -> [REDACTED-NAME]
- Every address, for anyone mentioned - including a partial fragment on its own, such as a \
unit/apartment number (e.g. "Apt 2B") -> [REDACTED-ADDRESS]
- Every date (effective dates, signing dates, deadlines, terms of duration) -> [REDACTED-DATE]
- Signatures or signature blocks -> [REDACTED-NAME]
- Email addresses -> [REDACTED-EMAIL]
- Phone numbers -> [REDACTED-PHONE]
- Case numbers, account numbers, reference numbers, or other identifying numbers -> [REDACTED-ID]
- Any amount of money someone is being paid, owed, or granted - salary, hourly rate, bonus, \
commission, equity/stock grants, severance, or any other payment - in ANY format and ANY \
currency: numeric ("$85,000/year", "£40,000", "€200,000"), written out in words ("one \
million dollars", "fifty thousand pounds a year"), abbreviated ("$1M", "$40/hr"), or \
expressed as a percentage of pay ("a bonus targeted at 10% of base pay") -> \
[REDACTED-COMPENSATION]

Do NOT redact or alter: the substantive terms, clauses, obligations, or content of the \
document itself (including non-pay dollar figures like liability caps or fees, which are not \
compensation), generic role/title references (e.g. "the Employer", "the Tenant", "the \
Parties"), or section numbering. Reproduce all non-redacted content faithfully and in full, \
without summarizing or shortening it - you are only reorganizing it into sections and \
applying the redactions above.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"sections": [{"title": "Section title", "lines": ["paragraph text", "- bullet text"]}]}

Use section titles that fit the document's own structure (e.g. Parties, Term, \
Compensation, Confidentiality, Signatures). Only include a section if the source document \
has content for it. Prefix bullet-point lines with "- ". Preserve the original ordering of \
content as it appears in the source."""

CONTRACT_LEVEL2_SYSTEM_PROMPT = """You are extracting a summarized-takeaways profile from a \
contract, agreement, or letter.

Discard entirely, with NO exceptions: every person's name mentioned anywhere (not only \
defined Parties - anyone referenced, in any capacity, including a first name alone, a last \
name alone, or an initial-plus-name combination), every company or organization name \
mentioned anywhere (a party, a signatory, an employer named in passing, or any other company \
referenced for any reason - never shown plain and never given a label or description in its \
place, e.g. "Susquehanna", "Atlas", "Citadel"), every date, every address or address \
fragment (including a partial fragment on its own, such as a unit/apartment number), \
signatures, contact information, case/account/reference numbers, and every compensation or \
payment amount in any format or currency (numeric, written out in words, or abbreviated - \
e.g. "$85,000", "one million dollars", "$1M").

Write a "Summarized Important Takeaways" section: a brief plain-language summary covering \
what kind of document this is (e.g. an offer letter, an NDA, a service agreement), its key \
terms or obligations, and any notable conditions - written the way someone skimming for the \
gist would want it, not a clause-by-clause breakdown. It must not reintroduce any name, \
company name, date, address, compensation/payment amount, or other identifying detail that \
is being discarded - if a "key term" would normally include a payment amount, describe it \
in general terms instead (e.g. "includes a signing bonus" rather than stating the amount).

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"sections": [{"title": "Summarized Important Takeaways", "lines": ["line 1", "line 2"]}]}"""

CONTRACT_LEVEL_PROMPTS = {
    ContractLevel.FULL_REDACTION: CONTRACT_LEVEL1_SYSTEM_PROMPT,
    ContractLevel.SUMMARIZED_TAKEAWAYS: CONTRACT_LEVEL2_SYSTEM_PROMPT,
}

CONTRACT_SPAN_PROMPT = """You are reviewing a contract, agreement, or letter to identify all \
identifying information for redaction, for in-place redaction on the original page. You will \
be given the text with emails, phone numbers, URLs, and ID numbers already removed.

Find and return the exact original substring for every occurrence of:
- Any person's name - full name, first name alone, last name alone, or an initial-plus-name \
combination - mentioned anywhere in this document, in any capacity: not only defined \
Parties or signatories, but also anyone referenced, mentioned in passing, named as an \
example, a witness, a guarantor, a beneficiary, or in any other role, with no exceptions
- The name of any company or organization mentioned anywhere in this document, in any \
capacity - a party, a signatory, an employer named in passing, a counterparty, anyone's \
current or former employer mentioned as background, or any other company referenced for any \
reason, with no exceptions (e.g. "Susquehanna", "Atlas", "Citadel")
- Every address or address fragment, for anyone mentioned - including a partial fragment on \
its own, such as a unit/apartment number (e.g. "Apt 2B")
- Every date (effective dates, signing dates, deadlines, terms of duration)
- Any other identifying number (case numbers, account numbers, reference numbers)
- Any amount of money someone is being paid, owed, or granted - salary, hourly rate, bonus, \
commission, equity/stock grants, severance, or any other payment - in ANY format and ANY \
currency: numeric ("$85,000/year", "£40,000", "€200,000"), written out in words ("one \
million dollars", "fifty thousand pounds a year"), abbreviated ("$1M", "$40/hr"), or \
expressed as a percentage of pay ("a bonus targeted at 10% of base pay")

Do not flag: generic role/title references (e.g. "the Employer", "the Tenant", "the \
Parties"), section numbering, non-pay dollar figures (liability caps, fees), or other \
substantive terms/clauses of the document. Names and company names both go in the "names" \
field below - there is no separate category for companies, they are redacted the same way.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"names": ["exact substring"], "addresses": ["exact substring"], "dates": ["exact substring"], \
"other_ids": ["exact substring"], "compensation": ["exact substring"]}

If none are found for a category, return an empty list for it."""

SPAN_IDENTIFICATION_PROMPT = """You are reviewing a resume/CV/reference document to identify \
personal identifiers for redaction. You will be given the text with emails, phone numbers, \
URLs, and ID numbers already removed.

Find every occurrence of any person's name - full name, first name alone, last name alone, \
or an initial-plus-name combination (e.g. "Sabah Reina", "Sarah", "Mark R.") - with NO \
exceptions: the document's subject, a reference, a manager, a co-author, anyone at all \
named. Also find every home/mailing address or address fragment, and any other direct \
personal identifier that isn't already removed. Return them verbatim, exactly as they \
appear in the text, so they can be located with a plain string match.

An address means a specific street/mailing address (e.g. "42 Baker Street, London, NW1 \
6XE") or a partial fragment on its own, such as a unit/apartment number (e.g. "Apt 2B"). A \
bare city and state/country (e.g. "New York, NY") is a location, not a home address - do \
not flag it, whether it's next to a name or a job entry.

Do not flag: job titles alone, company names on their own (but if a person's name is used \
as part of a company's own name, e.g. "Smith & Associates", that is a company name, not a \
personal name - do not flag it either), or city/state locations on their own.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"names": ["exact substring"], "addresses": ["exact substring"], "other_ids": ["exact substring"]}

If none are found for a category, return an empty list for it."""

IDENTITY_BLOCK_PROMPT = """You are locating the document's own name/identity header block - \
the candidate's full name and any contact or profile links directly associated with it at \
the top of the document (email, phone, GitHub, LinkedIn, personal website, portfolio, or \
similar), however it's styled in this particular document: a large stylized title, all \
caps, a small line directly under the name, a sidebar block - any layout. Emails, phone \
numbers, and common URL patterns may already be removed from this text; find anything of \
that kind that remains, plus the name itself, wherever and however it appears.

Do not flag: home/mailing addresses (these are found and redacted separately, under their \
own [REDACTED-ADDRESS] label, not this one), other people's names mentioned elsewhere in \
the document (managers, co-authors, references), company names, job titles, or any other \
content.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"identity_block": ["exact substring", "exact substring"]}

If nothing is found, return an empty list."""

LEVEL2_SPAN_PROMPT = """You are reviewing a resume/CV/reference document to de-identify the \
subject as thoroughly as possible, for in-place redaction on the original page. You will be \
given the text with emails, phone numbers, URLs, and ID numbers already removed.

Find and return the exact original substring for every occurrence of:
- Any person's name - full name, first name alone, last name alone, or an initial-plus-name \
combination (e.g. "Sabah Reina", "Sarah", "Mark R.") - with NO exceptions: the document's \
subject, a reference, a manager, a co-author, anyone at all named. (A person's name used as \
part of a company's own name, e.g. "Smith & Associates", is a company name, not a personal \
name - do not flag it.)
- Every home/mailing address or address fragment, for anyone mentioned (a specific street/\
mailing address, or a partial fragment on its own such as a unit/apartment number - not a \
bare city/state, which is a location handled separately below)
- Any other direct personal identifier
- Every non-education date (employment dates, publication dates, any specific year or \
date range) - do NOT include education/graduation dates here, they belong in \
education_entries below instead
- Every workplace or institution location (a bare city/state/country next to a job or \
education entry, e.g. "New York, NY" - this is a separate category from home/mailing \
addresses above)

Separately, for every education entry (each degree/program listed, typically under an \
Education section), return the institution name and its associated graduation/attendance \
date as a pair - the institution name is also redacted (replaced with a reference-list \
label, or removed entirely if it has no match), and the date is separately redacted, both \
deterministically based on this pairing afterward:
{"education_entries": [{"institution": "exact substring of the institution name", "date": \
"exact substring of the graduation/attendance date tied to that institution, including a \
start-end range if shown"}]}
Do not additionally list the institution name in any other field above.

Additionally, review the remaining content for anything so specific it could identify the \
person even without their name - a distinctive personal tagline, an unusually specific \
singular achievement, a rare institution+role combination, or a verbatim publication \
citation with enough detail (journal, volume/issue, co-author names) to be searched online \
and matched back to the person. For each such detail, return the exact original substring \
and a generalized replacement that keeps the general meaning but removes the identifying \
specificity (e.g. "VP of Engineering at a 12-person fintech startup founded in 2019" -> "VP \
of Engineering at an early-stage fintech startup"; a full paper citation -> "Co-authored a \
peer-reviewed study on general topic"). If it can't be generalized without losing all \
meaning, use "[REDACTED]" as the replacement.

IMPORTANT: since each replacement is placed into the same physical space the original text \
occupied on the page, keep every replacement no longer than roughly the original substring's \
length - do not write a longer sentence than what it replaces.

Return ONLY a JSON object with this exact shape, no other text, no markdown fences:
{"names": ["exact substring"], "addresses": ["exact substring"], "other_ids": ["exact substring"], \
"dates": ["exact substring"], "locations": ["exact substring"], \
"education_entries": [{"institution": "exact substring", "date": "exact substring"}], \
"generalizations": [{"original": "exact substring", "replacement": "concise text or [REDACTED]"}]}

If none are found for a category, return an empty list for it."""

TOKEN_CATEGORIES = [
    ("names", "[REDACTED-NAME]"),
    ("addresses", "[REDACTED-ADDRESS]"),
    ("emails", "[REDACTED-EMAIL]"),
    ("phones", "[REDACTED-PHONE]"),
    ("urls", "[REDACTED-URL]"),
    ("ids", "[REDACTED-ID]"),
    ("dates", "[REDACTED-DATE]"),
    ("locations", "[REDACTED-LOCATION]"),
    ("compensation", "[REDACTED-COMPENSATION]"),
    ("other", "[REDACTED]"),
]


@dataclass
class RedactionCounts:
    names: int = 0
    addresses: int = 0
    emails: int = 0
    phones: int = 0
    urls: int = 0
    ids: int = 0
    dates: int = 0
    locations: int = 0
    compensation: int = 0
    other: int = 0

    def as_dict(self) -> dict:
        return {field: getattr(self, field) for field, _ in TOKEN_CATEGORIES}


_EDU_MARKER_RE = re.compile(r"\[\[EDU:(.*?)\]\]([,\-–—]\s*)?")


def _resolve_education_markers(sections: list) -> list:
    """Resolve each [[EDU:institution]] marker to the institution's looked-up tier
    label(s) - a deterministic reference-list match, never a model guess. When the
    institution has no match, the marker (and an immediately-following separator, if
    any) is dropped entirely rather than shown as a placeholder, so the institution
    name is never shown plain, tokenized, or replaced with a stand-in."""

    def resolve(line: str) -> str:
        def repl(match: re.Match) -> str:
            labels = lookup_university_labels(match.group(1).strip())
            if not labels:
                return ""
            return ", ".join(labels) + (match.group(2) or "")

        return _EDU_MARKER_RE.sub(repl, line)

    for section in sections:
        section["lines"] = [resolve(str(line)) for line in section["lines"]]
    return sections


def redact_resume(
    client, text: str, model: str, level: RedactionLevel
) -> tuple[list, RedactionCounts]:
    pre_redacted = _apply_regex_redactions(text)

    identity_spans = find_identity_block(client, pre_redacted, model)
    replacement = "" if level == RedactionLevel.WORK_ONLY else "[REDACTED-NAME]"
    for span in sorted(set(identity_spans), key=len, reverse=True):
        if span.strip():
            pre_redacted = pre_redacted.replace(span, replacement)

    sections = _generate_structured_redaction(client, pre_redacted, model, LEVEL_PROMPTS[level])
    if level == RedactionLevel.DEIDENTIFIED:
        sections = _resolve_education_markers(sections)
    flat = flatten_sections(sections)
    counts = _count_tokens(flat)
    return sections, counts


def redact_contract(
    client, text: str, model: str, level: ContractLevel
) -> tuple[list, RedactionCounts]:
    # No find_identity_block pre-pass here, unlike redact_resume - that prompt is
    # written for a resume's own name/contact header block and (confirmed via
    # testing) can misidentify a party's address as part of it on a contract,
    # producing a second, conflicting redaction over the same text. The contract
    # prompts below already redact every name - including signature blocks - with
    # no exceptions on their own, so this safety net is both redundant and risky
    # here.
    pre_redacted = _apply_regex_redactions(text)

    sections = _generate_structured_redaction(
        client, pre_redacted, model, CONTRACT_LEVEL_PROMPTS[level]
    )
    flat = flatten_sections(sections)
    counts = _count_tokens(flat)
    return sections, counts


def flatten_sections(sections: list) -> str:
    parts = []
    for section in sections:
        parts.append(section["title"])
        parts.extend(section["lines"])
    return "\n".join(parts)


def _apply_regex_redactions(text: str) -> str:
    text = SSN_RE.sub("[REDACTED-ID]", text)
    text = NI_NUMBER_RE.sub("[REDACTED-ID]", text)
    text = EMPLOYEE_ID_RE.sub("[REDACTED-ID]", text)
    text = URL_RE.sub("[REDACTED-URL]", text)
    text = EMAIL_RE.sub("[REDACTED-EMAIL]", text)
    text = _redact_phone_numbers(text)
    return text


def _redact_phone_numbers(text: str) -> str:
    def replace(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 7 <= len(digits) <= 15:
            return "[REDACTED-PHONE]"
        return match.group(0)

    return PHONE_CANDIDATE_RE.sub(replace, text)


_LEAD_PAREN_RE = re.compile(r"\($")
_TRAIL_CLOSE_PAREN_RE = re.compile(r"^\s*\)")
_TRAIL_PIPE_RE = re.compile(r"^\s*\|\s*")


def _expand_span(text: str, start: int, end: int) -> str:
    """Widen a match to also swallow a separator character it's wrapped in or
    followed by (e.g. the parens around a phone number, or a trailing " | " in a
    pipe-separated contact line) so that punctuation gets redacted along with it
    instead of being left behind as a stray character."""
    before = text[max(0, start - 3) : start]
    lead_extra = 0
    m = _LEAD_PAREN_RE.search(before)
    if m:
        lead_extra = len(before) - m.start()

    after = text[end : end + 10]
    trail_extra = 0
    m = _TRAIL_CLOSE_PAREN_RE.match(after)
    if m:
        trail_extra = m.end()
        after = after[trail_extra:]
    m2 = _TRAIL_PIPE_RE.match(after)
    if m2:
        trail_extra += m2.end()

    return text[start - lead_extra : end + trail_extra]


def _expand_first_occurrence(text: str, span_text: str) -> str:
    """Like _expand_span, but for spans identified by Claude (no match position),
    located by their first occurrence in the source text."""
    idx = text.find(span_text)
    if idx == -1:
        return span_text
    return _expand_span(text, idx, idx + len(span_text))


_TRAIL_INSTITUTION_SEPARATOR_RE = re.compile(r"^[,\-–—]\s*")


def _expand_institution_removal(text: str, institution: str) -> str:
    """Like _expand_first_occurrence, but for an institution name being removed
    entirely (no reference-list match) rather than replaced in place - also swallows
    an immediately-following comma/dash separator, so the degree text that follows
    doesn't start with orphaned punctuation once the name is gone."""
    idx = text.find(institution)
    if idx == -1:
        return institution
    end = idx + len(institution)
    m = _TRAIL_INSTITUTION_SEPARATOR_RE.match(text[end : end + 6])
    return text[idx : end + (m.end() if m else 0)]


def find_regex_spans(text: str) -> list:
    """Return (matched_text, token) pairs without modifying text, for geometric redaction."""
    spans = []
    for regex, token in (
        (SSN_RE, "[REDACTED-ID]"),
        (NI_NUMBER_RE, "[REDACTED-ID]"),
        (EMPLOYEE_ID_RE, "[REDACTED-ID]"),
        (URL_RE, "[REDACTED-URL]"),
        (EMAIL_RE, "[REDACTED-EMAIL]"),
    ):
        spans.extend((_expand_span(text, m.start(), m.end()), token) for m in regex.finditer(text))

    for m in PHONE_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 7 <= len(digits) <= 15:
            spans.append((_expand_span(text, m.start(), m.end()), "[REDACTED-PHONE]"))

    return spans


def find_identity_block(client, text: str, model: str) -> list:
    """Locate the document's own name + contact block as a deterministic first pass,
    regardless of how it's styled - runs before any level-specific rewriting, at every
    level and every input path, so the identity block can never slip through just
    because a level's main rewrite prompt failed to recognize it."""
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=IDENTITY_BLOCK_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    payload = _require_parsed_json(raw, response.stop_reason)

    values = payload.get("identity_block", [])
    if not isinstance(values, list):
        return []
    return [
        _expand_first_occurrence(text, str(v))
        for v in values
        if isinstance(v, (str, int, float)) and str(v).strip()
    ]


def find_personal_spans(client, text: str, model: str) -> list:
    """Ask Claude for exact name/address/other-ID substrings, for geometric redaction."""
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SPAN_IDENTIFICATION_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    payload = _require_parsed_json(raw, response.stop_reason)

    spans = []
    for field, token in (
        ("names", "[REDACTED-NAME]"),
        ("addresses", "[REDACTED-ADDRESS]"),
        ("other_ids", "[REDACTED-ID]"),
    ):
        values = payload.get(field, [])
        if isinstance(values, list):
            spans.extend(
                (_expand_first_occurrence(text, str(v)), token)
                for v in values
                if isinstance(v, (str, int, float)) and str(v).strip()
            )
    return spans


def find_contract_spans(client, text: str, model: str) -> list:
    """Ask Claude for exact name/address/date/other-ID substrings across ALL parties
    in a contract/letter, for geometric redaction."""
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=CONTRACT_SPAN_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    payload = _require_parsed_json(raw, response.stop_reason)

    spans = []
    for field, token in (
        ("names", "[REDACTED-NAME]"),
        ("addresses", "[REDACTED-ADDRESS]"),
        ("dates", "[REDACTED-DATE]"),
        ("other_ids", "[REDACTED-ID]"),
        ("compensation", "[REDACTED-COMPENSATION]"),
    ):
        values = payload.get(field, [])
        if isinstance(values, list):
            spans.extend(
                (_expand_first_occurrence(text, str(v)), token)
                for v in values
                if isinstance(v, (str, int, float)) and str(v).strip()
            )
    return spans


def find_level2_spans(client, text: str, model: str) -> list:
    """Ask Claude for name/address/ID/date substrings plus generalization pairs, for
    geometric redaction that keeps Level 2 in the same true-layout pipeline as Level 1."""
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=LEVEL2_SPAN_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    payload = _require_parsed_json(raw, response.stop_reason)

    spans = []
    for field, token in (
        ("names", "[REDACTED-NAME]"),
        ("addresses", "[REDACTED-ADDRESS]"),
        ("other_ids", "[REDACTED-ID]"),
        ("dates", "[REDACTED-DATE]"),
        ("locations", "[REDACTED-LOCATION]"),
    ):
        values = payload.get(field, [])
        if isinstance(values, list):
            spans.extend(
                (_expand_first_occurrence(text, str(v)), token)
                for v in values
                if isinstance(v, (str, int, float)) and str(v).strip()
            )

    for item in payload.get("generalizations", []):
        if not isinstance(item, dict):
            continue
        original = item.get("original")
        replacement = item.get("replacement")
        if (
            isinstance(original, str)
            and original.strip()
            and isinstance(replacement, str)
            and replacement.strip()
        ):
            spans.append((_expand_first_occurrence(text, original), replacement))

    for item in payload.get("education_entries", []):
        if not isinstance(item, dict):
            continue
        institution = item.get("institution")
        date = item.get("date")
        if not (
            isinstance(institution, str)
            and institution.strip()
            and isinstance(date, str)
            and date.strip()
        ):
            continue
        labels = lookup_university_labels(institution)
        if labels:
            spans.append((_expand_first_occurrence(text, institution), ", ".join(labels)))
        else:
            spans.append((_expand_institution_removal(text, institution), ""))
        spans.append((_expand_first_occurrence(text, date), "[REDACTED-DATE]"))

    return spans


def _generate_structured_redaction(client, text: str, model: str, system_prompt: str) -> list:
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    raw = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    payload = _parse_json(raw)
    return _sanitize_sections(payload.get("sections", []))


def _parse_json(raw: str) -> dict:
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _require_parsed_json(raw: str, stop_reason: str) -> dict:
    """Like _parse_json, but raises instead of silently returning {} - used for the
    span-identification calls, where a silent empty result means PII stays unredacted
    with no indication anything went wrong."""
    payload = _parse_json(raw)
    if not payload and raw:
        reason = " (response was cut off before completing - try a shorter document)" if stop_reason == "max_tokens" else ""
        raise RuntimeError(f"Redaction model response could not be parsed as JSON{reason}.")
    return payload


def _sanitize_sections(sections) -> list:
    clean = []
    if not isinstance(sections, list):
        return clean
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = section.get("title")
        lines = section.get("lines")
        if not isinstance(title, str) or not isinstance(lines, list):
            continue
        clean_lines = [str(line) for line in lines if isinstance(line, (str, int, float))]
        if clean_lines:
            clean.append({"title": title.strip(), "lines": clean_lines})
    return clean


def _count_tokens(text: str) -> RedactionCounts:
    counts = RedactionCounts()
    for field, token in TOKEN_CATEGORIES:
        setattr(counts, field, text.count(token))
    return counts
