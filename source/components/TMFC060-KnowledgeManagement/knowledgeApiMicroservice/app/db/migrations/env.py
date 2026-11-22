"""Alembic environment. Migrations run on a connection passed in by app.db.migrate (never from a URL here)."""
from alembic import context

from app.db.models import Base

target_metadata = Base.metadata
connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through app.db.migrate, which passes a connection")

context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
