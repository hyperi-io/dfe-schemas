#  Project:      dfe-schemas
#  File:         tests/clickhouse_server.py
#  Purpose:      A real ClickHouse in docker, Keeper embedded, for the tests that need one
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""A real ClickHouse for the tests that apply rendered DDL, run in docker.

Every DFE table is an argumentless Replicated engine inside a Replicated
database, so the server runs Keeper embedded with the ``{cluster}``, ``{shard}``
and ``{replica}`` macros and a one-node ``remote_servers`` cluster
(docs/clickhouse-platform.md). The default user manages access, so the role
objects apply too.

The container is ``dfe-schemas-test-clickhouse-<pid>``, labelled
``io.hyperi.test.suite=dfe-schemas``, and is removed with its volume when the
session ends. To sweep one a killed run left behind::

    docker rm -f -v $(docker ps -aq --filter label=io.hyperi.test.suite=dfe-schemas)

A run that cannot start one skips, except in CI, where it fails: a skipped
integration suite reads as green and tested nothing. ``DFE_TEST_CH_IMAGE``
overrides the image.
"""

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, NoReturn

import pytest

# The ClickHouse dfe-infra deploys (versions.yaml, current stack), by digest.
# renovate: datasource=docker depName=clickhouse/clickhouse-server
_CH_TAG = "26.3.42.3"
_CH_DIGEST = "sha256:21d572843e59539c7d100286b6f5a6053c341fe4b33c2d7b74b1ff7cb24c5399"
_IMAGE = f"clickhouse/clickhouse-server:{_CH_TAG}@{_CH_DIGEST}"

_SUITE_LABEL = "io.hyperi.test.suite=dfe-schemas"
_READY_SECONDS = 120
_HTTP_TIMEOUT = 120

_SERVER_CONFIG = """<clickhouse>
    <keeper_server>
        <tcp_port>9181</tcp_port>
        <server_id>1</server_id>
        <log_storage_path>/var/lib/clickhouse/coordination/log</log_storage_path>
        <snapshot_storage_path>/var/lib/clickhouse/coordination/snapshots</snapshot_storage_path>
        <raft_configuration>
            <server><id>1</id><hostname>localhost</hostname><port>9234</port></server>
        </raft_configuration>
    </keeper_server>
    <zookeeper><node><host>localhost</host><port>9181</port></node></zookeeper>
    <macros><cluster>dfe</cluster><shard>01</shard><replica>r1</replica></macros>
    <remote_servers>
        <dfe><shard><replica><host>localhost</host><port>9000</port></replica></shard></dfe>
    </remote_servers>
</clickhouse>
"""

_USERS_CONFIG = """<clickhouse>
    <users><default><access_management>1</access_management></default></users>
</clickhouse>
"""

# Both files are written inside the container, so a remote docker host needs none of its own.
_ENTRYPOINT = (
    'printf %s "$DFE_TEST_CH_CONFIG" | base64 -d > /etc/clickhouse-server/config.d/dfe-keeper.xml'
    ' && printf %s "$DFE_TEST_CH_USERS" | base64 -d > /etc/clickhouse-server/users.d/dfe-access.xml'
    " && exec /entrypoint.sh"
)


class ClickHouseError(Exception):
    """ClickHouse refused a statement, and the message is the server's."""


def in_ci() -> bool:
    """Whether this is a CI run, where a missing ClickHouse fails rather than skips."""
    ci = os.environ.get("CI", "").strip().lower()
    return ci in {"1", "true", "yes"} or os.environ.get("GITHUB_ACTIONS") == "true"


def unavailable(reason: str) -> NoReturn:
    """Skip a test whose ClickHouse could not be had, or fail it in CI."""
    if in_ci():
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


class ClickHouse:
    """One ClickHouse reached over HTTP, with the stdlib.

    Attributes:
        url: The server's HTTP endpoint.
    """

    def __init__(self, url: str) -> None:
        """Point at *url*, the server's HTTP endpoint."""
        self.url = url

    def run(self, sql: str, *, user: str | None = None) -> str:
        """Run one statement and return the body the server answers with.

        Raises:
            ClickHouseError: The server refused it.
        """
        headers = {"X-ClickHouse-User": user} if user else {}
        # The URL is the http:// one start_clickhouse() builds, never caller input.
        request = urllib.request.Request(  # noqa: S310
            self.url, data=sql.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:  # noqa: S310
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise ClickHouseError(f"{detail}\n-- statement:\n{sql}") from exc

    def rows(self, sql: str, *, user: str | None = None) -> list[dict[str, Any]]:
        """Run one SELECT and return its rows, one dict per row."""
        body = self.run(
            f"{sql}\nFORMAT JSONEachRow SETTINGS output_format_json_quote_64bit_integers = 0",
            user=user,
        )
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def scalar(self, sql: str) -> Any:
        """The single value a one-row, one-column SELECT returns."""
        (row,) = self.rows(sql)
        (value,) = row.values()
        return value


def _docker(*args: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        raise FileNotFoundError("docker is not on PATH")
    return subprocess.run(  # noqa: S603
        [docker, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
    )


def _encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _published_port(name: str) -> int:
    # docker prints one "127.0.0.1:49153" line per address family.
    out = _docker("port", name, "8123").stdout.strip().splitlines()[-1]
    return int(out.rsplit(":", 1)[1])


def _wait_ready(server: ClickHouse) -> None:
    """Return once the server answers and Keeper is connected, or raise after a bound."""
    deadline = time.monotonic() + _READY_SECONDS
    last = "no answer yet"
    while time.monotonic() < deadline:
        try:
            if int(server.scalar("SELECT count() FROM system.zookeeper_connection")) > 0:
                return
            last = "Keeper not connected"
        except (OSError, ClickHouseError, ValueError) as exc:
            last = str(exc)
        time.sleep(0.5)
    raise TimeoutError(f"ClickHouse not ready after {_READY_SECONDS}s: {last}")


def start_clickhouse() -> tuple[ClickHouse, str]:
    """Start a ClickHouse container and return a client for it and its name.

    Skips (fails in CI) when docker cannot run it.
    """
    name = f"dfe-schemas-test-clickhouse-{os.getpid()}"
    image = os.environ.get("DFE_TEST_CH_IMAGE", _IMAGE)
    try:
        _docker("rm", "-f", "-v", name, check=False)
        _docker(
            "run",
            "-d",
            "--name",
            name,
            "--label",
            _SUITE_LABEL,
            "--label",
            f"io.hyperi.test.owner-pid={os.getpid()}",
            "-p",
            "127.0.0.1::8123",
            "-e",
            "CLICKHOUSE_SKIP_USER_SETUP=1",
            "-e",
            f"DFE_TEST_CH_CONFIG={_encoded(_SERVER_CONFIG)}",
            "-e",
            f"DFE_TEST_CH_USERS={_encoded(_USERS_CONFIG)}",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            _ENTRYPOINT,
            timeout=600,
        )
        server = ClickHouse(f"http://127.0.0.1:{_published_port(name)}/")
        _wait_ready(server)
    except (FileNotFoundError, subprocess.SubprocessError, TimeoutError, OSError) as exc:
        stop_clickhouse(name)
        unavailable(f"docker cannot start ClickHouse {image}: {exc}")
    return server, name


def stop_clickhouse(name: str) -> None:
    """Remove the container and its volume, whether or not it is still there."""
    if shutil.which("docker") is not None:
        _docker("rm", "-f", "-v", name, check=False)
