#  Project:      dfe-schemas
#  File:         dfe_schemas/ddl_catalogue.py
#  Purpose:      Read the DDL catalogue: table defaults and the index for each use case
#  Language:     Python
#
#  License:      BUSL-1.1
#  Copyright:    (c) 2026 HYPERI PTY LIMITED

"""The DDL catalogue, ``registries/ddl.yaml``.

A renderer takes its table defaults and every INDEX line from here, so no
index type, granularity or partition function is written in code. The caller
quotes the index name, so naming is passed in.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dfe_schemas import schemas_root
from dfe_schemas.loader import (
    CARDINALITIES,
    DEFAULT_CARDINALITY,
    REGISTRIES_DIR,
    SchemaError,
    read_yaml,
)

__all__ = ["CATALOGUE_FILE", "DDLCatalogue", "IndexSpec", "TableDefaults"]

CATALOGUE_FILE = "ddl.yaml"
_COLUMN = "{col}"
_DIMS = "{dims}"


@dataclass(frozen=True, slots=True)
class IndexSpec:
    """One INDEX line: the expression it covers, its TYPE and its GRANULARITY.

    Attributes:
        type: The ClickHouse index TYPE, verbatim, with ``{dims}`` where the
            use case's argument goes.
        granularity: The index GRANULARITY.
        expression: What the index covers, with ``{col}`` for the column.
        suffix: Appended to the index name when one use case needs two indexes.
    """

    type: str
    granularity: int
    expression: str = _COLUMN
    suffix: str | None = None

    @property
    def needs_dims(self) -> bool:
        """Return True when the TYPE carries the use case's dimension argument."""
        return _DIMS in self.type

    def render(self, *, name: str, column: str, dims: int | None = None) -> str:
        """Render the INDEX line for an already quoted index *name* and *column*."""
        index_type = self.type.replace(_DIMS, str(dims)) if self.needs_dims else self.type
        expression = self.expression.replace(_COLUMN, column)
        return f"INDEX {name} {expression} TYPE {index_type} GRANULARITY {self.granularity}"


@dataclass(frozen=True, slots=True)
class TableDefaults:
    """What a table gets when its definition does not say.

    Attributes:
        index_granularity: The table's ``index_granularity`` setting.
        partition_column: The column ``PARTITION BY`` and a source table's TTL use.
        partition_granularity: The default key into *partition_functions*.
        partition_functions: Granularity name -> the function wrapping the column.
    """

    index_granularity: int
    partition_column: str
    partition_granularity: str
    partition_functions: Mapping[str, str]

    def partition_expr(self, column: str, granularity: str) -> str:
        """The ``PARTITION BY`` expression for *column* at *granularity*.

        Raises:
            SchemaError: *granularity* is not in the catalogue.
        """
        func = self.partition_functions.get(granularity)
        if func is None:
            raise SchemaError(
                f"unknown partition_granularity {granularity!r}; "
                f"valid: {', '.join(sorted(self.partition_functions))}"
            )
        return f"{func}({column})"


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.isidentifier():
        raise SchemaError(f"{where}: must be an identifier")
    return value


def _table_defaults(raw: Any, where: str) -> TableDefaults:
    if not isinstance(raw, dict):
        raise SchemaError(f"{where}: must be a mapping")
    expected = {
        "index_granularity",
        "partition_column",
        "partition_granularity",
        "partition_functions",
    }
    if set(raw) != expected:
        raise SchemaError(f"{where}: must declare exactly {sorted(expected)}")
    granularity = raw["index_granularity"]
    if isinstance(granularity, bool) or not isinstance(granularity, int) or granularity < 1:
        raise SchemaError(f"{where}.index_granularity: must be a positive integer")
    functions = raw["partition_functions"]
    if not isinstance(functions, dict) or not functions:
        raise SchemaError(f"{where}.partition_functions: must be a non-empty mapping")
    for name, func in functions.items():
        _identifier(name, f"{where}.partition_functions")
        _identifier(func, f"{where}.partition_functions.{name}")
    default = raw["partition_granularity"]
    if default not in functions:
        raise SchemaError(
            f"{where}.partition_granularity: {default!r} is not in partition_functions"
        )
    return TableDefaults(
        index_granularity=granularity,
        partition_column=_identifier(raw["partition_column"], f"{where}.partition_column"),
        partition_granularity=default,
        partition_functions=dict(functions),
    )


def _spec(raw: Any, where: str) -> IndexSpec:
    if not isinstance(raw, dict):
        raise SchemaError(f"{where}: an index entry must be a mapping")
    unknown = set(raw) - {"type", "granularity", "expression", "suffix"}
    if unknown:
        raise SchemaError(f"{where}: unknown keys {sorted(unknown)}")
    index_type = raw.get("type")
    granularity = raw.get("granularity")
    if not isinstance(index_type, str) or not index_type.strip():
        raise SchemaError(f"{where}: 'type' must be a non-empty string")
    if isinstance(granularity, bool) or not isinstance(granularity, int) or granularity < 1:
        raise SchemaError(f"{where}: 'granularity' must be a positive integer")
    expression = raw.get("expression", _COLUMN)
    if not isinstance(expression, str) or _COLUMN not in expression:
        raise SchemaError(f"{where}: 'expression' must contain {_COLUMN}")
    suffix = raw.get("suffix")
    if suffix is not None:
        _identifier(suffix, f"{where}.suffix")
    return IndexSpec(index_type, granularity, expression, suffix)


def _spec_list(raw: Any, where: str) -> tuple[IndexSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise SchemaError(f"{where}: must be a non-empty list of index entries")
    specs = tuple(_spec(item, f"{where}[{i}]") for i, item in enumerate(raw))
    suffixes = [spec.suffix for spec in specs]
    if len(specs) > 1 and (None in suffixes or len(set(suffixes)) != len(suffixes)):
        raise SchemaError(f"{where}: two or more indexes need a distinct 'suffix' each")
    return specs


def _index_set(raw: Any, where: str) -> Mapping[str, tuple[IndexSpec, ...]]:
    """Read a list, or a ``by_cardinality`` mapping, into one list per cardinality."""
    if not isinstance(raw, dict):
        return dict.fromkeys(CARDINALITIES, _spec_list(raw, where))
    by_cardinality = raw.get("by_cardinality")
    if set(raw) != {"by_cardinality"} or not isinstance(by_cardinality, dict):
        raise SchemaError(f"{where}: a mapping must hold 'by_cardinality' alone")
    if set(by_cardinality) != set(CARDINALITIES):
        raise SchemaError(f"{where}.by_cardinality: must name exactly {list(CARDINALITIES)}")
    return {
        card: _spec_list(by_cardinality[card], f"{where}.by_cardinality.{card}")
        for card in CARDINALITIES
    }


@dataclass(frozen=True, slots=True)
class DDLCatalogue:
    """``registries/ddl.yaml``, read and checked.

    Attributes:
        version: The catalogue's format version.
        path: The file it was read from.
        table: The table defaults.
    """

    version: int
    path: Path
    table: TableDefaults
    _indexes: Mapping[str, Mapping[str, tuple[IndexSpec, ...]]]

    @classmethod
    def load(cls, *, root: Path | None = None) -> DDLCatalogue:
        """Read the catalogue under *root*, or the one the package ships."""
        return cls.from_file((root or schemas_root()) / REGISTRIES_DIR / CATALOGUE_FILE)

    @classmethod
    def from_file(cls, path: Path) -> DDLCatalogue:
        """Read and check one catalogue file.

        Raises:
            SchemaError: The file is missing, unparseable or malformed.
        """
        data = read_yaml(path)
        if not isinstance(data, dict):
            raise SchemaError(f"{path}: must be a mapping")
        unknown = set(data) - {"version", "table", "use_cases"}
        if unknown:
            raise SchemaError(f"{path}: unknown keys {sorted(unknown)}")
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise SchemaError(f"{path}: 'version' must be an integer")
        table = _table_defaults(data.get("table"), f"{path.name}: table")
        raw_cases = data.get("use_cases")
        if not isinstance(raw_cases, dict) or not raw_cases:
            raise SchemaError(f"{path}: must map its use cases under 'use_cases'")
        indexes: dict[str, Mapping[str, tuple[IndexSpec, ...]]] = {}
        for name, entry in raw_cases.items():
            where = f"{path.name}: use_cases.{name}"
            if not isinstance(entry, dict) or set(entry) != {"indexes"}:
                raise SchemaError(f"{where}: must declare 'indexes' and nothing else")
            indexes[name] = _index_set(entry["indexes"], f"{where}.indexes")
        return cls(version, path, table, indexes)

    @property
    def use_cases(self) -> list[str]:
        """Return every use case the catalogue answers, sorted."""
        return sorted(self._indexes)

    def specs(self, use_case: str, *, cardinality: str) -> tuple[IndexSpec, ...]:
        """The indexes that answer *use_case* on a column of *cardinality*.

        Raises:
            SchemaError: The catalogue does not answer *use_case*.
        """
        by_cardinality = self._indexes.get(use_case)
        if by_cardinality is None:
            raise SchemaError(
                f"unknown index use case {use_case!r}. Valid: {', '.join(self.use_cases)}"
            )
        return by_cardinality.get(cardinality, by_cardinality[DEFAULT_CARDINALITY])

    def index_defs(
        self,
        *,
        column: str,
        use_case: str,
        dims: int | None,
        cardinality: str,
        name: Callable[[str | None], str],
    ) -> list[str]:
        """Render the INDEX lines for one column.

        Args:
            column: The column name, unquoted.
            use_case: The use case name, its argument already split off.
            dims: The use case's argument, if it has one.
            cardinality: The column's declared cardinality.
            name: Turns an index suffix (or None) into the quoted index name.

        Returns:
            One INDEX line per catalogue entry.

        Raises:
            SchemaError: The use case is unknown, or needs an argument it lacks.
                The message leaves the column out for the caller to add.
        """
        specs = self.specs(use_case, cardinality=cardinality)
        if dims is None and any(spec.needs_dims for spec in specs):
            raise SchemaError(f"{use_case} needs the vector dimension count, as {use_case}(<dims>)")
        quoted = f"`{column}`"
        return [spec.render(name=name(spec.suffix), column=quoted, dims=dims) for spec in specs]
