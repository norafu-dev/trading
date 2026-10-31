from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import TIMESTAMP, BigInteger, MetaData, Text
from sqlalchemy.orm import DeclarativeBase

# Stable constraint names so Alembic autogenerate produces predictable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared declarative base for collector, api and migrations; models live in `models`."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Spec 0002 conventions: Discord IDs are BIGINT, strings TEXT, times TIMESTAMPTZ.
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        int: BigInteger,
        str: Text,
        datetime: TIMESTAMP(timezone=True),
    }
