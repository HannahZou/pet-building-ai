"""Week 2 migration safety: existing Week 1 data must survive the upgrade."""
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from pawpal.database import init_db, make_engine, session_scope
from pawpal.migrate import alembic_config, current_revision, head_revision
from pawpal.models import Base, Booking, BookingStatus

WEEK1_ROWS = """
INSERT INTO residents (id, full_name, email, building_name, apartment_number) VALUES (7, 'Ada', 'ada@x.test', 'The Meridian', '1A');
INSERT INTO pets (id, resident_id, name, species) VALUES (11, 7, 'Luna', 'cat');
INSERT INTO caregivers (id, full_name, email, active_status) VALUES (3, 'Cara', 'cara@x.test', 1);
INSERT INTO bookings (id, resident_id, pet_id, caregiver_id, start_date, end_date, visits_per_day, service_type, photo_updates_required, status, created_at)
VALUES (101, 7, 11, NULL, '2026-11-06', '2026-11-09', 2, 'feeding', 1, 'pending', '2026-10-12 10:00:00'),
       (102, 7, 11, 3,    '2025-05-01', '2025-05-03', 1, 'walking', 0, 'completed', '2025-04-20 10:00:00'),
       (103, 7, 11, NULL, '2025-06-01', '2025-06-02', 1, 'check_in', 0, 'cancelled', '2025-05-20 10:00:00');
"""


def _week1_database(tmp_path):
    """A database exactly as Week 1 left it: tables + data, no alembic_version."""
    eng = make_engine(f"sqlite:///{tmp_path / 'week1.db'}")
    cfg = alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001_week1")
        conn.exec_driver_sql("DROP TABLE alembic_version")
        for stmt in WEEK1_ROWS.strip().split(";"):
            if stmt.strip():
                conn.exec_driver_sql(stmt)
    return eng


def test_week1_database_upgrades_in_place(tmp_path):
    eng = _week1_database(tmp_path)
    init_db(eng)
    assert current_revision(eng) == head_revision()
    with session_scope(eng) as s:
        rows = {b.id: b for b in s.query(Booking).all()}
        assert set(rows) == {101, 102, 103}                      # ids preserved
        assert rows[101].status == BookingStatus.DRAFT          # 'pending' -> 'draft'
        assert rows[102].status == BookingStatus.COMPLETED and rows[102].caregiver_id == 3
        assert rows[103].status == BookingStatus.CANCELLED
        assert rows[101].visit_duration_minutes == 30 and rows[101].additional_services == []
        assert rows[101].pet.name == "Luna" and rows[101].resident.full_name == "Ada"
    with eng.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
    backups = list(tmp_path.glob("week1.db.bak-*"))
    assert len(backups) == 1  # automatic backup before migrating


def test_upgrade_is_idempotent(tmp_path):
    eng = _week1_database(tmp_path)
    init_db(eng)
    init_db(eng)
    assert len(list(tmp_path.glob("week1.db.bak-*"))) == 1


def test_migrated_schema_matches_models(file_engine):
    with file_engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        names = set(inspect(conn).get_table_names())
        triggers = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
    assert {"visits", "caregiver_availability", "caregiver_time_off", "booking_quotes",
            "booking_events", "property_managers", "booking_time_windows"} <= names
    assert {"trg_visits_no_overlap_insert", "trg_visits_no_overlap_update"} <= triggers


def test_downgrade_restores_week1_schema(tmp_path):
    eng = _week1_database(tmp_path)
    init_db(eng)
    cfg = alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001_week1")
    with eng.connect() as conn:
        assert "visits" not in inspect(conn).get_table_names()
        statuses = dict(conn.exec_driver_sql("SELECT id, status FROM bookings").all())
    assert statuses == {101: "pending", 102: "completed", 103: "cancelled"}


def test_week2_database_upgrades_to_week3_with_backfill(tmp_path):
    """Confirmed Week 2 visits get a 'scheduled' execution row; nothing else changes."""
    from pawpal.models import ExecutionStatus, Visit, VisitExecution, VisitStatus
    from tests.week3_world import confirmed_world

    eng = make_engine(f"sqlite:///{tmp_path / 'w2.db'}")
    init_db(eng)
    w = confirmed_world(eng)
    cfg = alembic_config()
    with eng.begin() as conn:  # back to Week 2 (drops Week 3 tables only)
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0002_week2")
    with eng.connect() as conn:
        assert "visit_executions" not in inspect(conn).get_table_names()
        before = conn.exec_driver_sql("SELECT id, status, version FROM bookings ORDER BY id").all()
        visits_before = conn.exec_driver_sql("SELECT id, caregiver_id, assignment_status FROM visits ORDER BY id").all()
    init_db(eng)  # upgrade again
    assert current_revision(eng) == head_revision()
    with eng.connect() as conn:
        assert conn.exec_driver_sql("SELECT id, status, version FROM bookings ORDER BY id").all() == before
        assert conn.exec_driver_sql("SELECT id, caregiver_id, assignment_status FROM visits ORDER BY id").all() == visits_before
        assert conn.exec_driver_sql("SELECT timezone FROM building_settings WHERE building_name='The Meridian'").scalar() == "America/New_York"
    with session_scope(eng) as s:
        execs = s.query(VisitExecution).all()
        confirmed = s.query(Visit).filter(Visit.assignment_status == VisitStatus.CONFIRMED).count()
        assert len(execs) == confirmed == 4
        assert {e.execution_status for e in execs} == {ExecutionStatus.SCHEDULED}
