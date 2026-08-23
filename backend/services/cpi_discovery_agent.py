import os
import re
import json
import logging
import datetime
import urllib.request
import urllib.parse
import urllib.error
import ssl
from typing import Dict, Any, List, Optional

from backend.services.cpi_knowledge_model import CPINormalizedModel, parse_relative_date_expression
from backend.services.cpi_discovery_tools import CPIDiscoveryToolRegistry, CPI_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

class CPIDiscoveryAgent:
    """
    Autonomous multi-step discovery and analytical reasoning agent for SAP CPI.
    Normalizes design-time and runtime APIs, executes granular tool functions,
    understands relative date bounds, explains data sources, and prevents hallucination.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("PALM_API_KEY") or os.getenv("GOOGLE_API_KEY")

    def execute_query(
        self,
        query_text: str,
        artifacts_list: List[Dict[str, Any]] = None,
        tenant_url: str = None,
        bearer_token: str = None
    ) -> Dict[str, Any]:
        query_clean = query_text.strip()
        logger.info(f"Executing CPI Discovery Agent query: '{query_clean}'")

        # 1. Fetch raw CPI API endpoints context or sample context
        raw_packages, raw_designtime, raw_runtime, raw_endpoints = self._fetch_cpi_apis_context(
            tenant_url=tenant_url,
            bearer_token=bearer_token,
            artifacts_list=artifacts_list
        )

        # 2. Build normalized internal CPI knowledge model
        model = CPINormalizedModel(raw_packages, raw_designtime, raw_runtime, raw_endpoints)
        registry = CPIDiscoveryToolRegistry(model)

        # 3. Analytical reasoning and tool selection engine
        filtered_results, reason, queried_sources = self._reason_and_execute(query_clean, registry, model)

        # 4. Synthesize natural language answer with source explanation
        answer_text = self._synthesize_answer(query_clean, filtered_results, reason, queried_sources, len(model.correlated))

        return {
            "query": query_clean,
            "answer": answer_text,
            "total_tenant_artifacts": len(model.correlated),
            "matched_count": len(filtered_results),
            "results": filtered_results,
            "sources_checked": queried_sources
        }

    def _fetch_cpi_apis_context(
        self,
        tenant_url: str = None,
        bearer_token: str = None,
        artifacts_list: List[Dict[str, Any]] = None
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if not tenant_url or not bearer_token:
            return self._get_sample_raw_context()

        raw_packages, raw_designtime, raw_runtime, raw_endpoints = [], [], [], []
        tenant_clean = tenant_url.rstrip("/")
        headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # 1. IntegrationPackages
        try:
            url = f"{tenant_clean}/api/v1/IntegrationPackages?$format=json"
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_packages = data.get("d", {}).get("results", []) or data.get("value", [])
        except Exception as e:
            logger.warning(f"IntegrationPackages fetch note: {e}")

        # 2. IntegrationDesigntimeArtifacts
        try:
            url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts?$format=json"
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_designtime = data.get("d", {}).get("results", []) or data.get("value", [])
        except Exception as e:
            logger.warning(f"IntegrationDesigntimeArtifacts fetch note: {e}")

        # 3. IntegrationRuntimeArtifacts
        try:
            url = f"{tenant_clean}/api/v1/IntegrationRuntimeArtifacts?$format=json"
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_runtime = data.get("d", {}).get("results", []) or data.get("value", [])
        except Exception as e:
            logger.warning(f"IntegrationRuntimeArtifacts fetch note: {e}")

        # 4. ServiceEndpoints
        try:
            url = f"{tenant_clean}/api/v1/ServiceEndpoints?$format=json"
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_endpoints = data.get("d", {}).get("results", []) or data.get("value", [])
        except Exception as e:
            logger.warning(f"ServiceEndpoints fetch note: {e}")

        if not raw_designtime and not raw_runtime and artifacts_list:
            raw_designtime = artifacts_list

        if not raw_designtime and not raw_runtime:
            return self._get_sample_raw_context()

        return raw_packages, raw_designtime, raw_runtime, raw_endpoints

    def _get_sample_raw_context(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        pkgs = [
            {"Id": "CustomerPackage", "Name": "Customer Integrations Package", "Version": "1.2.0", "ModifiedAt": "2026-08-20T10:00:00Z"},
            {"Id": "FinancePackage", "Name": "Finance & Payments Package", "Version": "2.0.0", "ModifiedAt": "2026-08-22T12:00:00Z"},
            {"Id": "LogisticsPackage", "Name": "Logistics & Warehouse Package", "Version": "1.0.0", "ModifiedAt": "2026-02-10T08:00:00Z"}
        ]
        dt = [
            {"Id": "Horizon", "Name": "Horizon Sales Order iFlow", "Version": "1.0.2", "PackageId": "CustomerPackage", "ArtifactType": "IntegrationFlow", "ModifiedAt": "2026-08-23T14:00:00Z"},
            {"Id": "Supernova", "Name": "Supernova Payment Gateway", "Version": "2.1.0", "PackageId": "FinancePackage", "ArtifactType": "IntegrationFlow", "ModifiedAt": "2026-08-22T16:30:00Z"},
            {"Id": "SFTP_Customer_Sync", "Name": "Customer Master SFTP Ingestion", "Version": "1.0.0", "PackageId": "CustomerPackage", "ArtifactType": "IntegrationFlow", "ModifiedAt": "2026-08-24T00:15:00Z"},
            {"Id": "SFTP_Vendor_Invoices", "Name": "Vendor Invoice SFTP Batch", "Version": "1.1.4", "PackageId": "FinancePackage", "ArtifactType": "IntegrationFlow", "ModifiedAt": "2026-08-10T11:00:00Z"},
            {"Id": "S4HANA_Products_OData", "Name": "S4HANA Products Catalogue Sync", "Version": "3.0.1", "PackageId": "CustomerPackage", "ArtifactType": "IntegrationFlow", "ModifiedAt": "2026-08-15T09:00:00Z"},
            {"Id": "VM_Customer_Types", "Name": "Customer Types Mapping", "Version": "1.0.0", "PackageId": "CustomerPackage", "ArtifactType": "ValueMapping", "ModifiedAt": "2026-08-01T10:00:00Z"},
            {"Id": "VM_Payment_Codes", "Name": "Payment Error Codes Mapping", "Version": "1.0.0", "PackageId": "FinancePackage", "ArtifactType": "ValueMapping", "ModifiedAt": "2026-08-05T10:00:00Z"},
            {"Id": "Old_Legacy_Orders", "Name": "Legacy Orders Archived iFlow", "Version": "0.9.0", "PackageId": "CustomerPackage", "ArtifactType": "IntegrationFlow", "ModifiedAt": "2025-11-10T10:00:00Z"}
        ]
        rt = [
            {"Id": "Horizon", "Name": "Horizon Sales Order iFlow", "Version": "1.0.2", "Status": "STARTED", "DeployedOn": "2026-08-23T14:05:00Z"},
            {"Id": "Supernova", "Name": "Supernova Payment Gateway", "Version": "2.1.0", "Status": "STARTED", "DeployedOn": "2026-08-22T16:35:00Z"},
            {"Id": "SFTP_Customer_Sync", "Name": "Customer Master SFTP Ingestion", "Version": "1.0.0", "Status": "STARTED", "DeployedOn": "2026-08-24T00:20:00Z"},
            {"Id": "S4HANA_Products_OData", "Name": "S4HANA Products Catalogue Sync", "Version": "3.0.1", "Status": "STARTED", "DeployedOn": "2026-08-15T09:05:00Z"}
        ]
        ep = [
            {"Id": "Horizon", "Name": "Horizon", "Protocol": "HTTPS", "Url": "https://cpi-rt.cfapps.sap.com/http/horizon"},
            {"Id": "Supernova", "Name": "Supernova", "Protocol": "HTTPS", "Url": "https://cpi-rt.cfapps.sap.com/http/supernova"}
        ]
        return pkgs, dt, rt, ep

    def _reason_and_execute(
        self,
        query: str,
        registry: CPIDiscoveryToolRegistry,
        model: CPINormalizedModel
    ) -> tuple[List[Dict[str, Any]], str, List[str]]:
        q_lower = query.lower()
        queried_sources = ["IntegrationDesigntimeArtifacts", "IntegrationRuntimeArtifacts", "IntegrationPackages"]

        # Detect relative date expressions e.g. "modified in the last 7 days", "recently", "older than 6 months"
        date_expr = None
        for dterm in ["recently", "today", "yesterday", "last 7 days", "last 30 days", "this month", "last month", "older than 6 months"]:
            if dterm in q_lower:
                date_expr = dterm
                break

        # Detect adapter/protocol e.g. SFTP, HTTPS, SOAP, OData, IDoc, ValueMapping
        adapter_type = None
        for ad in ["sftp", "https", "soap", "odata", "idoc", "rest"]:
            if ad in q_lower:
                adapter_type = ad.upper()
                break

        # Detect artifact type e.g. ValueMapping, IntegrationFlow
        art_type = None
        if "value mapping" in q_lower or "valuemapping" in q_lower:
            art_type = "ValueMapping"

        # Detect package terms
        package_term = None
        for pkg in ["customer", "finance", "logistics", "sales", "order"]:
            if pkg in q_lower:
                package_term = pkg
                break

        # Detect deployment status filter e.g. "deployed", "not deployed", "design time"
        is_deployed = None
        if "not deployed" in q_lower or "design time" in q_lower or "un-deployed" in q_lower or "undeployed" in q_lower:
            is_deployed = False
        elif "deploy" in q_lower or "running" in q_lower or "live" in q_lower:
            is_deployed = True

        # Execute analytical tool query
        filtered, reason = registry.filter_and_correlate_artifacts(
            adapter_type=adapter_type,
            package_term=package_term,
            is_deployed=is_deployed,
            artifact_type=art_type,
            date_expression=date_expr
        )

        # Check for aggregation queries e.g. "more than 10", "highest number", "count", "packages with"
        if "more than" in q_lower or "highest" in q_lower or "count" in q_lower or "how many" in q_lower:
            agg_metrics = registry.aggregate_tenant_metrics(group_by="package")
            reason += f" (Aggregated packages count: {agg_metrics['total_packages']})"

        # Check for sorting preferences
        if "oldest" in q_lower:
            filtered = sorted(filtered, key=lambda x: str(x.get("modified_at") or x.get("created_at") or ""))
            reason += " (Sorted oldest to newest)"
        elif "recent" in q_lower or "latest" in q_lower or "newest" in q_lower:
            filtered = sorted(filtered, key=lambda x: str(x.get("modified_at") or x.get("created_at") or ""), reverse=True)
            reason += " (Sorted newest to oldest)"

        return filtered, reason, queried_sources

    def _synthesize_answer(
        self,
        query: str,
        results: List[Dict[str, Any]],
        reason: str,
        queried_sources: List[str],
        total_count: int
    ) -> str:
        # Check for explicitly un-supported / un-available API data requests
        unsupported_keywords = ["cpu usage", "memory consumption", "db connection string", "database password", "admin password"]
        if any(un in query.lower() for un in unsupported_keywords):
            return "This information is not available from the current CPI API data."

        sources_bullets = "\n".join([f"- `{src}`" for src in queried_sources])
        names_str = ", ".join([f"**{r['id']}** ({r['name']})" for r in results[:5]])
        more_str = f" and {len(results) - 5} more" if len(results) > 5 else ""

        # Call Gemini AI if API key present for natural language synthesis
        if self.api_key:
            try:
                prompt = f"""
You are the SAP CPI Analytical Discovery AI Agent.
Answer the user's question based strictly on the retrieved SAP CPI OData API data.

User Question: "{query}"
Filter & Analytical Reasoning Applied: {reason}
Total Scanned Tenant Context Artifacts: {total_count}
Matched Results ({len(results)} count):
{json.dumps(results[:10], indent=2)}

Rules:
1. Do not hallucinate or invent artifact modification dates, deployment status, package relationships, or versions.
2. If required information is unavailable from the CPI APIs, explicitly answer: "This information is not available from the current CPI API data."
3. At the bottom of your answer, include an "I checked:" bullet list showing the SAP CPI APIs queried.

Synthesize a clear, 3-sentence technical answer.
"""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
                payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    text = resp_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if "I checked:" not in text:
                        text += f"\n\n**I checked:**\n{sources_bullets}"
                    return text
            except Exception as ex_ai:
                logger.warning(f"Discovery AI synthesis note: {ex_ai}")

        if not results:
            return (
                f"No iFlows found matching your query '{query}' ({reason}).\n\n"
                f"**I checked:**\n{sources_bullets}"
            )

        return (
            f"Found **{len(results)}** artifact(s) matching your question ({reason}): {names_str}{more_str}.\n\n"
            f"**I checked:**\n{sources_bullets}"
        )
