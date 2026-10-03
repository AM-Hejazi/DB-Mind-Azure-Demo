"""Explicit configuration/read-connection check; never seeds or calls an LLM."""
import argparse
import json
from .database import Database, DatabaseError
from .settings import load_settings, SettingsError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="Opt in to opening the configured database; Azure mode uses network/authentication")
    args = parser.parse_args()
    try:
        settings = load_settings()
        status = {"profile": settings.profile, "dialect": settings.dialect,
                  "auth": settings.auth, "scope": settings.schemas, "connected": False}
        if args.connect:
            rows, _ = Database(settings).query("SELECT COUNT(*) AS equipment_count FROM "
                                               + ("[demo].equipment" if settings.dialect == "sqlserver" else "equipment"))
            status.update(connected=True, equipment_count=rows[0][0])
        print(json.dumps(status, indent=2))
    except (SettingsError, DatabaseError) as error:
        parser.exit(1, f"Database configuration/check failed: {error}\n")


if __name__ == "__main__":
    main()
