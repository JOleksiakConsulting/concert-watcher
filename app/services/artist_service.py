from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.matching.normalization import strict_normalize
from app.models import ArtistAlias, ArtistCountry, WatchedArtist
from app.schemas.artist import ArtistCreate, ArtistResponse, ArtistUpdate


def _to_response(artist: WatchedArtist) -> ArtistResponse:
    return ArtistResponse(
        id=artist.id,
        canonical_name=artist.canonical_name,
        aliases=[a.alias for a in artist.aliases],
        countries=[c.country_code for c in artist.countries],
        match_mode=artist.match_mode,  # type: ignore[arg-type]
        enabled=artist.enabled,
        created_at=artist.created_at,
        updated_at=artist.updated_at,
    )


async def list_artists(session: AsyncSession) -> list[ArtistResponse]:
    result = await session.execute(
        select(WatchedArtist)
        .options(selectinload(WatchedArtist.aliases), selectinload(WatchedArtist.countries))
        .order_by(WatchedArtist.id)
    )
    return [_to_response(a) for a in result.scalars().all()]


async def get_artist(session: AsyncSession, artist_id: int) -> WatchedArtist | None:
    result = await session.execute(
        select(WatchedArtist)
        .where(WatchedArtist.id == artist_id)
        .options(selectinload(WatchedArtist.aliases), selectinload(WatchedArtist.countries))
    )
    return result.scalar_one_or_none()


async def get_artist_response(session: AsyncSession, artist_id: int) -> ArtistResponse | None:
    artist = await get_artist(session, artist_id)
    if artist is None:
        return None
    return _to_response(artist)


async def create_artist(session: AsyncSession, data: ArtistCreate) -> ArtistResponse:
    artist = WatchedArtist(
        canonical_name=data.canonical_name,
        normalized_name=strict_normalize(data.canonical_name),
        match_mode=data.match_mode,
        enabled=True,
    )
    session.add(artist)
    await session.flush()

    for alias in data.aliases:
        session.add(
            ArtistAlias(
                artist_id=artist.id,
                alias=alias,
                normalized_alias=strict_normalize(alias),
            )
        )
    for country in data.countries:
        session.add(ArtistCountry(artist_id=artist.id, country_code=country.upper()))

    await session.commit()
    await session.refresh(artist, ["aliases", "countries"])
    return _to_response(artist)


async def update_artist(session: AsyncSession, artist: WatchedArtist, data: ArtistUpdate) -> ArtistResponse:
    if data.canonical_name is not None:
        artist.canonical_name = data.canonical_name
        artist.normalized_name = strict_normalize(data.canonical_name)
    if data.match_mode is not None:
        artist.match_mode = data.match_mode
    if data.enabled is not None:
        artist.enabled = data.enabled

    if data.aliases is not None:
        artist.aliases.clear()
        await session.flush()
        for alias in data.aliases:
            session.add(
                ArtistAlias(
                    artist_id=artist.id,
                    alias=alias,
                    normalized_alias=strict_normalize(alias),
                )
            )

    if data.countries is not None:
        artist.countries.clear()
        await session.flush()
        for country in data.countries:
            session.add(ArtistCountry(artist_id=artist.id, country_code=country.upper()))

    await session.commit()
    await session.refresh(artist, ["updated_at", "aliases", "countries"])
    return _to_response(artist)


async def delete_artist(session: AsyncSession, artist: WatchedArtist) -> ArtistResponse:
    artist_id = artist.id
    artist.enabled = False
    await session.commit()
    refreshed = await get_artist(session, artist_id)
    assert refreshed is not None
    return _to_response(refreshed)
