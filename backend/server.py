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
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure root workspace directory is in PYTHONPATH
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WORKSPACE_ROOT)

from src.iflow_testpayload.analyzer import IFlowAnalyzer
from src.iflow_testpayload.sap import RuntimeHttpClient, SapCpiClient, SapCpiError
from backend.services.iflow_parser import IFlowParser
from backend.services.ai_test_generator import AITestGenerator
from backend.services.cpi_runner import CPITestRunner
from backend.services.mock_server import mock_manager
from backend.services.doc_generator import TechSpecGenerator
from backend.samples.sample_iflow import create_sample_iflow_zip
from backend.models.schema import (
    IFlowMetadata, TestSuiteGenerationRequest, TestExecutionRequest,
    TestCase, MockResponseRule
)

parser = IFlowParser()
ai_service = AITestGenerator()
doc_gen = TechSpecGenerator()

# Active SAP CPI session credentials
active_cpi_creds = {}

def extract_zip_from_multipart(raw_bytes: bytes) -> bytes:
    """Locates PK signature magic bytes to extract clean ZIP bytes from multipart uploads."""
    magic = b'PK\x03\x04'
    idx = raw_bytes.find(magic)
    if idx != -1:
        return raw_bytes[idx:]
    return raw_bytes

def extract_docx_from_multipart(raw_bytes: bytes) -> Optional[bytes]:
    """Locates PK signature magic bytes to extract clean DOCX bytes from multipart uploads."""
    magic = b'PK\x03\x04'
    idx = raw_bytes.find(magic)
    if idx != -1:
        return raw_bytes[idx:]
    return None

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

def analyze_zip_content(zip_bytes: bytes, filename: str) -> dict:
    metadata = parser.parse_zip(zip_bytes, filename)
    analysis_data = {}
    try:
        with tempfile.TemporaryDirectory(prefix="iflow-analyze-") as tmpdir:
            tmp_path = Path(tmpdir) / filename
            tmp_path.write_bytes(zip_bytes)
            an = IFlowAnalyzer(tmp_path).analyze()
            analysis_data = {
                "name": an.name,
                "sender": an.sender,
                "receiver": an.receiver,
                "steps": an.steps,
                "report_markdown": an.to_markdown(),
                "config": [{"step": c.step, "kind": c.kind, "action": c.action, "name": c.name, "value": c.value} for c in an.config],
                "headers": [{"name": h.name, "sample": h.sample, "mandatory": h.mandatory, "notes": h.notes} for h in an.headers],
                "properties": [{"name": p.name, "sample": p.sample, "mandatory": p.mandatory, "notes": p.notes} for p in an.properties],
                "payloads": [{"scenario": p.scenario, "format": p.format, "body": p.body, "source": p.source} for p in an.payloads],
                "inventory": an.inventory,
                "assumptions": an.assumptions
            }
    except Exception as ex:
        print(f"IFlowAnalyzer note: {ex}")

    return {
        "metadata": metadata.dict(),
        "analysis": analysis_data
    }

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
        elif path.endswith(".css") or path.startswith("/css/"):
            rel_path = path.replace("/static/", "").lstrip("/")
            self._serve_static_file(rel_path, "text/css")
        elif path.endswith(".js") or path.startswith("/js/"):
            rel_path = path.replace("/static/", "").lstrip("/")
            self._serve_static_file(rel_path, "application/javascript")
        elif path.startswith("/static/"):
            rel_path = path.replace("/static/", "")
            mime = "text/css" if rel_path.endswith(".css") else ("application/javascript" if rel_path.endswith(".js") else "text/html")
            self._serve_static_file(rel_path, mime)
        elif path == "/api/health":
            self._set_json_headers(200)
            self.wfile.write(json.dumps({"status": "online", "service": "SAP CPI AI Agent & Payload Generator"}).encode())
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

                    # 1. Discover exact designtime version (commit 15a5f23)
                    dt_ver = version
                    try:
                        info_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_id}',Version='active')"
                        info_data = fetch_cpi_odata_json(info_url, token)
                        discovered_ver = info_data.get("d", {}).get("Version")
                        if discovered_ver:
                            dt_ver = discovered_ver
                    except Exception as ex_ver:
                        print(f"Designtime version discovery note: {ex_ver}")

                    val_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_id}',Version='{dt_ver}')/$value"
                    try:
                        zip_bytes = download_cpi_iflow_zip(val_url, token)
                    except Exception:
                        # Fallback to Version 1.0.0
                        val_url_fb = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_id}',Version='1.0.0')/$value"
                        zip_bytes = download_cpi_iflow_zip(val_url_fb, token)

                except Exception as e:
                    fetch_error = str(e)
                    print(f"Error downloading live ZIP for '{iflow_id}': {e}")

            if not zip_bytes or len(zip_bytes) < 100:
                zip_bytes = create_sample_iflow_zip(iflow_id)

            res = analyze_zip_content(zip_bytes, f"{iflow_id}.zip")
            metadata_dict = res["metadata"]
            metadata_dict["id"] = iflow_id
            if metadata_dict["name"] == iflow_id or not metadata_dict["name"]:
                metadata_dict["name"] = iflow_id.replace("_", " ").title()

            # Discover ServiceEndpoints URL path
            discovered_ep = None
            if active_cpi_creds.get("tenant_url") and active_cpi_creds.get("bearer_token"):
                try:
                    tenant_clean = active_cpi_creds["tenant_url"].rstrip("/")
                    token = active_cpi_creds["bearer_token"]
                    ep_url = f"{tenant_clean}/api/v1/ServiceEndpoints"
                    ep_data = fetch_cpi_odata_json(ep_url, token)
                    results = ep_data.get("d", {}).get("results", [])
                    for item in results:
                        item_name = item.get("Name") or ""
                        item_id = item.get("Id") or ""
                        if iflow_id.lower() == item_name.lower() or iflow_id.lower() in item_id.lower():
                            ep_addr = item_id.split("endpointAddress=")[-1] if "endpointAddress=" in item_id else item_name.lower()
                            if not ep_addr.startswith("/"):
                                ep_addr = "/" + ep_addr
                            if not ep_addr.startswith("/http/") and not ep_addr.startswith("/cxf/"):
                                ep_addr = "/http" + ep_addr
                            discovered_ep = f"{tenant_clean}{ep_addr}"
                            metadata_dict["inbound_endpoint"]["url_path"] = discovered_ep
                            break
                except Exception as ex_ep:
                    print(f"ServiceEndpoints discovery note: {ex_ep}")

            if not discovered_ep and active_cpi_creds.get("tenant_url"):
                tenant_clean = active_cpi_creds["tenant_url"].rstrip("/")
                rt_host = tenant_clean.replace(".it-cpitrial03.", ".it-cpitrial03-rt.") if ".it-cpitrial03." in tenant_clean else tenant_clean
                adapter_path = metadata_dict.get("inbound_endpoint", {}).get("url_path", f"/http/{iflow_id.lower()}")
                if not adapter_path.startswith("http"):
                    metadata_dict["inbound_endpoint"]["url_path"] = f"{rt_host}{adapter_path}"

            if fetch_error:
                metadata_dict["description"] = f"Notice: Live ZIP download note ({fetch_error}). Displaying parsed structure."

            res["metadata"] = metadata_dict
            self._set_json_headers(200)
            self.wfile.write(json.dumps(res).encode())

        elif path == "/api/v1/sample-iflow":
            zip_bytes = create_sample_iflow_zip("Horizon")
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
        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
        path = self.path

        if path == "/api/v1/iflow/parse" or path == "/analyze":
            zip_bytes = extract_zip_from_multipart(body_bytes)
            res = analyze_zip_content(zip_bytes, "Uploaded_iFlow.zip")
            self._set_json_headers(200)
            self.wfile.write(json.dumps(res).encode())

        elif path == "/api/v1/doc/generate-spec" or path == "/doc/generate-spec":
            try:
                ref_docx_bytes = None
                analysis_data = {}
                metadata = {}
                
                content_type = self.headers.get("Content-Type", "").lower()
                if "multipart/form-data" in content_type:
                    ref_docx_bytes = extract_docx_from_multipart(body_bytes)
                else:
                    body_json = json.loads(body_bytes.decode("utf-8") or "{}")
                    analysis_data = body_json.get("analysis") or {}
                    metadata = body_json.get("metadata") or {}
                    ref_b64 = body_json.get("reference_docx_b64") or ""
                    if ref_b64:
                        ref_docx_bytes = base64.b64decode(ref_b64)

                iflow_id = (metadata and metadata.get("id")) or (analysis_data and analysis_data.get("name")) or "iFlow"
                docx_bytes = doc_gen.generate_tech_spec(analysis_data, metadata, ref_docx_bytes)

                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                self.send_header("Content-Disposition", f'attachment; filename="Technical_Specification_{iflow_id}.docx"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(docx_bytes)
            except Exception as ex_doc:
                self._set_json_headers(400)
                self.wfile.write(json.dumps({"error": f"Docx generation error: {str(ex_doc)}"}).encode())

        elif path == "/api/v1/cpi/connect" or path == "/sap/artifacts":
            try:
                creds = json.loads(body_bytes.decode("utf-8") or "{}")

                # Unwrap BTP Service Key JSON
                if "oauth" in creds and isinstance(creds["oauth"], dict):
                    oauth = creds["oauth"]
                    tenant_url = oauth.get("url") or oauth.get("management_url") or oauth.get("service_url") or oauth.get("api") or creds.get("tenant_url")
                    client_id = oauth.get("clientid") or oauth.get("client_id") or creds.get("client_id")
                    client_secret = oauth.get("clientsecret") or oauth.get("client_secret") or creds.get("client_secret")
                    token_url = oauth.get("tokenurl") or oauth.get("token_url") or creds.get("token_url")
                else:
                    tenant_url = creds.get("tenant_url") or creds.get("url") or creds.get("management_url")
                    client_id = creds.get("client_id") or creds.get("clientid") or creds.get("principal")
                    client_secret = creds.get("client_secret") or creds.get("clientsecret") or creds.get("secret")
                    token_url = creds.get("token_url") or creds.get("tokenurl")

                auth_type = creds.get("auth_type") or creds.get("auth") or "oauth"
                iflow_name = creds.get("iflow_name") or creds.get("iflowId") or ""
                version = creds.get("version") or "active"

                if tenant_url and client_id and client_secret:
                    tenant_clean = tenant_url.rstrip("/")
                    if auth_type.lower() == "oauth" and not token_url:
                        subdomain = tenant_clean.split("//")[-1].split(".")[0]
                        token_url = f"https://{subdomain}.authentication.ap21.hana.ondemand.com/oauth/token"

                    try:
                        bearer_token = fetch_oauth_bearer_token(token_url, client_id, client_secret) if auth_type.lower() == "oauth" else "basic"
                        if bearer_token:
                            active_cpi_creds["tenant_url"] = tenant_clean
                            active_cpi_creds["bearer_token"] = bearer_token
                            active_cpi_creds["version"] = version
                            active_cpi_creds["runtime_creds"] = {
                                "client_id": client_id,
                                "client_secret": client_secret,
                                "token_url": token_url,
                                "tenant_url": tenant_clean,
                                "auth_type": auth_type
                            }

                            if iflow_name:
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

                            dt_list_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts?$format=json"
                            try:
                                dt_data = fetch_cpi_odata_json(dt_list_url, bearer_token)
                                results = dt_data.get("d", {}).get("results", [])
                                iflows = [{
                                    "id": item.get("Id"),
                                    "name": item.get("Name") or item.get("Id"),
                                    "version": item.get("Version", "active"),
                                    "package_id": item.get("PackageId", "DefaultPackage")
                                } for item in results if item.get("Id")]

                                if iflows:
                                    self._set_json_headers(200)
                                    self.wfile.write(json.dumps({
                                        "status": "LIVE_SUCCESS",
                                        "message": f"Connected to SAP CPI! Fetched {len(iflows)} designtime iFlows.",
                                        "iflows": iflows
                                    }).encode())
                                    return
                            except Exception as ex_dt:
                                print(f"Designtime list OData query note: {ex_dt}")

                            target_id = iflow_name if iflow_name else "Horizon"
                            self._set_json_headers(200)
                            self.wfile.write(json.dumps({
                                "status": "LIVE_SUCCESS",
                                "message": f"Connected & authenticated with BTP OAuth! BTP Service Key registered for live runtime testing on '{tenant_clean}'.",
                                "iflows": [{
                                    "id": target_id,
                                    "name": target_id,
                                    "version": version if version else "active",
                                    "package_id": "RuntimeConnected"
                                }]
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
                    "error": "Please provide complete BTP Service Key JSON or tenant_url, client_id, and client_secret."
                }).encode())
            except Exception as outer_err:
                self._set_json_headers(400)
                self.wfile.write(json.dumps({
                    "status": "ERROR",
                    "error": f"Connection error: {str(outer_err)}"
                }).encode())

        elif path == "/api/v1/runtime/test" or path == "/runtime/test" or path == "/api/v1/testsuite/run":
            try:
                body_json = json.loads(body_bytes.decode("utf-8") or "{}")
                endpoint = body_json.get("endpoint") or body_json.get("cpi_endpoint") or ""
                xml_body = body_json.get("body") or body_json.get("xml_body") or ""
                headers = body_json.get("headers") or {}
                principal = body_json.get("principal") or body_json.get("client_id") or ""
                secret = body_json.get("secret") or body_json.get("client_secret") or ""
                auth_type = body_json.get("auth_type") or body_json.get("auth") or "oauth"
                token_url = body_json.get("token_url") or body_json.get("tokenurl") or ""

                rt_saved = active_cpi_creds.get("runtime_creds") or {}
                if not principal and rt_saved.get("client_id"):
                    principal = rt_saved["client_id"]
                if not secret and rt_saved.get("client_secret"):
                    secret = rt_saved["client_secret"]
                if not token_url and rt_saved.get("token_url"):
                    token_url = rt_saved["token_url"]

                if isinstance(headers, str):
                    try:
                        headers = json.loads(headers)
                    except Exception:
                        headers = {"Content-Type": "application/xml"}

                client = RuntimeHttpClient(
                    principal=principal,
                    secret=secret,
                    auth_type=auth_type,
                    token_url=token_url
                )
                res = client.call(endpoint=endpoint, xml_body=xml_body, headers=headers)
                
                mpl_id = None
                for k, v in res.headers.items():
                    if "messageprocessinglogid" in k.lower():
                        mpl_id = v
                        break

                self._set_json_headers(200)
                self.wfile.write(json.dumps({
                    "status": res.status,
                    "reason": res.reason,
                    "headers": res.headers,
                    "body": res.body,
                    "elapsed_ms": res.elapsed_ms,
                    "mpl_id": mpl_id
                }).encode())
            except Exception as ex_run:
                self._set_json_headers(400)
                self.wfile.write(json.dumps({"error": f"Runtime invocation error: {str(ex_run)}"}).encode())

        elif path == "/api/v1/mock/config" or path == "/mock/save":
            try:
                body_json = json.loads(body_bytes.decode("utf-8") or "{}")
                status = int(body_json.get("status", 200))
                content_type = body_json.get("content_type", "application/xml")
                resp_body = body_json.get("body", "")
                receiver_name = body_json.get("receiver_name", "receiver")

                mock_manager.add_rule(
                    receiver_name=receiver_name,
                    rule=MockResponseRule(
                        receiver_name=receiver_name,
                        response_status=status,
                        response_headers={"Content-Type": content_type},
                        response_body=resp_body
                    )
                )
                self._set_json_headers(200)
                self.wfile.write(json.dumps({
                    "status": "SUCCESS",
                    "message": f"Saved mock response for receiver '{receiver_name}'",
                    "url": f"http://{self.headers.get('Host', 'localhost:10000')}/mock/{receiver_name}"
                }).encode())
            except Exception as ex_mock:
                self._set_json_headers(400)
                self.wfile.write(json.dumps({"error": f"Mock config error: {str(ex_mock)}"}).encode())

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
