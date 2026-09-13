import csv
import hashlib
import io
import json
import unicodedata
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.database import PROJECT_ROOT, engine
from app.models import Company, CompanyAffiliation


MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
COLUMNS = {"name", "sector", "teknopark", "hedef_sektor_mu"}


def clean_text(value: str, limit: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError("Eksik veya geçersiz alan.")

    cleaned = " ".join(unicodedata.normalize("NFKC", value).split())

    if required and not cleaned:
        raise ValueError("Zorunlu alan boş.")
    if len(cleaned) > limit:
        raise ValueError("Alan uzunluk sınırını aşıyor.")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in cleaned):
        raise ValueError("Geçersiz kontrol karakteri.")

    return cleaned


def name_key(name: str) -> str:
    return hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()


REVIEW_KEYS = {
    name_key(clean_text(name, 500))
    for name in ("İTÜ ARI Teknokent", "Entertech İstanbul Teknokent")
}


def parse_csv(payload: bytes) -> list[dict]:
    if len(payload) > MAX_BYTES:
        raise ValueError("CSV en fazla 5 MB olabilir.")

    csv.field_size_limit(4096)
    reader = csv.DictReader(
        io.StringIO(payload.decode("utf-8-sig"), newline=""),
        strict=True,
    )

    headers = reader.fieldnames or []
    if len(headers) != len(COLUMNS) or set(headers) != COLUMNS:
        raise ValueError("CSV sütunları beklenen yapıyla eşleşmiyor.")

    records = []
    for number, row in enumerate(reader, start=1):
        if number > MAX_ROWS:
            raise ValueError("CSV en fazla 10.000 kayıt içerebilir.")

        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Sütun sayısı hatalı.")

            name = clean_text(row["name"], 500)
            sector = clean_text(row["sector"], 300, required=False)
            teknopark = clean_text(row["teknopark"], 200)

            flag = row["hedef_sektor_mu"].strip()
            if flag not in {"True", "False", ""}:
                raise ValueError("Sektör işareti True, False veya boş olmalı.")

            key = name_key(name)
            records.append({
                "name": name,
                "name_key": key,
                "sector": sector or None,
                "teknopark": teknopark,
                "needs_review": key in REVIEW_KEYS,
                "source_target_sector": None if flag == "" else flag == "True",
            })
        except ValueError as error:
            raise ValueError(f"CSV kayıt #{number}: {error}") from None

    if not records:
        raise ValueError("CSV veri kaydı içermiyor.")

    return records


def main() -> int:
    try:
        with (PROJECT_ROOT / "data/raw/teknopark_firmalar.csv").open("rb") as file:
            records = parse_csv(file.read(MAX_BYTES + 1))
    except FileNotFoundError:
        print("CSV bulunamadı. Dosyayı data/raw klasörüne koy.")
        return 1
    except (OSError, UnicodeError, csv.Error):
        print("CSV okunamadı. UTF-8 kodlamasını ve CSV biçimini kontrol et.")
        return 1
    except ValueError as error:
        print(str(error))
        return 1

    new_companies = 0
    new_affiliations = 0

    try:
        with engine.begin() as connection:
            for row in records:
                company_id = connection.execute(
                    insert(Company).values(
                        id=uuid4(),
                        name=row["name"],
                        name_key=row["name_key"],
                        sector=row["sector"],
                        needs_review=row["needs_review"],
                    ).on_conflict_do_nothing(
                        index_elements=[Company.name_key]
                    ).returning(Company.id)
                ).scalar_one_or_none()

                if company_id is None:
                    company_id = connection.execute(
                        select(Company.id).where(
                            Company.name_key == row["name_key"]
                        )
                    ).scalar_one()
                else:
                    new_companies += 1

                affiliation = connection.execute(
                    insert(CompanyAffiliation).values(
                        company_id=company_id,
                        teknopark=row["teknopark"],
                        source_target_sector=row["source_target_sector"],
                    ).on_conflict_do_nothing(
                        index_elements=[
                            CompanyAffiliation.company_id,
                            CompanyAffiliation.teknopark,
                        ]
                    ).returning(CompanyAffiliation.company_id)
                ).scalar_one_or_none()

                if affiliation is not None:
                    new_affiliations += 1

            summary = {
                "csv_rows": len(records),
                "new_companies": new_companies,
                "new_affiliations": new_affiliations,
                "total_companies": connection.scalar(
                    select(func.count()).select_from(Company)
                ),
                "total_affiliations": connection.scalar(
                    select(func.count()).select_from(CompanyAffiliation)
                ),
                "needs_review": connection.scalar(
                    select(func.count()).select_from(Company).where(
                        Company.needs_review.is_(True)
                    )
                ),
            }
    except SQLAlchemyError:
        print("Veritabanı aktarımı başarısız. Bağlantıyı kontrol edip yeniden çalıştır.")
        return 1
    finally:
        engine.dispose()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
