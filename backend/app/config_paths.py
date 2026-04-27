import os
from pathlib import Path


def mc_home() -> Path:
    """Returns $MC_HOME or ~/.mc — matches the mc CLI default."""
    val = os.getenv("MC_HOME", "")
    if val:
        return Path(val).expanduser()
    return Path.home() / ".mc"


def backups_dir() -> Path:
    return mc_home() / "backups"


def profile_dir(profile_name: str) -> Path:
    return mc_home() / "profiles" / profile_name
