import json

from services.dynamodb import (
    resolve_business_from_phone,
    get_or_create_customer,
    get_or_create_conversation,
    append_message,
    get_recent_messages,
    merge_and_update_conversation_state,
    get_latest_message,
    get_pending_messages,
    mark_messages_processed
)
from services.llm import call_llm
from services.messaging import send_sms
from services.debounce import should_process_now, combine_messages


def lambda_handler(event, context):
    print("EVENT:", json.dumps(event))

    for record in event["Records"]:
        try:
            process_record(record)

        except Exception as e:
            print(f"[ERROR] Failed processing record: {str(e)}")
            raise e


def process_record(record):
    #parse input
    body = json.loads(record["body"])

    customer_phone = body["customer_phone"]
    business_phone = body["business_phone"]
    user_message = body["message"]

    print(json.dumps({
        "event": "incoming_message",
        "customer_phone": customer_phone,
        "business_phone": business_phone,
        "message": user_message
    }))

    #get business details
    business = resolve_business_from_phone(business_phone)

    if not business:
        print(f"[WARN] Unknown business for phone {business_phone}, dropping message")
        return

    business_id = business["business_id"]

    #get customer to look up convo
    customer = get_or_create_customer(business_id, customer_phone)

    #find the convo or make new one 
    conversation = get_or_create_conversation(business_id, customer_phone)
    convo_id = conversation["conversation_id"]

    #write the user message under the convo - if from user put it as pending automatically 
    current_msg = append_message(convo_id, "user", user_message)

    if not current_msg:
        return

    #DEBOUNCE LOGIC
    # only process if this is the latest message AND debounce window passed
    if not should_process_now(convo_id, current_msg):
        print("[INFO] Not ready, retrying via SQS")
        raise Exception("Debounce not satisfied yet")

    # collect all pending user messages
    pending_messages = get_pending_messages(convo_id)

    if not pending_messages:
        return

    # combine into single message
    combined_text = combine_messages(pending_messages)

    # mark them as processed BEFORE LLM call
    mark_messages_processed(pending_messages)

    # override user_message with combined version
    user_message = combined_text

    print(json.dumps({
        "event": "debounced_message",
        "combined_text": combined_text,
        "num_messages": len(pending_messages)
    }))

    #get the recent messages if exist, store state
    history_items = get_recent_messages(convo_id)
    state = conversation["state"]

    #convert to llm format so it can make decisions
    history = [
        {"role": msg["role"], "content": msg["text"]}
        for msg in history_items
    ]

    print(json.dumps({
        "event": "llm_input",
        "history_len": len(history),
        "state": state
    }))

    #call LLM
    llm_output = call_llm(history, state)

    # Expect:
    # {
    #   "reply": "...",
    #   "state_updates": {...}
    # }

    reply = llm_output.get("reply", "").strip()
    state_updates = llm_output.get("state_updates", {})

    if not reply:
        raise Exception("LLM returned empty reply")

    print(json.dumps({
        "event": "llm_output",
        "reply": reply,
        "state_updates": state_updates
    }))

    #llm will return a updated state, merge with existing 
    new_state = merge_and_update_conversation_state(
        business_id,
        customer_phone,
        state_updates
    )

    #store the ai assistants reply as well
    append_message(convo_id, "assistant", reply)

    #display the response back to the user
    send_sms(customer_phone, reply)

    print(json.dumps({
        "event": "message_complete",
        "customer_phone": customer_phone
    }))