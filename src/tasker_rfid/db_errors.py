"""Turning a Postgres error into something a person can act on.

Every service that talks to Postgres retries on failure and logs why. The
log line is the only thing standing between an operator and a stopped
warehouse, so it has to say two things: which database went wrong, and
what to do about it.

The commonest cause by far is a migration that has not been run — after a
`git pull` that brought a new one, the services find a table missing and
retry forever against a schema that will never appear on its own. That
case gets the command spelled out.
"""

import psycopg

MIGRATIONS_COMMAND = "uv run alembic upgrade head"

CLOUD_MIGRATIONS_COMMAND = 'DATABASE_URL="$CLOUD_DATABASE_URL" uv run alembic upgrade head'

# Postgres raises these when the schema is older than the code expects.
SCHEMA_IS_BEHIND = (
    psycopg.errors.UndefinedTable,
    psycopg.errors.UndefinedColumn,
    psycopg.errors.UndefinedFunction,
    psycopg.errors.UndefinedObject,
)


def explain(
    exc: Exception,
    *,
    database: str = "The database",
    command: str = MIGRATIONS_COMMAND,
) -> str:
    """One line saying what happened, plus the fix when we know it.

    `database` names which one, because a service that reads one database
    and writes another must never blame the wrong machine.
    """
    first_line = str(exc).strip().splitlines()[0]
    if isinstance(exc, SCHEMA_IS_BEHIND):
        return (
            f"{first_line}\n"
            f"    {database} is missing something this service needs, which "
            f"almost always means its migrations have not been run:\n"
            f"        {command}"
        )
    return first_line
