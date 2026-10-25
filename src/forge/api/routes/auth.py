from typing import Annotated

from fastapi import APIRouter, Depends

from forge.api.dependencies.auth import authenticated_api_key
from forge.persistence.models import ApiKey
from forge.schemas.api_keys import ApiKeyResponse

router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


@router.get("/me", response_model=ApiKeyResponse)
async def get_current_key(
    api_key: Annotated[ApiKey, Depends(authenticated_api_key)],
) -> ApiKeyResponse:
    """Expose this key's identity and scopes without exposing its credential."""
    return ApiKeyResponse.model_validate(api_key)
