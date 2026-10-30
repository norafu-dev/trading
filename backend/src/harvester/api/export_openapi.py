"""Write backend/openapi.json: python -m harvester.api.export_openapi

The web app generates its API types from this committed file (pnpm gen:api).
"""

import json
from pathlib import Path

from harvester.api.main import app

OUTPUT = Path(__file__).resolve().parents[3] / "openapi.json"


def main() -> None:
    OUTPUT.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
