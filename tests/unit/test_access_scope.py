import unittest

from src.domain.access_scope import AccessScopeError, AccessScopes


class AccessScopeTests(unittest.TestCase):
    def test_full_and_restricted_scope_sets_are_canonical(self) -> None:
        self.assertEqual("owner:read,owner:write", AccessScopes.full().serialize())
        self.assertEqual("owner:read", AccessScopes.from_values(["owner:read"]).serialize())
        self.assertTrue(AccessScopes.from_values(["owner:read", "owner:write"]).allows("owner:write"))
        self.assertFalse(AccessScopes.from_values(["owner:read"]).allows("owner:write"))

    def test_scope_parser_rejects_wildcards_duplicates_and_unknown_values(self) -> None:
        for raw in ("", "*", "owner:read,owner:read", "owner:admin", "owner:read,"):
            with self.subTest(raw=raw):
                with self.assertRaises(AccessScopeError):
                    AccessScopes.parse(raw)

    def test_scope_values_are_immutable(self) -> None:
        scopes = AccessScopes.from_values(["owner:read"])
        with self.assertRaises(AccessScopeError):
            AccessScopes.from_values(["owner:read", 1])
        self.assertEqual(frozenset({"owner:read"}), scopes.values)


if __name__ == "__main__":
    unittest.main()
