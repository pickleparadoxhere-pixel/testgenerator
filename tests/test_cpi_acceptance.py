import unittest
import datetime
from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
from backend.services.cpi_structured_query import CPIIntentClassifier, calculate_exact_time_range

class TestCPIAcceptanceCriteria(unittest.TestCase):
    def setUp(self):
        self.agent = CPIDiscoveryAgent()

    def test_intent_classification_and_exact_time_math(self):
        # 1. Test "last 24 hours"
        intent24 = CPIIntentClassifier.classify("how many failures in last 24 hours?")
        self.assertEqual(intent24.domain, "MESSAGE_PROCESSING")
        self.assertEqual(intent24.operation, "COUNT")
        self.assertIn("Last 24 Hours", intent24.time_range.label)

        # 2. Test "last 6 hours"
        intent6 = CPIIntentClassifier.classify("how many failures in last 6 hours?")
        self.assertEqual(intent6.domain, "MESSAGE_PROCESSING")
        self.assertEqual(intent6.operation, "COUNT")
        self.assertIn("Last 6 Hours", intent6.time_range.label)

    def test_24h_failure_count_returns_text_answer_not_iflow_table(self):
        res = self.agent.execute_query("how many failures in last 24 hours?")
        self.assertEqual(res["query_type"], "TEXT_ANSWER")
        self.assertIsNotNone(res["statistics"])
        self.assertEqual(res["statistics"]["metric"], "message_failures")
        self.assertEqual(len(res["table_data"]), 0)  # NO arbitrary artifact table!
        self.assertIn("message failure(s)", res["answer"])

    def test_ranking_and_breakdown_returns_text_answer(self):
        res = self.agent.execute_query("Which 5 iFlows failed the most this week?")
        self.assertEqual(res["query_type"], "TEXT_ANSWER")
        self.assertEqual(len(res["table_data"]), 0)
        self.assertIn("Top Failing iFlows", res["answer"])
        self.assertIn("Horizon", res["answer"])

    def test_certificate_monitoring(self):
        res = self.agent.execute_query("Which certificates expire within 30 days?")
        self.assertEqual(res["query_type"], "TEXT_ANSWER")
        self.assertEqual(len(res["table_data"]), 0)
        self.assertIn("sap_cpi_client_cert", res["answer"])

    def test_tenant_health_report(self):
        res = self.agent.execute_query("Give me a CPI health report.")
        self.assertEqual(res["query_type"], "HEALTH_REPORT")
        self.assertIsNotNone(res["health_summary"])
        self.assertIn("CPI TENANT HEALTH SUMMARY", res["answer"])

    def test_trend_comparison(self):
        res = self.agent.execute_query("Compare failures in the last 24 hours with the previous 24 hours.")
        self.assertEqual(res["query_type"], "TEXT_ANSWER")
        self.assertIn("Failure Trend Analysis", res["answer"])

    def test_explicit_artifact_listing(self):
        res = self.agent.execute_query("Find all iFlows containing SFTP")
        self.assertEqual(res["query_type"], "ARTIFACTS_LIST")
        self.assertGreater(len(res["table_data"]), 0)

    def test_no_hallucination_for_unsupported_queries(self):
        res = self.agent.execute_query("Show me the private database password for Horizon")
        self.assertIn("not available from the current CPI API data", res["answer"])

if __name__ == "__main__":
    unittest.main()
