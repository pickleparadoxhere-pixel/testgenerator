import os
import logging
import json
import base64
import subprocess
from typing import Optional, Dict, Any
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import httpx

from backend.models.schema import (
    IFlowMetadata, TestSuiteGenerationRequest, TestExecutionRequest,
    TestSuiteReport, CPICredentials
)
from backend.services.iflow_parser import IFlowParser
from backend.services.ai_test_generator import AITestGenerator
from backend.services.cpi_runner import CPITestRunner
from backend.services.mock_server import mock_manager
from backend.samples.sample_iflow import create_sample_iflow_zip

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sap-cpi-agent")

app = FastAPI(
    title="SAP Integration Suite AI Test & Mock Agent",
    description="Automated iFlow Test Case Generator, Mock Server Engine, and SAP CPI Log Verifier",
    version="1.0.0"
)

# Enable CORS for local & cloud development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

parser = IFlowParser()

# Session storage for live tenant credentials & tokens
active_session = {}

# Health check
@app.get("/api/health")
def health_check():
    return {
        "status": "online",
        "service": "SAP CPI AI Test Agent",
        "gemini_api_configured": bool(os.getenv("GEMINI_API_KEY"))
    }

# 1. Parse iFlow ZIP file
@app.post("/api/v1/iflow/parse", response_model=IFlowMetadata)
async def parse_iflow(file: UploadFile = File(...)):
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .zip iFlow bundle")
    contents = await file.read()
    metadata = parser.parse_zip(contents, file.filename)
    return metadata

# Helper for executing shell cURL
def execute_shell_curl(curl_str: str) -> tuple[dict, str]:
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

# 2. Fetch iFlows directly from SAP CPI OData API using Credentials or Service Key JSON
@app.post("/api/v1/cpi/connect")
async def connect_and_fetch_iflows(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    raw_curl = body.get("raw_curl", "").strip()
    if raw_curl:
        try:
            res_json, raw_out = execute_shell_curl(raw_curl)
            if res_json and "d" in res_json:
                item = res_json.get("d", {})
                iflow_id = item.get("Id", "Horizon")
                return {
                    "status": "LIVE_SUCCESS",
                    "message": f"Successfully fetched iFlow '{iflow_id}' via cURL!",
                    "iflows": [{
                        "id": iflow_id,
                        "name": item.get("Name") or iflow_id,
                        "version": item.get("Version", "active"),
                        "package_id": item.get("PackageId", "DefaultPackage")
                    }]
                }
        except Exception as e:
            pass

    # Unwrap BTP Service Key JSON
    if "oauth" in body and isinstance(body["oauth"], dict):
        oauth = body["oauth"]
        tenant_url = oauth.get("url") or body.get("tenant_url")
        client_id = oauth.get("clientid") or oauth.get("client_id") or body.get("client_id")
        client_secret = oauth.get("clientsecret") or oauth.get("client_secret") or body.get("client_secret")
        token_url = oauth.get("tokenurl") or oauth.get("token_url") or body.get("token_url")
    else:
        tenant_url = body.get("tenant_url") or body.get("url")
        client_id = body.get("client_id") or body.get("clientid")
        client_secret = body.get("client_secret") or body.get("clientsecret")
        token_url = body.get("token_url") or body.get("tokenurl")

    iflow_name = body.get("iflow_name") or "Horizon"
    version = body.get("version") or "active"

    if not tenant_url or not client_id or not client_secret:
        return JSONResponse(status_code=400, content={"status": "ERROR", "error": "Please provide BTP Service Key JSON or Host URL, Client ID, and Client Secret."})

    tenant_clean = tenant_url.rstrip("/")
    if not token_url:
        subdomain = tenant_clean.split("//")[-1].split(".")[0]
        token_url = f"https://{subdomain}.authentication.ap21.hana.ondemand.com/oauth/token"

    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            token_resp = await client.post(
                token_url,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret)
            )
            if token_resp.status_code != 200:
                return JSONResponse(status_code=401, content={"status": "ERROR", "error": f"BTP OAuth Authentication Failed (HTTP {token_resp.status_code}): {token_resp.text}"})

            token = token_resp.json().get("access_token")
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            active_session["tenant_url"] = tenant_clean
            active_session["bearer_token"] = token
            active_session["version"] = version

            # 1. Direct OData query for specified iFlow (Works on Trial & Production!)
            single_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_name}',Version='{version}')"
            art_resp = await client.get(single_url, headers=headers)

            if art_resp.status_code == 200:
                data = art_resp.json().get("d", {})
                iflow_id = data.get("Id", iflow_name)
                return {
                    "status": "LIVE_SUCCESS",
                    "message": f"Connected to SAP CPI! Fetched live iFlow '{iflow_id}'.",
                    "iflows": [{
                        "id": iflow_id,
                        "name": data.get("Name") or iflow_id,
                        "version": data.get("Version", version),
                        "package_id": data.get("PackageId", "DefaultPackage")
                    }]
                }

            # 2. Fallback: Query deployed IntegrationRuntimeArtifacts
            rt_url = f"{tenant_clean}/api/v1/IntegrationRuntimeArtifacts"
            rt_resp = await client.get(rt_url, headers=headers)

            if rt_resp.status_code == 200:
                results = rt_resp.json().get("d", {}).get("results", [])
                iflows = [{
                    "id": item.get("Id"),
                    "name": item.get("Name") or item.get("Id"),
                    "version": item.get("Version", "active"),
                    "package_id": "DeployedRuntime"
                } for item in results if item.get("Id")]

                if iflows:
                    return {
                        "status": "LIVE_SUCCESS",
                        "message": f"Connected to SAP CPI! Fetched {len(iflows)} deployed runtime iFlows.",
                        "iflows": iflows
                    }

            return JSONResponse(status_code=400, content={
                "status": "ERROR",
                "error": f"Connected & authenticated with BTP OAuth, but iFlow '{iflow_name}' was not found. (HTTP {art_resp.status_code})"
            })

    except Exception as e:
        logger.error(f"Error connecting to SAP CPI: {e}")
        return JSONResponse(status_code=400, content={"status": "ERROR", "error": f"Connection error: {str(e)}"})

# 3. Download selected iFlow bundle from SAP CPI OData API
@app.get("/api/v1/cpi/fetch-iflow/{iflow_id}")
async def fetch_iflow_bundle(iflow_id: str):
    zip_bytes = None
    fetch_error = None

    if active_session.get("tenant_url") and active_session.get("bearer_token"):
        try:
            tenant_clean = active_session["tenant_url"]
            token = active_session["bearer_token"]
            version = active_session.get("version", "active")
            val_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_id}',Version='{version}')/$value"
            
            async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
                resp = await client.get(val_url, headers={"Authorization": f"Bearer {token}"})
                if resp.status_code == 200:
                    zip_bytes = resp.content
        except Exception as e:
            fetch_error = str(e)

    if not zip_bytes or len(zip_bytes) < 100:
        zip_bytes = create_sample_iflow_zip()

    metadata = parser.parse_zip(zip_bytes, f"{iflow_id}.zip")
    metadata.id = iflow_id
    if metadata.name == iflow_id or not metadata.name:
        metadata.name = iflow_id.replace("_", " ").title()

    if fetch_error:
        metadata.description = f"Notice: Live ZIP download note ({fetch_error}). Displaying parsed structure."

    return metadata

# 4. Generate AI Test Suite
@app.post("/api/v1/testsuite/generate")
async def generate_test_suite(request: TestSuiteGenerationRequest):
    ai_service = AITestGenerator()
    test_cases = ai_service.generate_test_suite(request)
    return {"status": "SUCCESS", "count": len(test_cases), "test_cases": test_cases}

# 5. Run Test Suite
@app.post("/api/v1/testsuite/run", response_model=TestSuiteReport)
async def run_test_suite(request: TestExecutionRequest):
    token = active_session.get("bearer_token")
    runner = CPITestRunner(request, default_bearer_token=token)
    report = runner.execute_suite()
    return report

# 6. Sample iFlow Download endpoint
@app.get("/api/v1/sample-iflow")
def get_sample_iflow():
    zip_bytes = create_sample_iflow_zip()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=sample_sales_order_iflow.zip"}
    )

# 7. Intercepted Mock Requests log
@app.get("/api/v1/mock/intercepts")
def get_mock_intercepts(receiver_name: Optional[str] = None):
    return {"intercepts": mock_manager.get_intercepted_requests(receiver_name)}

# 8. Clear Mock Rules and Logs
@app.post("/api/v1/mock/clear")
def clear_mock_server():
    mock_manager.clear()
    return {"status": "SUCCESS", "message": "Mock server rules and intercepted logs cleared."}

# 9. Dynamic Receiver Mock Endpoint Catch-All
@app.api_route("/mock/{receiver_name:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def mock_receiver_endpoint(receiver_name: str, request: Request):
    method = request.method
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="ignore")
    headers = dict(request.headers)

    status_code, resp_headers, resp_body = mock_manager.handle_request(
        receiver_name=receiver_name,
        method=method,
        path=request.url.path,
        headers=headers,
        body=body_str
    )

    return Response(content=resp_body, status_code=status_code, headers=resp_headers)

# Serve Frontend Web Studio
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

@app.get("/")
def read_root():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return {"message": "SAP CPI AI Test & Mock Agent Backend Running"}
