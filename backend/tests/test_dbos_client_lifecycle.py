"""Gateway enqueue-only prep: DBOSClient lifecycle in dbos_orchestrator (dormant
until a later task constructs it on the gateway)."""
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
        def destroy(self): destroyed["n"] += 1
    monkeypatch.setattr(o, "_client", FakeClient())
    o.shutdown_dbos_client()
    assert destroyed["n"] == 1
    assert o.get_dbos_client() is None


def test_init_dbos_client_noop_without_db_url(monkeypatch):
    monkeypatch.setattr(o, "_client", None)
    monkeypatch.delenv("DBOS_DATABASE_URL", raising=False)
    o.init_dbos_client()  # must not raise, must stay None
    assert o.get_dbos_client() is None
