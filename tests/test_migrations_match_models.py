"""The migrations and the models must build the same schema.

Every other test in this suite gets its schema from ``db.create_all()``, which
reads the models. A real installation gets it by running the migration chain.
Nothing compared the two, so a model change shipped without its migration would
leave the whole suite green and the next fresh install missing a column.

That matters more than usual right now: the production launch is a *fresh*
install, running every migration from the baseline on an empty database — a path
this project has otherwise never exercised outside somebody doing it by hand.

Comparing names rather than types on purpose. SQLite reports types loosely and
production is MariaDB, so a type comparison here would fail on dialect
differences while catching nothing real. A missing table or column is the
failure that actually happens when a migration is forgotten.
"""
import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect

from conftest import app_module
from aeronautics_members.db_models import db


def _schema(url, build):
    app = app_module.create_app(
        config_overrides={"TESTING": True, "SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": url}
    )
    with app.app_context():
        build()
        inspector = inspect(db.engine)
        return {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in inspector.get_table_names()
            if table != "alembic_version"
        }


@pytest.fixture(scope="module")
def schemas(tmp_path_factory):
    directory = tmp_path_factory.mktemp("schema-comparison")
    return (
        _schema(f"sqlite:///{directory}/from_migrations.db", upgrade),
        _schema(f"sqlite:///{directory}/from_models.db", db.create_all),
    )


def test_the_same_tables_exist(schemas):
    from_migrations, from_models = schemas

    missing_from_migrations = sorted(set(from_models) - set(from_migrations))
    missing_from_models = sorted(set(from_migrations) - set(from_models))

    assert not missing_from_migrations, (
        "these tables exist in the models but no migration creates them, so a "
        f"fresh install would not have them: {missing_from_migrations}"
    )
    assert not missing_from_models, (
        "these tables are created by a migration but are not in the models: "
        f"{missing_from_models}"
    )


def test_the_same_columns_exist(schemas):
    from_migrations, from_models = schemas

    differences = {}
    for table in sorted(set(from_migrations) & set(from_models)):
        only_migration = sorted(from_migrations[table] - from_models[table])
        only_model = sorted(from_models[table] - from_migrations[table])
        if only_migration or only_model:
            differences[table] = {
                "missing from a fresh install": only_model,
                "left over in migrations": only_migration,
            }

    assert not differences, f"migrations and models disagree: {differences}"


def test_the_comparison_is_not_vacuous(schemas):
    """A comparison of two empty schemas would pass and prove nothing."""
    from_migrations, _from_models = schemas

    assert len(from_migrations) > 10
    assert "users" in from_migrations
    assert "membership_periods" in from_migrations
