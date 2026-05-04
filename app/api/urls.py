from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.models import Url
from app.schemas import ShortenRequest, ShortenResponse
from app.services.shortener import generate_short_code

MAX_COLLISION_RETRIES = 5

router = APIRouter()


@router.post("/shorten", response_model=ShortenResponse)
async def shorten(
    payload: ShortenRequest,
    session: AsyncSession = Depends(get_session),
) -> ShortenResponse:
    settings = get_settings()
    long_url = str(payload.url)

    for _ in range(MAX_COLLISION_RETRIES):
        short_code = generate_short_code()
        url = Url(short_code=short_code, long_url=long_url)
        session.add(url)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            continue
        return ShortenResponse(
            short_code=short_code,
            short_url=f"{settings.base_url}/{short_code}",
            long_url=long_url,
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Could not generate a unique short code; please retry.",
    )
