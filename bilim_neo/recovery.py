"""Local recovery identity; credentials never go to BilimClass."""

import hashlib
import hmac
import json
import os
import secrets


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
    return f"pbkdf2:{salt}:{digest}"


def matches_login(login):
    configured = os.getenv("RECOVERY_ADMIN_LOGIN", "")
    return bool(configured and hmac.compare_digest(configured.encode(), login.encode()))


def verify(password):
    encoded = os.getenv("RECOVERY_ADMIN_HASH", "")
    try:
        version, salt, _ = encoded.split(":")
        return version == "pbkdf2" and hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def fingerprint():
    return hashlib.sha256((os.getenv("RECOVERY_ADMIN_LOGIN", "") + os.getenv("RECOVERY_ADMIN_HASH", "")).encode()).hexdigest()


def active(store, chat_id):
    return bool(os.getenv("RECOVERY_ADMIN_LOGIN") and os.getenv("RECOVERY_ADMIN_HASH") and store.has_admin_session(chat_id, fingerprint()))


def login(store, chat_id, username, password):
    if not matches_login(username):
        return False
    if not store.rate_limit(f"recovery:{chat_id}", 5, 900) or not store.rate_limit("recovery:global", 60, 900):
        return False
    if not verify(password):
        return False
    if not store.user(chat_id):
        try:
            profile = json.loads(os.getenv("RECOVERY_ADMIN_PROFILE", "{}"))
        except ValueError:
            profile = {}
        if not isinstance(profile, dict):
            profile = {}
        profile.update(account_kind="local_admin", fio=username)
        store.save_user(chat_id, username, "", profile)
    store.grant_admin(chat_id, fingerprint())
    store.set_pref(chat_id, "admin_enabled", True)
    return True
