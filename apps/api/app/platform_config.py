"""Runtime config read/write through the database, with env fallback.

The Settings GUI calls these helpers to store integration secrets
(ANTHROPIC_API_KEY, SMTP password) and non-secret values (SMTP host,
public URL) without anyone editing the .env file. Existing .env-based
deploys keep working — every getter falls back to the static config
when the DB row is missing.

Secret values are encrypted using the existing Fernet wrapper in
app.crypto. The API NEVER returns a decrypted secret over HTTP; the
operator can only check whether a value is set and overwrite it.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as env_settings
from app.crypto import decrypt, encrypt
from app.models import PlatformSetting

# Keys we manage. Any key not in here is rejected by set_value to keep
# the surface tight (no arbitrary key/value store).
KEY_ANTHROPIC_API_KEY = "anthropic_api_key"
KEY_AGENT_MODEL = "agent_model"
KEY_SMTP_HOST = "smtp_host"
KEY_SMTP_PORT = "smtp_port"
KEY_SMTP_USER = "smtp_user"
KEY_SMTP_PASSWORD = "smtp_password"
KEY_SMTP_FROM = "smtp_from"
KEY_SMTP_USE_TLS = "smtp_use_tls"
KEY_APP_BASE_URL = "app_base_url"

SECRET_KEYS = {KEY_ANTHROPIC_API_KEY, KEY_SMTP_PASSWORD}
ALLOWED_KEYS = {
    KEY_ANTHROPIC_API_KEY,
    KEY_AGENT_MODEL,
    KEY_SMTP_HOST,
    KEY_SMTP_PORT,
    KEY_SMTP_USER,
    KEY_SMTP_PASSWORD,
    KEY_SMTP_FROM,
    KEY_SMTP_USE_TLS,
    KEY_APP_BASE_URL,
}


class UnknownSettingError(KeyError):
    pass


async def get_raw(db: AsyncSession, key: str) -> str | None:
    """Return the decrypted value (for secrets) or plaintext value (for
    non-secrets). Returns None when the row doesn't exist."""
    if key not in ALLOWED_KEYS:
        raise UnknownSettingError(key)
    row = await db.scalar(select(PlatformSetting).where(PlatformSetting.key == key))
    if row is None or row.value is None or row.value == "":
        return None
    if row.is_secret:
        try:
            return decrypt(row.value)
        except ValueError:
            return None
    return row.value


async def set_value(db: AsyncSession, key: str, value: str | None) -> None:
    """Upsert. Empty/None deletes the value (falls back to env). Secret
    keys are stored as Fernet ciphertext; non-secrets as plaintext."""
    if key not in ALLOWED_KEYS:
        raise UnknownSettingError(key)
    row = await db.scalar(select(PlatformSetting).where(PlatformSetting.key == key))
    is_secret = key in SECRET_KEYS
    stored = (
        None if value in (None, "") else (encrypt(value) if is_secret else value)
    )
    if row is None:
        row = PlatformSetting(key=key, value=stored, is_secret=is_secret)
        db.add(row)
    else:
        row.value = stored
        row.is_secret = is_secret


# Convenience getters with env fallback. Used at request time so the
# operator's GUI edits take effect on the next call (no API restart).


async def anthropic_api_key(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_ANTHROPIC_API_KEY)) or env_settings.anthropic_api_key


async def agent_model(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_AGENT_MODEL)) or env_settings.agent_model


async def smtp_host(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_SMTP_HOST)) or env_settings.smtp_host


async def smtp_port(db: AsyncSession) -> int:
    raw = await get_raw(db, KEY_SMTP_PORT)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return env_settings.smtp_port


async def smtp_user(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_SMTP_USER)) or env_settings.smtp_user


async def smtp_password(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_SMTP_PASSWORD)) or env_settings.smtp_password


async def smtp_from(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_SMTP_FROM)) or env_settings.smtp_from


async def smtp_use_tls(db: AsyncSession) -> bool:
    raw = await get_raw(db, KEY_SMTP_USE_TLS)
    if raw is None:
        return env_settings.smtp_use_tls
    return raw.lower() in ("1", "true", "yes", "on")


async def app_base_url(db: AsyncSession) -> str:
    return (await get_raw(db, KEY_APP_BASE_URL)) or env_settings.app_base_url


async def status_snapshot(db: AsyncSession) -> dict[str, object]:
    """Public-safe snapshot for the Settings UI. Secrets are reported
    as booleans (set / not set) but never echoed back as values."""
    return {
        "anthropic": bool(await anthropic_api_key(db)),
        "smtp_configured": bool(
            (await smtp_host(db)) and (await smtp_from(db))
        ),
        "agent_model": await agent_model(db),
        "smtp_host": (await smtp_host(db)) or None,
        "smtp_port": await smtp_port(db),
        "smtp_user": (await smtp_user(db)) or None,
        "smtp_from": (await smtp_from(db)) or None,
        "smtp_password_set": bool(await smtp_password(db)),
        "smtp_use_tls": await smtp_use_tls(db),
        "app_base_url": (await app_base_url(db)) or None,
    }
