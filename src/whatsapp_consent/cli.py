"""`whatsapp-consent migrate` (run before the host's own migrations) and `whatsapp-consent maintenance` (daily)."""

import argparse
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from whatsapp_consent.config import ConsentSettings, DatabaseUrlSettings
from whatsapp_consent.db import SCHEMA
from whatsapp_consent.erasure import run_maintenance
from whatsapp_consent.migrations import migrate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="whatsapp-consent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply the kit's database migrations (schema whatsapp_consent)")
    commands.add_parser(
        "maintenance",
        help="erase stale sign-ups and purge expired anonymised records (inside a host, prefer kit.run_maintenance, "
        "which also calls the host's erasure hook)",
    )
    args = parser.parse_args(argv)
    url = DatabaseUrlSettings().url()

    if args.command == "migrate":
        migrate(url)
        print(f"{SCHEMA}: migrations applied")
    elif args.command == "maintenance":
        settings = ConsentSettings()  # pyright: ignore[reportCallIssue]
        engine = create_engine(url)
        with Session(engine) as db:
            result = run_maintenance(db, retention_days=settings.retention_days)
            db.commit()
        engine.dispose()
        print(json.dumps(result))


if __name__ == "__main__":
    main()
