"""Token estimation utilities."""


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return (ascii_chars // 4) + int(non_ascii_chars * 1.5)


def estimate_messages_tokens(messages: list[dict]) -> int:
    import json

    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(json.dumps(part))
    return total
