import re
import secrets

HASH_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def new_hash_id() -> str:
    return secrets.token_hex(6)


def is_hash_id(value: str | None) -> bool:
    if not value:
        return False
    return bool(HASH_ID_RE.fullmatch(value))
