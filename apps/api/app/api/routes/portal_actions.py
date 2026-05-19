from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models import User
from app.portal_actions import list_actions
from app.schemas.portal_actions import PortalActionOut

router = APIRouter(prefix="/portal-actions", tags=["portal-actions"])


@router.get("", response_model=list[PortalActionOut])
async def list_portal_actions(
    _: User = Depends(get_current_user),
) -> list[PortalActionOut]:
    return [
        PortalActionOut(
            service=a.service,
            action=a.action,
            label=a.label,
            description=a.description,
            required_params=list(a.required_params),
            optional_params=list(a.optional_params),
            tier=a.tier,
        )
        for a in list_actions()
    ]
