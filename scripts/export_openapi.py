import json
from pathlib import Path

from forge.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "docs" / "openapi.json"


def main() -> None:
    document = create_app().openapi()
    DESTINATION.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {DESTINATION}")


if __name__ == "__main__":
    main()
