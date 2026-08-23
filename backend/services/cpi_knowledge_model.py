import os
import re
import json
import logging
import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def parse_relative_date_expression(expression: str) -> tuple[Optional[datetime.datetime], Optional[datetime.datetime], str]:
    """
    Parses natural language relative date expressions into concrete (start_date, end_date) UTC bounds.
    Supported: 'recently', 'today', 'yesterday', 'last 7 days', 'last 30 days', 'this month',
               'last month', 'older than 6 months', etc.
    """
    expr_clean = expression.lower().strip()
    now = datetime.datetime.now(datetime.timezone.utc)
    
    if "today" in expr_clean:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, f"Today ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"

    if "yesterday" in expr_clean:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - datetime.timedelta(days=1)
        return start, end, f"Yesterday ({start.strftime('%Y-%m-%d')})"

    if "7 days" in expr_clean or "week" in expr_clean:
        start = now - datetime.timedelta(days=7)
        return start, now, f"Last 7 days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"

    if "30 days" in expr_clean or "month" in expr_clean and "last month" not in expr_clean and "older" not in expr_clean:
        start = now - datetime.timedelta(days=30)
        return start, now, f"Last 30 days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"

    if "last month" in expr_clean:
        first_day_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_prev_month = first_day_this_month - datetime.timedelta(seconds=1)
        first_day_prev_month = last_day_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return first_day_prev_month, last_day_prev_month, f"Last Month ({first_day_prev_month.strftime('%Y-%m-%d')} to {last_day_prev_month.strftime('%Y-%m-%d')})"

    if "6 months" in expr_clean or "older than 6 months" in expr_clean:
        end = now - datetime.timedelta(days=180)
        return None, end, f"Older than 6 months (Before {end.strftime('%Y-%m-%d')})"

    if "recent" in expr_clean:
        start = now - datetime.timedelta(days=14)
        return start, now, f"Recently (Last 14 days: {start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"

    return None, None, "Unrestricted Date Range"


def parse_iso_datetime(date_str: Any) -> Optional[datetime.datetime]:
    """Helper to parse OData ISO or timestamp format into timezone-aware datetime."""
    if not date_str or not isinstance(date_str, str):
        return None
    
    # Handle OData /Date(1700000000000)/ format
    odata_match = re.search(r'/Date\((\d+)\)/', date_str)
    if odata_match:
        ts_ms = int(odata_match.group(1))
        return datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz=datetime.timezone.utc)

    # Standard ISO string parsing
    try:
        clean_str = date_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        # Date string only (e.g. 2026-08-20)
        try:
            dt = datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            return None


class CPINormalizedModel:
    """
    Normalized internal CPI knowledge model correlating design-time artifacts,
    packages, runtime deployments, and service endpoints.
    """

    def __init__(self, raw_packages: List[Dict[str, Any]], raw_designtime: List[Dict[str, Any]], raw_runtime: List[Dict[str, Any]], raw_endpoints: List[Dict[str, Any]]):
        self.packages = self._normalize_packages(raw_packages)
        self.designtime = self._normalize_designtime(raw_designtime)
        self.runtime = self._normalize_runtime(raw_runtime)
        self.endpoints = self._normalize_endpoints(raw_endpoints)
        self.correlated = self._correlate()

    def _normalize_packages(self, raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for p in raw_list:
            pid = p.get("Id") or p.get("id") or ""
            if not pid:
                continue
            normalized.append({
                "id": pid,
                "name": p.get("Name") or p.get("name") or pid,
                "version": p.get("Version") or p.get("version") or "1.0.0",
                "description": p.get("Description") or p.get("ShortText") or p.get("description") or "",
                "created_at": p.get("CreatedAt") or p.get("created_at") or "",
                "modified_at": p.get("ModifiedAt") or p.get("modified_at") or "",
                "created_by": p.get("CreatedBy") or "",
                "vendor": p.get("Vendor") or ""
            })
        return normalized

    def _normalize_designtime(self, raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for a in raw_list:
            aid = a.get("Id") or a.get("id") or ""
            if not aid:
                continue
            atype = a.get("ArtifactType") or a.get("type") or ("ValueMapping" if "valuemapping" in aid.lower() else "IntegrationFlow")
            normalized.append({
                "id": aid,
                "name": a.get("Name") or a.get("name") or aid,
                "version": a.get("Version") or a.get("version") or "1.0.0",
                "type": atype,
                "package_id": a.get("PackageId") or a.get("package_id") or "DefaultPackage",
                "description": a.get("Description") or a.get("description") or "",
                "created_at": a.get("CreatedAt") or a.get("created_at") or "",
                "modified_at": a.get("ModifiedAt") or a.get("modified_at") or "",
                "adapters": a.get("adapters") or self._detect_adapters(aid)
            })
        return normalized

    def _normalize_runtime(self, raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for r in raw_list:
            rid = r.get("Id") or r.get("id") or ""
            if not rid:
                continue
            normalized.append({
                "id": rid,
                "name": r.get("Name") or r.get("name") or rid,
                "version": r.get("Version") or r.get("version") or "active",
                "status": (r.get("Status") or r.get("status") or "DEPLOYED").upper(),
                "deployed_on": r.get("DeployedOn") or r.get("deployed_on") or r.get("DeployedOnDate") or "",
                "deployed_by": r.get("DeployedBy") or r.get("deployed_by") or "",
                "type": r.get("Type") or r.get("type") or "IntegrationFlow"
            })
        return normalized

    def _normalize_endpoints(self, raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for e in raw_list:
            eid = e.get("Id") or e.get("id") or ""
            if not eid:
                continue
            normalized.append({
                "id": eid,
                "name": e.get("Name") or eid,
                "protocol": e.get("Protocol") or "HTTPS",
                "url": e.get("Url") or e.get("endpointAddress") or ""
            })
        return normalized

    def _detect_adapters(self, art_id: str) -> List[str]:
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

    def _correlate(self) -> List[Dict[str, Any]]:
        """Correlates Design-Time artifacts with Runtime deployment status and endpoints."""
        correlated_map = {}

        # 1. Map Design-Time artifacts
        for dt in self.designtime:
            aid = dt["id"]
            pkg_obj = next((p for p in self.packages if p["id"].lower() == dt["package_id"].lower()), None)
            pkg_name = pkg_obj["name"] if pkg_obj else dt["package_id"]

            correlated_map[aid.lower()] = {
                "id": aid,
                "name": dt["name"],
                "version": dt["version"],
                "type": dt["type"],
                "package_id": dt["package_id"],
                "package_name": pkg_name,
                "description": dt["description"],
                "created_at": dt["created_at"],
                "modified_at": dt["modified_at"],
                "has_designtime": True,
                "is_deployed": False,
                "runtime_status": "NOT_DEPLOYED",
                "deployed_on": "",
                "adapters": dt["adapters"],
                "endpoints": []
            }

        # 2. Correlate Runtime deployment status
        for rt in self.runtime:
            rid = rt["id"].lower()
            if rid in correlated_map:
                correlated_map[rid]["is_deployed"] = True
                correlated_map[rid]["runtime_status"] = rt["status"]
                correlated_map[rid]["deployed_on"] = rt["deployed_on"]
                if rt.get("version") and rt["version"] != "active":
                    correlated_map[rid]["runtime_version"] = rt["version"]
            else:
                # Deployed artifact without corresponding designtime in map
                correlated_map[rid] = {
                    "id": rt["id"],
                    "name": rt["name"],
                    "version": rt["version"],
                    "type": rt["type"],
                    "package_id": "OrphanRuntime",
                    "package_name": "Orphan Runtime",
                    "description": "Deployed runtime artifact without design-time package match",
                    "created_at": "",
                    "modified_at": "",
                    "has_designtime": False,
                    "is_deployed": True,
                    "runtime_status": rt["status"],
                    "deployed_on": rt["deployed_on"],
                    "adapters": self._detect_adapters(rt["id"]),
                    "endpoints": []
                }

        # 3. Correlate Endpoints
        for ep in self.endpoints:
            epid = ep["id"].lower()
            for key, obj in correlated_map.items():
                if key in epid or epid in key:
                    if ep["url"] and ep["url"] not in obj["endpoints"]:
                        obj["endpoints"].append(ep["url"])

        return list(correlated_map.values())
