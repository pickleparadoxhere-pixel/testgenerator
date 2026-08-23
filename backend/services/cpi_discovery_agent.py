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

logger = logging.getLogger(__name__)

class CPIDiscoveryAgent:
    """Intelligent discovery and natural language query agent for SAP CPI tenant artifacts."""

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

        # 1. Ensure tenant artifacts context is built
        context_artifacts = artifacts_list or []
        if not context_artifacts and tenant_url and bearer_token:
            context_artifacts = self._fetch_live_tenant_context(tenant_url, bearer_token)

        if not context_artifacts:
            # Generate rich fallback context if no live tenant connected
            context_artifacts = self._get_sample_context()

        # 2. Rule-based search and filter engine
        filtered_results, search_reason = self._filter_context(query_clean, context_artifacts)

        # 3. Synthesize natural language answer
        answer_text = self._synthesize_answer(query_clean, filtered_results, search_reason, len(context_artifacts))

        return {
            "query": query_clean,
            "answer": answer_text,
            "total_tenant_artifacts": len(context_artifacts),
            "matched_count": len(filtered_results),
            "results": filtered_results
        }

    def _fetch_live_tenant_context(self, tenant_url: str, bearer_token: str) -> List[Dict[str, Any]]:
        artifacts = []
        tenant_clean = tenant_url.rstrip("/")
        headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Fetch Runtime Deployed Artifacts
        try:
            rt_url = f"{tenant_clean}/api/v1/IntegrationRuntimeArtifacts"
            req = urllib.request.Request(rt_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("d", {}).get("results", []):
                    artifacts.append({
                        "id": item.get("Id"),
                        "name": item.get("Name") or item.get("Id"),
                        "version": item.get("Version", "active"),
                        "package_id": "DeployedRuntime",
                        "status": "DEPLOYED",
                        "modified_at": item.get("DeployedOn") or "Recently",
                        "adapters": self._guess_adapters(item.get("Id") or "")
                    })
        except Exception as e:
            logger.warning(f"Discovery Agent runtime fetch note: {e}")

        # Fetch Designtime Artifacts
        try:
            dt_url = f"{tenant_clean}/api/v1/IntegrationDesigntimeArtifacts?$format=json"
            req = urllib.request.Request(dt_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("d", {}).get("results", []):
                    art_id = item.get("Id")
                    if not any(a["id"] == art_id for a in artifacts):
                        artifacts.append({
                            "id": art_id,
                            "name": item.get("Name") or art_id,
                            "version": item.get("Version", "1.0.0"),
                            "package_id": item.get("PackageId", "DefaultPackage"),
                            "status": "DESIGNTIME",
                            "modified_at": item.get("ModifiedAt") or item.get("CreatedAt") or "Recently",
                            "adapters": self._guess_adapters(art_id)
                        })
        except Exception as e:
            logger.warning(f"Discovery Agent designtime fetch note: {e}")

        return artifacts

    def _guess_adapters(self, art_id: str) -> List[str]:
        id_lower = art_id.lower()
        adapters = []
        if "sftp" in id_lower or "file" in id_lower:
            adapters.append("SFTP")
        if "http" in id_lower or "rest" in id_lower:
            adapters.append("HTTPS")
        if "soap" in id_lower or "cxf" in id_lower or "wsdl" in id_lower:
            adapters.append("SOAP")
        if "odata" in id_lower or "s4" in id_lower:
            adapters.append("OData")
        if "idoc" in id_lower or "sap" in id_lower:
            adapters.append("IDoc")
        if "kafka" in id_lower or "amqp" in id_lower:
            adapters.append("AMQP/Kafka")
        if not adapters:
            adapters.append("HTTPS")
        return adapters

    def _get_sample_context(self) -> List[Dict[str, Any]]:
        return [
            {"id": "Horizon", "name": "Horizon Sales Order iFlow", "version": "1.0.2", "package_id": "CustomerPackage", "status": "DEPLOYED", "modified_at": "2026-08-20", "adapters": ["HTTPS", "OData", "Groovy"]},
            {"id": "Supernova", "name": "Supernova Payment Gateway", "version": "2.1.0", "package_id": "FinancePackage", "status": "DEPLOYED", "modified_at": "2026-08-22", "adapters": ["HTTPS", "REST", "XSD"]},
            {"id": "SFTP_Customer_Sync", "name": "Customer Master SFTP Ingestion", "version": "1.0.0", "package_id": "CustomerPackage", "status": "DEPLOYED", "modified_at": "2026-08-23", "adapters": ["SFTP", "IDoc", "Groovy"]},
            {"id": "SFTP_Vendor_Invoices", "name": "Vendor Invoice SFTP Batch", "version": "1.1.4", "package_id": "FinancePackage", "status": "DESIGNTIME", "modified_at": "2026-08-21", "adapters": ["SFTP", "SOAP"]},
            {"id": "S4HANA_Products_OData", "name": "S4HANA Products Catalogue Sync", "version": "3.0.1", "package_id": "CustomerPackage", "status": "DEPLOYED", "modified_at": "2026-08-24", "adapters": ["OData", "HTTPS"]}
        ]

    def _filter_context(self, query: str, artifacts: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], str]:
        q_lower = query.lower()

        # 1. Keyword / Adapter search e.g. SFTP, HTTPS, SOAP, OData, IDoc, Groovy
        adapter_keywords = ["sftp", "https", "http", "soap", "odata", "idoc", "groovy", "xslt", "kafka", "amqp"]
        matched_kw = [kw for kw in adapter_keywords if kw in q_lower]
        
        if matched_kw:
            target_kw = matched_kw[0].upper()
            filtered = [
                a for a in artifacts
                if target_kw in [ad.upper() for ad in a.get("adapters", [])] or target_kw in a["id"].upper() or target_kw in a["name"].upper()
            ]
            return filtered, f"filtering by protocol/adapter '{target_kw}'"

        # 2. Package search e.g. Customer, Finance, Logistics
        if "package" in q_lower or any(pkg in q_lower for pkg in ["customer", "finance", "logistics", "sales", "order"]):
            stop_words = {"what", "integrations", "are", "in", "the", "for", "package", "iflows", "show", "find", "me", "all", "which"}
            words = [w for w in re.findall(r'\b[a-zA-Z0-9_-]+\b', q_lower) if w not in stop_words]
            pkg_term = words[0] if words else "customer"
            filtered = [
                a for a in artifacts
                if pkg_term.lower() in a.get("package_id", "").lower() or pkg_term.lower() in a["name"].lower()
            ]
            return filtered, f"filtering by package term '{pkg_term}'"

        # 3. Deployed status search
        if "deploy" in q_lower or "live" in q_lower or "active" in q_lower:
            filtered = [a for a in artifacts if a.get("status", "").upper() == "DEPLOYED"]
            return filtered, "filtering for deployed runtime iFlows"

        # 4. Recently modified search
        if "recent" in q_lower or "modifi" in q_lower or "date" in q_lower or "latest" in q_lower:
            sorted_arts = sorted(artifacts, key=lambda x: str(x.get("modified_at", "")), reverse=True)
            return sorted_arts, "sorted by recent modification date"

        # General search match against ID, Name, Package
        terms = [t for t in q_lower.split() if len(t) > 2 and t not in ["find", "show", "what", "which", "all", "the", "iflows", "integrations", "are"]]
        if terms:
            filtered = [
                a for a in artifacts
                if any(t in a["id"].lower() or t in a["name"].lower() or t in a.get("package_id", "").lower() for t in terms)
            ]
            if filtered:
                return filtered, f"matching keywords '{', '.join(terms)}'"

        return artifacts, "displaying all tenant artifacts"

    def _synthesize_answer(self, query: str, results: List[Dict[str, Any]], reason: str, total_count: int) -> str:
        if not results:
            return f"No iFlows found matching your query '{query}' in the current SAP CPI tenant context ({total_count} total artifacts scanned)."

        names_str = ", ".join([f"**{r['id']}** ({r['name']})" for r in results[:5]])
        more_str = f" and {len(results) - 5} more" if len(results) > 5 else ""

        if self.api_key:
            try:
                prompt = f"""
You are the SAP CPI Discovery AI Agent.
Answer the user's question concisely based on the scanned CPI tenant context.

User Query: "{query}"
Filter Rule Applied: {reason}
Total Scanned Tenant Artifacts: {total_count}
Matched Artifacts ({len(results)} count):
{json.dumps(results[:10], indent=2)}

Provide a clear, 2-sentence conversational answer summarizing the results and key findings.
"""
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
                payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    return resp_data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as ex_ai:
                logger.warning(f"Discovery AI synthesis note: {ex_ai}")

        # Rule-based clean conversational answer
        return (
            f"Found **{len(results)}** iFlow(s) matching your query ({reason}): {names_str}{more_str}. "
            f"Click any iFlow row below to instantly analyze its BPMN flow and generate test payloads."
        )
