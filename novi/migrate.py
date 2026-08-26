"""novi migrate v1-to-v2 — strips mode from persisted conversations."""
import json
import shutil
from pathlib import Path

from .paths import home as app_home

CONVERSATIONS_DIR = app_home() / "conversations"
BACKUP_DIR = app_home() / "conversations.v1-backup"

def migrate():
    if not CONVERSATIONS_DIR.exists():
        print("No conversations directory found. Nothing to migrate.")
        return

    if BACKUP_DIR.exists():
        print(f"Backup already exists at {BACKUP_DIR}. Skipping backup.")
    else:
        shutil.copytree(CONVERSATIONS_DIR, BACKUP_DIR)
        print(f"Backed up to {BACKUP_DIR}")

    count = 0
    for path in CONVERSATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
            if "mode" in data:
                del data["mode"]
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
                count += 1
        except (json.JSONDecodeError, OSError) as e:
            print(f"  Skipping {path.name}: {e}")

    print(f"Migrated {count} conversations (mode field removed).")

if __name__ == "__main__":
    migrate()
