import base64
import hashlib
import io
import json
import tarfile
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import UserProfile
from app.routers.profiles import (
    _compute_tarball_fields,
    _get_owned_profile,
    _validate_name,
    activate_profile,
    create_profile,
    delete_profile,
    download_profile,
    get_profile,
    list_profiles,
    patch_profile,
    replace_profile,
)
from app.schemas import UserProfileCreate, UserProfileUpdate


def _request(subject: str, email: str = ""):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": email or subject})
    )


def _make_tarball(files: dict[str, str]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


class TestProfileValidation(unittest.TestCase):
    def test_valid_names(self):
        for name in ["work", "my-profile", "dev_01", "a", "abc123"]:
            _validate_name(name)  # should not raise

    def test_invalid_names(self):
        for name in ["", "Work", "has space", "-start", "_start", "a" * 64]:
            with self.assertRaises(HTTPException):
                _validate_name(name)

    def test_tarball_fields(self):
        tb = _make_tarball({"claude.md": "hello"})
        sha256, size = _compute_tarball_fields(tb)
        raw = base64.b64decode(tb)
        self.assertEqual(sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(size, len(raw))


class TestProfileCRUD(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

    def _create(self, subject: str = "user@example.com", name: str = "work", **kwargs):
        req = _request(subject)
        payload = UserProfileCreate(
            name=name,
            description=kwargs.get("description", ""),
            is_default=kwargs.get("is_default", False),
            manifest=kwargs.get("manifest", []),
            tarball_b64=kwargs.get("tarball_b64", _make_tarball({"claude.md": "hello"})),
        )
        return create_profile(payload, req)

    def test_create_and_list(self):
        self._create(name="work")
        self._create(name="research")
        req = _request("user@example.com")
        profiles = list_profiles(req)
        self.assertEqual(len(profiles), 2)
        names = {p.name for p in profiles}
        self.assertIn("work", names)
        self.assertIn("research", names)

    def test_create_sets_owner_subject(self):
        p = self._create(subject="alice@example.com", name="work")
        self.assertEqual(p.owner_subject, "alice@example.com")

    def test_create_computes_sha256(self):
        tb = _make_tarball({"soul.md": "values"})
        p = self._create(tarball_b64=tb)
        expected_sha = hashlib.sha256(base64.b64decode(tb)).hexdigest()
        self.assertEqual(p.sha256, expected_sha)

    def test_duplicate_name_rejected(self):
        self._create(name="work")
        with self.assertRaises(HTTPException) as ctx:
            self._create(name="work")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_get_profile(self):
        self._create(name="work", description="my work mode")
        req = _request("user@example.com")
        p = get_profile("work", req)
        self.assertEqual(p.description, "my work mode")

    def test_get_profile_not_found(self):
        req = _request("user@example.com")
        with self.assertRaises(HTTPException) as ctx:
            get_profile("nonexistent", req)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_replace_profile(self):
        self._create(name="work", description="old")
        req = _request("user@example.com")
        new_tb = _make_tarball({"personality.md": "bold"})
        payload = UserProfileCreate(
            name="work",
            description="new desc",
            is_default=False,
            manifest=[],
            tarball_b64=new_tb,
        )
        p = replace_profile("work", payload, req)
        self.assertEqual(p.description, "new desc")
        expected_sha = hashlib.sha256(base64.b64decode(new_tb)).hexdigest()
        self.assertEqual(p.sha256, expected_sha)

    def test_patch_description(self):
        self._create(name="work", description="old")
        req = _request("user@example.com")
        p = patch_profile("work", UserProfileUpdate(description="updated"), req)
        self.assertEqual(p.description, "updated")

    def test_patch_tarball(self):
        self._create(name="work")
        req = _request("user@example.com")
        new_tb = _make_tarball({"new.md": "content"})
        p = patch_profile("work", UserProfileUpdate(tarball_b64=new_tb), req)
        expected_sha = hashlib.sha256(base64.b64decode(new_tb)).hexdigest()
        self.assertEqual(p.sha256, expected_sha)

    def test_delete_profile(self):
        self._create(name="work")
        req = _request("user@example.com")
        delete_profile("work", req)
        with self.assertRaises(HTTPException):
            get_profile("work", req)

    def test_download_profile(self):
        tb = _make_tarball({"claude.md": "context"})
        self._create(name="work", tarball_b64=tb)
        req = _request("user@example.com")
        dl = download_profile("work", req)
        self.assertEqual(dl.tarball_b64, tb)

    def test_activate_profile(self):
        self._create(name="work", is_default=False)
        self._create(name="research", is_default=True)
        req = _request("user@example.com")
        p = activate_profile("work", req)
        self.assertTrue(p.is_default)
        # research should now be non-default
        research = get_profile("research", req)
        self.assertFalse(research.is_default)

    def test_is_default_uniqueness_on_create(self):
        self._create(name="work", is_default=True)
        self._create(name="research", is_default=True)
        req = _request("user@example.com")
        work = get_profile("work", req)
        research = get_profile("research", req)
        # Only research should be default after second create
        self.assertFalse(work.is_default)
        self.assertTrue(research.is_default)


class TestProfileAuthIsolation(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

    def _create_for(self, subject: str, name: str):
        req = _request(subject)
        payload = UserProfileCreate(
            name=name,
            description=f"profile for {subject}",
            is_default=False,
            manifest=[],
            tarball_b64=_make_tarball({"soul.md": subject}),
        )
        return create_profile(payload, req)

    def test_list_only_own_profiles(self):
        self._create_for("alice@example.com", "work")
        self._create_for("bob@example.com", "work")

        alice_req = _request("alice@example.com")
        alice_profiles = list_profiles(alice_req)
        self.assertEqual(len(alice_profiles), 1)
        self.assertEqual(alice_profiles[0].owner_subject, "alice@example.com")

    def test_cannot_get_other_users_profile(self):
        self._create_for("alice@example.com", "work")
        bob_req = _request("bob@example.com")
        with self.assertRaises(HTTPException) as ctx:
            get_profile("work", bob_req)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cannot_delete_other_users_profile(self):
        self._create_for("alice@example.com", "work")
        bob_req = _request("bob@example.com")
        with self.assertRaises(HTTPException) as ctx:
            delete_profile("work", bob_req)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cannot_download_other_users_profile(self):
        self._create_for("alice@example.com", "work")
        bob_req = _request("bob@example.com")
        with self.assertRaises(HTTPException) as ctx:
            download_profile("work", bob_req)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cannot_activate_other_users_profile(self):
        self._create_for("alice@example.com", "work")
        bob_req = _request("bob@example.com")
        with self.assertRaises(HTTPException) as ctx:
            activate_profile("work", bob_req)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_same_name_allowed_for_different_owners(self):
        # Both users can have a "work" profile
        p1 = self._create_for("alice@example.com", "work")
        p2 = self._create_for("bob@example.com", "work")
        self.assertNotEqual(p1.id, p2.id)
        self.assertEqual(p1.name, p2.name)


if __name__ == "__main__":
    unittest.main()
