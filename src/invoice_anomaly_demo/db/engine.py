from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from invoice_anomaly_demo.db.models import Base


def make_session_factory(db_path: str = "invoice_anomaly_demo.db") -> sessionmaker:
    # NullPool avoids SQLAlchemy holding a pooled connection open after the
    # caller is done with it -- on Windows, a lingering open handle to the
    # SQLite file blocks deleting/cleaning it up right after use (e.g. a
    # test's tempdir teardown), even though the app itself is finished with it.
    from sqlalchemy.pool import NullPool

    engine = create_engine(f"sqlite:///{db_path}", future=True, poolclass=NullPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
