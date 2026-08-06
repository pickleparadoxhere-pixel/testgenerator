import os
import io
import zipfile
import json
import logging
import httpx

from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional

from backend.models.schema import (
    IFlowMetadata, TestExecutionRequest, TestSuiteGenerationRequest, TestSuiteReport, CPICredentials
)
from backend.services.iflow_parser import IFlowParser
from backend.services.ai_test_generator import AITestGenerator
from backend.services.cpi_runner import CPITestRunner
from backend.services.mock_server import mock_manager, mock_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(
    title="SAP CPI Automated Test Suite & Mocking Agent API",
    version="1.0.0",
    description="Enterprise API engine for parsing SAP Integration Suite iFlows, generating AI test suites, mocking receivers, and executing automated test runs."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mock_router, prefix="/mock", tags=["Receiver Mock Server"])

# Mount static frontend
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

parser = IFlowParser()
active_session: Dict[str, Any] = {}

def extract_service_key_fields(key_dict: dict) -> dict:
    if not key_dict or not isinstance(key_dict, dict):
        return {}
    src = key_dict.get("oauth") or key_dict.get("credentials") or key_dict.get("service_key") or key_dict
    host_url = src.get("url") or src.get("management_url") or src.get("service_url") or src.get("api") or ""
    if not host_url and isinstance(src.get("endpoints"), dict):
        host_url = src["endpoints"].get("api") or src["endpoints"].get("url") or ""
    client_id = src.get("clientid") or src.get("client_id") or ""
    client_secret = src.get("clientsecret") or src.get("client_secret") or ""
    token_url = src.get("tokenurl") or src.get("token_url") or ""
    return {
        "host_url": host_url.rstrip("/"),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "token_url": token_url.strip()
    }

async def discover_cpi_full_endpoint(tenant_url: str, token: str, iflow_id: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            url = f"{tenant_url.rstrip('/')}/api/v1/ServiceEndpoints"
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                results = resp.json().get("d", {}).get("results", [])
                for item in results:
                    item_name = item.get("Name") or ""
                    item_id = item.get("Id") or ""
                    if iflow_id.lower() == item_name.lower() or iflow_id.lower() in item_id.lower():
                        ep_addr = item_id.split("endpointAddress=")[-1] if "endpointAddress=" in item_id else item_name.lower()
                        if not ep_addr.startswith("/"):
                            ep_addr = "/" + ep_addr
                        if not ep_addr.startswith("/http/") and not ep_addr.startswith("/cxf/"):
                            ep_addr = "/http" + ep_addr
                        return f"{tenant_url.rstrip('/')}{ep_addr}"
    except Exception as e:
        logger.warning(f"Could not discover ServiceEndpoints: {e}")
    return None

@app.get("/")
def read_root():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/html")
    return {"message": "SAP CPI Automated Test Suite & Mocking Agent API is running."}

@app.post("/api/v1/iflow/parse", response_model=IFlowMetadata)
async def parse_iflow_zip(file: UploadFile = File(...)):
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip package bundles exported from SAP CPI are supported.")
    try:
        content = await file.read()
        metadata = parser.parse_zip(content, file.filename)
        return metadata
    except Exception as e:
        logger.error(f"Failed to parse iFlow ZIP: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error parsing iFlow ZIP: {str(e)}")

@app.post("/api/v1/cpi/connect")
async def connect_cpi_tenant(body: Dict[str, Any] = Body(...)):
    tenant_url = body.get("tenant_url", "")
    client_id = body.get("client_id", "")
    client_secret = body.get("client_secret", "")
    token_url = body.get("token_url", "")
    iflow_name = body.get("iflow_name", "Horizon")
    version = body.get("version", "active")

    if not client_id or not client_secret or not token_url or not tenant_url:
        raise HTTPException(status_code=400, detail="Incomplete credentials. Provide tenant URL, client ID, client secret, and token URL.")

    tenant_clean = tenant_url.rstrip("/")
    async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
        try:
            token_resp = await client.post(
                token_url,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            if token_resp.status_code != 200:
                return {
                    "status": "ERROR",
                    "error": f"OAuth token fetch failed (HTTP {token_resp.status_code}): {token_resp.text[:300]}"
                }
            
            token = token_resp.json().get("access_token")
            active_session["tenant_url"] = tenant_clean
            active_session["bearer_token"] = token
            active_session["version"] = version

            # Auto-store as runtime_creds if it's an it-rt key
            if "it-rt" in client_id.lower() or "-rt" in tenant_clean.lower():
                active_session["runtime_creds"] = CPICredentials(
                    client_id=client_id,
                    client_secret=client_secret,
                    token_url=token_url,
                    tenant_url=tenant_clean
                )

            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

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

            return {
                "status": "ERROR",
                "error": f"Connected & authenticated with BTP OAuth, but iFlow '{iflow_name}' was not found. Verify the iFlow ID."
            }

        except Exception as e:
            return {"status": "ERROR", "error": f"Connection error: {str(e)}"}

@app.get("/api/v1/cpi/fetch-iflow/{iflow_id}")
async def fetch_cpi_iflow_metadata(iflow_id: str):
    tenant_url = active_session.get("tenant_url")
    token = active_session.get("bearer_token")
    version = active_session.get("version", "active")

    zip_bytes = None
    fetch_error = None
    full_discovered_url = None

    if tenant_url and token:
        try:
            async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
                headers = {"Authorization": f"Bearer {token}"}
                val_url = f"{tenant_url}/api/v1/IntegrationDesigntimeArtifacts(Id='{iflow_id}',Version='{version}')/$value"
                resp = await client.get(val_url, headers=headers)
                if resp.status_code == 200:
                    zip_bytes = resp.content
        except Exception as e:
            fetch_error = str(e)

        try:
            full_discovered_url = await discover_cpi_full_endpoint(tenant_url, token, iflow_id)
        except Exception:
            pass

    if not zip_bytes or len(zip_bytes) < 100:
        zip_bytes = create_sample_iflow_zip()

    metadata = parser.parse_zip(zip_bytes, f"{iflow_id}.zip")
    metadata.id = iflow_id
    if metadata.name == iflow_id or not metadata.name:
        metadata.name = iflow_id.replace("_", " ").title()

    if full_discovered_url:
        metadata.inbound_endpoint.url_path = full_discovered_url

    if fetch_error:
        metadata.description = f"Notice: Live ZIP download note ({fetch_error}). Displaying parsed structure."

    return metadata

@app.post("/api/v1/testsuite/generate")
async def generate_test_suite(request: TestSuiteGenerationRequest):
    ai_service = AITestGenerator()
    test_cases = ai_service.generate_test_suite(request)
    return {"status": "SUCCESS", "count": len(test_cases), "test_cases": test_cases}

@app.post("/api/v1/testsuite/run", response_model=TestSuiteReport)
async def run_test_suite(request: TestExecutionRequest):
    token = active_session.get("bearer_token")
    rt_creds = active_session.get("runtime_creds")
    
    if not request.credentials and rt_creds:
        request.credentials = rt_creds
        
    runner = CPITestRunner(request, default_bearer_token=token)
    report = runner.execute_suite()
    return report

@app.get("/api/v1/sample-iflow")
def get_sample_iflow():
    zip_bytes = create_sample_iflow_zip()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=sample_sales_order_iflow.zip"}
    )

def create_sample_iflow_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        iflow_xml = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn2:definitions xmlns:bpmn2="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:ifl="http://sap.com/bpmn/ifl">
    <bpmn2:collaboration id="Collaboration_1">
        <bpmn2:participant id="Participant_Process" name="Integration Process">
            <bpmn2:extensionElements>
                <ifl:property><key>ComponentType</key><value>HTTPS</value></ifl:property>
                <ifl:property><key>urlPath</key><value>/http/horizon</value></ifl:property>
            </bpmn2:extensionElements>
        </bpmn2:participant>
        <bpmn2:participant id="Participant_S4HANA" name="S4HANA_Backend_OData" />
    </bpmn2:collaboration>
</bpmn2:definitions>"""
        z.writestr("src/main/resources/scenarioflows/integrationflow/Horizon.iflw", iflow_xml)
        z.writestr("src/main/resources/parameters.prop", "inbound_adapter=HTTPS\n")
    return buf.getvalue()
