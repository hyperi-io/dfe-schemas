#  Project:      dfe-schemas
#  File:         dfe_schemas/deploy_defaults.py
#  Purpose:      The declared ClickHouse deploy defaults every DFE vehicle applies
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""ClickHouse deploy defaults, declared once.

These are the SETTINGS-layer facts a DFE deployment applies regardless of
vehicle. The k8s clickhouse-cluster chart (cluster CR ``extraUsersConfig``
and the single-node ``users.d`` overlay) and dfe-docker's
``clickhouse/default-user.xml`` each carry a rendering of this dict; this is
the declared source those renderings mirror, and what a future config
renderer consumes directly.

``JSON_TABLE_SETTINGS`` is table-level instead: every renderer of a table with
a JSON column emits it on every platform, together with the platform-only
settings :mod:`dfe_schemas.clickhouse` reads from ``sql/platform.sql``, and
dfe-engine reconciles both onto tables that exist.
"""

import re
from collections.abc import Iterable
from typing import Any

from dfe_schemas.clickhouse import Platform, platform_json_settings

# Applied to the DEFAULT settings profile -- the base every identity
# inherits -- because async_insert is user/query scope with no table-level
# form: one seam covers every table any writer touches. Identities with
# tuned profiles (the loader's minted governance profile) override on top.
# wait=1 keeps insert errors visible to the client; the flush cadence stays
# on ClickHouse's adaptive defaults (24.2+). INSERT..SELECT is always
# synchronous server-side and unaffected.
DEFAULT_PROFILE_SETTINGS: dict[str, int] = {  # noqa: V107 - public API
    "async_insert": 1,
    "wait_for_async_insert": 1,
}

# Server-config facts the deploy vehicles set alongside the profile:
# the SQL_ custom-settings prefix backs tenant row policies
# (getSetting('SQL_current_tenant_id')), and deny-by-default system-db
# access is re-granted per identity by dfe-engine governance.
SERVER_CONFIG: dict[str, Any] = {  # noqa: V107 - public API
    "custom_settings_prefixes": "SQL_",
    "access_control_improvements": {
        "select_from_system_db_requires_grant": True,
        "select_from_information_schema_requires_grant": True,
    },
}

# MergeTree settings for a table with a JSON column: uncapped, a merge writes one
# stream per dynamic subcolumn, which map-shaped keys push past the server's
# memory limit.
JSON_TABLE_SETTINGS: dict[str, int] = {
    "merge_max_dynamic_subcolumns_in_wide_part": 64,
}

_QUOTED_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'")
_JSON_TYPE = re.compile(r"\bJSON\b", re.IGNORECASE)


def json_table_settings(column_types: Iterable[str], *, platform: Platform) -> dict[str, int]:
    """The SETTINGS a table carries for its JSON columns, given every column's type.

    :data:`JSON_TABLE_SETTINGS` plus *platform*'s own JSON settings when any type
    is or wraps ``JSON``, and empty otherwise. Quoted literals are dropped first,
    so an ``Enum8('JSON' = 1)`` label does not read as a JSON column.

    Args:
        column_types: The full ClickHouse type of each column, as rendered.
        platform: The ClickHouse platform the table is created on.

    Returns:
        Setting name to value, ready for the table's ``SETTINGS`` clause.
    """
    for ch_type in column_types:
        if _JSON_TYPE.search(_QUOTED_LITERAL.sub("", ch_type)):
            return {**JSON_TABLE_SETTINGS, **platform_json_settings(platform)}
    return {}
