import time
from services.dynamodb import get_latest_message, get_pending_messages, mark_messages_processed

DEBOUNCE_SECONDS = 2

def combine_messages(messages):
    # sort oldest → newest
    messages.sort(key=lambda x: int(x["timestamp"]))

    return " ".join(msg["text"] for msg in messages)
    
def should_process_now(conversation_id: str, current_message: dict):
    """
    Decide whether this message should trigger LLM processing.
    """

    latest = get_latest_message(conversation_id)

    if not latest:
        return False

    # If this is NOT the latest message → do nothing
    if latest["SK"] != current_message["SK"]:
        return False

    # Check debounce window
    now = time.time_ns()
    msg_time = int(latest["timestamp"])

    if (now - msg_time) < DEBOUNCE_SECONDS * 1_000_000_000:
        return False

    return True