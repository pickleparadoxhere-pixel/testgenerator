import io
import json
from unittest.mock import patch
import unittest
import zipfile
from urllib.error import HTTPError

from iflow_testpayload.sap import RuntimeHttpClient, SapCpiClient, SapCpiError


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, reason: str = "OK", headers: dict | None = None):
        self.body = body
        self.status = status
        self.reason = reason
        self.headers = headers or {"Content-Type": "application/xml"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        return self.body


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout=None):
        self.request = request
        return self.response


class SapClientTests(unittest.TestCase):
    def test_requires_https(self):
        with self.assertRaisesRegex(SapCpiError, "HTTPS"):
            SapCpiClient("http://tenant.example", "user", "secret")

    def test_accepts_service_key_api_base(self):
        client = SapCpiClient("https://tenant.example/api", "user", "secret")
        self.assertEqual(client.service_root, "https://tenant.example/api/v1")

    def test_preserves_501_status_for_fallback(self):
        error = HTTPError("https://tenant.example/api/v1", 501, "Not Implemented", {}, None)
        friendly = SapCpiClient._friendly_error(error, "SAP CPI request failed")
        self.assertEqual(friendly.status, 501)
        self.assertIn("OData route", str(friendly))

    @patch("iflow_testpayload.sap.urlopen")
    def test_lists_and_downloads_artifacts(self, mocked_urlopen):
        listing = {"d": {"results": [{"Id": "Flow_Req", "Name": "Flow Request", "Version": "1.2", "PackageId": "PKG"}]}}
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("src/main/resources/test.txt", "ok")
        mocked_urlopen.side_effect = [FakeResponse(json.dumps(listing).encode()), FakeResponse(archive_buffer.getvalue())]
        client = SapCpiClient("https://tenant.example/api/v1", "user", "secret")
        artifacts = client.list_artifacts()
        content = client.download_artifact(artifacts[0].id, artifacts[0].version)
        self.assertEqual(artifacts[0].package_id, "PKG")
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(content)))
        first_request = mocked_urlopen.call_args_list[0].args[0]
        self.assertIn("IntegrationDesigntimeArtifacts", first_request.full_url)
        self.assertTrue(first_request.headers["Authorization"].startswith("Basic "))

    @patch("iflow_testpayload.sap.urlopen")
    def test_falls_back_to_package_scoped_listing(self, mocked_urlopen):
        global_error = HTTPError("https://tenant.example/api/v1/IntegrationDesigntimeArtifacts", 501, "Not Implemented", {}, None)
        packages = {"d": {"results": [{"Id": "PKG", "Name": "Package"}]}}
        flows = {"d": {"results": [{"Id": "Flow_Req", "Name": "Flow Request", "Version": "1.0"}]}}
        mocked_urlopen.side_effect = [global_error, FakeResponse(json.dumps(packages).encode()), FakeResponse(json.dumps(flows).encode())]
        artifacts = SapCpiClient("https://tenant.example", "user", "secret").list_artifacts()
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0].package_id, "PKG")
        self.assertIn("IntegrationPackages", mocked_urlopen.call_args_list[1].args[0].full_url)

    @patch("iflow_testpayload.sap.urlopen")
    def test_resolves_display_name_to_technical_id(self, mocked_urlopen):
        listing = {"d": {"results": [{"Id": "COOP_ACK_QA", "Name": "Co-OP Bank_To_ SAP Acknowledgment_QA", "Version": "1.0.11"}]}}
        mocked_urlopen.return_value = FakeResponse(json.dumps(listing).encode())
        resolved = SapCpiClient("https://tenant.example", "user", "secret").resolve_artifact(
            "Co-OP Bank_To_ SAP Acknowledgment_QA", "1.0.11"
        )
        self.assertEqual(resolved.id, "COOP_ACK_QA")

    @patch("iflow_testpayload.sap.build_opener")
    def test_runtime_client_returns_response_details(self, mocked_build_opener):
        opener = FakeOpener(FakeResponse(b"<Reply>OK</Reply>", 202, "Accepted", {"Content-Type": "application/xml"}))
        mocked_build_opener.return_value = opener
        result = RuntimeHttpClient("runtime-user", "runtime-secret", "basic").call(
            "https://runtime.example/http/test", "<Request/>", {"X-Correlation-ID": "TEST-001"}
        )
        self.assertEqual(result.status, 202)
        self.assertEqual(result.body, "<Reply>OK</Reply>")
        self.assertEqual(opener.request.get_method(), "POST")
        self.assertTrue(opener.request.headers["Authorization"].startswith("Basic "))


if __name__ == "__main__":
    unittest.main()
