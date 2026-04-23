import json
import os
import boto3
from botocore.exceptions import ClientError

bedrock = boto3.client(
    "bedrock-runtime",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "300"))
TEMPERATURE = float(os.getenv("BEDROCK_TEMPERATURE", "0.2"))


SYSTEM_PROMPT = """
You are an SMS receptionist for a small business.

Your job:
- Respond briefly and naturally over SMS.
- Extract structured state updates from the conversation.
- Only ask for the next missing piece of information.
- Do not be verbose.
- Do not include markdown.
- Do not invent facts.
- If the user wants to schedule, collect: problem, address, appointment_time.
- Keep replies short, friendly, and practical.

You must return valid JSON with exactly this shape:
{
  "reply": "string",
  "state_updates": {
    "intent": null or string,
    "problem": null or string,
    "address": null or string,
    "appointment_time": null or string,
    "stage": null or string
  }
}

Rules for state_updates:
- Only include fields you want to update.
- If no change, return an empty object.
- stage should be a short workflow label like "intake", "collecting_address", "collecting_time", "ready_to_book".
- reply must always be present and non-empty.
- Output JSON only. No extra text.
""".strip()


def _safe_json_from_text(text: str) -> dict:
    text = text.strip()

    # direct JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    # try extracting first JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    raise ValueError(f"Model did not return valid JSON: {text}")


def _normalize_history(history: list[dict], state: dict) -> list[dict]:
    """
    Convert your app history into Bedrock Converse format.
    Also prepend a compact state summary as context.
    """
    messages = []

    state_summary = {
        "role": "user",
        "content": [
            {
                "text": (
                    "Current conversation state:\n"
                    f"{json.dumps(state or {}, ensure_ascii=False)}"
                )
            }
        ],
    }
    messages.append(state_summary)

    for msg in history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()

        if not content:
            continue

        # Bedrock Converse expects assistant/user
        if role not in {"user", "assistant"}:
            continue

        messages.append({
            "role": role,
            "content": [{"text": content}],
        })

    return messages


def call_llm(history: list[dict], state: dict) -> dict:
    """
    Expected return:
    {
      "reply": "...",
      "state_updates": {...}
    }
    """
    messages = _normalize_history(history, state)

    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            inferenceConfig={
                "maxTokens": MAX_TOKENS,
                "temperature": TEMPERATURE,
            },
        )
    except ClientError as e:
        print(f"[ERROR] Bedrock converse failed: {str(e)}")
        raise

    output_message = response.get("output", {}).get("message", {})
    content_blocks = output_message.get("content", [])

    text_parts = []
    for block in content_blocks:
        if "text" in block:
            text_parts.append(block["text"])

    raw_text = "\n".join(text_parts).strip()

    if not raw_text:
        raise ValueError("Bedrock returned empty text output")

    parsed = _safe_json_from_text(raw_text)

    reply = (parsed.get("reply") or "").strip()
    state_updates = parsed.get("state_updates") or {}

    if not reply:
        raise ValueError(f"Bedrock returned JSON without reply: {parsed}")

    if not isinstance(state_updates, dict):
        raise ValueError(f"state_updates must be an object: {parsed}")

    return {
        "reply": reply,
        "state_updates": state_updates,
    }