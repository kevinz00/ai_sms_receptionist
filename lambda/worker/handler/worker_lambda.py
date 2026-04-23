import json

from services.dynamodb import (
    resolve_business_from_phone,
    get_or_create_customer,
    get_or_create_conversation,
    append_message,
    find_message_by_source_id,
    get_recent_messages,
    merge_and_update_conversation_state,
    get_pending_messages,
    mark_messages_processed,
)
from services.llm import call_llm
from services.messaging import send_sms
from services.debounce import should_process_now, combine_messages


def lambda_handler(event, context):
    print("EVENT:", json.dumps(event))

    for record in event.get("Records", []):
        try:
            process_record(record)
        except Exception as e:
            print(f"[ERROR] Failed processing record: {str(e)}")
            raise


def process_record(record):
    body_raw = record.get("body")
    sqs_message_id = record.get("messageId")

    if not body_raw:
        print("[ERROR] Missing body in SQS record")
        return

    if not sqs_message_id:
        print("[ERROR] Missing SQS messageId")
        return

    try:
        body = json.loads(body_raw)
    except Exception:
        print(f"[ERROR] Invalid JSON body: {body_raw}")
        return

    customer_phone = body.get("customer_phone")
    business_phone = body.get("business_phone")
    user_message = (body.get("message") or "").strip()

    if not customer_phone or not business_phone or not user_message:
        print(f"[ERROR] Invalid message schema: {body}")
        return

    print(json.dumps({
        "event": "incoming_message",
        "sqs_message_id": sqs_message_id,
        "customer_phone": customer_phone,
        "business_phone": business_phone,
        "message": user_message,
    }))

    business = resolve_business_from_phone(business_phone)
    if not business:
        print(f"[WARN] Unknown business for phone {business_phone}, dropping message")
        return

    business_id = business["business_id"]

    get_or_create_customer(business_id, customer_phone)

    conversation = get_or_create_conversation(business_id, customer_phone)
    convo_id = conversation["conversation_id"]

    # Store the inbound SMS exactly once
    current_msg = find_message_by_source_id(convo_id, sqs_message_id)
    if not current_msg:
        current_msg = append_message(
            convo_id,
            "user",
            user_message,
            source_message_id=sqs_message_id,
        )

    if not current_msg:
        print("[WARN] Could not create or find current message")
        return

    # Get all pending user messages for the conversation
    pending_messages = get_pending_messages(convo_id)

    if not pending_messages:
        print("[INFO] No pending messages to process")
        return

    # Only process when 2 seconds have passed since the latest pending user message
    if not should_process_now(pending_messages):
        print("[INFO] Not ready, retrying via SQS")
        raise Exception("Debounce not satisfied yet")

    combined_text = combine_messages(pending_messages)

    print(json.dumps({
        "event": "debounced_message",
        "combined_text": combined_text,
        "num_messages": len(pending_messages),
    }))

    history_items = get_recent_messages(convo_id)
    state = conversation["state"]

    history = [
        {"role": msg["role"], "content": msg["text"]}
        for msg in history_items
    ]

    history.append({
        "role": "user",
        "content": combined_text,
    })

    print(json.dumps({
        "event": "llm_input",
        "history_len": len(history),
        "state": state,
    }))

    llm_output = call_llm(history, state)

    reply = (llm_output.get("reply") or "").strip()
    state_updates = llm_output.get("state_updates", {}) or {}

    if not reply:
        raise Exception("LLM returned empty reply")

    print(json.dumps({
        "event": "llm_output",
        "reply": reply,
        "state_updates": state_updates,
    }))

    mark_messages_processed(pending_messages)

    merge_and_update_conversation_state(
        business_id,
        customer_phone,
        state_updates,
    )

    append_message(convo_id, "assistant", reply)

    send_sms(customer_phone, reply)

    print(json.dumps({
        "event": "message_complete",
        "customer_phone": customer_phone,
        "conversation_id": convo_id,
    }))