from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_api_key
from app.db.session import get_session
from app.schemas.artist import ArtistCreate, ArtistResponse, ArtistUpdate
from app.services import artist_service

router = APIRouter(prefix="/api/v1/artists", tags=["artists"], dependencies=[Depends(verify_api_key)])


@router.get("", response_model=list[ArtistResponse])
async def list_artists(session: AsyncSession = Depends(get_session)) -> list[ArtistResponse]:
    return await artist_service.list_artists(session)


@router.post("", response_model=ArtistResponse, status_code=status.HTTP_201_CREATED)
async def create_artist(data: ArtistCreate, session: AsyncSession = Depends(get_session)) -> ArtistResponse:
    return await artist_service.create_artist(session, data)


@router.get("/{artist_id}", response_model=ArtistResponse)
async def get_artist_by_id(artist_id: int, session: AsyncSession = Depends(get_session)) -> ArtistResponse:
    artist = await artist_service.get_artist_response(session, artist_id)
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artist not found")
    return artist


@router.patch("/{artist_id}", response_model=ArtistResponse)
async def update_artist(
    artist_id: int,
    data: ArtistUpdate,
    session: AsyncSession = Depends(get_session),
) -> ArtistResponse:
    artist = await artist_service.get_artist(session, artist_id)
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artist not found")
    return await artist_service.update_artist(session, artist, data)


@router.delete("/{artist_id}", response_model=ArtistResponse)
async def delete_artist(artist_id: int, session: AsyncSession = Depends(get_session)) -> ArtistResponse:
    artist = await artist_service.get_artist(session, artist_id)
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artist not found")
    return await artist_service.delete_artist(session, artist)
