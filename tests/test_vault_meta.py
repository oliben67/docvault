from __future__ import annotations


from docvault.core.vault_meta import VaultMeta, VaultVersion


class TestVaultVersion:
    def test_defaults(self):
        v = VaultVersion()
        assert v.major == 0
        assert v.minor == 1
        assert v.patch == 0

    def test_str(self):
        assert str(VaultVersion()) == "0.1.0"
        assert str(VaultVersion(major=1, minor=2, patch=3)) == "1.2.3"
        assert str(VaultVersion(major=10, minor=0, patch=99)) == "10.0.99"

    def test_bump_patch(self):
        v = VaultVersion(major=1, minor=2, patch=3)
        v2 = v.bump("patch")
        assert v2.major == 1
        assert v2.minor == 2
        assert v2.patch == 4

    def test_bump_minor(self):
        v = VaultVersion(major=1, minor=2, patch=9)
        v2 = v.bump("minor")
        assert v2.major == 1
        assert v2.minor == 3
        assert v2.patch == 0  # patch resets

    def test_bump_major(self):
        v = VaultVersion(major=1, minor=5, patch=7)
        v2 = v.bump("major")
        assert v2.major == 2
        assert v2.minor == 0  # minor resets
        assert v2.patch == 0  # patch resets

    def test_bump_is_immutable(self):
        v = VaultVersion(major=0, minor=1, patch=0)
        v2 = v.bump("patch")
        assert v.patch == 0  # original unchanged
        assert v2.patch == 1

    def test_bump_chain(self):
        v = VaultVersion()
        v = v.bump("patch")
        assert str(v) == "0.1.1"
        v = v.bump("minor")
        assert str(v) == "0.2.0"
        v = v.bump("major")
        assert str(v) == "1.0.0"

    def test_bump_from_zero(self):
        v = VaultVersion(major=0, minor=0, patch=0)
        assert str(v.bump("patch")) == "0.0.1"
        assert str(v.bump("minor")) == "0.1.0"
        assert str(v.bump("major")) == "1.0.0"

    def test_equality(self):
        v1 = VaultVersion(major=1, minor=2, patch=3)
        v2 = VaultVersion(major=1, minor=2, patch=3)
        assert v1 == v2

    def test_inequality(self):
        v1 = VaultVersion(major=1, minor=2, patch=3)
        v2 = VaultVersion(major=1, minor=2, patch=4)
        assert v1 != v2

    def test_large_numbers(self):
        v = VaultVersion(major=999, minor=888, patch=777)
        assert str(v) == "999.888.777"
        assert str(v.bump("patch")) == "999.888.778"

    def test_serialisation(self):
        v = VaultVersion(major=2, minor=3, patch=4)
        d = v.model_dump()
        assert d == {"major": 2, "minor": 3, "patch": 4}
        v2 = VaultVersion.model_validate(d)
        assert v2 == v


class TestVaultMeta:
    def test_defaults(self):
        m = VaultMeta(name="test-vault")
        assert m.name == "test-vault"
        assert m.description == ""
        assert m.version == VaultVersion()
        assert m.created_at is not None
        assert m.updated_at is not None

    def test_custom_values(self):
        v = VaultVersion(major=2, minor=0, patch=0)
        m = VaultMeta(name="prod", description="Production vault", version=v)
        assert m.name == "prod"
        assert m.description == "Production vault"
        assert str(m.version) == "2.0.0"

    def test_serialisation_round_trip(self):
        m = VaultMeta(name="round-trip", description="test")
        d = m.model_dump(mode="json")
        m2 = VaultMeta.model_validate(d)
        assert m2.name == m.name
        assert m2.description == m.description
        assert m2.version == m.version

    def test_timestamps_are_utc(self):
        from datetime import timezone

        m = VaultMeta(name="tz-test")
        assert m.created_at.tzinfo == timezone.utc
        assert m.updated_at.tzinfo == timezone.utc
