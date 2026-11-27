# Kudos Feature Specification

## 1. Overview

The Kudos feature lets an authenticated employee recognize a colleague with a short message of appreciation. A public feed on the main dashboard shows recent kudos to all authenticated employees. Administrators can hide or soft-delete inappropriate kudos, with each moderation action recorded with its reason.

This document defines the first release. It is stack-agnostic: the feature should follow the existing application’s authentication, user-directory, database, API, accessibility, and UI conventions rather than introduce a new stack.

### Goals

- Make it straightforward to find a colleague and send them appreciation.
- Make recently submitted kudos visible to the wider employee community on the main dashboard.
- Give administrators a way to remove inappropriate kudos from the public feed and retain a moderation record.
- Prevent unauthorized, invalid, or abusive submissions while keeping the experience responsive.

### Out of scope for the first release

- Editing submitted kudos.
- Reactions, comments, notifications, leaderboards, or private kudos.
- External users or anonymous submissions.

## 2. Assumptions and defaults

- The application has authenticated employee accounts and a canonical user directory/table.
- The server can determine the sender from the authenticated session or token; clients cannot choose the sender identity.
- Any authenticated employee can view the feed and send kudos to another active employee.
- Self-kudos are not allowed.
- Messages are plain text, required, and limited to 500 characters. Leading/trailing whitespace is trimmed; blank-after-trimming messages are invalid.
- The initial feed displays the 20 most recent entries and supports cursor-based loading of older entries. “Recent” means newest first; entries are not automatically expired.
- Visible kudos are available to all authenticated employees. Hidden and soft-deleted kudos are excluded from the feed. The feed shows sender and recipient display names, message, and submission time.
- Administrator status is determined by the application’s trusted admin role/authorization system, never by client-supplied data.
- Inappropriate-content filtering blocks submissions flagged by the configured filter. Configurable per-user cooldown/rate limits and duplicate detection protect against spam; thresholds follow internal policy.
- Moderation reasons are required and non-blank. Hide and soft-delete actions retain the latest moderator, timestamp, action, and reason on the kudos row; a full immutable moderation history is not required for this release.
- Existing application retention, privacy, and employee-directory policies take precedence. Product owners should confirm whether these defaults match internal policy before release.

## 3. Functional Requirements

### User stories and acceptance criteria

#### FR-1: Find a colleague

**User story:** As an employee sending kudos, I want to find a colleague from a list so I can address my appreciation to the right person.

**Acceptance criteria:**

1. The recipient control searches the canonical directory and returns active employees matching the entered query.
2. Each result identifies the employee using the application’s established display name and, where needed to disambiguate, a secondary identifier such as team or email.
3. The user must select one result; arbitrary text cannot be submitted as a recipient.
4. Inactive users are not selectable. The sender’s own account is not selectable.
5. Search results are bounded and the control communicates loading, no-results, and failure states.

#### FR-2: Submit kudos

**User story:** As an employee, I want to write a short message of appreciation and submit it so my colleague and others can see the recognition.

**Acceptance criteria:**

1. The form requires a selected recipient and a non-empty message of at most 500 characters after trimming.
2. The interface displays the remaining character count and validation errors without discarding the user’s input.
3. On submission, the server derives the sender from the authenticated identity, validates the recipient and message, and stores the kudos with a server-generated identifier and timestamp.
4. A successful submission is confirmed to the sender and appears at the top of the dashboard feed without requiring a full-page reload.
5. Repeated activation while a submission is pending does not create multiple entries from the same form action.
6. A user cannot send kudos to themselves, to an inactive/nonexistent user, or without authentication.
7. The server applies duplicate detection and configurable per-user cooldown/rate limits; blocked repeat submissions receive a clear retry response and do not create another record.
8. The server applies the configured inappropriate-content filter. Flagged submissions are rejected with a safe validation response and are not published.
9. Message markup is treated as untrusted plain text: it is sanitized/encoded so HTML or script content cannot execute in the feed.

#### FR-3: View the public dashboard feed

**User story:** As an employee, I want to see recent kudos on the main dashboard so I can share in colleagues’ achievements.

**Acceptance criteria:**

1. The dashboard displays kudos newest first, including sender, recipient, message, and a human-readable submission time.
2. The initial request returns at most 20 entries; users can request older entries using the provided pagination control or infinite-scroll pattern used by the app.
3. New kudos appear at the top after successful submission or on the next feed refresh.
4. The feed has clear loading, empty, and recoverable error states.
5. Feed content is visible only to authenticated employees and is rendered as text, not executable markup.

#### FR-4: Moderate inappropriate kudos

**User story:** As an administrator, I can hide or delete inappropriate kudos messages.

**Acceptance criteria:**

1. Only users with the trusted administrator role can perform hide or delete actions; non-admin requests are rejected by the server, regardless of UI state.
2. An administrator must provide a non-empty reason for each moderation action. The server records the moderator, action, timestamp, and reason.
3. Hiding or soft-deleting a kudos immediately removes it from the public dashboard feed. Subsequent `GET /api/kudos` responses exclude it, and active clients invalidate or refresh cached feed data so it disappears without waiting for a full-page reload.
4. Hide preserves the record for audit and sets it invisible. Delete is a soft delete: it also sets the record invisible and marks the action as deleted, retaining the moderation metadata.
5. Admin-only controls are available on feed items to hide or delete, with a reason entry and confirmation for delete. Controls are not shown to non-admin users.
6. A missing kudos returns not found. A moderation action against an already hidden/deleted item returns a clear conflict or current-state response and does not erase the existing moderation metadata silently.

### Validation and product behavior

- Reject messages that are empty after trimming or exceed 500 characters.
- Preserve internal whitespace; do not alter the message beyond trimming leading and trailing whitespace.
- Do not silently truncate invalid messages.
- Reject self-kudos on the server, even if a client bypasses the recipient picker.
- Detect duplicate submissions and enforce configurable per-user cooldown/rate limits. Return a retryable rate-limit response with `Retry-After` when applicable; thresholds are deployment/product configuration, not client-controlled.
- Run the configured inappropriate-content filter before publishing a message. Block flagged content and return a generic, actionable validation error without exposing sensitive filter rules.
- Accept and store message content as plain text only. Sanitize/normalize input as appropriate and encode it for the output context; markup must display as text or be safely rejected, never execute.
- Require a non-blank moderation reason for every hide/delete action; trim it and validate it server-side.
- Use the application’s standard locale and timezone conventions for displaying timestamps; store timestamps in UTC.
- Do not expose inactive employees in recipient search or accept them at submission time.

## 4. Technical Design

### 4.1 Database schema

Add a `kudos` table referencing the existing canonical users table. Exact identifier types and naming should match the host application.

| Column | Type | Constraints / purpose |
|---|---|---|
| `id` | UUID or application-standard ID | Primary key, generated by the server |
| `giver_id` | Same type as user primary key | Required foreign key to users |
| `recipient_id` | Same type as user primary key | Required foreign key to users |
| `message` | Variable-length text, maximum 500 characters | Required, non-blank after trimming |
| `created_at` | Timestamp with timezone | Required, server-generated UTC time |
| `is_visible` | Boolean | Required, default `true`; false after hide or soft-delete |
| `moderated_by` | Same type as user primary key | Nullable foreign key to users; set to the admin for the latest moderation action |
| `moderated_at` | Timestamp with timezone | Nullable; set by the server for the latest moderation action |
| `reason_for_moderation` | Text/variable-length text | Nullable for never-moderated entries; required and non-blank for a moderation action |
| `moderation_action` | Nullable enum or constrained text | Additional field distinguishing `hidden` from `deleted`; null until moderated |

**Integrity rules**

- `giver_id` and `recipient_id` reference valid users; use the application’s established deletion policy for user records.
- `giver_id <> recipient_id`.
- `is_visible` defaults to true. A moderation action sets it to false and records `moderated_by`, `moderated_at`, `reason_for_moderation`, and `moderation_action` together.
- For unmoderated rows, moderation metadata and action are null. For moderated rows, moderator, timestamp, non-blank reason, and action are present; action is `hidden` or `deleted`.
- `DELETE /api/kudos/:id` is a soft delete. Do not physically remove the row in this release, so its latest moderation metadata remains available to authorized systems.
- Enforce the message length in application validation and, where supported, with a database constraint.
- Store message content as plain text; do not store rendered HTML. Apply safe input handling and context-aware output encoding.
- Do not add a separate sender field supplied by clients.
- The row stores the latest moderation action only. A complete immutable action history is not required for this release.

**Indexes**

- Composite index on `(created_at DESC, id DESC)` to support stable newest-first pagination.
- Index on `recipient_id` if recipient-history queries are planned or commonly needed; omit if no query uses it in the first release.

Use keyset pagination with `(created_at, id)` as the cursor ordering tuple to avoid unstable offsets when new entries arrive.

### 4.2 API endpoints

Paths are illustrative; adapt them to the application’s routing and versioning conventions. All endpoints require authentication.

#### `GET /api/users?query={text}&limit={n}`

Searches the canonical directory for active recipient candidates.

- Require a trimmed query of at least 2 characters.
- Return a bounded result set (default 10, maximum 20), including only fields needed by the picker, such as `id`, `displayName`, and an optional disambiguating field.
- Exclude the authenticated user and inactive users.
- Apply the application’s standard search matching and access-control rules.

#### `POST /api/kudos`

Creates a kudos entry.

Request:

```json
{
  "recipientId": "user-id",
  "message": "Thanks for helping us deliver the release."
}
```

- Derive `giverId` from the authenticated principal.
- Validate the recipient is an active, distinct user and validate the trimmed message.
- Return `201 Created` with the created entry and the display data needed to update the feed.
- Consider an idempotency key if the application has a standard mechanism for retry-safe creates; otherwise disable repeat submission while a request is pending.

#### `GET /api/kudos?limit={n}&cursor={cursor}`

Returns the newest entries visible to the authenticated employee.

- Default `limit` is 20; enforce a maximum of 50.
- Return entries ordered by `(created_at DESC, id DESC)`.
- Filter to `is_visible = true` before pagination so hidden or soft-deleted entries never appear in the public feed.
- Return an opaque next cursor when more entries may be available; return no cursor when the page is exhausted.
- Include only the data required to render the feed: ID, sender and recipient display information, message, and creation time.
- Keep authorization and directory visibility rules consistent with the dashboard.

Example response:

```json
{
  "items": [
    {
      "id": "kudos-id",
      "giver": { "id": "sender-id", "displayName": "Alex Chen" },
      "recipient": { "id": "recipient-id", "displayName": "Sam Rivera" },
      "message": "Thanks for helping us deliver the release.",
      "createdAt": "2026-11-25T16:30:00Z"
    }
  ],
  "nextCursor": null
}
```

#### `PATCH /api/kudos/:id/hide`

Hides a kudos entry from the public feed while retaining the row and its latest moderation metadata.

Request:

```json
{
  "reason": "Contains inappropriate language."
}
```

- Require authentication and the trusted administrator role.
- Require a non-blank reason; trim and validate it on the server.
- Set `is_visible` to false, `moderation_action` to `hidden`, and record `moderated_by` from the authenticated principal plus a server-generated UTC `moderated_at`.
- Ensure the next `GET /api/kudos` response omits the item.

#### `DELETE /api/kudos/:id`

Soft-deletes a kudos entry: hides it from the public feed while retaining the row for audit.

Request body:

```json
{
  "reason": "Contains personal information."
}
```

- Require authentication and the trusted administrator role.
- Require a non-blank reason; trim and validate it on the server.
- Set `is_visible` to false, `moderation_action` to `deleted`, and record the authenticated administrator and server-generated UTC moderation time and reason.
- Do not physically delete the row. Ensure the next `GET /api/kudos` response omits the item.
- Return a clear not-found or already-moderated conflict/current-state response; do not silently overwrite an earlier moderation record.

### 4.3 Frontend components

- **Kudos composer:** recipient picker, message textarea, character count, submit action, inline validation, pending state, and success/error feedback.
- **Recipient picker:** debounced directory search, bounded selectable results, keyboard navigation, and loading/empty/error states. Debounce interval should follow existing app patterns (suggested default: 250 ms).
- **Dashboard kudos feed:** newest-first list, pagination/load-more behavior, relative or localized timestamps, and loading/empty/error states. Show hide/delete controls only to administrators.
- **Kudos feed item:** accessible presentation of sender, recipient, message, and timestamp. Treat message as untrusted plain text.
- **Moderation dialog/actions:** admin-only reason input, validation, delete confirmation, pending/error/success states, and feed cache invalidation after success.
- **Data/query layer:** reuse the app’s current API client, cache invalidation, and state-management conventions. Invalidate or prepend the new entry after a successful create.

Components must support keyboard-only interaction, visible focus, screen-reader labels, and responsive layouts consistent with the rest of the dashboard.

### 4.4 Security and privacy

- Require authentication for directory search, create, feed, hide, and delete requests.
- Derive sender identity exclusively on the server. Validate authorization on every request; do not rely on hidden UI controls.
- Validate all fields on the server, including recipient existence/status, self-recognition, message length, non-blank content, and moderation reason.
- Authorize moderation endpoints using the application’s trusted administrator role. Derive `moderated_by` from the authenticated principal and prevent users from spoofing moderation metadata.
- Escape/encode message content at render time and never interpret it as HTML or script; sanitize untrusted input according to the app’s plain-text policy.
- Apply configured duplicate detection and per-user cooldown/rate limits server-side so clients cannot bypass spam controls.
- Apply the configured inappropriate-content filter before creation; do not expose internal filter rules or scores in client responses.
- Apply the application’s CSRF protection for cookie-authenticated requests and standard token validation for bearer-token requests.
- Use parameterized database operations and avoid returning directory fields not needed by the UI.
- Apply rate limits or abuse controls using existing platform conventions; choose thresholds with product/security owners based on expected usage.
- Avoid logging full message contents or unnecessary personal data. Log request/record identifiers and operational outcomes according to existing retention policy.
- Ensure database access follows least privilege and established employee-data protections.

### 4.5 Performance and reliability

- Use server-side limits and indexed keyset pagination; do not return the full history in a single request.
- Exclude invisible kudos in the database query before applying feed pagination.
- Debounce recipient lookup and cancel or ignore stale searches so older responses cannot overwrite newer results.
- Apply duplicate checks and configurable sender cooldown/rate limits efficiently; return `429` and `Retry-After` when a configured limit is exceeded.
- Avoid per-entry user lookups; retrieve sender and recipient display data in a bounded query/join or equivalent batch operation.
- Cache the first feed page only according to existing authenticated-cache rules; invalidate or update it after creation. Do not share private/authenticated responses through a public cache.
- Suggested initial targets under expected normal load: recipient search p95 under 300 ms, feed p95 under 500 ms, and kudos creation p95 under 1 s, excluding client network latency. Confirm targets against existing service SLOs and revise if they conflict.
- Monitor endpoint latency, error rates, and rate-limit events without recording message contents.

### 4.6 Error handling

Use the application’s standard error envelope. If none exists, use a consistent response such as:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Check the highlighted fields.",
    "fieldErrors": {
      "message": "Message must be 500 characters or fewer."
    }
  }
}
```

Expected outcomes:

| Status | Meaning | Client behavior |
|---|---|---|
| `400 Bad Request` | Malformed request or invalid cursor | Show a recoverable error; do not retry unchanged input |
| `401 Unauthorized` | Missing/expired authentication | Follow the application’s sign-in/session-expiry flow |
| `403 Forbidden` | Authenticated user is not permitted | Show an access-denied state |
| `404 Not Found` | Recipient or kudos entry does not exist or is unavailable | Ask the sender to select a valid active colleague, or show that the moderation target is unavailable |
| `409 Conflict` | Duplicate/idempotency conflict or already-moderated state | Reconcile with server state; avoid duplicate submission or overwriting moderation metadata |
| `422 Unprocessable Entity` | Field validation failed, including invalid reason or content blocked by the filter | Show a safe field-level message and preserve input |
| `429 Too Many Requests` | Spam cooldown or other rate limit reached | Explain the delay and honor `Retry-After` when provided |
| `5xx` | Unexpected server failure | Show a generic retryable message and a correlation ID where available |

Do not expose stack traces, SQL errors, or sensitive account details to clients. Distinguish retryable transport/server errors from validation errors and avoid success-shaped fallbacks.

## 5. Implementation Plan

1. **Confirm application conventions:** identify the authentication principal, canonical user schema, directory search API, API/error patterns, dashboard composition, accessibility standards, and applicable retention/privacy rules.
2. **Implement persistence:** add the kudos table, foreign keys, 500-character message constraint, moderation metadata/action fields, integrity constraints, and feed-order index using the application’s migration system.
3. **Implement backend APIs:** add bounded directory search, validated kudos creation, cursor-paginated visible-feed retrieval, and admin-only hide/soft-delete endpoints. Enforce authentication and role authorization server-side.
4. **Build the composer and picker:** implement search, selection, 500-character message validation, character count, spam cooldown/duplicate feedback, inappropriate-content filtering feedback, pending/success/error states, and keyboard accessibility.
5. **Add the dashboard feed and moderation controls:** render visible newest-first entries, pagination, empty/loading/error states, and update the feed after create or moderation; show reason-required hide/delete actions only to administrators.
6. **Verify and release:** add focused tests for validation, self-recognition denial, inactive recipients, spam throttling and duplicate submissions, blocked inappropriate content, admin/non-admin moderation authorization, reason-required audit metadata, soft-delete behavior, immediate feed exclusion, pagination stability, and safe plain-text rendering; run existing lint/build/test checks; conduct accessibility and security review; release behind the application’s standard feature flag or rollout mechanism if available.

## 6. Decisions to Confirm Before Release

- Confirm that the configured inappropriate-content filter, 500-character limit, and plain-text visibility comply with product policy.
- Confirm the admin role source, moderation reason retention policy, and employee-record deletion/retention policy.
- Configure spam cooldown/rate-limit thresholds and confirm performance targets against expected traffic and existing service SLOs.
