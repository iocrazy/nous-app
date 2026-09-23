"""Gateway enqueue-only prep: DBOSClient lifecycle in dbos_orchestrator (dormant
until a later task constructs it on the gateway)."""

import pytest

import app.services.infra.dbos_orchestrator as o


def test_is_enabled_true_when_only_client_set(monkeypatch):
    monkeypatch.setattr(o, "_dbos", None)
    monkeypatch.setattr(o, "_client", object())
    assert o.is_enabled() is True


def test_is_enabled_false_when_neither_set(monkeypatch):
    monkeypatch.setattr(o, "_dbos", None)
    monkeypatch.setattr(o, "_client", None)
    assert o.is_enabled() is False


def test_get_dbos_client_returns_module_client(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(o, "_client", sentinel)
    assert o.get_dbos_client() is sentinel


def test_shutdown_dbos_client_calls_destroy_and_clears(monkeypatch):
    destroyed = {"n": 0}

    class FakeClient:
        def destroy(self):
            destroyed["n"] += 1

    monkeypatch.setattr(o, "_client", FakeClient())
    o.shutdown_dbos_client()
    assert destroyed["n"] == 1
    assert o.get_dbos_client() is None


def test_init_dbos_client_noop_without_db_url(monkeypatch):
    monkeypatch.setattr(o, "_client", None)
    monkeypatch.delenv("DBOS_DATABASE_URL", raising=False)
    o.init_dbos_client()  # must not raise, must stay None
    assert o.get_dbos_client() is None


# ── hotfix-2 (defect H): the client is real and built against init_dbos's
#    system database, not DBOSClient's derived default `<db>_dbos_sys`. ─────


class _CapturingClient:
    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        assert not args, "the deprecated positional database_url derives <db>_dbos_sys"
        _CapturingClient.last_kwargs = kwargs


def test_init_dbos_client_takes_the_system_url_from_build_dbos_config(monkeypatch):
    import dbos

    monkeypatch.setattr(o, "_client", None)
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://u:p@nous-db:55434/postgres")
    monkeypatch.setattr(dbos, "DBOSClient", _CapturingClient)
    real = o._build_dbos_config
    seen = []

    def _spy(db_url, executor_id, db_pool_size):
        cfg = real(db_url, executor_id, db_pool_size)
        seen.append(cfg["system_database_url"])
        return cfg

    monkeypatch.setattr(o, "_build_dbos_config", _spy)
    o.init_dbos_client()
    kw = _CapturingClient.last_kwargs
    assert seen == ["postgresql://u:p@nous-db:55434/postgres"]
    assert kw["system_database_url"] == seen[0]
    assert not kw["system_database_url"].endswith("_dbos_sys")
    assert kw["dbos_system_schema"] == "dbos"
    assert isinstance(o.get_dbos_client(), _CapturingClient)


def test_get_or_init_is_idempotent(monkeypatch):
    import dbos

    monkeypatch.setattr(o, "_client", None)
    monkeypatch.setattr(o, "_dbos", None)
    monkeypatch.setenv("NOUS_ROLE", "gateway")
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://u:p@h:5432/postgres")
    built = []

    class _Counting:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(dbos, "DBOSClient", _Counting)
    first = o.get_or_init_dbos_client()
    second = o.get_or_init_dbos_client()
    assert first is second is not None
    assert len(built) == 1


def test_get_or_init_refuses_in_a_singleton_role_process(monkeypatch):
    monkeypatch.setattr(o, "_client", None)
    monkeypatch.setattr(o, "_dbos", object())
    built = []
    monkeypatch.setattr(o, "init_dbos_client", lambda: built.append(1))
    assert o.get_or_init_dbos_client() is None
    assert built == []


@pytest.mark.parametrize("role", ["worker", "combined"])
def test_get_or_init_is_gateway_only_even_without_a_singleton(monkeypatch, role):
    """``_dbos is None`` is not enough: a worker whose ``init_dbos`` bailed
    (no DSN) must still never grow a client."""
    monkeypatch.setattr(o, "_client", None)
    monkeypatch.setattr(o, "_dbos", None)
    monkeypatch.setenv("NOUS_ROLE", role)
    built = []
    monkeypatch.setattr(o, "init_dbos_client", lambda: built.append(1))
    assert o.get_or_init_dbos_client() is None
    assert built == []
