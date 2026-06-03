"""ScoreUserRole 初期 seed — context/score.md 権限設計v5 準拠"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal, engine
from app.models import ScoreUserRole

SEED_DATA = [
    {"user_id": "tanaka@score.local", "project_id": "alpha", "role": "pm"},
    {"user_id": "yamada@score.local", "project_id": "alpha", "role": "director"},
    {"user_id": "kato@score.local",   "project_id": "alpha", "role": "lighting_lead"},
    {"user_id": "sato@score.local",   "project_id": "alpha", "role": "compositor"},
    {"user_id": "suzuki@score.local", "project_id": "alpha", "role": "compositor"},
]


def run_seed():
    db = SessionLocal()
    try:
        for entry in SEED_DATA:
            exists = (
                db.query(ScoreUserRole)
                .filter_by(user_id=entry["user_id"], project_id=entry["project_id"])
                .first()
            )
            if not exists:
                db.add(ScoreUserRole(**entry))
        db.commit()
        print(f"Seed complete: {len(SEED_DATA)} records processed.")
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
