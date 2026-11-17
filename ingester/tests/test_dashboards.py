import json
import pathlib
import unittest

DASHBOARDS = pathlib.Path(__file__).resolve().parents[2] / "grafana" / "provisioning" / "dashboards" / "health"


def raw_sql(node):
    if isinstance(node, dict):
        if "rawSql" in node:
            yield node["rawSql"]
        for value in node.values():
            yield from raw_sql(value)
    elif isinstance(node, list):
        for value in node:
            yield from raw_sql(value)


class DashboardSqlTest(unittest.TestCase):
    def test_distance_queries_convert_units(self):
        queries = [
            q for path in DASHBOARDS.glob("*.json")
            for q in raw_sql(json.loads(path.read_text()))
            if "measurement = 'Distance" in q
        ]
        self.assertTrue(queries)
        for q in queries:
            self.assertIn("WHEN 'mi' THEN", q)


if __name__ == "__main__":
    unittest.main()
