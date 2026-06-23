"""Unit tests for the ETL orchestrator (``infra/etl/load_osm.py``).

These tests exercise the pure-Python helpers of the orchestrator and verify
the high-level pipeline ordering via mocked subprocess calls. They DO NOT
boot Postgres, Redis, or any container — those scenarios live under
``tests/integration/`` and are gated by ``@pytest.mark.e2e``.

Implements Phase 1 task 1.9: "Unit tests under
``open-routing-service/tests/unit/`` for: the ETL Python wrapper (sha256
verification, idempotency check, log-line format), the ``swap_schema.sql``
SQL parser/dispatcher, the Alembic migration helpers (offline-mode
generation)."

The Python wrapper coverage is here; the Alembic migration round-trip lives
in ``test_baseline_migration.py``; the SQL "dispatcher" coverage is provided
by the ``main`` orchestration test which asserts the correct sequence of
``psql -f`` invocations.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import load_osm  # noqa: E402 — path injection done in tests/conftest.py


# --------------------------------------------------------------------------- #
# _sha256_file
# --------------------------------------------------------------------------- #


class TestSha256File:
    def test_matches_hashlib_for_small_file(self, tmp_path: Path) -> None:
        payload = b"hello pgrouting"
        f = tmp_path / "tiny.bin"
        f.write_bytes(payload)

        expected = hashlib.sha256(payload).hexdigest()
        assert load_osm._sha256_file(f) == expected

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert load_osm._sha256_file(f) == hashlib.sha256(b"").hexdigest()

    def test_streams_in_chunks_for_multi_meg_file(self, tmp_path: Path) -> None:
        # >1 MiB so we exercise the chunked-read loop in _sha256_file
        f = tmp_path / "biggish.bin"
        payload = b"x" * (3 * 1024 * 1024 + 17)
        f.write_bytes(payload)

        assert load_osm._sha256_file(f) == hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# _write_pgpass
# --------------------------------------------------------------------------- #


class TestWritePgpass:
    def test_writes_correct_content(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {
            "PGHOST": "pg-host",
            "PGPORT": "5432",
            "PGDATABASE": "routing",
            "PGUSER": "routing",
            "PGPASSWORD": "s3cr3t",
        }
        monkeypatch.setattr(load_osm.Path, "home", lambda: tmp_path)
        load_osm._write_pgpass(env)
        pgpass = tmp_path / ".pgpass"
        assert pgpass.exists()
        content = pgpass.read_text()
        assert content == "pg-host:5432:routing:routing:s3cr3t\n"

    def test_does_not_log_password(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        env = {
            "PGHOST": "h", "PGPORT": "5432", "PGDATABASE": "d",
            "PGUSER": "u", "PGPASSWORD": "very-secret",
        }
        monkeypatch.setattr(load_osm.Path, "home", lambda: tmp_path)
        with caplog.at_level(logging.DEBUG, logger="etl"):
            load_osm._write_pgpass(env)
        assert "very-secret" not in caplog.text


# --------------------------------------------------------------------------- #
# _redact_url
# --------------------------------------------------------------------------- #


class TestRedactUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("redis://localhost:6379/0", "redis://localhost:6379/0"),
            ("redis://:secret@redis:6379/0", "redis://***@redis:6379/0"),
            ("redis://user:secret@redis-host:6379/0", "redis://***@redis-host:6379/0"),
            ("rediss://user:secret@host/2", "rediss://***@host/2"),
        ],
    )
    def test_redaction(self, url: str, expected: str) -> None:
        assert load_osm._redact_url(url) == expected

    def test_returns_empty_for_empty_input(self) -> None:
        assert load_osm._redact_url("") == ""


# --------------------------------------------------------------------------- #
# _parse_scan (RESP cursor + key list parser)
# --------------------------------------------------------------------------- #


class TestParseScan:
    """Minimal coverage of the inline RESP parser used by the cf:* flusher.

    The parser is intentionally tiny; these tests pin the cases that the
    orchestrator actually encounters.
    """

    def test_zero_cursor_no_keys(self) -> None:
        # *2\r\n$1\r\n0\r\n*0\r\n
        resp = "*2\r\n$1\r\n0\r\n*0\r\n"
        cursor, keys = load_osm._parse_scan(resp)
        assert cursor == "0"
        assert keys == []

    def test_zero_cursor_with_keys(self) -> None:
        # cursor=0 + 2 keys ['cf:a', 'cf:b']
        resp = "*2\r\n$1\r\n0\r\n*2\r\n$4\r\ncf:a\r\n$4\r\ncf:b\r\n"
        cursor, keys = load_osm._parse_scan(resp)
        assert cursor == "0"
        assert keys == ["cf:a", "cf:b"]

    def test_non_zero_cursor_means_more_iterations(self) -> None:
        resp = "*2\r\n$3\r\n128\r\n*1\r\n$8\r\ncf:lat:1\r\n"
        cursor, keys = load_osm._parse_scan(resp)
        assert cursor == "128"
        assert keys == ["cf:lat:1"]


# --------------------------------------------------------------------------- #
# _db_env (env-var validation)
# --------------------------------------------------------------------------- #


class TestDbEnv:
    _REQUIRED = [
        ("ROUTING_DB_HOST", "pg-host"),
        ("ROUTING_DB_PORT", "5432"),
        ("ROUTING_DB_USER", "routing"),
        ("ROUTING_DB_PASSWORD", "supersecret"),
        ("ROUTING_DB_NAME", "routing"),
    ]

    def _set_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name, value in self._REQUIRED:
            monkeypatch.setenv(name, value)

    def test_maps_env_vars_to_pg_prefixed_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_all(monkeypatch)
        env = load_osm._db_env()
        assert env["PGHOST"] == "pg-host"
        assert env["PGPORT"] == "5432"
        assert env["PGUSER"] == "routing"
        assert env["PGPASSWORD"] == "supersecret"
        assert env["PGDATABASE"] == "routing"

    @pytest.mark.parametrize("missing_var", [name for name, _ in _REQUIRED])
    def test_exits_2_when_required_var_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        missing_var: str,
    ) -> None:
        self._set_all(monkeypatch)
        monkeypatch.delenv(missing_var, raising=False)
        with pytest.raises(SystemExit) as exc_info:
            load_osm._db_env()
        assert exc_info.value.code == 2

    def test_does_not_log_password(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        self._set_all(monkeypatch)
        monkeypatch.setenv("ROUTING_DB_PASSWORD", "T0p$ecret!")
        with caplog.at_level(logging.DEBUG, logger="etl"):
            load_osm._db_env()
        assert "T0p$ecret!" not in caplog.text


# --------------------------------------------------------------------------- #
# _parse_args
# --------------------------------------------------------------------------- #


class TestParseArgs:
    def test_requires_pbf(self) -> None:
        with pytest.raises(SystemExit):
            load_osm._parse_args([])

    def test_defaults(self) -> None:
        args = load_osm._parse_args(["--pbf", "/data/nevada.osm.pbf"])
        assert args.pbf == Path("/data/nevada.osm.pbf")
        assert args.force is False

    def test_force_flag(self) -> None:
        args = load_osm._parse_args(["--pbf", "/data/x.osm.pbf", "--force"])
        assert args.force is True


# --------------------------------------------------------------------------- #
# _assert_pgr_version (version guard)
# --------------------------------------------------------------------------- #


class TestAssertPgrVersion:
    def test_passes_for_recent_version(self) -> None:
        with patch.object(load_osm, "_psql", return_value="3.7.3"):
            load_osm._assert_pgr_version({"PGHOST": "h"})  # no raise

    def test_passes_at_exact_minimum(self) -> None:
        with patch.object(load_osm, "_psql", return_value="3.3.0"):
            load_osm._assert_pgr_version({"PGHOST": "h"})

    def test_exits_2_when_too_old(self) -> None:
        with patch.object(load_osm, "_psql", return_value="3.2.1"):
            with pytest.raises(SystemExit) as exc:
                load_osm._assert_pgr_version({"PGHOST": "h"})
        assert exc.value.code == 2

    def test_exits_2_when_unparseable(self) -> None:
        with patch.object(load_osm, "_psql", return_value=""):
            with pytest.raises(SystemExit) as exc:
                load_osm._assert_pgr_version({"PGHOST": "h"})
        assert exc.value.code == 2


# --------------------------------------------------------------------------- #
# _osm2pgsql (subprocess wrapper)
# --------------------------------------------------------------------------- #


class TestOsm2pgsql:
    def test_invokes_correct_argv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pbf = tmp_path / "in.osm.pbf"
        pbf.write_bytes(b"x" * 64)
        env = {"PGHOST": "h", "PGPORT": "5432", "PGUSER": "u", "PGDATABASE": "d"}
        monkeypatch.setenv("ROUTING_FLAT_NODES", "/data/osm/fn.bin")
        monkeypatch.setenv("OSM2PGSQL_CACHE_MB", "1234")

        with patch.object(load_osm.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], returncode=0)
            load_osm._osm2pgsql(env, pbf)

        cmd = run.call_args.args[0]
        assert cmd[0] == "osm2pgsql"
        assert "--slim" in cmd
        assert "--drop" in cmd
        assert "--output" in cmd and "flex" in cmd
        assert load_osm.OSM2PGSQL_STYLE in cmd
        assert "--flat-nodes" in cmd and "/data/osm/fn.bin" in cmd
        assert "--cache" in cmd and "1234" in cmd
        assert str(pbf) in cmd

    @pytest.mark.parametrize("disabled_value", ["", "none", "off", "0"])
    def test_flat_nodes_omitted_when_disabled(
        self,
        disabled_value: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pbf = tmp_path / "in.osm.pbf"
        pbf.write_bytes(b"x" * 64)
        env = {"PGHOST": "h", "PGPORT": "5432", "PGUSER": "u", "PGDATABASE": "d"}
        monkeypatch.setenv("ROUTING_FLAT_NODES", disabled_value)

        with patch.object(load_osm.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], returncode=0)
            load_osm._osm2pgsql(env, pbf)

        cmd = run.call_args.args[0]
        # The flag (and its value) must be absent so osm2pgsql uses the in-RAM cache.
        assert "--flat-nodes" not in cmd
        assert disabled_value not in cmd
        # The rest of the invocation is unchanged.
        assert cmd[0] == "osm2pgsql"
        assert "--output" in cmd and "flex" in cmd
        assert str(pbf) in cmd

    def test_exits_4_on_failure(self, tmp_path: Path) -> None:
        pbf = tmp_path / "in.osm.pbf"
        pbf.write_bytes(b"x")
        env = {"PGHOST": "h", "PGPORT": "5432", "PGUSER": "u", "PGDATABASE": "d"}
        with patch.object(
            load_osm.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd=["osm2pgsql"]),
        ):
            with pytest.raises(SystemExit) as exc:
                load_osm._osm2pgsql(env, pbf)
        assert exc.value.code == 4


# --------------------------------------------------------------------------- #
# _node_network (runs 01_node_network.sql)
# --------------------------------------------------------------------------- #


class TestNodeNetwork:
    def test_runs_sql_with_staging_schema(self) -> None:
        env = {"PGHOST": "h"}
        with patch.object(load_osm, "_psql_file") as psql_file:
            load_osm._node_network(env)
        assert psql_file.call_args.args[1] == load_osm.NODE_NETWORK_SQL
        assert psql_file.call_args.kwargs["variables"]["staging_schema"] == "routing_next"

    def test_exits_4_on_failure(self) -> None:
        env = {"PGHOST": "h"}
        with patch.object(
            load_osm,
            "_psql_file",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd=["psql"]),
        ):
            with pytest.raises(SystemExit) as exc:
                load_osm._node_network(env)
        assert exc.value.code == 4


# --------------------------------------------------------------------------- #
# _is_already_loaded (psql wrapper)
# --------------------------------------------------------------------------- #


class TestIsAlreadyLoaded:
    def test_true_when_psql_returns_1(self) -> None:
        env = {"PGHOST": "h"}
        with patch.object(load_osm, "_psql", return_value="1") as psql:
            assert load_osm._is_already_loaded(env, "abc123") is True
        assert "abc123" in psql.call_args.args[1]

    def test_false_when_psql_returns_empty(self) -> None:
        env = {"PGHOST": "h"}
        with patch.object(load_osm, "_psql", return_value=""):
            assert load_osm._is_already_loaded(env, "abc123") is False


# --------------------------------------------------------------------------- #
# main: idempotency short-circuits before any heavy work
# --------------------------------------------------------------------------- #


class TestMainIdempotency:
    def test_already_loaded_exits_zero_without_calling_osmconvert(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Set required env vars so _db_env succeeds.
        for name, value in TestDbEnv._REQUIRED:
            monkeypatch.setenv(name, value)

        pbf = tmp_path / "small.osm.pbf"
        pbf.write_bytes(b"fake-pbf-bytes")

        with (
            patch.object(load_osm, "_is_already_loaded", return_value=True) as already,
            patch.object(load_osm, "_assert_pgr_version") as pgrver,
            patch.object(load_osm, "_osm2pgsql") as osm2pgsql,
            patch.object(load_osm, "_drop_and_recreate_staging") as drop_recreate,
            patch.object(load_osm, "_node_network") as node_network,
            patch.object(load_osm, "_swap") as swap,
            patch.object(load_osm, "_flush_redis_cache") as flush,
        ):
            rc = load_osm.main(["--pbf", str(pbf)])

        assert rc == 0
        already.assert_called_once()
        # None of the heavy stages should have run.
        for stage in (pgrver, osm2pgsql, drop_recreate, node_network, swap, flush):
            stage.assert_not_called()

    def test_force_bypasses_idempotency_check(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for name, value in TestDbEnv._REQUIRED:
            monkeypatch.setenv(name, value)

        pbf = tmp_path / "small.osm.pbf"
        pbf.write_bytes(b"fake")

        with (
            patch.object(load_osm, "_is_already_loaded", return_value=True) as already,
            patch.object(load_osm, "_assert_pgr_version") as pgrver,
            patch.object(load_osm, "_drop_and_recreate_staging") as drop_recreate,
            patch.object(load_osm, "_osm2pgsql") as osm2pgsql,
            patch.object(load_osm, "_node_network") as node_network,
            patch.object(load_osm, "_apply_phase2_hooks") as phase2,
            patch.object(load_osm, "_vacuum_analyze_staging") as vacuum,
            patch.object(load_osm, "_swap") as swap,
            patch.object(load_osm, "_flush_redis_cache") as flush,
        ):
            rc = load_osm.main(["--pbf", str(pbf), "--force"])

        assert rc == 0
        # idempotency check is short-circuited by --force, so it should NEVER be called
        already.assert_not_called()
        # Every downstream stage should have been invoked exactly once, in order.
        for stage in (
            pgrver, drop_recreate, osm2pgsql, node_network, phase2, vacuum, swap, flush,
        ):
            stage.assert_called_once()
        # osm2pgsql receives the PBF path as its second positional arg.
        assert osm2pgsql.call_args.args[1] == pbf

    def test_missing_pbf_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for name, value in TestDbEnv._REQUIRED:
            monkeypatch.setenv(name, value)
        rc = load_osm.main(["--pbf", "/nonexistent/path.osm.pbf"])
        assert rc == 2


# --------------------------------------------------------------------------- #
# _flush_via_socket — verify it speaks RESP correctly using a fake socket
# --------------------------------------------------------------------------- #


class _ScriptedSocket:
    """Tiny RESP-replaying fake socket.

    Captures every payload sent and replies with pre-canned responses, so the
    test can verify both the request bytes and the response handling.
    """

    def __init__(self, replies: list[bytes]) -> None:
        self._replies = list(replies)
        self.sent: list[bytes] = []

    # Context-manager API used by ``with socket.create_connection(...)``
    def __enter__(self) -> "_ScriptedSocket":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, _bufsize: int) -> bytes:
        if not self._replies:
            return b""
        return self._replies.pop(0)


class TestFlushViaSocket:
    def test_two_scan_iterations_then_done(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # First SELECT returns +OK
        # First SCAN returns cursor=128 + 2 keys; the DEL replies +2
        # Second SCAN returns cursor=0 + 1 key; the DEL replies +1
        scripted = _ScriptedSocket(
            replies=[
                b"+OK\r\n",                                               # SELECT 0
                b"*2\r\n$3\r\n128\r\n*2\r\n$4\r\ncf:a\r\n$4\r\ncf:b\r\n",  # SCAN 0
                b":2\r\n",                                                # DEL
                b"*2\r\n$1\r\n0\r\n*1\r\n$5\r\ncf:cc\r\n",                # SCAN 128
                b":1\r\n",                                                # DEL
            ]
        )

        def fake_create_connection(_addr: tuple[str, int], timeout: float) -> _ScriptedSocket:  # noqa: ARG001
            return scripted

        monkeypatch.setattr(socket, "create_connection", fake_create_connection)

        # Should complete cleanly (no exception, no SystemExit).
        load_osm._flush_via_socket("redis://localhost:6379/0")

        # Verify the orchestrator sent at least one SCAN and one DEL.
        joined = b"".join(scripted.sent).decode()
        assert "SCAN" in joined
        assert "DEL" in joined
        assert "cf:*" in joined

    def test_auth_is_sent_when_password_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scripted = _ScriptedSocket(
            replies=[
                b"+OK\r\n",                       # AUTH
                b"+OK\r\n",                       # SELECT 0
                b"*2\r\n$1\r\n0\r\n*0\r\n",       # SCAN 0 → empty + cursor=0
            ]
        )

        def fake_create_connection(_addr: tuple[str, int], timeout: float) -> _ScriptedSocket:  # noqa: ARG001
            return scripted

        monkeypatch.setattr(socket, "create_connection", fake_create_connection)

        load_osm._flush_via_socket("redis://:hunter2@localhost:6379/0")

        first_payload = scripted.sent[0].decode()
        assert "AUTH" in first_payload
        assert "hunter2" in first_payload  # password DOES travel over the wire (expected)


# --------------------------------------------------------------------------- #
# _flush_redis_cache — best-effort: never raises, never exits
# --------------------------------------------------------------------------- #


class TestFlushRedisCacheIsBestEffort:
    def test_returns_when_url_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("REDIS_URL", raising=False)
        with caplog.at_level(logging.WARNING, logger="etl"):
            load_osm._flush_redis_cache()  # MUST NOT raise
        assert any("REDIS_URL unset" in r.getMessage() for r in caplog.records)

    def test_swallows_socket_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        with patch.object(load_osm, "_flush_via_socket", side_effect=OSError("conn refused")):
            with caplog.at_level(logging.WARNING, logger="etl"):
                load_osm._flush_redis_cache()  # MUST NOT raise
        assert any("cache flush failed" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# JSON log format sanity (per scalability-observability skill)
# --------------------------------------------------------------------------- #


class TestLogConfiguration:
    def test_json_formatter_emits_parseable_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LOG_FORMAT", "json")
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        # Reconfigure with a capturing handler injected after _configure_logging.
        load_osm._configure_logging()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        from pythonjsonlogger.json import JsonFormatter
        handler.setFormatter(
            JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
        logger = logging.getLogger("etl")
        logger.addHandler(handler)
        try:
            logger.info("stage start", extra={"stage": "test"})
            line = stream.getvalue().strip().splitlines()[-1]
            import json
            parsed = json.loads(line)
            assert parsed["message"] == "stage start"
            assert parsed["level"] == "INFO"
            assert parsed["stage"] == "test"
        finally:
            logger.removeHandler(handler)
