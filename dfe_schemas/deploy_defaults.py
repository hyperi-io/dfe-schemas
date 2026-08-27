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
"""

from __future__ import annotations

# Applied to the DEFAULT settings profile -- the base every identity
# inherits -- because async_insert is user/query scope with no table-level
# form: one seam covers every table any writer touches. Identities with
# tuned profiles (the loader's minted governance profile) override on top.
# wait=1 keeps insert errors visible to the client; the flush cadence stays
# on ClickHouse's adaptive defaults (24.2+). INSERT..SELECT is always
# synchronous server-side and unaffected.
DEFAULT_PROFILE_SETTINGS: dict[str, int] = {
    "async_insert": 1,
    "wait_for_async_insert": 1,
}

# Server-config facts the deploy vehicles set alongside the profile:
# the SQL_ custom-settings prefix backs tenant row policies
# (getSetting('SQL_current_tenant_id')), and deny-by-default system-db
# access is re-granted per identity by dfe-engine governance.
SERVER_CONFIG = {
    "custom_settings_prefixes": "SQL_",
    "access_control_improvements": {
        "select_from_system_db_requires_grant": True,
        "select_from_information_schema_requires_grant": True,
    },
}
