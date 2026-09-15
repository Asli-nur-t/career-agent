import argparse
import json
import unicodedata
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.company_names import company_name_key
from app.database import engine
from app.models import (
    Company,
    CompanyAffiliation,
    CompanyWebProfile,
    DiscoveryAttempt,
)


PROFILE_STATUS_RANK = {
    "verified": 4,
    "candidate_found": 3,
    "needs_review": 2,
    "not_found": 1,
}

MAX_AUTOMATIC_MERGE_GROUPS = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize company keys and merge exact canonical "
            "duplicates transactionally."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the displayed normalization plan.",
    )
    parser.add_argument(
        "--expected-merge-groups",
        type=int,
        default=None,
        help=(
            "Required with --apply. The operation stops if the "
            "actual duplicate group count differs."
        ),
    )
    return parser.parse_args()


def is_branch_name(name: str) -> bool:
    normalized = name.replace("ı", "i").replace("İ", "I")
    normalized = unicodedata.normalize(
        "NFKD",
        normalized,
    ).casefold()
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = normalized.rstrip(" .")

    return normalized.endswith("subesi")


def preferred_display_name(companies: list[Company]) -> str:
    return min(
        (company.name for company in companies),
        key=lambda name: (
            is_branch_name(name),
            -len(name),
            name.casefold(),
        ),
    )


def timestamp_value(value: datetime | None) -> float:
    if value is None:
        return 0.0
    return value.timestamp()


def profile_sort_key(
    profile: CompanyWebProfile,
) -> tuple[int, float, float, str]:
    return (
        PROFILE_STATUS_RANK.get(profile.status, 0),
        timestamp_value(profile.last_verified_at),
        timestamp_value(profile.updated_at),
        str(profile.company_id),
    )


def merged_target_sector(
    values: list[bool | None],
) -> bool | None:
    if any(value is True for value in values):
        return True
    if any(value is False for value in values):
        return False
    return None


def merge_evidence(
    profiles: list[CompanyWebProfile],
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for profile in sorted(
        profiles,
        key=profile_sort_key,
        reverse=True,
    ):
        for item in profile.evidence or []:
            if not isinstance(item, dict):
                continue

            safe_item = {
                str(key): str(value)
                for key, value in item.items()
                if isinstance(key, str)
                and isinstance(value, str)
            }

            if not safe_item:
                continue

            marker = json.dumps(
                safe_item,
                ensure_ascii=False,
                sort_keys=True,
            )

            if marker in seen:
                continue

            seen.add(marker)
            merged.append(safe_item)

    return merged


def build_groups(
    companies: list[Company],
) -> dict[str, list[Company]]:
    groups: dict[str, list[Company]] = defaultdict(list)

    for company in companies:
        groups[company_name_key(company.name)].append(company)

    return dict(groups)


def choose_survivor(
    companies: list[Company],
    profiles_by_company: dict[UUID, CompanyWebProfile],
) -> Company:
    profiles = [
        profiles_by_company[company.id]
        for company in companies
        if company.id in profiles_by_company
    ]

    if profiles:
        best_profile = max(profiles, key=profile_sort_key)
        return next(
            company
            for company in companies
            if company.id == best_profile.company_id
        )

    return min(
        companies,
        key=lambda company: (
            is_branch_name(company.name),
            company.created_at,
            str(company.id),
        ),
    )


def build_plan(
    duplicate_groups: list[tuple[str, list[Company]]],
    profiles_by_company: dict[UUID, CompanyWebProfile],
) -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []

    for key, companies in duplicate_groups:
        survivor = choose_survivor(
            companies,
            profiles_by_company,
        )
        profile = profiles_by_company.get(survivor.id)

        plan.append(
            {
                "canonical_key": key,
                "survivor_id": str(survivor.id),
                "survivor_current_name": survivor.name,
                "preferred_name": preferred_display_name(companies),
                "preserved_profile_status": (
                    profile.status if profile is not None else None
                ),
                "merged_companies": [
                    {
                        "company_id": str(company.id),
                        "name": company.name,
                    }
                    for company in sorted(
                        companies,
                        key=lambda item: item.name.casefold(),
                    )
                ],
            }
        )

    return plan


def merge_group(
    session: Session,
    canonical_key: str,
    companies: list[Company],
    profiles_by_company: dict[UUID, CompanyWebProfile],
) -> UUID:
    company_ids = [company.id for company in companies]
    survivor = choose_survivor(
        companies,
        profiles_by_company,
    )
    duplicate_ids = [
        company_id
        for company_id in company_ids
        if company_id != survivor.id
    ]

    profiles = [
        profiles_by_company[company_id]
        for company_id in company_ids
        if company_id in profiles_by_company
    ]

    if profiles:
        best_profile = max(profiles, key=profile_sort_key)
        best_profile.evidence = merge_evidence(profiles)

        for profile in profiles:
            if profile is not best_profile:
                session.delete(profile)

    affiliations = session.scalars(
        select(CompanyAffiliation).where(
            CompanyAffiliation.company_id.in_(company_ids)
        )
    ).all()

    affiliation_values: dict[str, list[bool | None]] = defaultdict(list)

    for affiliation in affiliations:
        affiliation_values[affiliation.teknopark].append(
            affiliation.source_target_sector
        )

    session.execute(
        delete(CompanyAffiliation).where(
            CompanyAffiliation.company_id.in_(company_ids)
        )
    )
    session.flush()

    for teknopark, values in affiliation_values.items():
        session.add(
            CompanyAffiliation(
                company_id=survivor.id,
                teknopark=teknopark,
                source_target_sector=merged_target_sector(values),
            )
        )

    if duplicate_ids:
        session.execute(
            update(DiscoveryAttempt)
            .where(
                DiscoveryAttempt.company_id.in_(duplicate_ids)
            )
            .values(company_id=survivor.id)
        )

    preferred_name = preferred_display_name(companies)
    sectors = [
        company.sector
        for company in companies
        if company.sector
    ]

    survivor.name = preferred_name
    survivor.name_key = canonical_key
    survivor.needs_review = any(
        company.needs_review
        for company in companies
    )

    if survivor.sector is None and sectors:
        survivor.sector = sectors[0]

    for company in companies:
        if company.id != survivor.id:
            session.delete(company)

    session.flush()
    return survivor.id


def main() -> int:
    args = parse_args()

    if (
        args.apply
        and args.expected_merge_groups is None
    ):
        print(
            "--apply kullanırken "
            "--expected-merge-groups zorunludur."
        )
        return 2

    try:
        with Session(engine) as session:
            statement = select(Company).order_by(
                Company.name,
                Company.id,
            )

            if args.apply:
                statement = statement.with_for_update()

            companies = list(session.scalars(statement).all())
            profiles = list(
                session.scalars(
                    select(CompanyWebProfile)
                ).all()
            )
            profiles_by_company = {
                profile.company_id: profile
                for profile in profiles
            }

            groups = build_groups(companies)
            duplicate_groups = sorted(
                (
                    (key, grouped_companies)
                    for key, grouped_companies in groups.items()
                    if len(grouped_companies) > 1
                ),
                key=lambda item: item[0],
            )

            plan = build_plan(
                duplicate_groups,
                profiles_by_company,
            )

            summary = {
                "mode": "apply" if args.apply else "dry_run",
                "companies_before": len(companies),
                "duplicate_group_count": len(
                    duplicate_groups
                ),
                "companies_to_merge": sum(
                    len(group) - 1
                    for _, group in duplicate_groups
                ),
                "keys_to_normalize": sum(
                    company.name_key
                    != company_name_key(company.name)
                    for company in companies
                ),
                "plan": plan,
            }

            if not args.apply:
                print(
                    json.dumps(
                        summary,
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            if (
                len(duplicate_groups)
                != args.expected_merge_groups
            ):
                print(
                    "Duplicate grup sayısı beklenen değerle "
                    "eşleşmedi; işlem uygulanmadı."
                )
                return 2

            if (
                len(duplicate_groups)
                > MAX_AUTOMATIC_MERGE_GROUPS
            ):
                print(
                    "Otomatik birleştirme güvenlik sınırı "
                    "aşıldı; işlem uygulanmadı."
                )
                return 2

            for canonical_key, grouped_companies in (
                duplicate_groups
            ):
                merge_group(
                    session,
                    canonical_key,
                    grouped_companies,
                    profiles_by_company,
                )

            session.flush()

            remaining_companies = list(
                session.scalars(
                    select(Company).order_by(
                        Company.name,
                        Company.id,
                    )
                ).all()
            )

            for company in remaining_companies:
                company.name_key = company_name_key(
                    company.name
                )

            session.flush()

            remaining_groups = build_groups(
                remaining_companies
            )

            if any(
                len(group) > 1
                for group in remaining_groups.values()
            ):
                raise RuntimeError(
                    "Duplicate companies remained after merge."
                )

            summary["companies_after"] = session.scalar(
                select(func.count()).select_from(Company)
            )
            summary["affiliations_after"] = session.scalar(
                select(func.count()).select_from(
                    CompanyAffiliation
                )
            )

            session.commit()

    except (SQLAlchemyError, RuntimeError, ValueError):
        print(
            "Şirket normalizasyonu uygulanamadı; "
            "transaction geri alındı."
        )
        return 1
    finally:
        engine.dispose()

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
