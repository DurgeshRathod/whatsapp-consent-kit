"""`whatsapp-consent migrate` (run before the host's own migrations)."""

import argparse

from whatsapp_consent.config import DatabaseUrlSettings
from whatsapp_consent.db import SCHEMA
from whatsapp_consent.migrations import migrate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="whatsapp-consent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply the kit's database migrations (schema whatsapp_consent)")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        migrate(DatabaseUrlSettings().url())
        print(f"{SCHEMA}: migrations applied")


if __name__ == "__main__":
    main()
