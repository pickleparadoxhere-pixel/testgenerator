import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.iflow_parser import IFlowParser
from backend.services.ai_test_generator import AITestGenerator
from backend.services.mock_server import MockServerManager
from backend.services.cpi_runner import CPITestRunner
from backend.models.schema import TestSuiteGenerationRequest, TestExecutionRequest, MockResponseRule
from backend.samples.sample_iflow import create_sample_iflow_zip

class TestSAPCPIAgent(unittest.TestCase):

    def test_iflow_parser(self):
        parser = IFlowParser()
        zip_bytes = create_sample_iflow_zip()
        metadata = parser.parse_zip(zip_bytes, "SalesOrder_S4HANA_Creation.zip")
        self.assertEqual(metadata.id, "SalesOrder_S4HANA_Creation")
        self.assertEqual(metadata.inbound_endpoint.adapter_type, "HTTPS")
        self.assertTrue(len(metadata.receiver_endpoints) >= 1)
        self.assertIn("ValidateOrder.groovy", metadata.groovy_scripts)

    def test_ai_test_generator_fallback(self):
        parser = IFlowParser()
        zip_bytes = create_sample_iflow_zip()
        metadata = parser.parse_zip(zip_bytes, "SalesOrder_S4HANA_Creation.zip")
        
        generator = AITestGenerator()
        request = TestSuiteGenerationRequest(iflow_metadata=metadata, num_cases_per_category=1)
        test_cases = generator.generate_test_suite(request)
        
        self.assertTrue(len(test_cases) >= 3)
        categories = [tc.category for tc in test_cases]
        self.assertIn("happy_path", categories)
        self.assertIn("boundary", categories)
        self.assertIn("negative", categories)

    def test_mock_server_manager(self):
        manager = MockServerManager()
        manager.clear()
        
        rule = MockResponseRule(
            receiver_name="S4HANA_Backend",
            response_status=201,
            response_body='{"SalesOrder": "100456"}'
        )
        manager.register_rule(rule)
        
        status, headers, body = manager.handle_request(
            receiver_name="S4HANA_Backend",
            method="POST",
            path="/mock/s4hana",
            headers={"Content-Type": "application/json"},
            body='{"Customer": "1001"}'
        )
        
        self.assertEqual(status, 201)
        self.assertIn("SalesOrder", body)
        
        intercepts = manager.get_intercepted_requests("S4HANA_Backend")
        self.assertEqual(len(intercepts), 1)

    def test_cpi_runner(self):
        parser = IFlowParser()
        zip_bytes = create_sample_iflow_zip()
        metadata = parser.parse_zip(zip_bytes, "SalesOrder_S4HANA_Creation.zip")
        
        generator = AITestGenerator()
        gen_req = TestSuiteGenerationRequest(iflow_metadata=metadata, num_cases_per_category=1)
        test_cases = generator.generate_test_suite(gen_req)
        
        exec_req = TestExecutionRequest(
            cpi_endpoint="http://simulated-cpi-inbound",
            test_cases=test_cases,
            enable_mpl_check=True
        )
        runner = CPITestRunner(exec_req)
        report = runner.execute_suite()
        
        self.assertEqual(report.total_tests, len(test_cases))
        self.assertEqual(report.passed, len(test_cases))
        self.assertIsNotNone(report.junit_xml)

    def test_cpi_discovery_agent(self):
        from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
        agent = CPIDiscoveryAgent()
        
        # Test 1: SFTP query
        res1 = agent.execute_query("Find all iFlows containing SFTP.")
        self.assertGreater(len(res1["results"]), 0)

        # Test 2: Customer package query
        res2 = agent.execute_query("What integrations are in the Customer package?")
        self.assertGreater(len(res2["results"]), 0)

        # Test 3: Deployed iFlows query
        res3 = agent.execute_query("Show me all deployed iFlows.")
        self.assertGreater(len(res3["results"]), 0)

        # Test 4: Recently modified query
        res4 = agent.execute_query("Which iFlows were modified recently?")
        self.assertGreater(len(res4["results"]), 0)

    def test_cpi_monitoring_agent(self):
        from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
        agent = CPIDiscoveryAgent()

        # Test Health Summary Report
        res1 = agent.execute_query("Give me a health report of my CPI tenant.")
        self.assertIn("CPI TENANT HEALTH SUMMARY", res1["answer"])

        # Test Failures Today
        res2 = agent.execute_query("How many failures happened today?")
        self.assertIn("MessageProcessingLogs", res2["sources_checked"])

        # Test Expiring Certificates
        res3 = agent.execute_query("Which certificates expire within 30 days?")
        self.assertIn("KeystoreEntries", res3["sources_checked"])

    def test_cpi_acceptance_criteria(self):
        from backend.services.cpi_discovery_agent import CPIDiscoveryAgent
        agent = CPIDiscoveryAgent()
        
        # Test 1: 24h Failure Count returns TEXT_ANSWER without arbitrary artifact table
        res1 = agent.execute_query("how many failures in last 24 hours?")
        self.assertEqual(res1["query_type"], "TEXT_ANSWER")
        self.assertEqual(len(res1["table_data"]), 0)

        # Test 2: 6h Failure Count returns TEXT_ANSWER with 6h label
        res2 = agent.execute_query("how many failures in last 6 hours?")
        self.assertEqual(res2["query_type"], "TEXT_ANSWER")
        self.assertIn("Last 6 Hours", res2["statistics"]["period_label"])

if __name__ == "__main__":
    unittest.main()
