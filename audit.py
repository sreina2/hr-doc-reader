import json
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "audit_log.jsonl"


def log_redaction(
    filename: str, counts: dict, document_type: str, level: int, timestamp: str
) -> None:
    entry = {
        "timestamp": timestamp,
        "file": filename,
        "document_type": document_type,
        "level": level,
        "counts": counts,
        "total_redactions": sum(counts.values()),
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
