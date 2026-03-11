# Studket Backend API Reference

This document describes the HTTP API exposed by this repository as of March 11, 2026.

## Base URL and Routing

- Application router root: `/api`
- Versioned API root: `/api/v1`
- Static files and web pages are mounted separately and are not covered here.
- Realtime websocket endpoints are also outside the scope of this page.

Effective API URL pattern:

```text
/api/v1/<resource>
```

Examples:

- `/api/v1/auth/register`
- `/api/v1/listings/feed`
- `/api/v1/accounts/`

## Authentication and Access Rules

### Public API endpoints

These endpoints do not require a management session:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/seller-status/request`

### Management-protected API endpoints

All other `/api/v1/*` endpoints require a valid session created by the web login flow for a:

- `management` account
- `superadmin` account

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
- `"Seller profile not found"`
- `"User profile not found"`
- `"Seller access requires approved seller status"`

## Endpoint Inventory

### Custom routers

- `/api/v1/auth`
- `/api/v1/listings`

### CRUD routers

- `/api/v1/accounts`
- `/api/v1/user-profiles`
- `/api/v1/management-accounts`
- `/api/v1/listing-inventory`
- `/api/v1/listing-media`
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
- The API now exposes `marketplace_role`.
- For `user` accounts:
  - `marketplace_role` is `buyer` by default
  - It becomes `seller` only after approved seller verification

Success response:

```json
{
  "message": "Registered successfully",
  "account_id": 12,
  "email": "user@example.com",
  "username": "campusbuyer1",
  "account_type": "user",
  "marketplace_role": "buyer"
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
    "marketplace_role": "buyer"
  }
}
```

Possible errors:

- `401 {"detail":{"error":"Credentials are required"}}`
- `401 {"detail":{"error":"Invalid credentials"}}`
- `401 {"detail":{"error":"Account is not active"}}`

### POST `/api/v1/auth/seller-status/request`

Creates a seller verification request for a normal marketplace user.

Access:

- Public

Request body:

```json
{
  "account_id": 12,
  "submission_note": "I want to start selling textbooks."
}
```

Arguments:

- `account_id` `integer`, required
  - Must reference an existing `Account`
  - The account must have `account_type = "user"`
- `submission_note` `string | null`, optional
  - Freeform reason or context for requesting seller status

Behavior notes:

- If an identical pending seller request already exists, the API returns that existing request instead of creating a duplicate.
- If the user is already a verified seller, the request is rejected.

Success response:

```json
{
  "message": "Seller access request submitted",
  "request_id": 5,
  "account_id": 12,
  "status": "pending"
}
```

Possible errors:

- `400 {"detail":{"error":"User account not found"}}`
- `400 {"detail":{"error":"User profile not found"}}`
- `400 {"detail":{"error":"User is already a seller"}}`

## Listings Endpoints

Base path: `/api/v1/listings`

This router is custom and does not follow the generic CRUD behavior exactly.

### Listing role rules

- Standard marketplace listings require an approved seller.
- `looking_for` posts may be created by any existing marketplace user profile.
- The database column is still `seller_id`, but the API now also exposes a neutral alias:
  - `owner_id` for all listing types
  - `poster_id` for `looking_for` posts

### GET `/api/v1/listings/feed`

Returns a recommendation-style listing feed.

Access:

- Management session required

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
      "listing_type": "single_item",
      "condition": "used",
      "status": "available",
      "created_at": "2026-03-11T09:12:00",
      "seller_username": "seller44",
      "seller_campus": "North Campus",
      "tags": ["books", "math"],
      "recommendation_score": 7.8,
      "recommendation_reasons": ["available", "recent"],
      "seller_is_verified": true,
      "seller_average_rating": 4.9,
      "seller_review_count": 12
    }
  ]
}
```

Feed item fields:

- Standard serialized `Listing` columns
- `owner_id` `integer | null`
- `poster_id` `integer | null`
  - Present only for `looking_for` posts
- `seller_username` `string | null`
- `seller_campus` `string | null`
- `tags` `string[]`
- `recommendation_score` `number`
- `recommendation_reasons` `string[]`
- `seller_is_verified` `boolean`
- `seller_average_rating` `number | null`
- `seller_review_count` `integer`

### GET `/api/v1/listings/search`

Searches listings using text, filters, and optional owner filtering.

Access:

- Management session required

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
      "listing_type": "single_item",
      "condition": "used",
      "status": "available",
      "created_at": "2026-03-11T09:12:00",
      "seller_username": "seller44",
      "seller_campus": "North Campus",
      "tags": ["books", "math"],
      "search_score": 8.5,
      "search_reasons": ["title_match"],
      "seller_is_verified": true,
      "seller_average_rating": 4.9,
      "seller_review_count": 12
    }
  ]
}
```

### GET `/api/v1/listings/`

Lists all listings.

Access:

- Management session required

Arguments:

- None

Response:

- Array of serialized listings
- Includes:
  - all table columns
  - `owner_id`
  - `poster_id` for `looking_for`

### GET `/api/v1/listings/{item_id}`

Fetches one listing by `listing_id`.

Access:

- Management session required

Path arguments:

- `item_id` `integer`, required
  - Mapped to `Listing.listing_id`

Response:

- Serialized listing object
- Includes `owner_id`
- Includes `poster_id` when `listing_type == "looking_for"`

Possible errors:

- `404 {"detail":"Listing not found"}`

### POST `/api/v1/listings/`

Creates a listing or a `looking_for` post.

Access:

- Management session required

Request body fields:

- `seller_id` `integer`, optional if `owner_id` is provided
  - Internal ownership field
- `owner_id` `integer`, optional alias
  - Preferred public alias for `seller_id`
- `title` `string`, required by database model
- `description` `string | null`, optional
- `price` `number | null`, optional
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
- For `listing_type = "looking_for"`:
  - the creator only needs an existing `UserProfile`
- For all other listing types:
  - the creator must have `UserProfile.is_verified = true`

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
  "listing_type": "looking_for",
  "status": "available"
}
```

Possible errors:

- `400 {"detail":"seller_id is required"}`
- `403 {"detail":"Seller access requires approved seller status"}`
- `404 {"detail":"Seller profile not found"}`
- `404 {"detail":"User profile not found"}`

### PATCH `/api/v1/listings/{item_id}`

Updates an existing listing.

Access:

- Management session required

Path arguments:

- `item_id` `integer`, required

Request body:

- Any writable `Listing` field
- `owner_id` may be used instead of `seller_id`

Validation notes:

- If `seller_id`, `owner_id`, or `listing_type` changes, creator-role validation runs again.
- This prevents converting a `looking_for` post into a seller listing unless the owner is a verified seller.

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

All CRUD routers below require a management or superadmin session.

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

Primary key:

- `media_id`

Fields:

- `media_id` `integer`
- `listing_id` `integer | null`
- `file_path` `string | null`
- `sort_order` `integer | null`

### Tags

Base path:

- `/api/v1/tags`

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
- Use `/api/v1/auth/seller-status/request` when a buyer wants to become a seller.
- Use `/api/v1/listings` for listing and `looking_for` management.
- Use the CRUD endpoints only when you intentionally want direct table-level access from the management console.

## Known API Design Caveats

- Most non-auth endpoints are internal-style CRUD wrappers over database tables, not consumer-hardened public APIs.
- CRUD `POST` and `PATCH` accept raw model-shaped JSON, so validation is intentionally thin.
- `listing_tags` is exposed through a single-key CRUD route even though the table is really keyed by both `listing_id` and `tag_id`.
- The listings API still stores ownership in `seller_id` internally, but now also exposes `owner_id` and `poster_id` to reduce ambiguity for `looking_for` posts.
