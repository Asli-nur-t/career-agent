"""Preview or explicitly apply a local CV-derived profile proposal."""

import argparse
import json
import os
from pathlib import Path

from app.configure_candidate_profile import load_profile
from app.cv_profile import (
    CVDocumentError,
    CVExtractionError,
    OllamaCVExtractor,
    apply_profile_proposal,
    extract_cv_document,
    propose_profile_update,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract an evidence-backed candidate profile from a CV."
    )
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--profile-file", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the reviewed proposal to the private profile file.",
    )
    args = parser.parse_args()

    try:
        current = load_profile(args.profile_file)
        document = extract_cv_document(args.file)
        with OllamaCVExtractor(
            model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            base_url=os.environ.get(
                "OLLAMA_BASE_URL",
                "http://127.0.0.1:11434",
            ),
        ) as extractor:
            extraction = extractor.extract(document.text)
        proposal = propose_profile_update(current, extraction)
        backup = (
            apply_profile_proposal(args.profile_file, proposal)
            if args.apply
            else None
        )
    except CVDocumentError as error:
        result = {"status": "error", "error_code": error.code}
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1) from None
    except CVExtractionError as error:
        result = {"status": "error", "error_code": error.code}
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1) from None
    except ValueError:
        result = {"status": "error", "error_code": "invalid_configuration"}
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1) from None

    result = {
        "status": "applied" if args.apply else "preview",
        "source": {
            "file_name": document.file_name,
            "file_type": document.file_type,
            "sha256": document.sha256,
            "text_length": len(document.text),
        },
        "extraction": extraction.model_dump(mode="json"),
        "proposed_profile": proposal.model_dump(mode="json"),
        "backup_file": str(backup) if backup else None,
        "database_updated": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
