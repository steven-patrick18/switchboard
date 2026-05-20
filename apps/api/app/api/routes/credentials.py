import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.audit import record_audit
from app.db import get_db
from app.models import Client, Credential, User
from app.schemas.credentials import CredentialOut, CredentialPatch, CredentialUpsert
from app.vault import list_credentials, store_credential, update_credential

router = APIRouter(prefix="/clients/{client_id}", tags=["credentials"])


# Canonical login URLs for well-known services. The operator can
# override per credential; this just removes typing for the obvious
# cases. Add to this table as new common services come up.
_KNOWN_URLS: dict[str, str] = {
    "fcc_cores": "https://apps.fcc.gov/cores/userLogin.do",
    "usac_efile": "https://efile.usac.org/",
    "neca": "https://www.neca.org/business-solutions/companycodeocnadministration",
    "stipa": "https://authenticate.iconectiv.com/authenticate-app/login.htm",
    "iconectiv": "https://authenticate.iconectiv.com/authenticate-app/login.htm",
    "rmd": "https://fccprod.servicenowservices.com/rmd",
    "irs_eftps": "https://www.eftps.gov/eftps/",
    "twilio": "https://console.twilio.com/",
    "telnyx": "https://portal.telnyx.com/",
    "bandwidth": "https://dashboard.bandwidth.com/",
    "inteliquent": "https://gold.inteliquent.com/",
}


def _default_url_for(service: str) -> str | None:
    """Best-effort URL lookup. Normalises the service name (lowercase,
    spaces/hyphens → underscore) and looks up in the canonical table.
    Returns None for unknown services so the operator types their own."""
    key = service.strip().lower().replace(" ", "_").replace("-", "_")
    return _KNOWN_URLS.get(key)


async def _owned_client(
    client_id: uuid.UUID, user: User, db: AsyncSession
) -> Client:
    client = await db.get(Client, client_id)
    if client is None or client.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client not found"
        )
    return client


@router.post(
    "/credentials", response_model=CredentialOut, status_code=status.HTTP_201_CREATED
)
async def upsert_credential(
    client_id: uuid.UUID,
    body: CredentialUpsert,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Credential:
    """Create or rotate a (client, service) credential. If the operator
    didn't supply a URL but the service is in the well-known table,
    we auto-fill it — saves typing for FCC CORES / Twilio / etc."""
    await _owned_client(client_id, user, db)
    url = body.url or _default_url_for(body.service)
    cred = await store_credential(
        db,
        client_id=client_id,
        service=body.service,
        secret=body.secret,
        username=body.username,
        scope=body.scope,
        expires_at=body.expires_at,
        url=url,
    )
    await record_audit(
        db,
        actor=user.email,
        action="credential.stored",
        subject=f"credential:{cred.id}",
        client_id=client_id,
        # NEVER include the secret. URL is non-secret metadata.
        after={"service": body.service, "url_set": bool(url)},
    )
    await db.commit()
    return cred


@router.get("/credentials", response_model=list[CredentialOut])
async def list_client_credentials(
    client_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Credential]:
    await _owned_client(client_id, user, db)
    return await list_credentials(db, client_id)


@router.patch(
    "/credentials/{credential_id}", response_model=CredentialOut
)
async def patch_credential(
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    body: CredentialPatch,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Credential:
    """Edit credential metadata (URL, username, scope, expiry) or
    rotate its secret. Send only fields you want to change — others
    stay untouched. The actual secret (new or old) is never returned
    or logged."""
    await _owned_client(client_id, user, db)
    cred = await db.get(Credential, credential_id)
    if cred is None or cred.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found"
        )
    fields_set = body.model_fields_set
    changes: dict[str, object] = {}
    kwargs: dict = {}
    if "url" in fields_set:
        kwargs["url"] = body.url
        changes["url"] = "set" if body.url else "cleared"
    if "username" in fields_set:
        kwargs["username"] = body.username
        changes["username"] = "set" if body.username else "cleared"
    if "scope" in fields_set:
        kwargs["scope"] = body.scope
        changes["scope"] = "set" if body.scope else "cleared"
    if "expires_at" in fields_set:
        kwargs["expires_at"] = body.expires_at
        changes["expires_at"] = (
            body.expires_at.isoformat() if body.expires_at else "cleared"
        )
    if body.new_secret is not None:
        kwargs["new_secret"] = body.new_secret
        changes["secret"] = "rotated"
    if not kwargs:
        return cred  # no-op patch
    await update_credential(db, cred, **kwargs)
    await record_audit(
        db,
        actor=user.email,
        action="credential.updated",
        subject=f"credential:{cred.id}",
        client_id=client_id,
        after={"service": cred.service, "changes": changes},
    )
    await db.commit()
    return cred


@router.delete(
    "/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_credential(
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _owned_client(client_id, user, db)
    cred = await db.get(Credential, credential_id)
    if cred is None or cred.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found"
        )
    await db.delete(cred)
    await record_audit(
        db,
        actor=user.email,
        action="credential.deleted",
        subject=f"credential:{credential_id}",
        client_id=client_id,
        after={"service": cred.service},
    )
    await db.commit()
