---
# ai_receptionist DynamoDB Schema
---

## Table Structure
| Key | Type | Description |
|-----|------|-------------|
| PK  | String | Partition key |
| SK  | String | Sort key      |

---

## Key Prefix Conventions
| Prefix         | Example                |
|----------------|------------------------|
| BUSINESS#      | BUSINESS#b_123         |
| PHONE#         | PHONE#+16175550001     |
| CUSTOMER#      | CUSTOMER#+16175551234  |
| CONVO#         | CONVO#+16175551234     |
| MSG#           | MSG#1710000001         |
| APPOINTMENT#   | APPOINTMENT#a_789      |

---

## Entities

### Business
**PK:** `BUSINESS#{business_id}`  
**SK:** `PROFILE`

| Field          | Type    | Required | Description |
|----------------|---------|----------|-------------|
| business_id    | string  | Yes      |             |
| name           | string  | Yes      |             |
| primary_phone  | string  | Yes      |             |
| created_at     | number  | Yes      |             |
| plan           | string  | No       |             |

**Example:**
```json
{
  "PK": "BUSINESS#b_123",
  "SK": "PROFILE",
  "business_id": "b_123",
  "name": "John's Plumbing",
  "primary_phone": "+16175550001",
  "created_at": 1710000000,
  "plan": "pro"
}
```

---

### Phone
**PK:** `PHONE#{phone_number}`  
**SK:** `BUSINESS`

| Field         | Type   | Required | Description |
|---------------|--------|----------|-------------|
| phone_number  | string | Yes      |             |
| business_id   | string | Yes      |             |

**Example:**
```json
{
  "PK": "PHONE#+16175550001",
  "SK": "BUSINESS",
  "phone_number": "+16175550001",
  "business_id": "b_123"
}
```

---

### Customer
**PK:** `BUSINESS#{business_id}`  
**SK:** `CUSTOMER#{customer_phone}`

| Field          | Type   | Required | Description |
|----------------|--------|----------|-------------|
| customer_phone | string | Yes      |             |
| name           | string | No       |             |
| address        | string | No       |             |
| created_at     | number | Yes      |             |

**Example:**
```json
{
  "PK": "BUSINESS#b_123",
  "SK": "CUSTOMER#+16175551234",
  "customer_phone": "+16175551234",
  "created_at": 1710000000
}
```

---

### Conversation
**PK:** `BUSINESS#{business_id}`  
**SK:** `CONVO#{customer_phone}`

| Field              | Type   | Required | Description |
|--------------------|--------|----------|-------------|
| conversation_id    | string | Yes      |             |
| customer_phone     | string | Yes      |             |
| status             | string | Yes      | active/closed |
| last_message_time  | number | Yes      |             |
| state              | map    | Yes      |             |

**state** = `{ problem: string \| null, address: string \| null, appointment_time: string \| null, intent: string \| null }`

**Example:**
```json
{
  "PK": "BUSINESS#b_123",
  "SK": "CONVO#+16175551234",
  "conversation_id": "c_456",
  "customer_phone": "+16175551234",
  "status": "active",
  "last_message_time": 1710000000,
  "state": {
    "problem": "leaking sink",
    "address": null,
    "appointment_time": null,
    "intent": "repair_request"
  }
}
```

---

### Message
**PK:** `CONVO#{conversation_id}`  
**SK:** `MSG#{timestamp}`

| Field     | Type    | Required | Description |
|-----------|---------|----------|-------------|
| role      | string  | Yes      | "user" \| "assistant" |
| text      | string  | Yes      |             |
| timestamp | number  | Yes      |             |

**Example:**
```json
{
  "PK": "CONVO#c_456",
  "SK": "MSG#1710000001",
  "role": "user",
  "text": "My sink is leaking",
  "timestamp": 1710000001
}
```

---

### Appointment
**PK:** `BUSINESS#{business_id}`  
**SK:** `APPOINTMENT#{appointment_id}`

| Field           | Type    | Required | Description |
|-----------------|---------|----------|-------------|
| appointment_id  | string  | Yes      |             |
| customer_phone  | string  | Yes      |             |
| address         | string  | Yes      |             |
| scheduled_time  | string  | Yes      |             |
| status          | string  | Yes      |             |
| created_at      | number  | Yes      |             |

**Example:**
```json
{
  "PK": "BUSINESS#b_123",
  "SK": "APPOINTMENT#a_789",
  "appointment_id": "a_789",
  "customer_phone": "+16175551234",
  "address": "123 Main St",
  "scheduled_time": "2026-03-15T10:00:00",
  "status": "scheduled",
  "created_at": 1710000000
}
```

---

## Data Flow

```mermaid
flowchart TD
    A[Incoming SMS] --> B[Phone entity → find business]
    B --> C[Customer entity → identify user]
    C --> D[Conversation entity → load state]
    D --> E[Messages → get history]
    E --> F[LLM → generate response]
    F --> G[Update: Messages, Conversation state, Appointment (if needed)]
```

---

## Entity Questions

- **Business** → Who owns this system?
- **Phone** → Which business got the message?
- **Customer** → Who is texting?
- **Conversation** → What is happening right now?
- **Message** → What was said?
- **Appointment** → What was scheduled?

phone_number (string)       REQUIRED
business_id (string)        REQUIRED

{
  "PK": "PHONE#+16175550001",
  "SK": "BUSINESS",
  "phone_number": "+16175550001",
  "business_id": "b_123"
}


Customer
PK = BUSINESS#{business_id}
SK = CUSTOMER#{customer_phone}

customer_phone (string)     REQUIRED
name (string)               OPTIONAL
address (string)            OPTIONAL
created_at (number)         REQUIRED

{
  "PK": "BUSINESS#b_123",
  "SK": "CUSTOMER#+16175551234",
  "customer_phone": "+16175551234",
  "created_at": 1710000000
}


Conversation
PK = BUSINESS#{business_id}
SK = CONVO#{customer_phone}

conversation_id (string)        REQUIRED
customer_phone (string)         REQUIRED
status (string)                 REQUIRED (active / closed)
last_message_time (number)      REQUIRED
state (map)                     REQUIRED

state = {
  problem: string | null,
  address: string | null,
  appointment_time: string | null,
  intent: string | null
}

{
  "PK": "BUSINESS#b_123",
  "SK": "CONVO#+16175551234",
  "conversation_id": "c_456",
  "customer_phone": "+16175551234",
  "status": "active",
  "last_message_time": 1710000000,
  "state": {
    "problem": "leaking sink",
    "address": null,
    "appointment_time": null,
    "intent": "repair_request"
  }
}


Message
PK = CONVO#{conversation_id}
SK = MSG#{timestamp}

role (string)             REQUIRED ("user" | "assistant")
text (string)             REQUIRED
timestamp (number)        REQUIRED

{
  "PK": "CONVO#c_456",
  "SK": "MSG#1710000001",
  "role": "user",
  "text": "My sink is leaking",
  "timestamp": 1710000001
}


Appointment
PK = BUSINESS#{business_id}
SK = APPOINTMENT#{appointment_id}

appointment_id (string)       REQUIRED
customer_phone (string)       REQUIRED
address (string)              REQUIRED
scheduled_time (string)       REQUIRED
status (string)               REQUIRED
created_at (number)           REQUIRED

{
  "PK": "BUSINESS#b_123",
  "SK": "APPOINTMENT#a_789",
  "appointment_id": "a_789",
  "customer_phone": "+16175551234",
  "address": "123 Main St",
  "scheduled_time": "2026-03-15T10:00:00",
  "status": "scheduled",
  "created_at": 1710000000
}

Incoming SMS
   ↓
Phone entity → find business
   ↓
Customer entity → identify user
   ↓
Conversation entity → load state
   ↓
Messages → get history
   ↓
LLM → generate response
   ↓
Update:
   - Messages
   - Conversation state
   - Appointment (if needed)


Business → who owns this system?
Phone → which business got the message?
Customer → who is texting?
Conversation → what is happening right now?
Message → what was said?
Appointment → what was scheduled?