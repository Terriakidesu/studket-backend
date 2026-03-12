# Studket Backend API Reference

This document describes the HTTP API exposed by this repository as of March 11, 2026.

## Base URL and Routing

- Application router root: `/api`
- Versioned API root: `/api/v1`
- Static files and web pages are mounted separately and are not covered here.
- Realtime websocket endpoints are documented in the websocket section below.

Effective API URL pattern:

```text
/api/v1/<resource>
```

Examples:

- `/api/v1/auth/register`
- `/api/v1/listings/feed`
- `/api/v1/accounts/`

Realtime examples:

- `/ws/management`
- `/ws/users/12`

## Authentication and Access Rules

### Public API endpoints

These endpoints do not require a management session:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/seller-status/elevate`
- `POST /api/v1/auth/seller-status/request`

### User-facing API endpoints

These routes are currently mounted without the dashboard-session dependency:

- `/api/v1/listings`
- `/api/v1/listing-inventory`
- `/api/v1/listing-media`
- `/api/v1/tags`
- `/api/v1/listing-tags`
- `/api/v1/conversations`
- `/api/v1/messages`
- `/api/v1/transactions`
- `/api/v1/reviews`
- `/api/v1/transaction-qr`
- `/api/v1/notifications`
- `/api/v1/seller-reports`
- `/api/v1/profile-pictures`

Important note:

- These routes are user-facing in the sense that they no longer return the dashboard-only management-session error by default.
- Several of them are still thin CRUD-style endpoints and are not yet hardened with ownership or user-session checks.

### Management-protected API endpoints

The following routes require a valid session created by the web login flow for a:

- `management` account
- `superadmin` account

- `/api/v1/accounts`
- `/api/v1/user-profiles`
- `/api/v1/management-accounts`
- `/api/v1/listing-reports`
- `/api/v1/looking-for-reports`
- `/api/v1/conversation-reports`

The session check is enforced by `require_dashboard_api_session()` in [app/api/v1/dependencies.py](/abs/path/d:/Dev/Python/studket-backend/app/api/v1/dependencies.py).

Required session state:

- `request.session["account"]` must exist
- `request.session["account"]["account_type"]` must be `management` or `superadmin`
- `request.session["account_expires_at"]`, if present, must not be expired

Possible auth-related errors:

- `401 {"error": "Management or superadmin login required"}`
- `401 {"error": "Session expired. Please sign in again"}`
- `403 {"error": "Dashboard API access is restricted to management and superadmin accounts"}`

## Content Type

All documented endpoints use JSON request and response bodies unless otherwise noted.

Recommended request header:

```http
Content-Type: application/json
```

For websocket connections:

- Connect with a normal websocket client
- Send and receive JSON messages over the socket

## Websocket Endpoints

Base websocket paths:

- `/ws/management`
- `/ws/users/{account_id}`

These routes are mounted directly on the main app, not under `/api` or `/api/v1`.

Implementation sources:

- [app/realtime.py](/abs/path/d:/Dev/Python/studket-backend/app/realtime.py)
- [app/services/realtime.py](/abs/path/d:/Dev/Python/studket-backend/app/services/realtime.py)

### Connection Model

The realtime layer keeps three in-memory subscription groups:

- account-level connections
- conversation-level subscriptions
- management-wide connections

Events can therefore be sent:

- to one account
- to all subscribers of one conversation
- to all connected management sockets

### Management Socket

Endpoint:

- `/ws/management`

Access model:

- Requires a valid web session in `websocket.session`
- Session account must be `management` or `superadmin`
- If the session is missing, expired, malformed, or not an allowed account type, the socket closes with code `1008`

Bootstrap event sent immediately after connect:

```json
{
  "type": "bootstrap",
  "channel": "management",
  "account": {
    "account_id": 2,
    "username": "ops_manager",
    "account_type": "management"
  },
  "conversation_ids": [15, 18],
  "conversations": [
    {
      "conversation_id": 15,
      "conversation_type": "staff_support",
      "last_message_at": "2026-03-11T10:00:00+00:00",
      "message_count": 4,
      "other_account_id": 12,
      "other_username": "campusbuyer1",
      "other_account_type": "user"
    }
  ],
  "summary": {
    "pending_verifications": 3,
    "unread_messages": 6,
    "open_reports": 8
  }
}
```

Bootstrap fields:

- `type` always `bootstrap`
- `channel` always `management`
- `account` session account object from the web session
- `conversation_ids` `integer[]`
  - conversation IDs the account is already part of
- `conversations` `object[]`
  - recent conversation summary rows
- `summary.pending_verifications` `integer`
  - count of `seller_verification_request` rows with `status = "pending"`
- `summary.unread_messages` `integer`
  - count of unread user-sent messages across staff-user conversations
- `summary.open_reports` `integer`
  - combined count of open listing, looking-for, and seller reports

Supported client actions:

- `ping`
- `subscribe_conversation`
- `mark_conversation_read`
- `typing_status`
- `send_message`

#### Management action: `ping`

Client message:

```json
{
  "action": "ping"
}
```

Server response:

```json
{
  "type": "pong"
}
```

#### Management action: `subscribe_conversation`

Subscribes the current socket to broadcast events for a conversation the management account participates in.

Client message:

```json
{
  "action": "subscribe_conversation",
  "conversation_id": 15
}
```

Success response:

```json
{
  "type": "chat.subscribed",
  "conversation_id": 15
}
```

Behavior notes:

- The subscription is granted only if the conversation exists and the current account is a participant.
- Invalid or unauthorized conversation IDs are silently ignored.

#### Management action: `send_message`

Creates a message in a conversation and broadcasts it in realtime.

Client message:

```json
{
  "action": "send_message",
  "conversation_id": 15,
  "message_text": "We are reviewing your account."
}
```

Effects:

- Persists the message through the messaging service
- Broadcasts a `chat.typing` event with `is_typing = false` for the sender before the message event
- Broadcasts a `chat.message` event to:
  - all sockets subscribed to the conversation
  - the sender account’s sockets
  - the recipient account’s sockets
- If the recipient is a normal `user`, also creates a notification and emits `notification.created`
- If the recipient is management or superadmin, also broadcasts a `management.notification` event to all management sockets

#### Management action: `typing_status`

Broadcasts a transient typing state for a conversation participant.

Client message:

```json
{
  "action": "typing_status",
  "conversation_id": 15,
  "is_typing": true
}
```

Behavior notes:

- The conversation must exist.
- The current management account must be a participant in the conversation.
- The server broadcasts a `chat.typing` event to the conversation/account sockets for both participants.
- The server does not persist typing state in the database.

#### Management action: `mark_conversation_read`

Marks unread incoming messages in the open conversation as read for the current staff account.

Client message:

```json
{
  "action": "mark_conversation_read",
  "conversation_id": 15
}
```

Success response:

```json
{
  "type": "chat.read",
  "conversation_id": 15,
  "read_count": 3
}
```

### User Socket

Endpoint:

- `/ws/users/{account_id}`

Path arguments:

- `account_id` `integer`, required

Access model:

- The path account must exist
- The account must have `account_type = "user"`
- The account must not be banned
- If the account check fails, the socket closes with code `1008`

Important security note:

- This route currently authenticates only by path `account_id`.
- It does not verify a matching user session or token inside the websocket handler.
- Documentation here reflects the current implementation, not an ideal security design.

Bootstrap event sent immediately after connect:

```json
{
  "type": "bootstrap",
  "channel": "user",
  "account": {
    "account_id": 12,
    "username": "campusbuyer1",
    "account_type": "user",
    "account_status": "active"
  },
  "conversation_ids": [15],
  "conversations": [
    {
      "conversation_id": 15,
      "conversation_type": "staff_support",
      "last_message_at": "2026-03-11T10:00:00+00:00",
      "message_count": 4,
      "other_account_id": 2,
      "other_username": "ops_manager",
      "other_account_type": "management"
    }
  ],
  "notifications": [
    {
      "notification_id": 7,
      "user_id": 12,
      "notification_type": "chat_message",
      "title": "New message from ops_manager",
      "body": "We are reviewing your account.",
      "related_entity_type": "conversation",
      "related_entity_id": 15,
      "is_read": false,
      "read_at": null,
      "created_at": "2026-03-11T10:00:00+00:00"
    }
  ]
}
```

Bootstrap fields:

- `type` always `bootstrap`
- `channel` always `user`
- `account` basic account summary
- `conversation_ids` `integer[]`
- `conversations` `object[]`
  - same summary structure as the management bootstrap
- `notifications` `object[]`
  - up to 20 most recent user notifications

Supported client actions:

- `ping`
- `subscribe_conversation`
- `mark_conversation_read`
- `mark_notification_read`
- `typing_status`
- `send_message`

#### User action: `ping`

Client message:

```json
{
  "action": "ping"
}
```

Server response:

```json
{
  "type": "pong"
}
```

#### User action: `subscribe_conversation`

Client message:

```json
{
  "action": "subscribe_conversation",
  "conversation_id": 15
}
```

Success response:

```json
{
  "type": "chat.subscribed",
  "conversation_id": 15
}
```

Behavior notes:

- The conversation must exist.
- The path `account_id` must be a participant in that conversation.
- Invalid or unauthorized conversation IDs are silently ignored.

#### User action: `mark_notification_read`

Marks one notification as read and returns the updated notification payload.

Client message:

```json
{
  "action": "mark_notification_read",
  "notification_id": 7
}
```

Success response:

```json
{
  "type": "notification.updated",
  "notification": {
    "notification_id": 7,
    "user_id": 12,
    "notification_type": "chat_message",
    "title": "New message from ops_manager",
    "body": "We are reviewing your account.",
    "related_entity_type": "conversation",
    "related_entity_id": 15,
    "is_read": true,
    "read_at": "2026-03-11T10:05:00+00:00",
    "created_at": "2026-03-11T10:00:00+00:00"
  }
}
```

Behavior notes:

- If the notification does not exist or does not belong to the current user socket, the action is ignored.

#### User action: `mark_conversation_read`

Marks unread incoming messages in one conversation as read for the connected user.

Client message:

```json
{
  "action": "mark_conversation_read",
  "conversation_id": 15
}
```

Success response:

```json
{
  "type": "chat.read",
  "conversation_id": 15,
  "read_count": 2
}
```

#### User action: `send_message`

Client message:

```json
{
  "action": "send_message",
  "conversation_id": 15,
  "message_text": "Thanks for the update."
}
```

Effects:

- Persists the message through the messaging service
- Broadcasts a `chat.typing` event with `is_typing = false` for the sender before the message event
- Broadcasts a `chat.message` event to the conversation and both participant accounts
- If the recipient is also a `user`, creates a notification and emits `notification.created`

#### User action: `typing_status`

Client message:

```json
{
  "action": "typing_status",
  "conversation_id": 15,
  "is_typing": true
}
```

Behavior notes:

- The conversation must exist.
- The path `account_id` must be a participant in that conversation.
- The server broadcasts a `chat.typing` event to the conversation/account sockets for both participants.
- Typing state is transient only and is not stored in the database.

### Server Event Types

The websocket layer currently emits the following event types:

- `bootstrap`
- `pong`
- `chat.subscribed`
- `chat.read`
- `chat.typing`
- `chat.message`
- `notification.created`
- `notification.updated`
- `management.summary`
- `management.notification`
- `error`

#### Event: `chat.typing`

Emitted when one participant starts or stops typing in a conversation.

Payload shape:

```json
{
  "type": "chat.typing",
  "conversation_id": 15,
  "account_id": 12,
  "username": "campusbuyer1",
  "account_type": "user",
  "is_typing": true
}
```

#### Event: `chat.read`

Emitted after a socket marks a conversation's unread incoming messages as read.

Payload shape:

```json
{
  "type": "chat.read",
  "conversation_id": 15,
  "read_count": 2
}
```

#### Event: `chat.message`

Emitted when either side sends a message.

Payload shape:

```json
{
  "type": "chat.message",
  "conversation_id": 15,
  "message": {
    "message_id": 22,
    "conversation_id": 15,
    "sender_id": 2,
    "message_text": "We are reviewing your account.",
    "sent_at": "2026-03-11T10:00:00+00:00",
    "is_read": false,
    "sender_username": "ops_manager"
  }
}
```

#### Event: `notification.created`

Emitted when the backend creates a user notification and pushes it to the connected account.

Payload shape:

```json
{
  "type": "notification.created",
  "notification": {
    "notification_id": 7,
    "user_id": 12,
    "notification_type": "chat_message",
    "title": "New message from ops_manager",
    "body": "We are reviewing your account.",
    "related_entity_type": "conversation",
    "related_entity_id": 15,
    "is_read": false,
    "read_at": null,
    "created_at": "2026-03-11T10:00:00+00:00"
  }
}
```

Typical notification types seen in the codebase:

- `chat_message`
- `welcome`
- `seller_verification`
- `listing_inquiry`
- `listing_inquiry_accepted`
- `listing_inquiry_rejected`
- `listing_removed`
- `account_warning`
- `account_status`

#### Event: `notification.updated`

Emitted after a user marks one notification as read.

Payload shape:

```json
{
  "type": "notification.updated",
  "notification": {
    "notification_id": 7,
    "is_read": true,
    "read_at": "2026-03-11T10:05:00+00:00"
  }
}
```

#### Event: `management.notification`

Broadcast to all connected management sockets when a user-facing chat event needs management awareness.

Payload shape:

```json
{
  "type": "management.notification",
  "category": "chat",
  "title": "New user message",
  "body": "campusbuyer1 sent a message.",
  "conversation_id": 15,
  "account_id": 2
}
```

#### Event: `management.summary`

Broadcast to management sockets when dashboard summary counts need to update live.

Payload shape:

```json
{
  "type": "management.summary",
  "summary": {
    "pending_verifications": 2,
    "unread_messages": 5
  }
}
```

#### Event: `error`

Returned when a client sends an unsupported action.

Payload shape:

```json
{
  "type": "error",
  "detail": "Unsupported action"
}
```

### Disconnect and Cleanup Behavior

- Socket acceptance happens inside the realtime hub on successful connect.
- On disconnect or send failure, the hub removes the socket from:
  - account-level subscriptions
  - management-wide subscriptions
  - conversation subscriptions
- Empty conversation subscription buckets are cleaned up automatically.

## Response and Error Conventions

### Generic CRUD success responses

The shared CRUD router in [app/api/v1/common.py](/abs/path/d:/Dev/Python/studket-backend/app/api/v1/common.py) is used by most resources.

- `GET /resource/` returns `200` and a JSON array
- `GET /resource/{item_id}` returns `200` and a JSON object
- `POST /resource/` returns `201` and the created JSON object
- `PATCH /resource/{item_id}` returns `200` and the updated JSON object
- `DELETE /resource/{item_id}` returns `204` with an empty body

### Generic CRUD not-found response

If an item is not found, the shared CRUD router raises:

```json
{
  "detail": "<ModelName> not found"
}
```

Example:

```json
{
  "detail": "Account not found"
}
```

### Custom auth error response

The auth endpoints return structured error objects:

```json
{
  "detail": {
    "error": "Invalid credentials"
  }
}
```

### Custom listings error responses

The listings router may return plain-string `detail` values such as:

- `"Listing not found"`
- `"seller_id is required"`
- `"User profile not found"`

## Endpoint Inventory

### Human-readable docs

- HTML API reference: `/docs`
- LLM-friendly markdown/plaintext API reference: `/docs/llm`
- LLM discovery file: `/llms.txt`
- Swagger UI: `/swagger`

### Custom routers

- `/api/v1/auth`
- `/api/v1/listings`
- `/api/v1/listing-media`
- `/api/v1/transactions`
- `/api/v1/transaction-qr`
- `/api/v1/profile-pictures`

### CRUD routers

- `/api/v1/accounts`
- `/api/v1/user-profiles`
- `/api/v1/management-accounts`
- `/api/v1/listing-inventory`
- `/api/v1/tags`
- `/api/v1/listing-tags`
- `/api/v1/listing-reports`
- `/api/v1/looking-for-reports`
- `/api/v1/conversations`
- `/api/v1/conversation-reports`
- `/api/v1/messages`
- `/api/v1/transactions`
- `/api/v1/reviews`
- `/api/v1/transaction-qr`
- `/api/v1/notifications`
- `/api/v1/seller-reports`

## Auth Endpoints

Base path: `/api/v1/auth`

### POST `/api/v1/auth/register`

Registers a new account.

Access:

- Public

Request body:

```json
{
  "email": "user@example.com",
  "username": "campusbuyer1",
  "password": "StrongPassword123!",
  "account_type": "user",
  "first_name": "Ari",
  "last_name": "Lopez",
  "campus": "North Campus",
  "role_name": null,
  "superadmin_code": null
}
```

Arguments:

- `email` `string`, required
  - Normalized to lowercase before storage.
  - Must be unique.
- `username` `string`, required
  - Stored as provided after trimming.
  - Must be unique.
- `password` `string`, required
  - Must pass password strength validation in the auth service.
- `account_type` `string`, optional, default `user`
  - Allowed values: `user`, `management`, `superadmin`
  - `user` creates a `UserProfile`
  - `management` creates a `ManagementAccount`
  - `superadmin` requires `superadmin_code`
- `first_name` `string | null`, optional
  - Used for `UserProfile` or `ManagementAccount`
- `last_name` `string | null`, optional
  - Used for `UserProfile` or `ManagementAccount`
- `campus` `string | null`, optional
  - Used only for `user` registrations
- `role_name` `string | null`, optional
  - Used only for `management` registrations
  - Defaults to `"manager"` if omitted or blank
- `superadmin_code` `string | null`, optional
  - Required only when `account_type` is `superadmin`

Behavior notes:

- Marketplace users are created as `account_type: "user"`.
- The API exposes both `marketplace_role` and `trusted_seller`.
- For `user` accounts:
  - `marketplace_role` is `buyer` by default
  - It becomes `seller` only after explicit seller elevation
  - `trusted_seller` is a separate staff-controlled trust status

Success response:

```json
{
  "message": "Registered successfully",
  "account_id": 12,
  "email": "user@example.com",
  "username": "campusbuyer1",
  "account_type": "user",
  "marketplace_role": "buyer",
  "trusted_seller": false
}
```

Possible errors:

- `400 {"detail":{"error":"Email and username are required"}}`
- `400 {"detail":{"error":"Password is required"}}`
- `400 {"detail":{"error":"Invalid account type"}}`
- `400 {"detail":{"error":"Email or username already registered"}}`
- `400 {"detail":{"error":"Superadmin registration is disabled"}}`
- `400 {"detail":{"error":"Invalid superadmin invite code"}}`
- `400` with password-strength validation details

### POST `/api/v1/auth/login`

Authenticates an existing account.

Access:

- Public

Request body:

```json
{
  "email_or_username": "campusbuyer1",
  "password": "StrongPassword123!",
  "account_type": "user"
}
```

Arguments:

- `email_or_username` `string`, required
  - Can match `account.email` or `account.username`
- `password` `string`, required
- `account_type` `string | null`, optional
  - If provided, login is restricted to that account type
  - Allowed values are the same as registration: `user`, `management`, `superadmin`

Success response:

```json
{
  "message": "Login successful",
  "account": {
    "account_id": 12,
    "email": "user@example.com",
    "username": "campusbuyer1",
    "account_type": "user",
    "account_status": "active",
    "marketplace_role": "buyer",
    "trusted_seller": false
  }
}
```

Possible errors:

- `401 {"detail":{"error":"Credentials are required"}}`
- `401 {"detail":{"error":"Invalid credentials"}}`
- `401 {"detail":{"error":"Account is not active"}}`

### POST `/api/v1/auth/seller-status/elevate`

Enables seller access for a normal marketplace user.

Access:

- Public

Request body:

```json
{
  "account_id": 12
}
```

Arguments:

- `account_id` `integer`, required
  - Must reference an existing `Account`
  - The account must have `account_type = "user"`
  - The account must have a `UserProfile`

Behavior notes:

- This is the buyer -> seller elevation endpoint.
- It does not require staff approval.
- It does not grant trusted-seller status.
- Existing accounts that already have normal listings are backfilled to seller status automatically at startup.

Success response:

```json
{
  "message": "Seller access enabled",
  "account_id": 12,
  "marketplace_role": "seller",
  "trusted_seller": false
}
```

Possible errors:

- `400 {"detail":{"error":"User account not found"}}`
- `400 {"detail":{"error":"User profile not found"}}`

### POST `/api/v1/auth/seller-status/request`

Creates a trusted-seller verification request for a normal marketplace user.

Access:

- Public

Request body:

```json
{
  "account_id": 12,
  "submission_note": "I want trusted seller status for my store."
}
```

Arguments:

- `account_id` `integer`, required
  - Must reference an existing `Account`
  - The account must have `account_type = "user"`
- `submission_note` `string | null`, optional
  - Freeform reason or context for requesting trusted-seller review

Behavior notes:

- This endpoint is not the buyer -> seller elevation flow.
- Use `POST /api/v1/auth/seller-status/elevate` first when a buyer wants seller access.
- This request only asks staff to grant trusted-seller status.
- If an identical pending request already exists, the API returns that existing request instead of creating a duplicate.
- If the user is already a trusted seller, the request is rejected.

Success response:

```json
{
  "message": "Trusted seller verification request submitted",
  "request_id": 5,
  "account_id": 12,
  "status": "pending"
}
```

Possible errors:

- `400 {"detail":{"error":"User account not found"}}`
- `400 {"detail":{"error":"User profile not found"}}`
- `400 {"detail":{"error":"User is already a trusted seller"}}`

## Listings Endpoints

Base path: `/api/v1/listings`

This router is custom and does not follow the generic CRUD behavior exactly.

### Listing role rules

- Only users with seller access can create standard listings.
- Any marketplace user with a valid `UserProfile` can also create `looking_for` posts.
- Seller access is enabled through `POST /api/v1/auth/seller-status/elevate`.
- Trusted-seller verification is a separate trust signal and does not gate listing creation.
- The database column is still `seller_id`, but the API now also exposes a neutral alias:
  - `owner_id` for all listing types
  - `poster_id` for `looking_for` posts

### GET `/api/v1/listings/feed`

Returns a recommendation-style listing feed.

Access:

- User-facing route

Query arguments:

- `user_id` `integer | null`, optional
  - Used to personalize recommendations
- `tags` `string[] | null`, optional
  - Repeating query parameter
  - Example: `?tags=books&tags=electronics`
- `limit` `integer`, optional, default `20`
  - Minimum `1`
  - Maximum `100`

Response shape:

```json
{
  "user_id": 12,
  "personalized": true,
  "tags": ["books"],
  "count": 2,
  "items": [
    {
      "listing_id": 9,
      "seller_id": 44,
      "owner_id": 44,
      "title": "Linear Algebra Book",
      "description": "Used but clean",
      "price": 350.0,
      "budget_min": null,
      "budget_max": null,
      "listing_type": "single_item",
      "condition": "used",
      "status": "available",
      "created_at": "2026-03-11T09:12:00",
      "media": [
        {
          "media_id": 3,
          "listing_id": 9,
          "file_path": "/static/listing-media/9/example.jpg",
          "file_url": "/static/listing-media/9/example.jpg",
          "sort_order": 0
        }
      ],
      "primary_media_url": "/static/listing-media/9/example.jpg",
      "seller_username": "seller44",
      "seller_campus": "North Campus",
      "tags": ["books", "math"],
      "recommendation_score": 7.8,
      "recommendation_reasons": ["available", "recent"],
      "seller_is_verified": true,
      "seller_is_trusted": true,
      "seller_average_rating": 4.9,
      "seller_review_count": 12
    }
  ]
}
```

Feed item fields:

- Standard serialized `Listing` columns
- `owner_id` `integer | null`
- `share_token` `string | null`
- `share_url` `string | null`
- `poster_id` `integer | null`
  - Present only for `looking_for` posts
- `media` `object[]`
- `primary_media_url` `string | null`
- `seller_username` `string | null`
- `seller_campus` `string | null`
- `tags` `string[]`
- `recommendation_score` `number`
- `recommendation_reasons` `string[]`
- `seller_is_verified` `boolean`
- `seller_is_trusted` `boolean`
- `seller_average_rating` `number | null`
- `seller_review_count` `integer`

### Listing inquiries

The listings router also manages buyer/requester inquiries for both normal listings and `looking_for` posts.

Inquiry behavior:

- one pending inquiry is allowed per `listing_id + inquirer_id`
- the chat conversation may be reused between the same two users
- inquiry status is stored separately from chat as:
  - `pending`
  - `accepted`
  - `rejected`
- only the listing owner can accept or reject an inquiry

Inquiry payload fields:

- `inquiry_id` `integer`
- `conversation_id` `integer`
- `conversation_type` `string | null`
- `listing_id` `integer`
- `listing_type` `string | null`
- `listing_title` `string | null`
- `listing_status` `string | null`
- `owner_id` `integer | null`
- `owner_username` `string | null`
- `inquirer_id` `integer | null`
- `inquirer_username` `string | null`
- `offered_price` `number | null`
- `status` `string`
- `response_note` `string | null`
- `responded_by` `integer | null`
- `responded_at` `datetime | null`
- `created_at` `datetime | null`
- `is_owner_view` `boolean`
- `last_message` `object | null`

`last_message`, when present, uses the same message shape as `/api/v1/messages`.

### GET `/api/v1/listings/search`

Searches listings using text, filters, and optional owner filtering.

Access:

- User-facing route

Query arguments:

- `q` `string | null`, optional
  - Matches title, description, and tag names
- `listing_type` `string | null`, optional
  - Common values in this codebase:
    - `single_item`
    - `stock_item`
    - `looking_for`
- `min_price` `number | null`, optional
- `max_price` `number | null`, optional
- `tag` `string | null`, optional
  - Exact case-insensitive tag filter
- `seller_id` `integer | null`, optional
  - Legacy owner filter
- `owner_id` `integer | null`, optional
  - Preferred alias for `seller_id`
  - If both are provided, `owner_id` wins
- `limit` `integer`, optional, default `20`
  - Minimum `1`
  - Maximum `100`

Response shape:

```json
{
  "query": "algebra",
  "count": 1,
  "items": [
    {
      "listing_id": 9,
      "seller_id": 44,
      "owner_id": 44,
      "title": "Linear Algebra Book",
      "description": "Used but clean",
      "price": 350.0,
      "budget_min": null,
      "budget_max": null,
      "listing_type": "single_item",
      "condition": "used",
      "status": "available",
      "created_at": "2026-03-11T09:12:00",
      "media": [
        {
          "media_id": 3,
          "listing_id": 9,
          "file_path": "/static/listing-media/9/example.jpg",
          "file_url": "/static/listing-media/9/example.jpg",
          "sort_order": 0
        }
      ],
      "primary_media_url": "/static/listing-media/9/example.jpg",
      "seller_username": "seller44",
      "seller_campus": "North Campus",
      "tags": ["books", "math"],
      "search_score": 8.5,
      "search_reasons": ["title_match"],
      "seller_is_verified": true,
      "seller_is_trusted": true,
      "seller_average_rating": 4.9,
      "seller_review_count": 12
    }
  ]
}
```

### GET `/api/v1/listings/`

Lists all listings.

Access:

- User-facing route

Arguments:

- None

Response:

- Array of serialized listings
- Includes:
  - all table columns
  - `price`
  - `budget_min`
  - `budget_max`
  - `owner_id`
  - `share_token`
  - `share_url`
  - `poster_id` for `looking_for`
  - `media`
  - `primary_media_url`

### GET `/api/v1/listings/users/{account_id}`

Lists all listings created by one user account.

Access:

- User-facing route

Path arguments:

- `account_id` `integer`, required
  - Must point to an existing `user` account

Behavior notes:

- Returns both normal listings and `looking_for` posts for that user
- Results are ordered by:
  - `created_at DESC`
  - `listing_id DESC`
- Each item uses the same enriched listing payload as the other listings endpoints

Response shape:

```json
{
  "account_id": 44,
  "count": 1,
  "items": [
    {
      "listing_id": 9,
      "seller_id": 44,
      "owner_id": 44,
      "title": "Linear Algebra Book",
      "listing_type": "single_item",
      "status": "available",
      "tags": ["books", "math"],
      "media": [],
      "primary_media_url": null,
      "seller_profile_available": true
    }
  ]
}
```

Response fields:

- `account_id` `integer`
- `count` `integer`
- `items` `object[]`
  - each item also includes `share_token` and `share_url`

Possible errors:

- `404 {"detail":"User account not found"}`

### GET `/api/v1/listings/users/{account_id}/looking-for`

Lists only the `looking_for` posts created by one user account.

Access:

- User-facing route

Path arguments:

- `account_id` `integer`, required
  - Must point to an existing `user` account

Behavior notes:

- Filters the user’s listings to `listing_type = "looking_for"`
- Results are ordered by:
  - `created_at DESC`
  - `listing_id DESC`

Response shape:

```json
{
  "account_id": 44,
  "listing_type": "looking_for",
  "count": 1,
  "items": [
    {
      "listing_id": 11,
      "seller_id": 44,
      "owner_id": 44,
      "poster_id": 44,
      "title": "Looking for a used calculator",
      "price": 500,
      "budget_min": 400,
      "budget_max": 650,
      "listing_type": "looking_for",
      "status": "available",
      "tags": ["calculator"],
      "media": [],
      "primary_media_url": null,
      "seller_profile_available": true
    }
  ]
}
```

Response fields:

- `account_id` `integer`
- `listing_type` always `"looking_for"`
- `count` `integer`
- `items` `object[]`
  - each item also includes `share_token` and `share_url`

Possible errors:

- `404 {"detail":"User account not found"}`

### GET `/api/v1/listings/share/{share_token}`

Fetches one listing by its permanent share token.

Access:

- User-facing route

Path arguments:

- `share_token` `string`, required

Behavior notes:

- share tokens are stable, unique per listing, and backfilled automatically for existing listings
- the response shape matches the normal single-listing endpoint

Possible errors:

- `404 {"detail":"Listing not found"}`

### GET `/api/v1/listings/users/{account_id}/inquiries`

Lists inquiry records involving one user account.

Access:

- User-facing route

Path arguments:

- `account_id` `integer`, required
  - Must point to an existing `user` account

Query arguments:

- `listing_type` `string | null`, optional
  - Common values:
    - `single_item`
    - `stock_item`
    - `looking_for`

Behavior notes:

- returns inquiries where the user is either:
  - the listing owner
  - the inquirer
- results are ordered newest first using latest message time, then inquiry id

Response shape:

```json
{
  "account_id": 44,
  "listing_type": null,
  "count": 1,
  "items": [
    {
      "inquiry_id": 7,
      "conversation_id": 12,
      "listing_id": 9,
      "listing_type": "single_item",
      "listing_title": "Linear Algebra Book",
      "owner_id": 44,
      "inquirer_id": 12,
      "offered_price": 300,
      "status": "pending",
      "last_message": {
        "message_id": 55,
        "conversation_id": 12,
        "sender_id": 12,
        "sender_username": "campusbuyer1",
        "message_text": "Is this still available?",
        "sent_at": "2026-03-11T10:00:00Z",
        "is_read": false
      }
    }
  ]
}
```

Possible errors:

- `404 {"detail":"User account not found"}`
- `404 {"detail":"User profile not found"}`

### GET `/api/v1/listings/{item_id}/inquiries`

Lists inquiry records for one listing or `looking_for` post.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required

Query arguments:

- `account_id` `integer`, required

Behavior notes:

- if `account_id` is the listing owner, all inquiries for that listing are returned
- otherwise, only that user’s inquiry for the listing is returned

Response shape:

```json
{
  "listing_id": 9,
  "listing_type": "single_item",
  "account_id": 44,
  "count": 1,
  "items": [
    {
      "inquiry_id": 7,
      "conversation_id": 12,
      "listing_id": 9,
      "status": "pending"
    }
  ]
}
```

Possible errors:

- `404 {"detail":"Listing not found"}`
- `404 {"detail":"User account not found"}`
- `404 {"detail":"User profile not found"}`
- `400 {"detail":"Listing owner not found"}`

### POST `/api/v1/listings/{item_id}/inquiries`

Creates a new inquiry for a listing or `looking_for` post.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required

Request body:

```json
{
  "account_id": 12,
  "message_text": "Is this still available?",
  "offered_price": 300
}
```

Arguments:

- `account_id` `integer`, required
- `message_text` `string | null`, optional
- `offered_price` `number | null`, optional

Behavior notes:

- the caller must be a normal user account with a `UserProfile`
- the caller cannot inquire on their own listing
- if a pending inquiry already exists for the same listing and inquirer, the API returns that active inquiry instead of creating another
- the underlying conversation may be reused if the two users already have a chat thread
- if `message_text` is provided, the text is posted into the conversation and the owner receives a `listing_inquiry` notification

Response fields:

- `message` `string`
- `created` `boolean`
- `reused` `boolean`
- `conversation` `object`
  - Serialized inquiry payload
- `initial_message` `object | null`

Possible errors:

- `404 {"detail":"Listing not found"}`
- `404 {"detail":"User account not found"}`
- `404 {"detail":"User profile not found"}`
- `400 {"detail":"Listing owner not found"}`
- `400 {"detail":"You cannot open an inquiry on your own listing"}`

### POST `/api/v1/listings/{item_id}/inquiries/{inquiry_id}/accept`

Accepts a pending inquiry.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required
- `inquiry_id` `integer`, required

Request body:

```json
{
  "account_id": 44,
  "response_note": "Let's proceed with this offer."
}
```

Arguments:

- `account_id` `integer`, required
- `response_note` `string | null`, optional

Behavior notes:

- only the listing owner can accept
- only inquiries with `status = pending` can be accepted
- on success, the inquirer receives a `listing_inquiry_accepted` notification

Possible errors:

- `404 {"detail":"Listing not found"}`
- `404 {"detail":"Inquiry not found"}`
- `403 {"detail":"Only the listing owner can accept an inquiry"}`
- `400 {"detail":"Only pending inquiries can be accepted"}`

### POST `/api/v1/listings/{item_id}/inquiries/{inquiry_id}/reject`

Rejects a pending inquiry.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required
- `inquiry_id` `integer`, required

Request body:

```json
{
  "account_id": 44,
  "response_note": "I’m not taking this offer."
}
```

Arguments:

- `account_id` `integer`, required
- `response_note` `string | null`, optional

Behavior notes:

- only the listing owner can reject
- only inquiries with `status = pending` can be rejected
- on success, the inquirer receives a `listing_inquiry_rejected` notification

Possible errors:

- `404 {"detail":"Listing not found"}`
- `404 {"detail":"Inquiry not found"}`
- `403 {"detail":"Only the listing owner can reject an inquiry"}`
- `400 {"detail":"Only pending inquiries can be rejected"}`

### GET `/api/v1/listings/{item_id}`

Fetches one listing by `listing_id`.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required
  - Mapped to `Listing.listing_id`

Response:

- Serialized listing object
- Includes `owner_id`
- Includes `share_token`
- Includes `share_url`
- Includes `poster_id` when `listing_type == "looking_for"`
- Includes `media`
- Includes `primary_media_url`

Possible errors:

- `404 {"detail":"Listing not found"}`

### Public share page

The backend also serves a browser-friendly public share route outside `/api/v1`:

- `GET /share/{share_token}`

Behavior notes:

- resolves the permanent share token to the listing
- renders a public HTML page with:
  - title
  - status
  - seller username
  - primary media
  - description
  - tags
- the page also links to:
  - `/api/v1/listings/share/{share_token}`

### GET `/api/v1/listings/{item_id}/media`

Returns the ordered media collection for a single listing.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required
  - Mapped to `Listing.listing_id`

Response shape:

```json
{
  "listing_id": 9,
  "count": 2,
  "items": [
    {
      "media_id": 3,
      "listing_id": 9,
      "file_path": "/static/listing-media/9/example.jpg",
      "file_url": "/static/listing-media/9/example.jpg",
      "sort_order": 0
    },
    {
      "media_id": 4,
      "listing_id": 9,
      "file_path": "/static/listing-media/9/example-2.jpg",
      "file_url": "/static/listing-media/9/example-2.jpg",
      "sort_order": 1
    }
  ],
  "primary_media_url": "/static/listing-media/9/example.jpg"
}
```

Response fields:

- `listing_id` `integer`
- `count` `integer`
- `items` `object[]`
- `primary_media_url` `string | null`

Media item fields:

- `media_id` `integer`
- `listing_id` `integer`
- `file_path` `string | null`
- `file_url` `string | null`
- `sort_order` `integer | null`

Possible errors:

- `404 {"detail":"Listing not found"}`

### POST `/api/v1/listings/`

Creates a listing or a `looking_for` post.

Access:

- User-facing route

Request body fields:

- `seller_id` `integer`, optional if `owner_id` is provided
  - Internal ownership field
- `owner_id` `integer`, optional alias
  - Preferred public alias for `seller_id`
- `title` `string`, required by database model
- `description` `string | null`, optional
- `price` `number | null`, optional
- `budget_min` `number | null`, optional
  - Intended for `looking_for` posts
- `budget_max` `number | null`, optional
  - Intended for `looking_for` posts
- `listing_type` `string | null`, optional
  - Important values:
    - `single_item`
    - `stock_item`
    - `looking_for`
- `condition` `string | null`, optional
- `status` `string | null`, optional
  - Defaults to `available` if omitted

Validation rules:

- A creator identifier is required:
  - `owner_id` or `seller_id`
- For `looking_for`:
  - the creator only needs an existing `UserProfile`
  - `budget_min` and `budget_max` are accepted
  - if both are provided, `budget_min` cannot be greater than `budget_max`
- For normal listings:
  - the creator must have an existing `UserProfile`
  - the creator must have seller access enabled
- Trusted-seller approval is not required for creating or updating listings

Examples:

Standard listing:

```json
{
  "owner_id": 44,
  "title": "Linear Algebra Book",
  "description": "Used but clean",
  "price": 350,
  "listing_type": "single_item",
  "condition": "used",
  "status": "available"
}
```

Looking-for post:

```json
{
  "owner_id": 12,
  "title": "Looking for a used calculator",
  "description": "Need one before finals week",
  "price": 500,
  "budget_min": 400,
  "budget_max": 650,
  "listing_type": "looking_for",
  "status": "available"
}
```

Possible errors:

- `400 {"detail":"seller_id is required"}`
- `400 {"detail":"budget_min cannot be greater than budget_max"}`
- `404 {"detail":"User profile not found"}`
- `403 {"detail":"Seller access required for normal listings"}`

### PATCH `/api/v1/listings/{item_id}`

Updates an existing listing.

Access:

- User-facing route

Path arguments:

- `item_id` `integer`, required

Request body:

- Any writable `Listing` field
- `owner_id` may be used instead of `seller_id`
- `budget_min` and `budget_max` may be used for `looking_for` posts

Validation notes:

- If `seller_id`, `owner_id`, or `listing_type` changes, creator-role validation runs again.
- If the effective listing type is `looking_for`, `budget_min <= budget_max` is enforced.
- If the listing is changed away from `looking_for`, stored `budget_min` and `budget_max` are cleared.
- The validation only checks that the owner has a valid `UserProfile`.

### DELETE `/api/v1/listings/{item_id}`

Deletes a listing by `listing_id`.

Access:

- Management session required

Path arguments:

- `item_id` `integer`, required

Success response:

- `204 No Content`

## Shared CRUD Pattern

Every CRUD router created with `create_crud_router()` exposes the same endpoint set:

- `GET /api/v1/<resource>/`
- `GET /api/v1/<resource>/{item_id}`
- `POST /api/v1/<resource>/`
- `PATCH /api/v1/<resource>/{item_id}`
- `DELETE /api/v1/<resource>/{item_id}`

The CRUD-style routers below do not all share the same access level anymore.

Exceptions:

- `/api/v1/listings` is a custom router documented earlier.
- `/api/v1/listing-media` is also a custom router and is documented separately below.
- `/api/v1/transactions` now has a custom cancellation workflow endpoint documented below, while the legacy CRUD routes still exist.
- `/api/v1/transaction-qr` now has custom QR workflow endpoints documented below, while the legacy CRUD routes still exist.
- `/api/v1/profile-pictures` is also a custom router and is documented separately below.

User-facing CRUD-style routers currently mounted without the dashboard dependency:

- `/api/v1/listing-inventory`
- `/api/v1/tags`
- `/api/v1/listing-tags`
- `/api/v1/conversations`
- `/api/v1/messages`
- `/api/v1/reviews`
- `/api/v1/notifications`
- `/api/v1/seller-reports`

Dashboard/staff-only CRUD routers:

- `/api/v1/accounts`
- `/api/v1/user-profiles`
- `/api/v1/management-accounts`
- `/api/v1/listing-reports`
- `/api/v1/looking-for-reports`
- `/api/v1/conversation-reports`

### Generic path argument

- `item_id` `integer`, required
  - Mapped to the configured primary key field for that resource

### Generic POST body

- JSON object containing model fields
- The shared CRUD layer does not do schema validation beyond what SQLAlchemy and the database enforce

### Generic PATCH body

- JSON object containing partial updates
- Unknown keys are ignored because the code checks `hasattr(instance, field)` before assignment

## CRUD Resource Reference

The following subsections list each resource path, its primary key field, and the accepted JSON body fields.

### Accounts

Base path:

- `/api/v1/accounts`

Primary key:

- `account_id`

Fields:

- `account_id` `integer`
- `email` `string`, required on create
- `username` `string`, required on create
- `password_hash` `string`, required on direct create
- `account_type` `string`, required on create
- `account_status` `string | null`
- `warning_count` `integer | null`
- `last_warned_at` `datetime | null`
- `created_at` `datetime | null`

Notes:

- Direct CRUD creation bypasses the auth service. If you use this route, you must supply `password_hash`, not a plain password.
- For normal account registration, prefer `POST /api/v1/auth/register`.

### User Profiles

Base path:

- `/api/v1/user-profiles`

Primary key:

- `user_id`

Fields:

- `user_id` `integer`, required on create
- `first_name` `string | null`
- `last_name` `string | null`
- `campus` `string | null`
- `profile_photo` `string | null`
- `is_seller` `boolean | null`
- `is_verified` `boolean | null`
- `created_at` `datetime | null`

### Management Accounts

Base path:

- `/api/v1/management-accounts`

Primary key:

- `manager_id`

Fields:

- `manager_id` `integer`, required on create
- `first_name` `string | null`
- `last_name` `string | null`
- `role_name` `string | null`
- `created_at` `datetime | null`

### Listing Inventory

Base path:

- `/api/v1/listing-inventory`

Primary key:

- `inventory_id`

Fields:

- `inventory_id` `integer`
- `listing_id` `integer | null`
- `quantity_available` `integer | null`
- `max_daily_limit` `integer | null`
- `restockable` `boolean | null`

### Listing Media

Base path:

- `/api/v1/listing-media`

Access:

- User-facing route

This resource is not a generic CRUD wrapper anymore. It supports both direct media record creation and multipart image upload.

Response shape:

```json
{
  "media_id": 3,
  "listing_id": 9,
  "file_path": "/static/listing-media/9/abc123def456.jpg",
  "file_url": "/static/listing-media/9/abc123def456.jpg",
  "sort_order": 0
}
```

Response fields:

- `media_id` `integer`
- `listing_id` `integer | null`
- `file_path` `string | null`
- `file_url` `string | null`
  - Public URL alias for `file_path`
- `sort_order` `integer | null`

#### GET `/api/v1/listing-media/`

Lists all listing media rows ordered by:

1. `listing_id`
2. `sort_order`
3. `media_id`

#### GET `/api/v1/listing-media/{item_id}`

Fetches one media row by `media_id`.

Path arguments:

- `item_id` `integer`, required

Possible errors:

- `404 {"detail":"ListingMedia not found"}`

#### POST `/api/v1/listing-media/`

Creates a media row from an existing public path.

Request body:

```json
{
  "listing_id": 9,
  "file_path": "/static/listing-media/9/example.jpg",
  "sort_order": 0
}
```

Arguments:

- `listing_id` `integer`, required
  - Must point to an existing listing
- `file_path` `string`, required
  - Accepted forms:
    - `/static/listing-media/9/example.jpg`
    - `static/listing-media/9/example.jpg`
    - `listing-media/9/example.jpg`
- `sort_order` `integer | null`, optional

Behavior notes:

- The backend normalizes slashes and static prefixes.
- This route is for attaching already-available static media.
- It does not upload the file itself.

Possible errors:

- `400 {"detail":"listing_id is required"}`
- `400 {"detail":"file_path is required"}`
- `404 {"detail":"Listing not found"}`

#### POST `/api/v1/listing-media/upload`

Uploads a media file, stores it on disk, and creates the `ListingMedia` row.

Request type:

- `multipart/form-data`

Form arguments:

- `listing_id` `integer`, required
  - Must point to an existing listing
- `sort_order` `integer`, optional, default `0`
- `file` `binary`, required

Accepted file extensions:

- `.jpg`
- `.jpeg`
- `.png`
- `.webp`
- `.gif`

Storage behavior:

- Files are written under `app/static/listing-media/<listing_id>/`
- File names are randomized with a UUID-based name
- The stored DB path is returned as a public `/static/...` URL

Example response:

```json
{
  "media_id": 3,
  "listing_id": 9,
  "file_path": "/static/listing-media/9/3d0f9d4c0f96428eb17d0f8b8f4d3f1a.jpg",
  "file_url": "/static/listing-media/9/3d0f9d4c0f96428eb17d0f8b8f4d3f1a.jpg",
  "sort_order": 0
}
```

Possible errors:

- `400 {"detail":"file is required"}`
- `400 {"detail":"Unsupported media type. Allowed extensions: .gif, .jpeg, .jpg, .png, .webp"}`
- `404 {"detail":"Listing not found"}`

Operational note:

- This route depends on FastAPI multipart support at runtime.
- In practice, `python-multipart` must be installed for the upload endpoint to work.

### Profile Pictures

Base path:

- `/api/v1/profile-pictures`

Access:

- Mixed:
  - `GET /{account_id}`, `POST /upload`, and `POST /generate` are user-facing
  - `POST /{account_id}/replace` requires a management or superadmin session

This resource is custom. It manages `UserProfile.profile_photo` and can generate a default PNG avatar automatically.

Response shape:

```json
{
  "account_id": 12,
  "user_id": 12,
  "profile_photo": "/static/profile-pictures/12/generated-avatar.png",
  "file_url": "/static/profile-pictures/12/generated-avatar.png",
  "generated": true
}
```

Response fields:

- `account_id` `integer`
- `user_id` `integer`
- `profile_photo` `string | null`
- `file_url` `string | null`
- `generated` `boolean`

#### GET `/api/v1/profile-pictures/{account_id}`

Returns the user profile picture metadata.

Behavior notes:

- If `profile_photo` is empty or points to a missing file, the API generates a new default PNG avatar automatically.
- Generated avatars are saved under `app/static/profile-pictures/<account_id>/generated-avatar.png`.

Possible errors:

- `404 {"detail":"User account not found"}`
- `404 {"detail":"User profile not found"}`

#### POST `/api/v1/profile-pictures/upload`

Uploads a custom user profile picture.

Request type:

- `multipart/form-data`

Form arguments:

- `account_id` `integer`, required
- `file` `binary`, required

Accepted file extensions:

- `.jpg`
- `.jpeg`
- `.png`
- `.webp`
- `.gif`
- `.svg`

Storage behavior:

- Files are written under `app/static/profile-pictures/<account_id>/`
- File names are randomized with a UUID-based name

Possible errors:

- `400 {"detail":"Unsupported profile picture file type"}`
- `404 {"detail":"User account not found"}`
- `404 {"detail":"User profile not found"}`

#### POST `/api/v1/profile-pictures/generate`

Forces regeneration of the default PNG avatar.

Request type:

- `multipart/form-data`

Form arguments:

- `account_id` `integer`, required

Behavior notes:

- If the current profile picture is stored locally under `app/static/profile-pictures/...`, the old file is removed first.
- The response returns the new generated file path.

#### POST `/api/v1/profile-pictures/{account_id}/replace`

Management moderation endpoint for replacing an inappropriate user profile picture.

Access:

- Management or superadmin session required

Request type:

- `multipart/form-data`

Form arguments:

- `reason` `string`, optional
  - Defaults to `Inappropriate profile picture`

Behavior notes:

- Removes the existing local profile picture when it is stored under `app/static/profile-pictures/...`
- Replaces it with a generated default PNG avatar
- Writes an audit log entry with action `replace_profile_picture`

#### PATCH `/api/v1/listing-media/{item_id}`

Updates an existing media row.

Path arguments:

- `item_id` `integer`, required

Writable fields:

- `listing_id`
- `file_path`
- `sort_order`

Behavior notes:

- `listing_id` is validated against the `listing` table if changed.
- `file_path` is normalized to a public static path if changed.

Possible errors:

- `404 {"detail":"ListingMedia not found"}`
- `404 {"detail":"Listing not found"}`

#### DELETE `/api/v1/listing-media/{item_id}`

Deletes the media row.

Behavior notes:

- If the stored `file_path` points into `/static/listing-media/...`, the corresponding file is also deleted from disk.
- If its parent listing-media folder becomes empty, that folder is removed as well.

Possible errors:

- `404 {"detail":"ListingMedia not found"}`

### Transaction QR

Base path:

- `/api/v1/transaction-qr`

Access:

- User-facing route

This resource now has workflow endpoints for generating and confirming transaction QR codes.
The legacy CRUD endpoints still exist for compatibility, but the workflow routes below are the intended API.

#### POST `/api/v1/transaction-qr/generate`

Generates a new QR token for a transaction, or returns the active one if an unused QR already exists.

Request body:

```json
{
  "transaction_id": 14,
  "account_id": 22
}
```

Arguments:

- `transaction_id` `integer`, required
- `account_id` `integer`, required

Behavior notes:

- The `account_id` must belong to a normal `user` account with a `UserProfile`.
- Only the buyer or seller participating in the transaction can generate the QR.
- Transactions already marked `completed` are rejected.
- If there is already an active QR for the transaction, the API returns that row instead of creating another one.
- Generated transaction QR codes do not expire automatically.
- `expires_at` is now `null` for QR codes created by this workflow.

Response shape:

```json
{
  "message": "Transaction QR generated",
  "transaction": {
    "transaction_id": 14,
    "listing_id": 9,
    "buyer_id": 22,
    "seller_id": 7,
    "quantity": 1,
    "agreed_price": 250,
    "transaction_status": "pending",
    "completed_at": null
  },
  "transaction_qr": {
    "transaction_qr_id": 3,
    "transaction_id": 14,
    "qr_token": "generated-token",
    "expires_at": null,
    "is_used": false,
    "generated_by": 7,
    "scanned_by": null,
    "scanned_at": null,
    "created_at": "2026-03-11T06:30:00Z"
  }
}
```

Possible errors:

- `404 {"detail":{"error":"Transaction not found"}}`
- `404 {"detail":{"error":"User account not found"}}`
- `404 {"detail":{"error":"User profile not found"}}`
- `400 {"detail":{"error":"Transaction is already completed"}}`
- `403 {"detail":{"error":"Only transaction participants can generate a QR code"}}`

#### GET `/api/v1/transaction-qr/token/{qr_token}`

Fetches a QR token and its transaction metadata.

Path arguments:

- `qr_token` `string`, required

Response fields:

- `transaction`
- `transaction_qr`
- `is_expired` `boolean`
  - Always `false` for the current no-expiration QR flow

Possible errors:

- `404 {"detail":{"error":"Transaction QR not found"}}`
- `404 {"detail":{"error":"Transaction not found"}}`

#### POST `/api/v1/transaction-qr/confirm`

Confirms a QR scan and completes the transaction.

Request body:

```json
{
  "qr_token": "generated-token",
  "account_id": 22
}
```

Arguments:

- `qr_token` `string`, required
- `account_id` `integer`, required

Behavior notes:

- The confirming `account_id` must belong to a normal `user` account with a `UserProfile`.
- Only the buyer or seller participating in the transaction can confirm the QR.
- The account that generated the QR cannot also confirm it.
- On success:
  - `transaction_qr.is_used` becomes `true`
  - `transaction_qr.scanned_by` and `transaction_qr.scanned_at` are set
  - `transaction.transaction_status` becomes `completed`
  - `transaction.completed_at` is set
  - both transaction participants receive a `transaction_completed` notification

Possible errors:

- `404 {"detail":{"error":"Transaction QR not found"}}`
- `404 {"detail":{"error":"Transaction not found"}}`
- `404 {"detail":{"error":"User account not found"}}`
- `404 {"detail":{"error":"User profile not found"}}`
- `400 {"detail":{"error":"Transaction is already completed"}}`
- `400 {"detail":{"error":"Transaction QR has already been used"}}`
- `400 {"detail":{"error":"The QR generator cannot confirm their own QR"}}`
- `403 {"detail":{"error":"Only transaction participants can confirm this QR"}}`

### Tags

Base path:

- `/api/v1/tags`

#### GET `/api/v1/tags/popular`

Returns the most-used tags ranked by how many listings use them.

Access:

- User-facing route

Query arguments:

- `limit` `integer`, optional, default `20`
  - Minimum `1`
  - Maximum `100`
- `include_unavailable` `boolean`, optional, default `false`
  - When `false`, only tags attached to `available` listings are counted
  - When `true`, all listings are counted regardless of listing status

Response shape:

```json
{
  "count": 3,
  "limit": 20,
  "include_unavailable": false,
  "items": [
    {
      "tag_id": 1,
      "tag_name": "books",
      "listing_count": 12
    },
    {
      "tag_id": 7,
      "tag_name": "calculator",
      "listing_count": 8
    }
  ]
}
```

Response fields:

- `count` `integer`
- `limit` `integer`
- `include_unavailable` `boolean`
- `items` `object[]`

Item fields:

- `tag_id` `integer`
- `tag_name` `string`
- `listing_count` `integer`

Primary key:

- `tag_id`

Fields:

- `tag_id` `integer`
- `tag_name` `string | null`

### Listing Tags

Base path:

- `/api/v1/listing-tags`

Configured primary key in router:

- `listing_id`

Actual model primary key:

- composite key of `listing_id` and `tag_id`

Fields:

- `listing_id` `integer`, required on create
- `tag_id` `integer`, required on create

Important caveat:

- The current CRUD router can address rows only by `listing_id`, because that is what the router was configured to use.
- If multiple tags share the same `listing_id`, `GET`, `PATCH`, and `DELETE` operations may not behave as a true composite-key API.

### Listing Reports

Base path:

- `/api/v1/listing-reports`

Primary key:

- `report_id`

Fields:

- `report_id` `integer`
- `listing_id` `integer`, required on create
- `reporter_id` `integer`, required on create
- `reason` `string`, required on create
- `details` `string | null`
- `status` `string | null`
- `reviewed_by` `integer | null`
- `resolution_note` `string | null`
- `created_at` `datetime | null`
- `reviewed_at` `datetime | null`

### Looking-For Reports

Base path:

- `/api/v1/looking-for-reports`

Primary key:

- `report_id`

Fields:

- `report_id` `integer`
- `listing_id` `integer`, required on create
- `reporter_id` `integer`, required on create
- `reason` `string`, required on create
- `details` `string | null`
- `status` `string | null`
- `reviewed_by` `integer | null`
- `resolution_note` `string | null`
- `created_at` `datetime | null`
- `reviewed_at` `datetime | null`

### Conversations

Base path:

- `/api/v1/conversations`

Primary key:

- `conversation_id`

Fields:

- `conversation_id` `integer`
- `participant1_id` `integer | null`
- `participant2_id` `integer | null`
- `conversation_type` `string | null`
- `created_at` `datetime | null`

### Conversation Reports

Base path:

- `/api/v1/conversation-reports`

Primary key:

- `report_id`

Fields:

- `report_id` `integer`
- `conversation_id` `integer`, required on create
- `reporter_id` `integer`, required on create
- `reported_account_id` `integer | null`
- `reason` `string`, required on create
- `details` `string | null`
- `status` `string | null`
- `reviewed_by` `integer | null`
- `resolution_note` `string | null`
- `created_at` `datetime | null`
- `reviewed_at` `datetime | null`

### Messages

Base path:

- `/api/v1/messages`

Primary key:

- `message_id`

Fields:

- `message_id` `integer`
- `conversation_id` `integer | null`
- `sender_id` `integer | null`
- `message_text` `string | null`
- `sent_at` `datetime | null`
- `is_read` `boolean | null`

### Transactions

Base path:

- `/api/v1/transactions`

Access:

- User-facing route

This resource now has custom creation and cancellation workflow endpoints.
The remaining CRUD endpoints still exist for compatibility.

Legacy CRUD routes remain available at:

- `GET /api/v1/transactions/`
- `GET /api/v1/transactions/{item_id}`
- `PATCH /api/v1/transactions/{item_id}`
- `DELETE /api/v1/transactions/{item_id}`

#### POST `/api/v1/transactions/`

Creates a transaction with listing-aware validation.

Request body:

```json
{
  "listing_id": 19,
  "buyer_id": 3,
  "seller_id": 2,
  "quantity": 1,
  "agreed_price": 250.0,
  "transaction_status": "pending",
  "completed_at": null
}
```

Arguments:

- `listing_id` `integer`, required
- `buyer_id` `integer`, required
- `seller_id` `integer`, required
- `quantity` `integer`, optional
  - Defaults to `1`
- `agreed_price` `number`, required
- `transaction_status` `string | null`, optional
  - Defaults to `"pending"` when omitted or blank
- `completed_at` `datetime | null`, optional

Behavior notes:

- `buyer_id` and `seller_id` must both belong to normal `user` accounts with `UserProfile` rows
- `listing_id` must reference an existing listing
- `agreed_price` must be greater than `0`
- numeric values are validated before insert; oversized values are rejected with `400` instead of bubbling up as a database error
- for normal listings:
  - `seller_id` must match the listing owner
  - `buyer_id` and `seller_id` must be different
- for `looking_for` listings:
  - `buyer_id` must be the listing owner
  - `seller_id` must be the user fulfilling the request
  - if an accepted inquiry exists for that pair and it has `offered_price`, `agreed_price` must match the accepted offer
  - `agreed_price` values that look like concatenated `budget_min + budget_max` values are rejected

Response shape:

```json
{
  "transaction_id": 14,
  "listing_id": 19,
  "buyer_id": 3,
  "seller_id": 2,
  "quantity": 1,
  "agreed_price": 250,
  "transaction_status": "pending",
  "completed_at": null
}
```

Possible errors:

- `404 {"detail":{"error":"User account not found"}}`
- `404 {"detail":{"error":"User profile not found"}}`
- `404 {"detail":{"error":"Listing not found"}}`
- `400 {"detail":{"error":"agreed_price must be greater than 0"}}`
- `400 {"detail":"agreed_price is too large. Maximum absolute value is less than 100000000."}`
- `400 {"detail":{"error":"seller_id must match the listing owner"}}`
- `400 {"detail":{"error":"buyer_id and seller_id must be different"}}`
- `400 {"detail":{"error":"For looking-for listings, buyer_id must be the listing owner"}}`
- `400 {"detail":{"error":"For looking-for listings, seller_id must be the user fulfilling the request"}}`
- `400 {"detail":{"error":"agreed_price looks like a concatenated budget range, not a real price"}}`
- `400 {"detail":{"error":"agreed_price must match the accepted inquiry offer for this looking-for listing"}}`

#### POST `/api/v1/transactions/{item_id}/cancel`

Cancels an in-progress transaction as the seller.

Request body:

```json
{
  "account_id": 7,
  "reason": "Buyer is no longer available"
}
```

Path arguments:

- `item_id` `integer`, required
  - Mapped to `Transaction.transaction_id`

Arguments:

- `account_id` `integer`, required
- `reason` `string | null`, optional

Behavior notes:

- the caller must belong to a normal `user` account with a `UserProfile`
- only the seller on the transaction can cancel it
- completed transactions cannot be cancelled
- already-cancelled transactions are rejected
- on success:
  - `transaction.transaction_status` becomes `cancelled`
  - `transaction.completed_at` becomes `null`
  - any unused QR rows for that transaction are invalidated by marking them used
  - the other participant receives a `transaction_cancelled` notification

Response shape:

```json
{
  "message": "Transaction cancelled",
  "transaction": {
    "transaction_id": 14,
    "listing_id": 9,
    "buyer_id": 22,
    "seller_id": 7,
    "quantity": 1,
    "agreed_price": 250,
    "transaction_status": "cancelled",
    "completed_at": null
  }
}
```

Possible errors:

- `404 {"detail":{"error":"Transaction not found"}}`
- `404 {"detail":{"error":"User account not found"}}`
- `404 {"detail":{"error":"User profile not found"}}`
- `403 {"detail":{"error":"Only the seller can cancel this transaction"}}`
- `400 {"detail":{"error":"Completed transactions cannot be cancelled"}}`
- `400 {"detail":{"error":"Transaction is already cancelled"}}`

#### Transaction fields

Primary key:

- `transaction_id`

Fields:

- `transaction_id` `integer`
- `listing_id` `integer | null`
- `buyer_id` `integer | null`
- `seller_id` `integer | null`
- `quantity` `integer | null`
- `agreed_price` `number | null`
- `transaction_status` `string | null`
- `completed_at` `datetime | null`

### Reviews

Base path:

- `/api/v1/reviews`

Primary key:

- `review_id`

Fields:

- `review_id` `integer`
- `transaction_id` `integer | null`
- `reviewer_id` `integer | null`
- `reviewee_id` `integer | null`
- `rating` `integer | null`
- `comment` `string | null`
- `created_at` `datetime | null`

### Transaction QR

Base path:

- `/api/v1/transaction-qr`

Primary key:

- `transaction_qr_id`

Fields:

- `transaction_qr_id` `integer`
- `transaction_id` `integer | null`
- `qr_token` `string | null`
- `expires_at` `datetime | null`
- `is_used` `boolean | null`
- `generated_by` `integer | null`
- `scanned_by` `integer | null`
- `scanned_at` `datetime | null`
- `created_at` `datetime | null`

### Notifications

Base path:

- `/api/v1/notifications`

Primary key:

- `notification_id`

Fields:

- `notification_id` `integer`
- `user_id` `integer | null`
- `notification_type` `string | null`
- `title` `string | null`
- `body` `string | null`
- `related_entity_type` `string | null`
- `related_entity_id` `integer | null`
- `is_read` `boolean | null`
- `read_at` `datetime | null`
- `created_at` `datetime | null`

### Seller Reports

Base path:

- `/api/v1/seller-reports`

Primary key:

- `report_id`

Fields:

- `report_id` `integer`
- `seller_id` `integer`, required on create
- `reporter_id` `integer`, required on create
- `reason` `string`, required on create
- `details` `string | null`
- `status` `string | null`
- `reviewed_by` `integer | null`
- `resolution_note` `string | null`
- `created_at` `datetime | null`
- `reviewed_at` `datetime | null`

## Operational Notes

### Automatic table creation

The app calls `create_tables()` during startup in [app/main.py](/abs/path/d:/Dev/Python/studket-backend/app/main.py), so the schema is expected to be created automatically when the app boots.

### CORS

The app currently enables permissive CORS:

- all origins allowed
- all methods allowed
- all headers allowed

### Session middleware

The app uses Starlette session middleware, not token-based API auth:

- `same_site="lax"`
- `https_only=False`
- `max_age=60 * 60 * 8`

## Suggested Usage Strategy

- Use `/api/v1/auth/register` for creating marketplace, management, or superadmin accounts.
- Use `/api/v1/auth/login` for account credential checks.
- Use `/api/v1/auth/seller-status/elevate` when a buyer wants to become a seller.
- Use `/api/v1/auth/seller-status/request` when a seller wants staff-reviewed trusted-seller status.
- Use `/api/v1/listings` for listing and `looking_for` management.
- Use the CRUD endpoints only when you intentionally want direct table-level access from the management console.

## Known API Design Caveats

- Most non-auth endpoints are internal-style CRUD wrappers over database tables, not consumer-hardened public APIs.
- CRUD `POST` and `PATCH` accept raw model-shaped JSON, so validation is intentionally thin.
- `listing_tags` is exposed through a single-key CRUD route even though the table is really keyed by both `listing_id` and `tag_id`.
- The listings API still stores ownership in `seller_id` internally, but now also exposes `owner_id` and `poster_id` to reduce ambiguity for `looking_for` posts.
- `seller_is_verified` currently mirrors trusted-seller state for backward compatibility. Prefer `trusted_seller` or `seller_is_trusted` in new clients.
