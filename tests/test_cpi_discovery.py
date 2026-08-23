import unittest
from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
from backend.services.cpi_structured_query import calculate_exact_time_range

class TestCPIDiscoveryAgentAnalytical(unittest.TestCase):
    def setUp(self):
        self.agent = CPIDiscoveryAgent()

    def test_relative_date_parser(self):
        tr7 = calculate_exact_time_range("last 7 days")
        self.assertIsNotNone(tr7.start_time)
        self.assertIn("Last 7 Days", tr7.label)

        tr_old = calculate_exact_time_range("older than 30 days")
        self.assertIsNotNone(tr_old.end_time)
        self.assertIn("Older than 30 Days", tr_old.label)

    def test_analytical_questions(self):
        # 1. Which SFTP iFlows in Customer package are currently deployed?
        res1 = self.agent.execute_query("Which SFTP iFlows in the Customer package are currently deployed?")
        self.assertIn("sources_checked", res1)
        self.assertGreater(len(res1["results"]), 0)
        for item in res1["results"]:
            self.assertTrue(item.get("is_deployed") or item.get("status") == "DEPLOYED")

        # 2. Show all value mappings
        res2 = self.agent.execute_query("Show all value mappings.")
        self.assertGreater(len(res2["results"]), 0)
        for item in res2["results"]:
            self.assertIn(item.get("type", item.get("artifact_type", "ValueMapping")), ["ValueMapping", "IntegrationFlow"])

        # 3. Show deployed iFlows belonging to Finance package
        res3 = self.agent.execute_query("Show me all deployed iFlows that belong to the Finance package.")
        self.assertGreater(len(res3["results"]), 0)
        for item in res3["results"]:
            self.assertEqual(item.get("package_id"), "FinancePackage")

        # 4. Recently modified iFlows
        res4 = self.agent.execute_query("Show the most recently modified iFlows.")
        self.assertGreater(len(res4["results"]), 0)

        # 5. Non-existent API property check (No hallucination)
        res5 = self.agent.execute_query("Show database connection password for Horizon")
        self.assertIn("not available from the current CPI API data", res5["answer"])

if __name__ == "__main__":
    unittest.main()
