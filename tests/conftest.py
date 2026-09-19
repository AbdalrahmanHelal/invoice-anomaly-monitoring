import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from invoice_anomaly_demo.db.engine import make_session_factory, session_scope


@pytest.fixture
def db_session():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        session_factory = make_session_factory(db_path)
        with session_scope(session_factory) as session:
            yield session
