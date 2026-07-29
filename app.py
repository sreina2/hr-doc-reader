import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from anthropic import Anthropic
from dotenv import load_dotenv

from audit import log_redaction
from docx_export import build_docx_from_redacted_pdf, build_docx_from_sections
from parsers import UnsupportedFileType, extract_text
from pdf_export import build_pdf
from pdf_layout_redact import redact_contract_pdf_in_place, redact_pdf_in_place
from qa import ask_question
from redactor import ContractLevel, RedactionLevel, flatten_sections, redact_contract, redact_resume
from resume_render import HEADER_MODE_EVERY_PAGE, HEADER_MODE_LABELS, sections_to_html

load_dotenv()

st.set_page_config(page_title="HR Document Reader", page_icon="🔒")

st.markdown(
    """
    <style>
    button[data-variant="pills"] {
        padding: 10px 22px !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        min-height: 44px !important;
        font-size: 0.95rem !important;
        flex: 1 1 0 !important;
        transition: all 0.12s ease !important;
    }
    button[data-variant="pills"][aria-checked="false"] {
        background-color: transparent !important;
        border: 1.5px solid rgba(140, 170, 155, 0.6) !important;
        box-shadow: none !important;
    }
    button[data-variant="pills"][aria-checked="false"]:hover {
        border-color: rgb(30, 45, 40) !important;
        color: rgb(30, 45, 40) !important;
    }
    button[data-variant="pills"][aria-checked="true"] {
        background-color: rgb(30, 45, 40) !important;
        color: white !important;
        border: 1.5px solid rgb(30, 45, 40) !important;
        box-shadow: 0 2px 8px rgba(30, 45, 40, 0.4) !important;
    }
    button[data-variant="pills"][aria-checked="true"] p {
        color: white !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

DOCUMENT_TYPE_LABELS = {
    "resume": "Resumes/CVs",
    "contract": "Contracts/Letters",
}

RESUME_LEVEL_LABELS = {
    1: "Level 1 — Personal info only",
    2: "Level 2 — Personal info + de-identification",
    3: "Level 3 — Work description only",
}

CONTRACT_LEVEL_LABELS = {
    1: "Level 1 — Full identity redaction",
    2: "Level 2 — Summarized takeaways",
}

COUNT_LABELS = [
    ("names", "name", "names"),
    ("addresses", "address", "addresses"),
    ("emails", "email", "emails"),
    ("phones", "phone number", "phone numbers"),
    ("urls", "URL/link", "URLs/links"),
    ("ids", "ID/SSN", "IDs/SSNs"),
    ("dates", "date", "dates"),
    ("locations", "location", "locations"),
    ("compensation", "compensation figure", "compensation figures"),
    ("other", "other identifying detail", "other identifying details"),
]


@st.cache_resource
def get_client() -> Anthropic:
    return Anthropic()


def format_summary(counts: dict) -> str:
    parts = []
    for key, singular, plural in COUNT_LABELS:
        n = counts.get(key, 0)
        if n:
            parts.append(f"{n} {singular if n == 1 else plural}")
    return ", ".join(parts) if parts else "no sensitive items detected"


st.title("HR Document Reader")
st.caption(
    "Upload a document, choose a document type and redaction level, review the result, "
    "then ask questions. Only the redacted text is ever sent to the model for Q&A."
)

for key, default in {
    "raw_text": None,
    "uploaded_name": None,
    "uploaded_bytes": None,
    "is_pdf": False,
    "output_mode": None,
    "sections": None,
    "pdf_bytes": None,
    "page_previews": None,
    "redacted_text": None,
    "counts": None,
    "document_type": None,
    "level": None,
    "header_mode_baked": HEADER_MODE_EVERY_PAGE,
    "chat_history": [],
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

uploaded = st.file_uploader("Upload a PDF, DOCX, or TXT file", type=["pdf", "docx", "txt"])

needs_processing = uploaded is not None and (
    uploaded.name != st.session_state.uploaded_name or st.session_state.uploaded_bytes is None
)
if needs_processing:
    uploaded_bytes = uploaded.getvalue()
    try:
        raw_text = extract_text(uploaded_bytes, uploaded.name)
    except UnsupportedFileType as exc:
        st.error(str(exc))
        st.stop()

    if not raw_text.strip():
        st.warning("No extractable text found in this document.")
        st.stop()

    st.session_state.raw_text = raw_text
    st.session_state.uploaded_name = uploaded.name
    st.session_state.uploaded_bytes = uploaded_bytes
    st.session_state.is_pdf = Path(uploaded.name).suffix.lower() == ".pdf"
    st.session_state.output_mode = None
    st.session_state.sections = None
    st.session_state.pdf_bytes = None
    st.session_state.page_previews = None
    st.session_state.redacted_text = None
    st.session_state.counts = None
    st.session_state.document_type = None
    st.session_state.level = None
    st.session_state.chat_history = []

if st.session_state.raw_text:
    document_type = st.pills(
        "Document type",
        options=["resume", "contract"],
        format_func=lambda x: DOCUMENT_TYPE_LABELS[x],
        default=None,
        width="stretch",
    )

    level_choice = None
    if document_type == "resume":
        level_choice = st.pills(
            "Redaction level",
            options=[1, 2, 3],
            format_func=lambda x: RESUME_LEVEL_LABELS[x],
            default=None,
            width="stretch",
        )
    elif document_type == "contract":
        level_choice = st.pills(
            "Redaction level",
            options=[1, 2],
            format_func=lambda x: CONTRACT_LEVEL_LABELS[x],
            default=None,
            width="stretch",
        )

    uses_layout_pipeline = st.session_state.is_pdf and (
        (document_type == "resume" and level_choice in (1, 2))
        or (document_type == "contract" and level_choice == 1)
    )
    if uses_layout_pipeline:
        st.caption(
            "PDF + this level redacts directly on the original page, preserving the source "
            "layout exactly."
        )

    header_mode = st.pills(
        "Maven header",
        options=list(HEADER_MODE_LABELS.keys()),
        format_func=lambda x: HEADER_MODE_LABELS[x],
        default=HEADER_MODE_EVERY_PAGE,
        width="stretch",
    )
    if uses_layout_pipeline:
        st.caption(
            "Note: for this PDF + level combination, this only affects the header banner — "
            "the body below it is the original document's own preserved content and isn't "
            "recolored. Changing this after redacting requires clicking Redact again (no "
            "reload or re-upload needed) since the header is embedded directly in the page."
        )

    if st.button("Redact", disabled=(document_type is None or level_choice is None)):
        client = get_client()
        with st.spinner("Redacting..."):
            if uses_layout_pipeline:
                if document_type == "resume":
                    pdf_bytes, counts, redacted_text, previews = redact_pdf_in_place(
                        st.session_state.uploaded_bytes,
                        client,
                        MODEL,
                        level=level_choice,
                        header_mode=header_mode,
                    )
                else:
                    pdf_bytes, counts, redacted_text, previews = redact_contract_pdf_in_place(
                        st.session_state.uploaded_bytes, client, MODEL, header_mode=header_mode
                    )
                st.session_state.output_mode = "pdf_layout"
                st.session_state.pdf_bytes = pdf_bytes
                st.session_state.page_previews = previews
                st.session_state.redacted_text = redacted_text
                st.session_state.sections = None
                st.session_state.header_mode_baked = header_mode
            else:
                if document_type == "resume":
                    sections, counts = redact_resume(
                        client, st.session_state.raw_text, MODEL, RedactionLevel(level_choice)
                    )
                else:
                    sections, counts = redact_contract(
                        client, st.session_state.raw_text, MODEL, ContractLevel(level_choice)
                    )
                st.session_state.output_mode = "sections"
                st.session_state.sections = sections
                st.session_state.redacted_text = flatten_sections(sections)
                st.session_state.pdf_bytes = None
                st.session_state.page_previews = None

        st.session_state.counts = counts
        st.session_state.document_type = document_type
        st.session_state.level = level_choice
        st.session_state.chat_history = []

        log_redaction(
            filename=st.session_state.uploaded_name,
            counts=counts.as_dict(),
            document_type=document_type,
            level=level_choice,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

if st.session_state.output_mode:
    level_labels = (
        RESUME_LEVEL_LABELS if st.session_state.document_type == "resume" else CONTRACT_LEVEL_LABELS
    )
    level_label = level_labels[st.session_state.level]
    summary = format_summary(st.session_state.counts.as_dict())
    st.success(f"Redaction complete ({level_label}): {summary}.")

    if st.session_state.output_mode == "pdf_layout":
        with st.container(border=True):
            for png_bytes in st.session_state.page_previews:
                st.image(png_bytes)
        pdf_bytes = st.session_state.pdf_bytes
        docx_bytes = build_docx_from_redacted_pdf(
            pdf_bytes, header_mode=st.session_state.header_mode_baked
        )
    else:
        approximate_layout = st.session_state.document_type == "resume" and st.session_state.level == 2
        with st.container(border=True):
            st.markdown(
                sections_to_html(st.session_state.sections, approximate_layout, header_mode),
                unsafe_allow_html=True,
            )
        pdf_bytes = build_pdf(st.session_state.sections, approximate_layout, header_mode)
        docx_bytes = build_docx_from_sections(
            st.session_state.sections, approximate_layout, header_mode
        )

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download as PDF",
            data=pdf_bytes,
            file_name="redacted_document.pdf",
            mime="application/pdf",
        )
    with col2:
        st.download_button(
            "Download as Word Document",
            data=docx_bytes,
            file_name="redacted_document.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    st.subheader("Ask questions about this document")
    for turn in st.session_state.chat_history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    question = st.chat_input("Ask a question about the redacted document...")
    if question:
        prior_history = list(st.session_state.chat_history)
        st.session_state.chat_history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        client = get_client()
        with st.spinner("Thinking..."):
            answer = ask_question(
                client, MODEL, st.session_state.redacted_text, question, prior_history
            )
        st.session_state.chat_history.append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.markdown(answer)
elif not st.session_state.raw_text:
    st.info("Upload a document to get started.")
