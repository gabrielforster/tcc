from collection.config import settings
from collection.crypto import derive_key


def test_a_purpose_gets_its_own_key():
    assert derive_key("pseudonym") != derive_key("session-token")


def test_the_same_purpose_gets_the_same_key():
    assert derive_key("pseudonym", "tenant-a") == derive_key("pseudonym", "tenant-a")


def test_the_parts_cannot_be_rearranged_into_another_purpose():
    """Joining on a separator alone would make ("a:b",) and ("a", "b") the same key."""
    assert derive_key("a:b") != derive_key("a", "b")


def test_rotating_the_salt_changes_every_key(monkeypatch):
    before = derive_key("pseudonym")
    monkeypatch.setattr(settings, "anonymization_salt", "a-different-salt")
    assert derive_key("pseudonym") != before


def test_the_key_does_not_reveal_the_salt(monkeypatch):
    monkeypatch.setattr(settings, "anonymization_salt", "a-recognisable-salt")
    assert b"a-recognisable-salt" not in derive_key("pseudonym")
