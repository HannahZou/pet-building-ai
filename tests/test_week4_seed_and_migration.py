from sqlalchemy import func, select

from pawpal.database import session_scope
from pawpal.models import Booking, PlatformAdmin, Resident
from pawpal.seed_analytics import seed_analytics_history
from pawpal.seed_care import seed_care_data
from pawpal.seed_data import seed_database
from pawpal.seed_scheduling import seed_scheduling_data


def test_history_seed_is_synthetic_reproducible_and_idempotent(file_engine):
    seed_database(file_engine)
    seed_scheduling_data(file_engine)
    seed_care_data(file_engine)
    first = seed_analytics_history(file_engine, weeks=2)
    second = seed_analytics_history(file_engine, weeks=2)
    assert first["bookings"] > 0 and all(v == 0 for v in second.values())
    with session_scope(file_engine) as s:
        assert s.scalar(select(func.count()).select_from(Resident).where(Resident.is_synthetic.is_(False))) == 0
        assert s.scalar(select(func.count()).select_from(PlatformAdmin)) == 2
        assert s.scalar(select(func.count()).select_from(Booking)) > 60


def test_migration_flags_existing_demo_residents(tmp_path):
    from alembic import command

    from pawpal.database import init_db, make_engine
    from pawpal.migrate import alembic_config

    eng = make_engine(f"sqlite:///{tmp_path / 'w3.db'}")
    cfg = alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0003_week3")
        conn.exec_driver_sql("INSERT INTO residents (full_name, email, building_name, apartment_number) "
                             "VALUES ('Demo', 'demo@example.test', 'B', '1'), ('Real', 'real.resident@residents.invalid', 'B', '2')")
    init_db(eng)
    with eng.connect() as conn:
        flags = dict(conn.exec_driver_sql("SELECT email, is_synthetic FROM residents").all())
    assert flags == {"demo@example.test": 1, "real.resident@residents.invalid": 0}


def test_seeding_never_calls_openai(file_engine, monkeypatch):
    """Demo data must not use the API (cost, rate limits), even when a key is configured."""
    from pawpal import care_reports

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")

    def boom():
        raise AssertionError("seed called OpenAI")

    monkeypatch.setattr(care_reports, "_openai_client", boom)
    seed_database(file_engine)
    seed_scheduling_data(file_engine)
    seed_care_data(file_engine)
    assert seed_analytics_history(file_engine, weeks=1)["reports"] > 0
