import os
import tempfile

# Must be set before stetho.server is imported (it builds a default app).
os.environ.setdefault("STETHO_DATA_DIR", tempfile.mkdtemp(prefix="stetho-test-"))
os.environ.pop("MODEL_API_KEY", None)
os.environ.pop("WHATSAPP_ACCESS_TOKEN", None)

import pytest  # noqa: E402

from stetho.synth import SimSpec, make_recording  # noqa: E402


@pytest.fixture(scope="session")
def simdir(tmp_path_factory):
    return tmp_path_factory.mktemp("sim")


@pytest.fixture(scope="session")
def sim(simdir):
    """Factory for cached SIMULATED recordings: sim(name, **SimSpec kwargs) -> (path, truth)."""
    cache = {}

    def make(name, **kw):
        if name not in cache:
            spec = SimSpec(**({"fps": 60, "duration_s": 9.0} | kw))
            p = simdir / f"{name}.mp4"
            cache[name] = (p, make_recording(spec, p))
        return cache[name]

    return make


@pytest.fixture()
def settings(tmp_path):
    from stetho.config import Settings
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.ensure_dirs()
    s.magnify = False
    return s
