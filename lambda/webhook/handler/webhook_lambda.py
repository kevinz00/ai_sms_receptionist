import boto3
import json
import urllib.parse
import uuid

sqs = boto3.client("sqs")

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/267158290641/sms-processing-queue.fifo"

def lambda_handler(event, context):

    print("EVENT:", event)

    raw_body = event.get("body")

    if not raw_body:
        print("No body found")
        return {
            "statusCode": 200,
            "body": "no body"
        }

    body = urllib.parse.parse_qs(raw_body)

    customer_phone = body.get("From", [None])[0]
    business_phone = body.get("To", [None])[0]
    message = body.get("Body", [""])[0]

    if not customer_phone or not business_phone:
        return {"statusCode": 200, "body": "missing phone fields"}

    print(json.dumps({
        "event": "parsed_message",
        "customer_phone": customer_phone,
        "business_phone": business_phone,
        "message": message
    }))

    response = sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps({
            "customer_phone": customer_phone,
            "business_phone": business_phone,
            "message": message,
            "timestamp": time.time_ns()
        }),
        MessageGroupId = f"{business_phone}#{customer_phone}"
        MessageDeduplicationId=str(uuid.uuid4())
    )

    print("SQS RESPONSE:", response)

    return {
        "statusCode": 200,
        "body": "ok"
    }