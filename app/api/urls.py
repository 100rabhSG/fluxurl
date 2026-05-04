from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.models import Url
from app.schemas import ErrorResponse, ShortenRequest, ShortenResponse
from app.services.shortener import ALPHABET, DEFAULT_LENGTH, generate_short_code

MAX_COLLISION_RETRIES = 5
_ALPHABET_SET = frozenset(ALPHABET)

router = APIRouter()


@router.post(
    "/shorten",
    response_model=ShortenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        422: {"model": ErrorResponse, "description": "Invalid or too-long URL"},
        500: {"model": ErrorResponse, "description": "Short code generation failed"},
    },
)
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


@router.get(
    "/{short_code}",
    status_code=status.HTTP_302_FOUND,
    responses={
        302: {"description": "Redirect to the original long URL"},
        404: {"model": ErrorResponse, "description": "Short code not found"},
    },
)
async def redirect_to_long_url(
    short_code: str,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if len(short_code) != DEFAULT_LENGTH or not _ALPHABET_SET.issuperset(short_code):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="short code not found",
        )

    result = await session.execute(select(Url).where(Url.short_code == short_code))
    url = result.scalar_one_or_none()
    if url is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="short code not found",
        )

    return RedirectResponse(
        url=url.long_url,
        status_code=status.HTTP_302_FOUND,
    )

