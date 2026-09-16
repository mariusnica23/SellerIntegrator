from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid


@contextmanager
def workspace_temp():
    root = (Path(__file__).resolve().parent.parent / ".qa").resolve()
    root.mkdir(exist_ok=True)
    target = root / ("test-" + uuid.uuid4().hex)
    target.mkdir()
    try:
        yield target
    finally:
        resolved = target.resolve()
        if resolved.parent != root or not resolved.name.startswith("test-"):
            raise RuntimeError("Refuz curățarea unei căi din afara spațiului de test.")
        shutil.rmtree(resolved)
