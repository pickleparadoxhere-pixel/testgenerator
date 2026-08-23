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

    def test_tech_spec_generator(self):
        from backend.services.doc_generator import TechSpecGenerator
        generator = TechSpecGenerator()
        sample_analysis = {
            "name": "Supernova",
            "sender": "HTTPS Sender Adapter",
            "receiver": "S4HANA Backend",
            "steps": ["HTTPS Adapter", "Content Modifier", "Message Mapping"],
            "config": [{"step": "HTTPS Adapter", "kind": "Sender", "action": "Listens", "name": "urlPath", "value": "/http/supernova"}],
            "headers": [{"name": "Content-Type", "sample": "application/xml", "mandatory": True, "notes": "Required"}],
            "properties": [{"name": "SAP_MessageProcessingLogID", "sample": "AGY-12345", "mandatory": False, "notes": "Logging"}],
            "payloads": [{"scenario": "Inbound Happy Path", "format": "xml", "body": "<Order><ID>100</ID></Order>", "source": "Order.xsd"}],
            "assumptions": ["Schema sample payload"]
        }
        docx_bytes = generator.generate_tech_spec(sample_analysis, {"id": "Supernova"})
        self.assertIsNotNone(docx_bytes)
        self.assertGreater(len(docx_bytes), 10000)
        self.assertTrue(docx_bytes.startswith(b"PK\x03\x04"))

if __name__ == "__main__":
    unittest.main()
