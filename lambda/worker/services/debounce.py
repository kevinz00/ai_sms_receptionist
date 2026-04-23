import time

DEBOUNCE_SECONDS = 2
DEBOUNCE_NS = DEBOUNCE_SECONDS * 1_000_000_000

def combine_messages(messages):
    messages.sort(key=lambda x: int(x["timestamp"]))
    return " ".join(msg["text"] for msg in messages)

def should_process_now(pending_messages: list[dict]) -> bool:
    if not pending_messages:
        return False

    latest_pending = max(pending_messages, key=lambda x: int(x["timestamp"]))
    now_ns = time.time_ns()
    latest_ts_ns = int(latest_pending["timestamp"])

    return (now_ns - latest_ts_ns) >= DEBOUNCE_NS