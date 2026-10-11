"""`whatsapp-consent migrate` (run before the host's own migrations) and `whatsapp-consent maintenance` (daily)."""

import argparse
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from whatsapp_consent.config import ConsentSettings, DatabaseUrlSettings
from whatsapp_consent.db import SCHEMA
from whatsapp_consent.erasure import cleanup_unverified, run_purge_only
from whatsapp_consent.migrations import migrate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="whatsapp-consent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply the kit's database migrations (schema whatsapp_consent)")
    maintenance = commands.add_parser(
        "maintenance",
        help="purge expired anonymised records. A host with an erasure hook must call kit.run_maintenance(db) "
        "instead: this command cannot call the hook, so it does not erase stale sign-ups unless told to",
    )
    maintenance.add_argument(
        "--cleanup-unverified-without-host-hook",
        action="store_true",
        help="also erase stale unverified practice-link sign-ups, WITHOUT calling any host erasure hook",
    )
    args = parser.parse_args(argv)
    url = DatabaseUrlSettings().url()

    if args.command == "migrate":
        migrate(url)
        print(f"{SCHEMA}: migrations applied")
    elif args.command == "maintenance":
        settings = ConsentSettings()  # pyright: ignore[reportCallIssue]
        engine = create_engine(url)
        try:
            with Session(engine) as db:
                result: dict[str, int] = {}
                if args.cleanup_unverified_without_host_hook:
                    result["unverified_signups_removed"] = cleanup_unverified(db)
                result |= run_purge_only(db, retention_days=settings.retention_days)
                db.commit()
        finally:
            engine.dispose()
        print(json.dumps(result))


if __name__ == "__main__":
    main()
