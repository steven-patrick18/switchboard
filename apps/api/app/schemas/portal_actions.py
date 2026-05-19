from pydantic import BaseModel, ConfigDict


class PortalActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service: str
    action: str
    label: str
    description: str
    required_params: list[str]
    optional_params: list[str]
    tier: str
