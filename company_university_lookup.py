import json
import re
from pathlib import Path

_COMPANIES_PATH = Path(__file__).resolve().parent / "companies.json"
_UNIVERSITIES_PATH = Path(__file__).resolve().parent / "universities_1.json"

_PUNCT_RE = re.compile(r"[.,&'’-]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    name = _PUNCT_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name)
    return name.strip().lower()


def _load_company_map() -> dict:
    if not _COMPANIES_PATH.exists():
        return {}
    data = json.loads(_COMPANIES_PATH.read_text(encoding="utf-8"))
    mapping = {}
    for entry in data.get("companies", []):
        label = entry.get("label")
        if not label:
            continue
        for name in [entry.get("name"), *entry.get("aliases", [])]:
            if name:
                mapping[_normalize(name)] = label
    return mapping


def _load_university_map() -> dict:
    if not _UNIVERSITIES_PATH.exists():
        return {}
    data = json.loads(_UNIVERSITIES_PATH.read_text(encoding="utf-8"))
    mapping = {}
    for key, entries in data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            label = entry.get("label")
            if not label:
                continue
            for name in [entry.get("name"), *entry.get("aliases", [])]:
                if name:
                    mapping.setdefault(_normalize(name), []).append(label)
    return mapping


_COMPANY_MAP = _load_company_map()
_UNIVERSITY_MAP = _load_university_map()


def lookup_company_label(name: str):
    """Deterministic case/punctuation-insensitive exact/alias match against the stored
    company reference list. Returns None (never a guess) when there's no match."""
    if not name:
        return None
    return _COMPANY_MAP.get(_normalize(name))


def lookup_university_labels(name: str) -> list:
    """Binary in/out tier lookup against the stored university reference list. A
    school can match more than one list (e.g. Ivy League + QS Top 25), so this
    returns every matching label rather than a single rank. Returns an empty list
    (never a guess) when there's no match."""
    if not name:
        return []
    return list(_UNIVERSITY_MAP.get(_normalize(name), []))
