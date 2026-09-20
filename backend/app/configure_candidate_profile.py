"""Validate and persist a private candidate matching profile."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.matching import CandidateProfileSpec
from app.models import CandidateProfile


MAX_PROFILE_BYTES = 64 * 1024


def load_profile(path: Path) -> CandidateProfileSpec:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.suffix.lower() != ".json":
            raise ValueError("Profil normal bir JSON dosyası olmalıdır.")
        if resolved.stat().st_size > MAX_PROFILE_BYTES:
            raise ValueError("Profil dosyası 64 KiB sınırını aşıyor.")
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Profil kökü bir JSON nesnesi olmalıdır.")
        return CandidateProfileSpec.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Profil dosyası okunamadı veya geçersiz JSON.") from error
    except ValidationError as error:
        fields = sorted(
            {
                str(item["loc"][0])
                for item in error.errors()
                if item.get("loc")
            }
        )
        summary = ", ".join(fields[:10]) or "bilinmeyen alan"
        raise ValueError(f"Profil doğrulanamadı. Alanlar: {summary}") from error


def save_profile(database: object, spec: CandidateProfileSpec) -> tuple[str, bool]:
    config_hash = spec.config_hash()
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(
                CandidateProfile.label == spec.label
            )
        )
        created = profile is None
        if profile is None:
            profile = CandidateProfile(label=spec.label)
            session.add(profile)
        profile.target_roles = spec.target_roles
        profile.secondary_roles = spec.secondary_roles
        profile.skills = spec.skills
        profile.preferred_locations = spec.preferred_locations
        profile.excluded_locations = spec.excluded_locations
        profile.allowed_work_modes = spec.allowed_work_modes
        profile.location_filter_mode = spec.location_filter_mode
        profile.max_listing_age_days = spec.max_listing_age_days
        profile.excluded_keywords = spec.excluded_keywords
        profile.max_years_experience = spec.max_years_experience
        profile.remote_allowed = spec.remote_allowed
        profile.config_hash = config_hash
        session.commit()
        profile_id = str(profile.id)
    return profile_id, created


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and save a candidate matching profile."
    )
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    try:
        spec = load_profile(args.file)
    except ValueError as error:
        raise SystemExit(str(error)) from None

    from app.database import engine

    try:
        profile_id, created = save_profile(engine, spec)
    except SQLAlchemyError:
        raise SystemExit("Aday profili veritabanına kaydedilemedi.") from None
    print(json.dumps({
        "profile_id": profile_id,
        "label": spec.label,
        "config_hash": spec.config_hash(),
        "created": created,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
