import unittest

from apple_health_ingester import db


class ReadSchemaTest(unittest.TestCase):
    def test_schema_is_packaged(self):
        schema = db.read_schema()
        self.assertIn("CREATE TABLE", schema)
        self.assertRegex(schema, r"CREATE TABLE IF NOT EXISTS\s+records\b")


if __name__ == "__main__":
    unittest.main()
