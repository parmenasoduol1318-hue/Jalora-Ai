from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.errors import BadRequestError
from app.models.professional_profile import ProfessionalProfile
from app.models.user import User
from app.schemas import ProfessionalProfileOut, ProfessionalProfileUpdate
from app.services import search_service

router = APIRouter(prefix="/api/people", tags=["people"])


def _profile_out(user: User, profile: ProfessionalProfile | None) -> dict:
    profile = profile or ProfessionalProfile(user_id=user.id)
    return {
        "name": user.name,
        "email": user.email,
        "courses": profile.courses or [],
        "qualifications": profile.qualifications or [],
        "skills": profile.skills or [],
        "phone": profile.phone or "",
        "discoverable": bool(profile.discoverable),
        "share_email": bool(profile.share_email),
        "share_phone": bool(profile.share_phone),
    }


@router.get("/profile", response_model=ProfessionalProfileOut)
def get_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.get(ProfessionalProfile, user.id)
    return _profile_out(user, profile)


@router.put("/profile", response_model=ProfessionalProfileOut)
def update_profile(
    payload: ProfessionalProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = db.get(ProfessionalProfile, user.id)
    if profile is None:
        profile = ProfessionalProfile(user_id=user.id)
        db.add(profile)

    profile.courses = payload.courses
    profile.qualifications = payload.qualifications
    profile.skills = payload.skills
    profile.phone = payload.phone
    profile.discoverable = payload.discoverable
    profile.share_email = payload.share_email and payload.discoverable
    profile.share_phone = payload.share_phone and payload.discoverable
    profile.search_text = " ".join(
        [*payload.skills, *payload.qualifications, *payload.courses]
    ).casefold()
    db.commit()
    db.refresh(profile)
    return _profile_out(user, profile)


@router.get("/search")
def search_members(
    query: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=30, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search only opted-in member profiles; private contact fields stay hidden."""
    normalized = " ".join(query.split())
    if not normalized:
        raise BadRequestError("Enter a skill or qualification to search.", "EMPTY_QUERY")

    tokens = list(dict.fromkeys(re.findall(r"[\w+#.-]+", normalized.casefold())))
    if not tokens:
        raise BadRequestError("Enter a skill or qualification to search.", "EMPTY_QUERY")
    stmt = (
        select(User, ProfessionalProfile)
        .join(ProfessionalProfile, ProfessionalProfile.user_id == User.id)
        .where(ProfessionalProfile.discoverable.is_(True))
        .where(User.is_active.is_(True))
    )
    for token in tokens:
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(ProfessionalProfile.search_text.ilike(
            f"%{escaped}%", escape="\\"
        ))

    rows = db.execute(stmt.order_by(User.name).limit(limit)).all()
    results = []
    for member, profile in rows:
        results.append({
            "id": member.id,
            "name": member.name,
            "courses": profile.courses or [],
            "qualifications": profile.qualifications or [],
            "skills": profile.skills or [],
            "email": member.email if profile.share_email else "",
            "phone": profile.phone if profile.share_phone else "",
        })
    return {"query": normalized, "results": results}


@router.get("/search/external")
async def search_external_profiles(
    skill: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=8, ge=1, le=20),
    user: User = Depends(get_current_user),
):
    """Search public search-index results only; never scrape gated profile sites."""
    term = " ".join(skill.split())
    if not term:
        raise BadRequestError("Enter a skill to search for.", "EMPTY_QUERY")
    query = (
        f'"{term}" (professional OR engineer OR developer OR specialist) '
        "(site:linkedin.com/in OR site:github.com)"
    )
    try:
        rows = await search_service.search(query, max_results=limit)
    except search_service.SearchError as exc:
        raise BadRequestError(
            f"Public profile search failed: {exc.detail}", exc.code
        ) from exc

    results = []
    for row in rows:
        item = row.as_dict()
        url = str(item.get("url") or "")
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            continue
        host = parsed.hostname.lower().removeprefix("www.")
        if host not in {"linkedin.com", "github.com"} and not host.endswith(
            (".linkedin.com", ".github.com")
        ):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": url,
            "snippet": item.get("snippet", ""),
            "source": host,
        })
    return {
        "skill": term,
        "results": results[:limit],
        "notice": (
            "Publicly indexed results only. Profile accuracy and identity have not "
            "been independently verified; open the source to confirm."
        ),
    }
