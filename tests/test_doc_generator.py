import io
import unittest
from backend.services.doc_generator import TechSpecGenerator

class TestTechSpecGenerator(unittest.TestCase):

    def setUp(self):
        self.generator = TechSpecGenerator()
        self.sample_analysis = {
            "name": "Supernova",
            "sender": "HTTPS Sender Adapter",
            "receiver": "S4HANA Backend",
            "steps": ["HTTPS Adapter", "Content Modifier", "Message Mapping", "Request-Reply"],
            "config": [
                {"step": "HTTPS Adapter", "kind": "Sender", "action": "Listens", "name": "urlPath", "value": "/http/supernova"}
            ],
            "headers": [
                {"name": "Content-Type", "sample": "application/xml", "mandatory": True, "notes": "Required"}
            ],
            "properties": [
                {"name": "SAP_MessageProcessingLogID", "sample": "AGY-12345", "mandatory": False, "notes": "Logging"}
            ],
            "payloads": [
                {"scenario": "Inbound Happy Path", "format": "xml", "body": "<Order><ID>100</ID></Order>", "source": "Order.xsd"}
            ],
            "assumptions": ["Schema-derived sample payload"]
        }
        self.sample_metadata = {
            "id": "Supernova",
            "name": "Supernova Sales Order iFlow"
        }

    def test_generate_standard_tech_spec_docx(self):
        docx_bytes = self.generator.generate_tech_spec(self.sample_analysis, self.sample_metadata)
        self.assertIsNotNone(docx_bytes)
        self.assertGreater(len(docx_bytes), 10000)
        self.assertTrue(docx_bytes.startswith(b"PK\x03\x04"))

if __name__ == "__main__":
    unittest.main()
