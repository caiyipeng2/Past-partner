import unittest
from pathlib import Path

from src.services.storage import InvalidStorageIdentifier, StorageLayout


class StoragePathTests(unittest.TestCase):
    def setUp(self) -> None:
        # Path validation is pure; an uncreated workspace-local root avoids
        # platform-specific tempfile cleanup permissions in restricted runners.
        self.root = Path.cwd() / "data" / "test-runtime"

    def test_builds_server_owned_path_inside_root(self) -> None:
        layout = StorageLayout(self.root)
        result = layout.object_path("personas", "b05bed24-98e6-4d24-bf7c-3f541226686a", ".json")

        self.assertEqual(self.root.resolve(), result.parent.parent)
        self.assertEqual("b05bed24-98e6-4d24-bf7c-3f541226686a.json", result.name)

    def test_rejects_path_traversal_and_unsafe_identifiers(self) -> None:
        invalid_values = (
            "",
            ".",
            "..",
            "../outside",
            "..\\outside",
            "/absolute",
            "C:\\absolute",
            "nested/name",
            "nested\\name",
            "line\nbreak",
            "safe.",
            "CON",
            "nul.txt",
        )

        layout = StorageLayout(self.root)
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(InvalidStorageIdentifier):
                    layout.object_path("imports", value, ".json")

    def test_rejects_unsafe_collection_and_suffix(self) -> None:
        layout = StorageLayout(self.root)
        with self.assertRaises(InvalidStorageIdentifier):
            layout.object_path("../imports", "safe-id", ".json")
        with self.assertRaises(InvalidStorageIdentifier):
            layout.object_path("imports", "safe-id", "/outside")


if __name__ == "__main__":
    unittest.main()
