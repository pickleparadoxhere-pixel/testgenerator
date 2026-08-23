import unittest
from backend.services.cpi_discovery_agent import CPIDiscoveryAgent

class TestCPIDiscoveryAgent(unittest.TestCase):
    def setUp(self):
        self.agent = CPIDiscoveryAgent()

    def test_sftp_query(self):
        res = self.agent.execute_query("Find all iFlows containing SFTP.")
        self.assertIn("query", res)
        self.assertIn("results", res)
        self.assertGreater(len(res["results"]), 0)
        for item in res["results"]:
            has_sftp = "SFTP" in [a.upper() for a in item.get("adapters", [])] or "SFTP" in item["id"].upper()
            self.assertTrue(has_sftp)

    def test_customer_package_query(self):
        res = self.agent.execute_query("What integrations are in the Customer package?")
        self.assertGreater(len(res["results"]), 0)
        for item in res["results"]:
            self.assertIn("customer", item["package_id"].lower() + item["name"].lower())

    def test_deployed_iflows_query(self):
        res = self.agent.execute_query("Show me all deployed iFlows.")
        self.assertGreater(len(res["results"]), 0)
        for item in res["results"]:
            self.assertEqual(item["status"], "DEPLOYED")

    def test_recently_modified_query(self):
        res = self.agent.execute_query("Which iFlows were modified recently?")
        self.assertGreater(len(res["results"]), 0)

if __name__ == "__main__":
    unittest.main()
