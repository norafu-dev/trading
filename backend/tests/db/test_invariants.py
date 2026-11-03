"""Source level invariants of `harvester.db.ingest` (spec 0002 Key invariants, AC-12)."""

import re
from pathlib import Path

import harvester.db.ingest as ingest

SOURCE = Path(ingest.__file__).read_text()


def test_write_functions_never_commit() -> None:  # covers: AC-12
    assert ".commit(" not in SOURCE


def test_only_channel_coverage_rows_are_ever_deleted() -> None:
    orm_deletes = re.findall(r"\bdelete\((\w+)\)", SOURCE)
    sql_deletes = re.findall(r"DELETE\s+FROM\s+(\w+)", SOURCE, re.IGNORECASE)

    assert set(orm_deletes) == {"ChannelCoverage"}
    assert set(sql_deletes) == {"channel_coverage"}
