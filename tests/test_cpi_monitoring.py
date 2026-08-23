import unittest
from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
from backend.services.cpi_monitoring_model import CPIMonitoringModel, parse_relative_date_range
from backend.services.cpi_monitoring_tools import CPIMonitoringToolRegistry

class TestCPIMonitoringAgent(unittest.TestCase):
    def setUp(self):
        self.agent = CPIDiscoveryAgent()

    def test_relative_date_parsing(self):
        start, end, label = parse_relative_date_range("last 24 hours")
        self.assertIsNotNone(start)
        self.assertIn("Last 24 Hours", label)

        start7, end7, label7 = parse_relative_date_range("last 7 days")
        self.assertIsNotNone(start7)
        self.assertIn("Last 7 Days", label7)

    def test_tenant_health_report(self):
        res = self.agent.execute_query("Give me a health report of my CPI tenant.")
        self.assertIn("CPI TENANT HEALTH SUMMARY", res["answer"])
        self.assertIn("I checked:", res["answer"])
        self.assertIn("MessageProcessingLogs", res["sources_checked"])
        self.assertIn("KeystoreEntries", res["sources_checked"])

    def test_top_worries_attention_items(self):
        res = self.agent.execute_query("What are the top 5 things I should worry about in my CPI tenant right now?")
        self.assertIn("Overall Status:", res["answer"])

    def test_failures_today(self):
        res = self.agent.execute_query("How many failures happened today?")
        self.assertIn("sources_checked", res)
        self.assertIn("MessageProcessingLogs", res["sources_checked"])

    def test_expiring_certificates(self):
        res = self.agent.execute_query("Which certificates expire within 30 days?")
        self.assertIn("KeystoreEntries", res["sources_checked"])
        self.assertGreater(len(res["results"]), 0)

    def test_cross_domain_sftp_deployed_failures(self):
        res = self.agent.execute_query(
            "Which SFTP integrations in the Customer package are currently deployed and have experienced failures in the last 7 days?"
        )
        self.assertIn("sources_checked", res)
        self.assertGreater(len(res["results"]), 0)

    def test_failure_trend_comparison(self):
        res = self.agent.execute_query("Compare this week's failures with last week.")
        self.assertIn("Failure trend analysis", res["answer"])

    def test_no_hallucination_unsupported(self):
        res = self.agent.execute_query("Show database password for Horizon")
        self.assertIn("not available from the current CPI API data", res["answer"])

if __name__ == "__main__":
    unittest.main()
