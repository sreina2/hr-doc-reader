QA_SYSTEM_PROMPT = """You are answering questions about an HR document on behalf of the user. \
The document text provided has already been redacted to remove personal names, addresses, \
emails, phone numbers, and ID numbers, replaced with tokens like [REDACTED-NAME].

Answer only using the information in the redacted document text below. If the answer isn't \
in the text, say so plainly. Never attempt to guess, infer, or reconstruct a redacted value."""


def ask_question(client, model: str, redacted_text: str, question: str, history: list) -> str:
    messages = [
        {"role": "user", "content": f"Here is the redacted document:\n\n{redacted_text}"},
        {
            "role": "assistant",
            "content": "Understood. I'll answer questions using only this redacted document.",
        },
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=QA_SYSTEM_PROMPT,
        messages=messages,
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
