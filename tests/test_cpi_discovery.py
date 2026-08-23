import unittest
from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
from backend.services.cpi_knowledge_model import parse_relative_date_expression

class TestCPIDiscoveryAgentAnalytical(unittest.TestCase):
    def setUp(self):
        self.agent = CPIDiscoveryAgent()

    def test_relative_date_parser(self):
        start, end, desc = parse_relative_date_expression("last 7 days")
        self.assertIsNotNone(start)
        self.assertIn("Last 7 days", desc)

        start6, end6, desc6 = parse_relative_date_expression("older than 6 months")
        self.assertIsNotNone(end6)
        self.assertIn("Before", desc6)

    def test_analytical_questions(self):
        # 1. Which SFTP iFlows in Customer package are currently deployed?
        res1 = self.agent.execute_query("Which SFTP iFlows in the Customer package are currently deployed?")
        self.assertIn("sources_checked", res1)
        self.assertGreater(len(res1["results"]), 0)
        for item in res1["results"]:
            self.assertTrue(item["is_deployed"])

        # 2. Show all value mappings
        res2 = self.agent.execute_query("Show all value mappings.")
        self.assertGreater(len(res2["results"]), 0)
        for item in res2["results"]:
            self.assertEqual(item["type"], "ValueMapping")

        # 3. Show deployed iFlows belonging to Finance package
        res3 = self.agent.execute_query("Show me all deployed iFlows that belong to the Finance package.")
        self.assertGreater(len(res3["results"]), 0)
        for item in res3["results"]:
            self.assertEqual(item["package_id"], "FinancePackage")
            self.assertTrue(item["is_deployed"])

        # 4. Recently modified iFlows
        res4 = self.agent.execute_query("Show the most recently modified iFlows.")
        self.assertGreater(len(res4["results"]), 0)

        # 5. Non-existent API property check (No hallucination)
        res5 = self.agent.execute_query("Show database connection password for Horizon")
        self.assertIn("not available from the current CPI API data", res5["answer"])

if __name__ == "__main__":
    unittest.main()
