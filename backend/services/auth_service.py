from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from database import _connect, _transaction


password_hasher = PasswordHasher()

TOKEN_EXPIRY_HOURS = 24


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_farmer(
    name: str,
    email: str,
    password: str,
) -> dict:

    name = name.strip()
    email = email.strip().lower()

    if not name:
        raise ValueError("Name is required.")

    if len(name) > 100:
        raise ValueError("Name is too long.")

    if "@" not in email or len(email) > 254:
        raise ValueError("Enter a valid email address.")

    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")

    password_hash = password_hasher.hash(password)

    try:
        with _transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (name, email, password_hash, role)
                VALUES (?, ?, ?, 'farmer')
                """,
                (name, email, password_hash),
            )

            user_id = cursor.lastrowid

    except Exception as exc:
        if "unique" in str(exc).lower() or isinstance(exc, sqlite3.IntegrityError):
            raise ValueError("An account with this email already exists.")
        raise

    return {
        "id": user_id,
        "name": name,
        "email": email,
        "role": "farmer",
    }


def login_farmer(
    email: str,
    password: str,
) -> dict:

    email = email.strip().lower()

    with _connect() as connection:
        user = connection.execute(
            """
            SELECT id, name, email, password_hash, role, is_active
            FROM users
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

    if user is None:
        raise ValueError("Invalid email or password.")

    if not user["is_active"] or user["role"] != "farmer":
        raise ValueError("This account is unavailable.")

    try:
        password_hasher.verify(user["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise ValueError("Invalid email or password.")

    token = secrets.token_urlsafe(32)
    token_hash = hash_token(token)

    expires_at = (
        datetime.now(timezone.utc) +
        timedelta(hours=TOKEN_EXPIRY_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")

    with _transaction() as connection:
        connection.execute(
            """
            INSERT INTO auth_sessions (
                user_id, token_hash, expires_at
            )
            VALUES (?, ?, ?)
            """,
            (user["id"], token_hash, expires_at),
        )

    return {
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
        },
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
    }


def get_farmer_from_token(token: str) -> dict | None:

    if not token:
        return None

    token_hash = hash_token(token)

    with _connect() as connection:
        user = connection.execute(
            """
            SELECT
                u.id,
                u.name,
                u.email,
                u.role
            FROM auth_sessions AS a
            JOIN users AS u ON u.id = a.user_id
            WHERE a.token_hash = ?
              AND a.revoked_at IS NULL
              AND a.expires_at > strftime('%Y-%m-%d %H:%M:%S', 'now')
              AND u.is_active = 1
            """,
            (token_hash,),
        ).fetchone()

    return dict(user) if user else None


def logout_farmer(token: str) -> bool:

    if not token:
        return False

    token_hash = hash_token(token)

    with _transaction() as connection:
        cursor = connection.execute(
            """
            UPDATE auth_sessions
            SET revoked_at = strftime('%Y-%m-%d %H:%M:%S', 'now')
            WHERE token_hash = ?
              AND revoked_at IS NULL
            """,
            (token_hash,),
        )

    return cursor.rowcount > 0