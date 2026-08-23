import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from iflow_testpayload import IFlowAnalyzer
from iflow_testpayload.analyzer import Analysis, ConfigEntry
from iflow_testpayload.web import IFlowWebHandler


FIXTURE = Path(__file__).parent / "fixtures" / "order_iflow"


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), IFlowWebHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_home_and_health(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", "/")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        homepage = response.read()
        self.assertIn(b"Turn an SAP CPI IFlow", homepage)
        self.assertIn(b'value="test"', homepage)
        self.assertIn(b'value="1.0.0"', homepage)
        self.assertNotIn(b"Connect and list IFlows", homepage)
        self.assertIn(b"Paste SAP connection JSON", homepage)
        self.assertNotIn(b"a90626a8trial", homepage)
        self.assertIn(b"Derived from the runtime key and fetched sender adapter", homepage)
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual(json.loads(response.read()), {"status": "ok"})
        connection.close()

    def test_synthetic_demo_analysis(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("POST", "/demo/analyze")
        response = connection.getresponse()
        payload = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertIn("Synthetic_Order_Req.zip", payload["report"])
        self.assertIn("Synthetic_Order_Res.zip", payload["report"])
        self.assertIn("BackendCredentialAlias", payload["report"])
        self.assertIn("ProcessingStatus", payload["report"])
        self.assertEqual(len(payload["payloads"]), 2)
        self.assertTrue(all(item["filename"].endswith(".xml") for item in payload["payloads"]))
        self.assertTrue(all(item["body"].lstrip().startswith("<") for item in payload["payloads"]))
        self.assertEqual(payload["test"]["sender_endpoints"][0]["adapter"], "HTTPS")
        self.assertEqual(payload["test"]["sender_endpoints"][0]["configured_address"], "/test")
        self.assertEqual(payload["test"]["sender_endpoints"][0]["runtime_path"], "/http/test")
        connection.close()

    def test_configurable_mock_receiver(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port)
        config = json.dumps({"status": 201, "content_type": "application/xml", "body": "<Mock>Created</Mock>"})
        connection.request("POST", "/mock/configure", body=config, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.request("POST", "/mock/receiver", body="<Input/>", headers={"Content-Type": "application/xml"})
        response = connection.getresponse()
        self.assertEqual(response.status, 201)
        self.assertEqual(response.read(), b"<Mock>Created</Mock>")
        connection.close()

    def test_analysis_response_exposes_runtime_metadata(self):
        analysis = Analysis(name="Runtime Flow")
        analysis.steps = ["Backend Call (Request-Reply)"]
        analysis.config = [
            ConfigEntry("HTTPS Sender", "Sender Adapter", "Configures", "ComponentType", "HTTPS"),
            ConfigEntry("HTTPS Sender", "Sender Adapter", "Configures", "urlPath", "/orders"),
        ]
        response = IFlowWebHandler._analysis_response([("flow.zip", "Request", analysis)], "report")
        self.assertTrue(response["test"]["request_reply"])
        self.assertEqual(response["test"]["sender_paths"], ["/http/orders"])
        self.assertEqual(response["test"]["sender_endpoints"], [{
            "name": "HTTPS Sender",
            "adapter": "HTTPS",
            "configured_address": "/orders",
            "runtime_path": "/http/orders",
        }])

    def test_sender_runtime_path_does_not_duplicate_adapter_prefix(self):
        self.assertEqual(IFlowWebHandler._sender_runtime_path("HTTPS", "/test"), "/http/test")
        self.assertEqual(IFlowWebHandler._sender_runtime_path("HTTP", "/http/test"), "/http/test")
        self.assertEqual(IFlowWebHandler._sender_runtime_path("SOAP", "ack"), "/cxf/ack")

    def test_sender_adapter_property_can_supply_adapter_type(self):
        analysis = Analysis(name="Start Event Flow")
        analysis.config = [
            ConfigEntry("HTTPS Sender", "Sender Adapter", "Configures", "senderAdapter", "HTTP"),
            ConfigEntry("HTTPS Sender", "Sender Adapter", "Configures", "address", "test"),
        ]
        response = IFlowWebHandler._analysis_response([("flow.zip", "Request", analysis)], "report")
        self.assertEqual(response["test"]["sender_paths"], ["/http/test"])

    def test_combines_zip_and_unzipped_files(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            for path in FIXTURE.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(FIXTURE))
        extra_schema = (FIXTURE / "src/main/resources/xsd/order.xsd").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            packages = IFlowWebHandler._assemble_bundle(bundle, [
                ("order.zip", archive_bytes.getvalue()),
                ("extra-project/src/main/resources/xsd/second.xsd", extra_schema),
            ])
            analyses = [IFlowAnalyzer(path).analyze() for _, path in packages]
            self.assertEqual(len(packages), 2)
            self.assertEqual(sum(len(analysis.payloads) for analysis in analyses), 2)

    def test_pairs_request_and_response_by_filename(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            for path in FIXTURE.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(FIXTURE))
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            bundle.mkdir()
            packages = IFlowWebHandler._assemble_bundle(bundle, [
                ("Goods_Issue_Req.zip", archive_bytes.getvalue()),
                ("Goods_Issue_Res.zip", archive_bytes.getvalue()),
            ])
            analyses = [(name, IFlowWebHandler._infer_role(name), IFlowAnalyzer(path).analyze()) for name, path in packages]
            report = IFlowWebHandler._paired_report(analyses)
            self.assertIn("| Goods_Issue_Req.zip | Request |", report)
            self.assertIn("| Goods_Issue_Res.zip | Response |", report)
            self.assertEqual(report.count("src/main/resources/xsd/order.xsd"), 4)


if __name__ == "__main__":
    unittest.main()
