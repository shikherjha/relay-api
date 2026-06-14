from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import models so they register on Base.metadata (used by Alembic autogenerate).
from app.models import entities  # noqa: E402,F401
