import os
import sys
import json
import urllib.parse
import urllib.request
import urllib.error
import base64
import ssl
import socketserver
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure root workspace directory is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.services.iflow_parser import IFlowParser
from backend.services.ai_test_generator import AITestGenerator
from backend.services.cpi_runner import CPITestRunner
from backend.services.mock_server import mock_manager
from backend.samples.sample_iflow import create_sample_iflow_zip
from backend.models.schema import (
    IFlowMetadata, TestSuiteGenerationRequest, TestExecutionRequest,
    TestCase
)

parser = IFlowParser()
ai_service = AITestGenerator()

# Active SAP CPI session credentials
active_cpi_creds = {}

def extract_zip_from_multipart(raw_bytes: bytes) -> bytes:
    """Locates PK signature magic bytes to extract clean ZIP bytes from multipart uploads."""
    magic = b'PK\x03\x04'
    idx = raw_bytes.find(magic)
    if idx != -1:
        return raw_bytes[idx:]
    return raw_bytes

def parse_and_execute_raw_curl(curl_str: str) -> tuple[dict, str]:
    cleaned = curl_str.strip().replace("\\\n", " ").replace("\n", " ")
    res = subprocess.run(cleaned, shell=True, capture_output=True, text=True, timeout=25)
    stdout = res.stdout.strip()
    json_str = stdout
    if "\r\n\r\n" in stdout:
        json_str = stdout.split("\r\n\r\n")[-1]
    elif "\n\n" in stdout:
        json_str = stdout.split("\n\n")[-1]

    try:
        data = json.loads(json_str)
        return data, stdout
    except Exception:
        return None, stdout

def fetch_oauth_bearer_token(token_url: str, client_id: str, client_secret: str) -> str:
    """Fetches OAuth 2.0 Access Bearer token from SAP BTP XSUAA token service."""
    auth_b64 = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8")
    
    req = urllib.request.Request(token_url, data=data, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return body.get("access_token", "")

def fetch_cpi_odata_json(url: str, token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def download_cpi_iflow_zip(url: str, token: str) -> bytes:
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
        return resp.read()

class AgentHTTPRequestHandler(BaseHTTPRequestHandler):

    def _set_json_headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_json_headers(200)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/" or path == "/index.html":
            self._serve_static_file("index.html", "text/html")
        elif path.startswith("/static/"):
            rel_path = path.replace("/static/", "")
            mime = "text/css" if rel_path.endswith(".css") else ("application/javascript" if rel_path.endswith(".js") else "text/html")
            self._serve_static_file(rel_path, mime)
        elif path == "/api/health":
            self._set_json_headers(200)
            self.wfile.write(json.dumps({"status": "online", "service": "SAP CPI AI Agent"}).encode())
        elif path.startswith("/api/v1/cpi/fetch-iflow/"):
            iflow_id = path.split("/")[-1]
            zip_bytes = None
            fetch_error = None

            # Download REAL live iFlow ZIP bundle via OAuth Bearer Token
            if active_cpi_creds.get("tenant_url") and active_cpi_creds.get("bearer_token"):
                try:
                    tenant_clean = active_cpi_creds["tenant_url"].rstrip("/")
                    token = active_cpi_creds["bearer_token"]
                    version = active_cpi_creds.get("version", "active")
                    val_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_id}',Version='{version}')/$value"
                    
                    zip_bytes = download_cpi_iflow_zip(val_url, token)
                except Exception as e:
                    fetch_error = str(e)
                    print(f"Error downloading live ZIP for '{iflow_id}': {e}")

            if not zip_bytes or len(zip_bytes) < 100:
                zip_bytes = create_sample_iflow_zip()

            metadata = parser.parse_zip(zip_bytes, f"{iflow_id}.zip")
            metadata.id = iflow_id
            if metadata.name == iflow_id or not metadata.name:
                metadata.name = iflow_id.replace("_", " ").title()

            if fetch_error:
                metadata.description = f"Notice: Live ZIP download note ({fetch_error}). Displaying parsed structure."

            self._set_json_headers(200)
            self.wfile.write(json.dumps(metadata.dict()).encode())

        elif path == "/api/v1/sample-iflow":
            zip_bytes = create_sample_iflow_zip()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=sample_sales_order_iflow.zip")
            self.end_headers()
            self.wfile.write(zip_bytes)
        elif path == "/api/v1/mock/intercepts":
            self._set_json_headers(200)
            self.wfile.write(json.dumps({"intercepts": mock_manager.get_intercepted_requests()}).encode())
        else:
            self._serve_static_file(path.lstrip("/"), "text/html")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length)

        if path == "/api/v1/iflow/parse":
            zip_bytes = extract_zip_from_multipart(body_bytes)
            filename = "uploaded_iflow.zip"
            metadata = parser.parse_zip(zip_bytes, filename)
            self._set_json_headers(200)
            self.wfile.write(json.dumps(metadata.dict()).encode())

        elif path == "/api/v1/cpi/connect":
            creds = json.loads(body_bytes.decode("utf-8") or "{}")

            # Unwrap BTP Service Key JSON
            if "oauth" in creds and isinstance(creds["oauth"], dict):
                oauth = creds["oauth"]
                tenant_url = oauth.get("url") or creds.get("tenant_url")
                client_id = oauth.get("clientid") or oauth.get("client_id") or creds.get("client_id")
                client_secret = oauth.get("clientsecret") or oauth.get("client_secret") or creds.get("client_secret")
                token_url = oauth.get("tokenurl") or oauth.get("token_url") or creds.get("token_url")
            else:
                tenant_url = creds.get("tenant_url") or creds.get("url")
                client_id = creds.get("client_id") or creds.get("clientid")
                client_secret = creds.get("client_secret") or creds.get("clientsecret")
                token_url = creds.get("token_url") or creds.get("tokenurl")

            iflow_name = creds.get("iflow_name") or "Horizon"
            version = creds.get("version") or "active"
            raw_curl = creds.get("raw_curl", "").strip()

            if raw_curl:
                active_cpi_creds["raw_curl"] = raw_curl
                try:
                    res_json, raw_stdout = parse_and_execute_raw_curl(raw_curl)
                    if res_json and "d" in res_json:
                        item = res_json.get("d", {})
                        iflow_id = item.get("Id", iflow_name)
                        name = item.get("Name") or iflow_id
                        ver = item.get("Version", version)
                        pkg = item.get("PackageId", "DefaultPackage")

                        self._set_json_headers(200)
                        self.wfile.write(json.dumps({
                            "status": "LIVE_SUCCESS",
                            "message": f"Successfully executed cURL! Fetched iFlow '{iflow_id}'.",
                            "iflows": [{"id": iflow_id, "name": name, "version": ver, "package_id": pkg}]
                        }).encode())
                        return
                except Exception as e:
                    pass

            if tenant_url and client_id and client_secret:
                tenant_clean = tenant_url.rstrip("/")
                if not token_url:
                    subdomain = tenant_clean.split("//")[-1].split(".")[0]
                    token_url = f"https://{subdomain}.authentication.ap21.hana.ondemand.com/oauth/token"

                try:
                    bearer_token = fetch_oauth_bearer_token(token_url, client_id, client_secret)
                    if bearer_token:
                        active_cpi_creds["tenant_url"] = tenant_clean
                        active_cpi_creds["bearer_token"] = bearer_token
                        active_cpi_creds["version"] = version

                        # Query specific iFlow designtime metadata first
                        odata_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_name}',Version='{version}')"
                        try:
                            res_data = fetch_cpi_odata_json(odata_url, bearer_token)
                            if res_data and "d" in res_data:
                                item = res_data.get("d", {})
                                iflow_id = item.get("Id", iflow_name)
                                name = item.get("Name") or iflow_id
                                ver = item.get("Version", version)
                                pkg = item.get("PackageId", "DefaultPackage")

                                self._set_json_headers(200)
                                self.wfile.write(json.dumps({
                                    "status": "LIVE_SUCCESS",
                                    "message": f"Connected to SAP CPI! Fetched live iFlow '{iflow_id}'.",
                                    "iflows": [{"id": iflow_id, "name": name, "version": ver, "package_id": pkg}]
                                }).encode())
                                return
                        except Exception as ex_single:
                            print(f"Single iFlow OData query note: {ex_single}")

                        # Fallback: Query IntegrationRuntimeArtifacts
                        runtime_url = f"{tenant_clean}/api/v1/IntegrationRuntimeArtifacts"
                        try:
                            runtime_data = fetch_cpi_odata_json(runtime_url, bearer_token)
                            results = runtime_data.get("d", {}).get("results", [])
                            iflows = [{
                                "id": item.get("Id"),
                                "name": item.get("Name") or item.get("Id"),
                                "version": item.get("Version", "active"),
                                "package_id": "DeployedRuntime"
                            } for item in results if item.get("Id")]

                            if iflows:
                                self._set_json_headers(200)
                                self.wfile.write(json.dumps({
                                    "status": "LIVE_SUCCESS",
                                    "message": f"Connected to SAP CPI! Fetched {len(iflows)} deployed runtime iFlows.",
                                    "iflows": iflows
                                }).encode())
                                return
                        except Exception as ex_rt:
                            print(f"Runtime artifacts OData query note: {ex_rt}")

                        self._set_json_headers(400)
                        self.wfile.write(json.dumps({
                            "status": "ERROR",
                            "error": f"Connected & authenticated with BTP OAuth, but iFlow '{iflow_name}' was not found. Please verify the exact iFlow ID."
                        }).encode())
                        return
                except Exception as e:
                    self._set_json_headers(400)
                    self.wfile.write(json.dumps({
                        "status": "ERROR",
                        "error": f"OAuth token authentication error: {str(e)}"
                    }).encode())
                    return

            self._set_json_headers(400)
            self.wfile.write(json.dumps({
                "status": "ERROR",
                "error": "Please provide BTP Service Key JSON or credentials."
            }).encode())

        elif path == "/api/v1/testsuite/generate":
            body_json = json.loads(body_bytes.decode("utf-8") or "{}")
            iflow_dict = body_json.get("iflow_metadata", {})
            metadata = IFlowMetadata.from_dict(iflow_dict) if iflow_dict else parser.parse_zip(create_sample_iflow_zip(), "SalesOrder.zip")
            
            req = TestSuiteGenerationRequest(iflow_metadata=metadata, num_cases_per_category=1)
            test_cases = ai_service.generate_test_suite(req)
            
            self._set_json_headers(200)
            self.wfile.write(json.dumps({
                "status": "SUCCESS",
                "count": len(test_cases),
                "test_cases": [tc.dict() for tc in test_cases]
            }).encode())

        elif path == "/api/v1/testsuite/run":
            body_json = json.loads(body_bytes.decode("utf-8") or "{}")
            raw_cases = body_json.get("test_cases", [])
            test_cases = [TestCase.from_dict(rc) for rc in raw_cases]

            req = TestExecutionRequest(
                cpi_endpoint=body_json.get("cpi_endpoint", "simulated"),
                test_cases=test_cases,
                enable_mpl_check=body_json.get("enable_mpl_check", True)
            )
            runner = CPITestRunner(req)
            report = runner.execute_suite()

            self._set_json_headers(200)
            self.wfile.write(json.dumps(report.dict()).encode())

        elif path == "/api/v1/mock/clear":
            mock_manager.clear()
            self._set_json_headers(200)
            self.wfile.write(json.dumps({"status": "SUCCESS", "message": "Cleared mock rules"}).encode())

        elif path.startswith("/mock/"):
            receiver_name = path.replace("/mock/", "")
            status, headers, resp_body = mock_manager.handle_request(
                receiver_name=receiver_name,
                method="POST",
                path=path,
                headers=dict(self.headers),
                body=body_bytes.decode("utf-8", errors="ignore")
            )
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp_body.encode("utf-8"))
        else:
            self._set_json_headers(404)
            self.wfile.write(json.dumps({"error": "Not Found"}).encode())

    def _serve_static_file(self, rel_path, content_type):
        base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
        file_path = os.path.join(base_dir, rel_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self._set_json_headers(404)
            self.wfile.write(json.dumps({"error": f"File not found: {rel_path}"}).encode())

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def run_server(port=10000):
    server_address = ('', port)
    httpd = ReusableTCPServer(server_address, AgentHTTPRequestHandler)
    print(f"🚀 SAP CPI AI Agent Web Studio running at: http://localhost:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    run_server(port)
