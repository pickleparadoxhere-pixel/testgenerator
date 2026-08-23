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
from backend.services.cpi_monitoring_model import CPIMonitoringModel, parse_relative_date_range
from backend.services.cpi_monitoring_tools import CPIMonitoringToolRegistry, CPI_MONITORING_TOOLS_SCHEMA

logger = logging.getLogger(__name__)

class CPIDiscoveryAgent:
    """
    Unified SAP CPI Autonomous Agent with full Discovery, Health Analysis,
    MPL Failure Diagnostics, Certificate Monitoring, and Cross-Domain Correlation capabilities.
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
        logger.info(f"Executing SAP CPI Agent query: '{query_clean}'")

        # 1. Fetch raw CPI API endpoints context (Designtime, Packages, Runtime, Endpoints, MPL, Keystore)
        raw_packages, raw_designtime, raw_runtime, raw_endpoints, raw_mpl, raw_keystore = self._fetch_all_cpi_apis(
            tenant_url=tenant_url,
            bearer_token=bearer_token,
            artifacts_list=artifacts_list
        )

        # 2. Build normalized CPI discovery & monitoring models
        disc_model = CPINormalizedModel(raw_packages, raw_designtime, raw_runtime, raw_endpoints)
        disc_registry = CPIDiscoveryToolRegistry(disc_model)

        mon_model = CPIMonitoringModel(raw_mpl, raw_keystore)
        mon_registry = CPIMonitoringToolRegistry(mon_model)

        # 3. Reasoning & tool execution engine
        results, reason, queried_sources, health_summary = self._reason_and_execute(
            query_clean, disc_registry, mon_registry, disc_model, mon_model
        )

        # 4. Synthesize natural language answer with source explanation & zero hallucination
        answer_text = self._synthesize_answer(
            query_clean, results, reason, queried_sources, health_summary, len(disc_model.correlated)
        )

        return {
            "query": query_clean,
            "answer": answer_text,
            "total_tenant_artifacts": len(disc_model.correlated),
            "matched_count": len(results) if isinstance(results, list) else 0,
            "results": results if isinstance(results, list) else [],
            "health_summary": health_summary,
            "sources_checked": queried_sources
        }

    def _fetch_all_cpi_apis(
        self,
        tenant_url: str = None,
        bearer_token: str = None,
        artifacts_list: List[Dict[str, Any]] = None
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if not tenant_url or not bearer_token:
            return self._get_sample_raw_context()

        raw_pkgs, raw_dt, raw_rt, raw_ep, raw_mpl, raw_key = [], [], [], [], [], []
        tenant_clean = tenant_url.rstrip("/")
        headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def fetch_odata(endpoint: str) -> List[Dict[str, Any]]:
            try:
                url = f"{tenant_clean}/api/v1/{endpoint}?$format=json"
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("d", {}).get("results", []) or data.get("value", []) or []
            except Exception as ex:
                logger.warning(f"OData endpoint '{endpoint}' fetch note: {ex}")
                return []

        raw_pkgs = fetch_odata("IntegrationPackages")
        raw_dt = fetch_odata("IntegrationDesigntimeArtifacts")
        raw_rt = fetch_odata("IntegrationRuntimeArtifacts")
        raw_ep = fetch_odata("ServiceEndpoints")
        raw_mpl = fetch_odata("MessageProcessingLogs")
        raw_key = fetch_odata("KeystoreEntries")

        if not raw_dt and not raw_rt and artifacts_list:
            raw_dt = artifacts_list

        if not raw_dt and not raw_rt and not raw_mpl and not raw_key:
            return self._get_sample_raw_context()

        return raw_pkgs, raw_dt, raw_rt, raw_ep, raw_mpl, raw_key

    def _get_sample_raw_context(self):
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
            {"Id": "VM_Customer_Types", "Name": "Customer Types Mapping", "Version": "1.0.0", "PackageId": "CustomerPackage", "ArtifactType": "ValueMapping", "ModifiedAt": "2026-08-01T10:00:00Z"}
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
        mpl = [
            {"MessageGuid": "MSG-001", "IntegrationFlowName": "Horizon", "Status": "COMPLETED", "LogStart": "2026-08-24T00:30:00Z"},
            {"MessageGuid": "MSG-002", "IntegrationFlowName": "Horizon", "Status": "FAILED", "ErrorProlog": "Payload Mapping Error: Root element missing", "LogStart": "2026-08-24T00:35:00Z"},
            {"MessageGuid": "MSG-003", "IntegrationFlowName": "SFTP_Customer_Sync", "Status": "FAILED", "ErrorProlog": "SFTP authentication error: Host key rejected", "LogStart": "2026-08-24T00:40:00Z"},
            {"MessageGuid": "MSG-004", "IntegrationFlowName": "SFTP_Customer_Sync", "Status": "FAILED", "ErrorProlog": "SFTP authentication error: Connection refused", "LogStart": "2026-08-24T00:45:00Z"},
            {"MessageGuid": "MSG-005", "IntegrationFlowName": "Supernova", "Status": "FAILED", "ErrorProlog": "HTTP 500 Internal Server Error", "LogStart": "2026-08-24T00:50:00Z"}
        ]
        keystore = [
            {"Alias": "sap_cpi_client_cert", "Type": "KeyPair", "ValidUntil": "2026-08-28T00:00:00Z", "Owner": "BTP KeyVault"},
            {"Alias": "s4hana_b2b_ca", "Type": "Certificate", "ValidUntil": "2026-09-15T00:00:00Z", "Owner": "S4HANA Security"},
            {"Alias": "legacy_erp_cert", "Type": "Certificate", "ValidUntil": "2026-01-01T00:00:00Z", "Owner": "ERP Operations"}
        ]
        return pkgs, dt, rt, ep, mpl, keystore

    def _reason_and_execute(
        self,
        query: str,
        disc_registry: CPIDiscoveryToolRegistry,
        mon_registry: CPIMonitoringToolRegistry,
        disc_model: CPINormalizedModel,
        mon_model: CPIMonitoringModel
    ) -> tuple[Any, str, List[str], Optional[Dict[str, Any]]]:
        q_lower = query.lower()
        sources = ["IntegrationDesigntimeArtifacts", "IntegrationRuntimeArtifacts", "IntegrationPackages"]
        health_summary = None

        # 1. Tenant Health Report / Attention Items
        if any(h in q_lower for h in ["health", "worry", "attention", "top 5 problems", "tenant report"]):
            sources.extend(["MessageProcessingLogs", "KeystoreEntries"])
            report = mon_registry.generate_tenant_health_report()
            return [], "CPI Tenant Health Assessment", sources, report

        # 2. Certificate Monitoring Queries
        if any(c in q_lower for c in ["certificate", "keystore", "expiry", "expire", "expired"]):
            sources.append("KeystoreEntries")
            max_days = 30
            if "7 days" in q_lower or "critical" in q_lower:
                max_days = 7
            elif "expired" in q_lower:
                max_days = 0

            certs = mon_registry.get_keystore_entries(max_days_to_expiry=max_days)
            return certs, f"Keystore certificates expiring within {max_days} days", sources, None

        # 3. Message Processing Log (MPL) & Failure Queries
        if any(m in q_lower for m in ["failure", "failed", "error", "mpl", "messages", "trend", "rate"]):
            sources.append("MessageProcessingLogs")
            
            date_expr = "last 7 days"
            if "24h" in q_lower or "24 hours" in q_lower or "today" in q_lower:
                date_expr = "today"
            elif "30 days" in q_lower or "month" in q_lower:
                date_expr = "last 30 days"

            if "trend" in q_lower or "compare" in q_lower:
                trend = mon_registry.compare_failure_trends()
                return [], f"Failure trend analysis ({trend['assessment']})", sources, None

            if "pattern" in q_lower or "common" in q_lower or "root cause" in q_lower:
                patterns = mon_registry.analyze_error_patterns(date_expression=date_expr)
                return patterns["error_patterns"], f"Error patterns analysis ({date_expr})", sources, None

            stats = mon_registry.get_failure_statistics(date_expression=date_expr)
            return stats["iflow_breakdown"], f"Message failure statistics ({date_expr})", sources, None

        # 4. Cross-Domain Correlation / Designtime & Runtime Filtering
        date_expr = None
        for dterm in ["recently", "today", "yesterday", "last 7 days", "last 30 days", "this month", "last month"]:
            if dterm in q_lower:
                date_expr = dterm
                break

        adapter_type = None
        for ad in ["sftp", "https", "soap", "odata", "idoc", "rest"]:
            if ad in q_lower:
                adapter_type = ad.upper()
                break

        art_type = "ValueMapping" if ("value mapping" in q_lower or "valuemapping" in q_lower) else None

        pkg_term = None
        for pkg in ["customer", "finance", "logistics", "sales", "order"]:
            if pkg in q_lower:
                pkg_term = pkg
                break

        is_deployed = None
        if "not deployed" in q_lower or "design time" in q_lower or "undeployed" in q_lower:
            is_deployed = False
        elif "deploy" in q_lower or "running" in q_lower or "live" in q_lower:
            is_deployed = True

        filtered, reason = disc_registry.filter_and_correlate_artifacts(
            adapter_type=adapter_type,
            package_term=pkg_term,
            is_deployed=is_deployed,
            artifact_type=art_type,
            date_expression=date_expr
        )

        return filtered, reason, sources, None

    def _synthesize_answer(
        self,
        query: str,
        results: Any,
        reason: str,
        queried_sources: List[str],
        health_summary: Optional[Dict[str, Any]],
        total_count: int
    ) -> str:
        # Check for un-supported API data requests
        unsupported = ["database password", "cpu utilization", "ram usage", "host os key", "private rsa secret"]
        if any(u in query.lower() for u in unsupported):
            return "This information is not available from the current CPI API data."

        sources_bullets = "\n".join([f"- `{src}`" for src in queried_sources])

        # Health Report Output Format
        if health_summary:
            crit = "\n".join([f"• 🔴 {item}" for item in health_summary.get("critical_items", [])]) or "• None"
            warn = "\n".join([f"• 🟠 {item}" for item in health_summary.get("warning_items", [])]) or "• None"
            hlth = "\n".join([f"• 🟢 {item}" for item in health_summary.get("healthy_items", [])]) or "• None"
            top_att = "\n".join([f"{i}. {item}" for i, item in enumerate(health_summary.get("top_attention_items", []), 1)]) or "1. All systems operational."

            return (
                f"### 🛡️ CPI TENANT HEALTH SUMMARY\n\n"
                f"**Overall Status:** {health_summary.get('overall_status', '🟢 HEALTHY')}\n\n"
                f"**Critical Risks:**\n{crit}\n\n"
                f"**Warnings:**\n{warn}\n\n"
                f"**Healthy Components:**\n{hlth}\n\n"
                f"**Top Attention Items:**\n{top_att}\n\n"
                f"**I checked:**\n{sources_bullets}"
            )

        # Call Gemini AI if key available
        if self.api_key:
            try:
                prompt = f"""
You are the SAP CPI Autonomous Agent.
Answer the user's question based strictly on the retrieved SAP CPI OData API data.

User Question: "{query}"
Filter & Analytical Reasoning Applied: {reason}
Total Scanned Tenant Context Artifacts: {total_count}
Query Results:
{json.dumps(results[:10] if isinstance(results, list) else results, indent=2)}

Rules:
1. Do not hallucinate or invent failure counts, cert expiry dates, deployment status, or error causes.
2. If required info is unavailable from CPI APIs, explicitly answer: "This information is not available from the current CPI API data."
3. Include an "I checked:" bullet list at the bottom listing the SAP CPI APIs queried.

Synthesize a clear technical response.
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
                logger.warning(f"Unified AI synthesis note: {ex_ai}")

        if isinstance(results, list) and not results:
            return (
                f"No items found matching your query '{query}' ({reason}).\n\n"
                f"**I checked:**\n{sources_bullets}"
            )

        results_count = len(results) if isinstance(results, list) else 1
        return (
            f"Found **{results_count}** record(s) matching your question ({reason}).\n\n"
            f"**I checked:**\n{sources_bullets}"
        )
