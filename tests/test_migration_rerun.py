"""A migration that stopped part-way runs again to its end.

MariaDB commits each table change at once, so a migration that fails late
leaves its earlier steps done while the database still counts as before it.
The first run of e2b7c4f9a1d3 on the live server stopped that way, at its last
step; the next update runs it again over what it had already done.
"""
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect, text

from conftest import app_module
from aeronautics_members.db_models import db


def test_the_stripe_books_migration_finishes_after_stopping_at_its_last_step(tmp_path):
    app = app_module.create_app(config_overrides={
        "TESTING": True, "SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/rerun.db"})
    with app.app_context():
        upgrade()
        # Everything but the last step done, the database still counted as before it.
        stamp(revision="d7a3e9c2f5b1")
        db.session.execute(text("INSERT INTO setting (\"key\", value) VALUES ('team_credit_sales_share_bps', '300')"))
        db.session.commit()

        upgrade()

        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "e2b7c4f9a1d3"
        assert db.session.execute(text("SELECT COUNT(*) FROM setting")).scalar() == 0
        inspector = inspect(db.engine)
        assert {"stripe_bookings", "stripe_books_state", "money_differences"} <= set(inspector.get_table_names())
        assert "team_bears_fee" not in {column["name"] for column in inspector.get_columns("payments")}
