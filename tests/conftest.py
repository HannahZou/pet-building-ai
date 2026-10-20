from types import SimpleNamespace

import pytest

from pawpal.database import init_db, make_engine, session_scope
from pawpal.models import Pet, Resident


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch, tmp_path):
    """Never touch the real pawpal.db or the real OpenAI API from tests."""
    from pawpal.database import get_engine

    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'default-test.db'}")
    monkeypatch.delenv("PAWPAL_PRICE_BOOK", raising=False)
    get_engine.cache_clear()
    yield
    get_engine.cache_clear()


@pytest.fixture()
def file_engine(tmp_path):
    """A migrated SQLite *file* database (needed for locking/concurrency tests)."""
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def engine():
    eng = make_engine("sqlite://")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    with session_scope(engine) as s:
        yield s


@pytest.fixture()
def two_residents(session):
    a = Resident(full_name="Ada Test", email="ada@example.test", building_name="B1", apartment_number="1A")
    b = Resident(full_name="Ben Test", email="ben@example.test", building_name="B1", apartment_number="2B")
    session.add_all([a, b])
    session.flush()
    luna = Pet(resident_id=a.id, name="Luna", species="cat", medical_notes="SECRET-NOTE")
    rex = Pet(resident_id=b.id, name="Rex", species="dog")
    session.add_all([luna, rex])
    session.flush()
    return a, b, luna, rex


class FakeOpenAI:
    """Mimics client.beta.chat.completions.parse. No network access."""

    def __init__(self, parsed=None, refusal=None, raises=None):
        self.calls = []
        outer = self

        class _Completions:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                if raises:
                    raise raises
                msg = SimpleNamespace(parsed=parsed, refusal=refusal)
                return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
