from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.database import Database, utc_timestamp


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
SESSION_COOKIE = "kudos_demo_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_BLOCKED_TERMS = ("fuck", "shit", "bitch", "asshole")


class ProfileSwitch(BaseModel):
    user_id: str = Field(alias="userId", min_length=1, max_length=80)
    model_config = ConfigDict(populate_by_name=True)


class KudosSubmission(BaseModel):
    recipient_id: str = Field(alias="recipientId", min_length=1, max_length=80)
    message: str
    model_config = ConfigDict(populate_by_name=True)


class ModerationRequest(BaseModel):
    reason: str


def _encode_token(user_id: str, secret: bytes) -> str:
    expires_at = int(datetime.now(timezone.utc).timestamp()) + SESSION_TTL_SECONDS
    payload = f"{user_id}|{expires_at}".encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{encoded_signature}"


def _decode_token(token: str | None, secret: bytes) -> str | None:
    if not token or "." not in token:
        return None
    encoded_payload, encoded_signature = token.rsplit(".", 1)
    expected_signature = hmac.new(secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    expected_encoded = base64.urlsafe_b64encode(expected_signature).decode("ascii").rstrip("=")
    if not hmac.compare_digest(encoded_signature, expected_encoded):
        return None
    try:
        padding = "=" * (-len(encoded_payload) % 4)
        payload = base64.urlsafe_b64decode(encoded_payload + padding).decode("utf-8")
        user_id, expires_at = payload.rsplit("|", 1)
        if int(expires_at) <= int(datetime.now(timezone.utc).timestamp()):
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    return user_id


def _clean_plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    without_controls = "".join(
        character
        for character in normalized
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )
    return without_controls.strip()


def create_app(
    database_path: str | Path | None = None,
    *,
    session_secret: bytes | None = None,
    cooldown_seconds: int | None = None,
    duplicate_window_seconds: int | None = None,
    blocked_terms: tuple[str, ...] | None = None,
) -> FastAPI:
    path = database_path or os.environ.get("KUDOS_DATABASE_PATH", ROOT / "data" / "kudos.db")
    database = Database(path)
    secret = session_secret or os.environ.get("KUDOS_SESSION_SECRET", "").encode("utf-8") or secrets.token_bytes(32)
    cooldown = max(
        0,
        cooldown_seconds
        if cooldown_seconds is not None
        else int(os.environ.get("KUDOS_COOLDOWN_SECONDS", "15")),
    )
    duplicate_window = max(
        0,
        duplicate_window_seconds
        if duplicate_window_seconds is not None
        else int(os.environ.get("KUDOS_DUPLICATE_WINDOW_SECONDS", "120")),
    )
    if blocked_terms is None:
        configured_terms = os.environ.get("KUDOS_BLOCKED_TERMS")
        terms = (
            tuple(term.strip() for term in configured_terms.split(",") if term.strip())
            if configured_terms is not None
            else DEFAULT_BLOCKED_TERMS
        )
    else:
        terms = tuple(term for term in blocked_terms if term)
    blocked_patterns = tuple(
        re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        for term in terms
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        yield

    application = FastAPI(
        title="Kudos",
        description="A local-first colleague recognition demo.",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.middleware("http")
    async def disable_api_caching(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def fail(
        status_code: int,
        code: str,
        message: str,
        *,
        field_errors: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        raise HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "fieldErrors": field_errors or {},
            },
            headers=headers,
        )

    @application.exception_handler(HTTPException)
    async def handle_http_error(_: Request, exception: HTTPException) -> JSONResponse:
        detail = exception.detail
        if not isinstance(detail, dict):
            detail = {"code": "HTTP_ERROR", "message": str(detail), "fieldErrors": {}}
        return JSONResponse(
            status_code=exception.status_code,
            content={"error": detail},
            headers=exception.headers,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exception: Exception) -> JSONResponse:
        request_id = secrets.token_hex(8)
        logging.getLogger("uvicorn.error").exception(
            "Unhandled request error id=%s",
            request_id,
            exc_info=exception,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Something went wrong. Please try again.",
                    "fieldErrors": {},
                    "requestId": request_id,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exception: RequestValidationError) -> JSONResponse:
        errors: dict[str, str] = {}
        for item in exception.errors():
            location = item.get("loc", ())
            field = str(location[-1]) if location else "request"
            errors[field] = "This field is required." if item.get("type") == "missing" else "Check this value."
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Check the submitted fields.",
                    "fieldErrors": errors,
                }
            },
        )

    def assert_same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if not origin:
            return
        parsed = urlsplit(origin)
        request_host = request.headers.get("host", "")
        if parsed.netloc != request_host or parsed.scheme != request.url.scheme:
            fail(403, "ORIGIN_FORBIDDEN", "This request origin is not allowed.")

    def current_user(request: Request) -> dict[str, Any]:
        user_id = _decode_token(request.cookies.get(SESSION_COOKIE), secret)
        if user_id is None:
            fail(401, "UNAUTHENTICATED", "Select an employee profile to continue.")
        with database.transaction() as connection:
            user = connection.execute(
                f"""
                SELECT id, display_name, team, role, is_active
                FROM users
                WHERE id = ? AND is_active = 1
                """,
                (user_id,),
            ).fetchone()
        if user is None:
            fail(401, "UNAUTHENTICATED", "Select an employee profile to continue.")
        return dict(user)

    def require_admin(user: dict[str, Any]) -> None:
        if user["role"] != "admin":
            fail(403, "ADMIN_REQUIRED", "Administrator access is required.")

    def profile_payload(user: Any) -> dict[str, str]:
        return {
            "id": user["id"],
            "displayName": user["display_name"],
            "team": user["team"],
            "role": user["role"],
        }

    def encode_cursor(created_at: str, kudos_id: int) -> str:
        raw = f"{created_at}|{kudos_id}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def decode_cursor(cursor: str) -> tuple[str, int]:
        try:
            padding = "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
            created_at, raw_id = raw.rsplit("|", 1)
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            kudos_id = int(raw_id)
            if kudos_id <= 0:
                raise ValueError
            return created_at, kudos_id
        except (ValueError, UnicodeDecodeError):
            fail(400, "INVALID_CURSOR", "The feed cursor is invalid.")
        raise AssertionError("unreachable")

    def kudos_payload(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "giver": {"id": row["giver_id"], "displayName": row["giver_name"]},
            "recipient": {"id": row["recipient_id"], "displayName": row["recipient_name"]},
            "message": row["message"],
            "createdAt": row["created_at"],
        }

    def filter_match(message: str) -> bool:
        return any(pattern.search(message) for pattern in blocked_patterns)

    @application.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/auth/profiles")
    def list_profiles() -> dict[str, list[dict[str, str]]]:
        with database.transaction() as connection:
            users = connection.execute(
                f"""
                SELECT id, display_name, team, role
                FROM users
                WHERE is_active = 1
                ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, display_name
                """
            ).fetchall()
        return {"profiles": [profile_payload(user) for user in users]}

    @application.get("/api/auth/me")
    def who_am_i(request: Request) -> dict[str, Any]:
        user = current_user(request)
        return {"user": profile_payload(user)}

    @application.post("/api/auth/switch")
    def switch_profile(payload: ProfileSwitch, request: Request) -> JSONResponse:
        assert_same_origin(request)
        with database.transaction() as connection:
            user = connection.execute(
                """
                SELECT id, display_name, team, role
                FROM users
                WHERE id = ? AND is_active = 1
                """,
                (payload.user_id,),
            ).fetchone()
        if user is None:
            fail(404, "PROFILE_NOT_FOUND", "That employee profile is unavailable.")
        response = JSONResponse({"user": profile_payload(user)})
        response.set_cookie(
            SESSION_COOKIE,
            _encode_token(user["id"], secret),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            path="/",
        )
        return response

    @application.post("/api/auth/logout")
    def logout(request: Request) -> JSONResponse:
        assert_same_origin(request)
        response = JSONResponse({"status": "ok"})
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
        return response

    @application.get("/api/users")
    def search_users(
        request: Request,
        query: str = Query(default="", max_length=100),
        limit: int = Query(default=10, ge=1, le=20),
    ) -> dict[str, list[dict[str, str]]]:
        user = current_user(request)
        normalized_query = query.strip()
        if len(normalized_query) < 2:
            fail(422, "VALIDATION_ERROR", "Enter at least two characters to search.")
        search = f"%{normalized_query}%"
        with database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, display_name, team, role
                FROM users
                WHERE is_active = 1 AND id <> ?
                    AND (display_name LIKE ? COLLATE NOCASE OR team LIKE ? COLLATE NOCASE)
                ORDER BY display_name
                LIMIT ?
                """,
                (user["id"], search, search, limit),
            ).fetchall()
        return {"users": [profile_payload(row) for row in rows]}

    @application.post("/api/kudos", status_code=201)
    def create_kudos(payload: KudosSubmission, request: Request) -> dict[str, Any]:
        assert_same_origin(request)
        user = current_user(request)
        message = _clean_plain_text(payload.message)
        if not message:
            fail(422, "VALIDATION_ERROR", "Enter a message of appreciation.", field_errors={"message": "Message is required."})
        if len(message) > 500:
            fail(422, "VALIDATION_ERROR", "Message must be 500 characters or fewer.", field_errors={"message": "Maximum length is 500 characters."})
        if filter_match(message):
            fail(
                422,
                "CONTENT_NOT_ALLOWED",
                "This message could not be submitted. Please revise it and try again.",
                field_errors={"message": "Please revise this message before submitting."},
            )

        now = datetime.now(timezone.utc)
        created_at = utc_timestamp(now)
        with database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recipient = connection.execute(
                "SELECT id, is_active FROM users WHERE id = ?",
                (payload.recipient_id,),
            ).fetchone()
            if recipient is None or not recipient["is_active"]:
                fail(404, "RECIPIENT_NOT_FOUND", "Choose an active colleague.")
            if recipient["id"] == user["id"]:
                fail(422, "SELF_KUDOS_NOT_ALLOWED", "You cannot send kudos to yourself.", field_errors={"recipientId": "Choose another colleague."})

            if duplicate_window:
                duplicate_after = utc_timestamp(now - timedelta(seconds=duplicate_window))
                duplicate = connection.execute(
                    """
                    SELECT 1 FROM kudos
                    WHERE giver_id = ? AND recipient_id = ?
                        AND lower(message) = lower(?) AND created_at >= ?
                    LIMIT 1
                    """,
                    (user["id"], recipient["id"], message, duplicate_after),
                ).fetchone()
                if duplicate:
                    fail(
                        409,
                        "DUPLICATE_KUDOS",
                        "You recently sent the same kudos to this colleague.",
                    )

            if cooldown:
                latest = connection.execute(
                    """
                    SELECT created_at FROM kudos
                    WHERE giver_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (user["id"],),
                ).fetchone()
                if latest:
                    latest_at = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
                    retry_after = cooldown - (now - latest_at).total_seconds()
                    if retry_after > 0:
                        seconds = max(1, int(retry_after + 0.999))
                        fail(
                            429,
                            "KUDOS_COOLDOWN",
                            "Please wait before sending more kudos.",
                            headers={"Retry-After": str(seconds)},
                        )

            cursor = connection.execute(
                """
                INSERT INTO kudos (giver_id, recipient_id, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user["id"], recipient["id"], message, created_at),
            )
            created = connection.execute(
                """
                SELECT k.id, k.giver_id, giver.display_name AS giver_name,
                    k.recipient_id, recipient.display_name AS recipient_name,
                    k.message, k.created_at
                FROM kudos AS k
                JOIN users AS giver ON giver.id = k.giver_id
                JOIN users AS recipient ON recipient.id = k.recipient_id
                WHERE k.id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return {"item": kudos_payload(created)}

    @application.get("/api/kudos")
    def list_kudos(
        request: Request,
        limit: int = Query(default=20, ge=1, le=50),
        cursor: str | None = None,
        scope: str = Query(default="community"),
    ) -> dict[str, Any]:
        user = current_user(request)
        if scope not in {"community", "given", "received"}:
            fail(400, "INVALID_SCOPE", "The kudos filter is invalid.")
        cursor_values = decode_cursor(cursor) if cursor else None
        scope_clause = ""
        scope_params: tuple[Any, ...] = ()
        if scope == "given":
            scope_clause = " AND k.giver_id = ?"
            scope_params = (user["id"],)
        elif scope == "received":
            scope_clause = " AND k.recipient_id = ?"
            scope_params = (user["id"],)
        with database.transaction() as connection:
            if cursor_values:
                created_at, kudos_id = cursor_values
                rows = connection.execute(
                    f"""
                    SELECT k.id, k.giver_id, giver.display_name AS giver_name,
                        k.recipient_id, recipient.display_name AS recipient_name,
                        k.message, k.created_at
                    FROM kudos AS k
                    JOIN users AS giver ON giver.id = k.giver_id
                    JOIN users AS recipient ON recipient.id = k.recipient_id
                    WHERE k.is_visible = 1
                        AND (k.created_at < ? OR (k.created_at = ? AND k.id < ?))
                        {scope_clause}
                    ORDER BY k.created_at DESC, k.id DESC
                    LIMIT ?
                    """,
                    (created_at, created_at, kudos_id, *scope_params, limit + 1),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT k.id, k.giver_id, giver.display_name AS giver_name,
                        k.recipient_id, recipient.display_name AS recipient_name,
                        k.message, k.created_at
                    FROM kudos AS k
                    JOIN users AS giver ON giver.id = k.giver_id
                    JOIN users AS recipient ON recipient.id = k.recipient_id
                    WHERE k.is_visible = 1
                        {scope_clause}
                    ORDER BY k.created_at DESC, k.id DESC
                    LIMIT ?
                    """,
                    (*scope_params, limit + 1),
                ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more and page else None
        return {"items": [kudos_payload(row) for row in page], "nextCursor": next_cursor}

    @application.get("/api/kudos/stats")
    def kudos_stats(request: Request) -> dict[str, Any]:
        current_user(request)
        with database.transaction() as connection:
            visible = connection.execute("SELECT COUNT(*) AS count FROM kudos WHERE is_visible = 1").fetchone()["count"]
            moderated = connection.execute("SELECT COUNT(*) AS count FROM kudos WHERE is_visible = 0").fetchone()["count"]
        return {"visible": visible, "moderated": moderated}

    @application.get("/api/kudos/moderation")
    def moderation_log(request: Request) -> dict[str, Any]:
        user = current_user(request)
        require_admin(user)
        with database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT k.id, k.giver_id, giver.display_name AS giver_name,
                    k.recipient_id, recipient.display_name AS recipient_name,
                    k.message, k.created_at, k.moderated_by, k.moderated_at,
                    k.reason_for_moderation, k.moderation_action
                FROM kudos AS k
                JOIN users AS giver ON giver.id = k.giver_id
                JOIN users AS recipient ON recipient.id = k.recipient_id
                WHERE k.is_visible = 0
                ORDER BY k.moderated_at DESC, k.id DESC
                """,
            ).fetchall()
        items = []
        for row in rows:
            item = kudos_payload(row)
            item.update({
                "moderatedBy": row["moderated_by"],
                "moderatedAt": row["moderated_at"],
                "reason": row["reason_for_moderation"],
                "action": row["moderation_action"],
            })
            items.append(item)
        return {"items": items}

    def moderate(kudos_id: int, request: Request, payload: ModerationRequest, action: str) -> dict[str, Any]:
        assert_same_origin(request)
        user = current_user(request)
        require_admin(user)
        reason = _clean_plain_text(payload.reason)
        if not reason:
            fail(422, "VALIDATION_ERROR", "A moderation reason is required.", field_errors={"reason": "Enter a reason."})
        if len(reason) > 500:
            fail(422, "VALIDATION_ERROR", "The reason must be 500 characters or fewer.", field_errors={"reason": "Maximum length is 500 characters."})

        with database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, is_visible FROM kudos WHERE id = ?",
                (kudos_id,),
            ).fetchone()
            if row is None:
                fail(404, "KUDOS_NOT_FOUND", "This kudos entry no longer exists.")
            if not row["is_visible"]:
                fail(409, "ALREADY_MODERATED", "This kudos entry has already been moderated.")
            connection.execute(
                """
                UPDATE kudos
                SET is_visible = 0, moderated_by = ?, moderated_at = ?,
                    reason_for_moderation = ?, moderation_action = ?
                WHERE id = ? AND is_visible = 1
                """,
                (user["id"], utc_timestamp(), reason, action, kudos_id),
            )
        return {"id": kudos_id, "status": action, "isVisible": False}

    @application.patch("/api/kudos/{kudos_id}/hide")
    def hide_kudos(kudos_id: int, payload: ModerationRequest, request: Request) -> dict[str, Any]:
        return moderate(kudos_id, request, payload, "hidden")

    @application.delete("/api/kudos/{kudos_id}")
    def delete_kudos(kudos_id: int, payload: ModerationRequest, request: Request) -> dict[str, Any]:
        return moderate(kudos_id, request, payload, "deleted")

    return application


app = create_app()
