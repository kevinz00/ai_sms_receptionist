import time
import uuid
import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("ai_receptionist")

DEFAULT_STATE = {
    "intent": None,
    "problem": None,
    "address": None,
    "appointment_time": None,
    "stage": "intake"
}

ALLOWED_STATE_FIELDS = {
    "intent",
    "problem",
    "address",
    "appointment_time",
    "stage"
}

VALID_ROLES = {
    "user", 
    "assistant"
}

DEBOUNCE_SECONDS = 2

def _merge_with_default_state(state: dict | None):
    """Ensure state always has all required fields"""
    merged = DEFAULT_STATE.copy()
    if state:
        merged.update(state)
    return merged

def resolve_business_from_phone(business_phone: str):
    """
    Resolve a business from its phone number.

    Args:
        business_phone (str): E.164 formatted phone number (e.g. +16175551234)

    Returns:
        dict | None:
            {
                "business_id": str,
                "phone": str
            }
            or None if not found
    """

    if not business_phone:
        return None

    pk = f"PHONE#{business_phone}"
    sk = "BUSINESS"

    try:
        response = table.get_item(
            Key={
                "PK": pk,
                "SK": sk
            }
        )
    except Exception as e:
        # Let Lambda retry via upstream error handling
        print(f"[ERROR] DynamoDB get_item failed for phone {business_phone}: {str(e)}")
        raise e

    item = response.get("Item")

    if not item:
        # No mapping found — expected for unregistered numbers
        print(f"[WARN] No business mapping found for phone {business_phone}")
        return None

    # Normalize return shape (decouple from raw Dynamo item)
    return {
        "business_id": item["business_id"],
        "phone": business_phone
    }

def get_or_create_conversation(business_id: str, customer_phone: str):
    """
    Production-safe conversation resolver.

    Guarantees:
    - Exactly one conversation per (business, customer)
    - State always exists and is fully populated
    - Safe under concurrent requests (retry on race)
    """

    pk = f"BUSINESS#{business_id}"
    sk = f"CONVO#{customer_phone}"

    # ---- 1. Try to fetch existing conversation ----
    try:
        response = table.get_item(
            Key={"PK": pk, "SK": sk},
            ConsistentRead=True  # important for correctness
        )
    except Exception as e:
        print(f"[ERROR] get_item failed: {str(e)}")
        raise

    item = response.get("Item")

    if item:
        return {
            "conversation_id": item["conversation_id"],
            "customer_phone": item["customer_phone"],
            "state": _merge_with_default_state(item.get("state")),
        }

    # ---- 2. Create new conversation (with race protection) ----
    conversation_id = str(uuid.uuid4())
    timestamp = time.time_ns()

    new_item = {
        "PK": pk,
        "SK": sk,
        "conversation_id": conversation_id,
        "customer_phone": customer_phone,
        "status": "active",
        "last_message_time": timestamp,
        "state": DEFAULT_STATE.copy()
    }

    try:
        table.put_item(
            Item=new_item,
            ConditionExpression="attribute_not_exists(PK)"  # prevents duplicates
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # ---- Another request created it first → fetch again ----
            print("[INFO] Race detected, fetching existing conversation")

            response = table.get_item(
                Key={"PK": pk, "SK": sk},
                ConsistentRead=True
            )
            item = response.get("Item")

            if not item:
                # logically can never hit
                raise Exception("Conversation race condition: item still missing")

            return {
                "conversation_id": item["conversation_id"],
                "customer_phone": item["customer_phone"],
                "state": _merge_with_default_state(item.get("state")),
            }
        else:
            print(f"[ERROR] put_item failed: {str(e)}")
            raise

    # ---- 3. Return newly created conversation ----
    return {
        "conversation_id": conversation_id,
        "customer_phone": customer_phone,
        "state": DEFAULT_STATE.copy()
    }

def merge_and_update_conversation_state(business_id: str, customer_phone: str, updates: dict):
    """
    Safely merge LLM state updates into conversation state.
    """

    pk = f"BUSINESS#{business_id}"
    sk = f"CONVO#{customer_phone}"

    # ---- 1. Fetch current conversation ----
    response = table.get_item(
        Key={"PK": pk, "SK": sk},
        ConsistentRead=True
    )

    item = response.get("Item")

    if not item:
        raise Exception("Conversation not found for state update")

    current_state = item.get("state", {}) or {}

    # ---- 2. Filter + clean updates ----
    valid_updates = {}

    for key, value in updates.items():
        if key not in ALLOWED_STATE_FIELDS:
            continue

        if isinstance(value, str):
            value = value.strip()

        valid_updates[key] = value

    if not valid_updates:
        print("[INFO] No valid state updates to apply")
        return current_state

    # ---- 3. Merge ----
    new_state = current_state.copy()
    new_state.update(valid_updates)

    # ---- 4. Persist ----
    table.update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression="SET #s = :state, last_message_time = :t",
        ExpressionAttributeNames={
            "#s": "state"
        },
        ExpressionAttributeValues={
            ":state": new_state,
            ":t": time.time_ns()
        }
    )

    return new_state

def get_latest_message(conversation_id: str):
    response = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={
            ":pk": f"CONVO#{conversation_id}"
        },
        ScanIndexForward=False,
        Limit=1
    )

    items = response.get("Items", [])
    return items[0] if items else None

def get_pending_messages(conversation_id: str):
    response = table.query(
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={
            ":pk": f"CONVO#{conversation_id}"
        }
    )

    items = response.get("Items", [])

    return [
        item for item in items
        if item.get("status") == "pending" and item.get("role") == "user"
    ]

def mark_messages_processed(messages):
    for msg in messages:
        table.update_item(
            Key={
                "PK": msg["PK"],
                "SK": msg["SK"]
            },
            UpdateExpression="SET #s = :processed",
            ExpressionAttributeNames={
                "#s": "status"
            },
            ExpressionAttributeValues={
                ":processed": "processed"
            }
        )

def append_message(conversation_id: str, role: str, text: str):
    """
    Append a message to a conversation.

    Guarantees:
    - append-only
    - ordered by timestamp
    - safe schema
    """

    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")

    if not text or not text.strip():
        print("[WARN]: Skipping empty message")
        return None

    text = text.strip()
    timestamp = str(time.time_ns())

    pk = f"CONVO#{conversation_id}"
    sk = f"MSG#{timestamp}"

    item = {
        "PK": pk,
        "SK": sk,
        "conversation_id": conversation_id,
        "role": role,
        "text": text,
        "timestamp": timestamp,
        "status": "pending" if role == "user" else "processed"
    }

    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)"
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            print("[WARN]: Rare message collision, skipping")
            return None
        else:
            print(f"[ERROR]: Failed to append message: {str(e)}")
            raise

    return item

def get_recent_messages(conversation_id: str, limit: int = 10):
    """
    Fetch the most recent messages for a conversation.

    Returns messages in chronological order (oldest → newest),
    ready for LLM consumption.
    """

    pk = f"CONVO#{conversation_id}"

    try:
        response = table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={
                ":pk": pk
            },
            ScanIndexForward=False,  # newest first
            Limit=limit
        )
    except Exception as e:
        print(f"[ERROR]: Failed to query messages: {str(e)}")
        raise

    items = response.get("Items", [])

    if not items:
        return []

    # Reverse to chronological order (oldest → newest)
    items.reverse()

    # Normalize output
    messages = []
    for item in items:
        messages.append({
            "role": item.get("role"),
            "text": item.get("text"),
            "timestamp": item.get("timestamp")
        })

    return messages