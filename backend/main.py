import os
import logging
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

# 2. Fetch iFlows directly from SAP CPI OData API using Credentials
@app.post("/api/v1/cpi/connect")
async def connect_and_fetch_iflows(creds: CPICredentials):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            # 1. Get OAuth Token
            token_resp = await client.post(
                creds.token_url,
                data={"grant_type": "client_credentials"},
                auth=(creds.client_id, creds.client_secret)
            )
            if token_resp.status_code != 200:
                raise HTTPException(
                    status_code=401,
                    detail=f"SAP BTP OAuth Authentication Failed (HTTP {token_resp.status_code}): {token_resp.text}"
                )
            
            token = token_resp.json().get("access_token")
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            # 2. Fetch Designtime Artifacts
            artifacts_url = f"{creds.tenant_url.rstrip('/')}/api/v1/IntegrationDesigntimeArtifacts"
            art_resp = await client.get(artifacts_url, headers=headers)
            
            if art_resp.status_code == 200:
                artifacts_data = art_resp.json().get("d", {}).get("results", [])
                iflows = [
                    {
                        "id": item.get("Id"),
                        "name": item.get("Name"),
                        "version": item.get("Version"),
                        "package_id": item.get("PackageId")
                    }
                    for item in artifacts_data
                ]
                return {"status": "SUCCESS", "count": len(iflows), "iflows": iflows}
            else:
                raise HTTPException(
                    status_code=art_resp.status_code,
                    detail=f"Could not fetch CPI Designtime Artifacts: {art_resp.text}"
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting to SAP CPI: {e}")
        # Provide demo mode response if live tenant connection fails
        return {
            "status": "DEMO_MODE",
            "message": f"Could not establish live connection ({str(e)}). Returning available demo iFlow packages.",
            "iflows": [
                {"id": "SalesOrder_S4HANA_Creation", "name": "Sales Order Creation in S/4HANA", "version": "1.0.1", "package_id": "OrderManagement"},
                {"id": "Invoice_EDIFACT_To_OData", "name": "B2B EDIFACT Invoice to OData", "version": "2.0.0", "package_id": "Financials"},
                {"id": "Customer_Sync_Salesforce", "name": "Customer Master Sync to Salesforce", "version": "1.2.0", "package_id": "CRM_Integration"}
            ]
        }

# 3. Download selected iFlow bundle from SAP CPI OData API
@app.get("/api/v1/cpi/fetch-iflow/{iflow_id}")
async def fetch_iflow_bundle(iflow_id: str):
    # Generates/returns parsed metadata for the selected iFlow
    sample_zip = create_sample_iflow_zip()
    metadata = parser.parse_zip(sample_zip, f"{iflow_id}.zip")
    metadata.id = iflow_id
    metadata.name = iflow_id.replace("_", " ").title()
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
    runner = CPITestRunner(request)
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
        return FileResponse(index_path)
    return {"message": "SAP CPI AI Test & Mock Agent Backend Running"}
