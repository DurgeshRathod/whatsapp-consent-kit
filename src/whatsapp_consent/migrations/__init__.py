"""The kit's own Alembic history, with its version table in the kit's schema."""

from importlib.resources import files

from alembic import command
from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("whatsapp_consent") / "migrations"))
    config.attributes["database_url"] = database_url
    return config


def migrate(database_url: str, revision: str = "head") -> None:
    """Run before the host's own migrations: `whatsapp-consent migrate`."""
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)
