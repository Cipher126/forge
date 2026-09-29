import json
from pathlib import Path

from forge.api.app import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_committed_openapi_document_matches_application() -> None:
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))

    assert committed == create_app().openapi()


def test_openapi_exposes_security_and_operational_contracts() -> None:
    document = create_app().openapi()

    bearer = document["components"]["securitySchemes"]["HTTPBearer"]
    assert bearer == {"type": "http", "scheme": "bearer"}
    assert "/v1/jobs" in document["paths"]
    assert "/v1/admin/operations/performance" in document["paths"]
    assert "/v1/admin/operations/metrics" in document["paths"]
