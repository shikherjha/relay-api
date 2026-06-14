"""CLI seed entry: `python -m scripts.seed` (or `python scripts/seed.py`).

Mirrors POST /demo/reset; useful for local bootstrapping + integration tests.
"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.services.seed import seed_all


def main() -> None:
    db = SessionLocal()
    try:
        counts = seed_all(db)
        print("seeded:", counts)
    finally:
        db.close()


if __name__ == "__main__":
    main()
