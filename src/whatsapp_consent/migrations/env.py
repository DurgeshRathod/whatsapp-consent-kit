from alembic import context
from sqlalchemy import create_engine, text

import whatsapp_consent.models  # noqa: F401  (registers the tables)
from whatsapp_consent.db import SCHEMA, ConsentBase

config = context.config


def run_migrations() -> None:
    engine = create_engine(config.attributes["database_url"])
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=ConsentBase.metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
