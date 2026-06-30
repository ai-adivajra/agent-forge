from pathlib import Path
import os
import yaml

ROOT = Path(__file__).parent

SETTINGS = yaml.safe_load(
    (ROOT / "settings.yaml").read_text(encoding="utf-8")
)


def expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()
