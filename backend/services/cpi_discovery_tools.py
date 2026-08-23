import logging
import datetime
from typing import Dict, Any, List, Optional
from backend.services.cpi_knowledge_model import CPINormalizedModel, parse_relative_date_expression, parse_iso_datetime

logger = logging.getLogger(__name__)

# CPI Tool Definitions & Schemas for LLM Reasoning
CPI_TOOLS_SCHEMA = [
    {
        "name": "list_packages",
        "description": "Returns CPI integration packages including package ID, name, description, version, and modified date. Use this tool when the user asks about CPI integration packages, package counts, or package lists.",
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {"type": "string", "description": "Optional keyword term to filter package name or ID (e.g. 'Customer', 'Finance')"}
            }
        }
    },
    {
        "name": "list_designtime_artifacts",
        "description": "Returns CPI design-time artifacts including artifact ID, name, artifact type (IntegrationFlow, ValueMapping, ScriptCollection), package association, version, and modified timestamp.",
        "parameters": {
            "type": "object",
            "properties": {
                "package_id": {"type": "string", "description": "Filter artifacts belonging to a specific package ID or package name"},
                "artifact_type": {"type": "string", "description": "Filter by artifact type: 'IntegrationFlow', 'ValueMapping', or 'ScriptCollection'"}
            }
        }
    },
    {
        "name": "list_runtime_artifacts",
        "description": "Returns currently deployed CPI runtime artifacts including runtime status (STARTED, STOPPED, DEPLOYED), deployed timestamp, and runtime version.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Optional runtime status filter: 'STARTED', 'STOPPED', 'DEPLOYED'"}
            }
        }
    },
    {
        "name": "get_artifact_details",
        "description": "Returns comprehensive correlated design-time and runtime details for a specific artifact by artifact ID or name.",
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "The unique ID or name of the artifact to inspect"}
            },
            "required": ["artifact_id"]
        }
    },
    {
        "name": "filter_and_correlate_artifacts",
        "description": "Multi-attribute analytical filtering engine. Filters and correlates CPI artifacts by adapter/protocol (SFTP, HTTPS, SOAP, OData, IDoc), package ID/name, deployment status, artifact type, and relative or concrete date bounds.",
        "parameters": {
            "type": "object",
            "properties": {
                "adapter_type": {"type": "string", "description": "Adapter/protocol filter: 'SFTP', 'HTTPS', 'SOAP', 'OData', 'IDoc', 'REST'"},
                "package_term": {"type": "string", "description": "Package ID or name term filter (e.g. 'Customer', 'Finance')"},
                "is_deployed": {"type": "boolean", "description": "Set True for deployed artifacts, False for un-deployed design-time artifacts"},
                "artifact_type": {"type": "string", "description": "Artifact type: 'IntegrationFlow', 'ValueMapping', 'ScriptCollection'"},
                "date_expression": {"type": "string", "description": "Natural language date term: 'recently', 'today', 'yesterday', 'last 7 days', 'last 30 days', 'this month', 'last month', 'older than 6 months'"},
                "limit": {"type": "integer", "description": "Maximum results limit"}
            }
        }
    },
    {
        "name": "aggregate_tenant_metrics",
        "description": "Computes aggregated tenant analytics such as packages with the highest number of integration flows, packages with more than N iFlows, deployed vs un-deployed artifact counts, or version counts.",
        "parameters": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "description": "Aggregation dimension: 'package', 'deployment_status', 'artifact_type'"},
                "min_count": {"type": "integer", "description": "Filter packages/groups having count greater than or equal to min_count"}
            }
        }
    }
]


class CPIDiscoveryToolRegistry:
    """Executes granular analytical discovery queries against the normalized CPI knowledge model."""

    def __init__(self, model: CPINormalizedModel):
        self.model = model

    def list_packages(self, search_term: str = None) -> List[Dict[str, Any]]:
        pkgs = self.model.packages
        if search_term:
            st = search_term.lower().strip()
            pkgs = [p for p in pkgs if st in p["id"].lower() or st in p["name"].lower() or st in p["description"].lower()]
        return pkgs

    def list_designtime_artifacts(self, package_id: str = None, artifact_type: str = None) -> List[Dict[str, Any]]:
        arts = self.model.designtime
        if package_id:
            pid = package_id.lower().strip()
            arts = [a for a in arts if pid in a["package_id"].lower() or pid in a["name"].lower()]
        if artifact_type:
            at = artifact_type.lower().strip()
            arts = [a for a in arts if at in a["type"].lower()]
        return arts

    def list_runtime_artifacts(self, status: str = None) -> List[Dict[str, Any]]:
        rts = self.model.runtime
        if status:
            st = status.upper().strip()
            rts = [r for r in rts if r["status"] == st]
        return rts

    def get_artifact_details(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        aid = artifact_id.lower().strip()
        for item in self.model.correlated:
            if item["id"].lower() == aid or aid in item["name"].lower():
                return item
        return None

    def filter_and_correlate_artifacts(
        self,
        adapter_type: str = None,
        package_term: str = None,
        is_deployed: Optional[bool] = None,
        artifact_type: str = None,
        date_expression: str = None,
        limit: int = 100
    ) -> tuple[List[Dict[str, Any]], str]:
        results = list(self.model.correlated)
        filters_applied = []

        # 1. Adapter / Protocol filter
        if adapter_type:
            ad_target = adapter_type.upper().strip()
            results = [
                r for r in results
                if any(ad_target == ad.upper() for ad in r.get("adapters", [])) or ad_target in r["id"].upper() or ad_target in r["name"].upper()
            ]
            filters_applied.append(f"Adapter/Protocol = '{ad_target}'")

        # 2. Package Term filter
        if package_term:
            pt = package_term.lower().strip()
            results = [
                r for r in results
                if pt in r["package_id"].lower() or pt in r["package_name"].lower()
            ]
            filters_applied.append(f"Package = '{package_term}'")

        # 3. Deployment Status filter
        if is_deployed is not None:
            results = [r for r in results if r["is_deployed"] == is_deployed]
            status_str = "Currently Deployed" if is_deployed else "Not Deployed (Design-Time Only)"
            filters_applied.append(f"Deployment Status = '{status_str}'")

        # 4. Artifact Type filter
        if artifact_type:
            at = artifact_type.lower().strip()
            results = [r for r in results if at in r["type"].lower()]
            filters_applied.append(f"Artifact Type = '{artifact_type}'")

        # 5. Date Expression filter
        if date_expression:
            start_dt, end_dt, date_desc = parse_relative_date_expression(date_expression)
            filters_applied.append(f"Date Filter = {date_desc}")

            filtered_by_date = []
            for r in results:
                raw_date = r.get("modified_at") or r.get("created_at") or r.get("deployed_on")
                item_dt = parse_iso_datetime(raw_date)
                if not item_dt:
                    continue
                if start_dt and item_dt < start_dt:
                    continue
                if end_dt and item_dt > end_dt:
                    continue
                filtered_by_date.append(r)
            results = filtered_by_date

        summary = "; ".join(filters_applied) if filters_applied else "All Correlated Artifacts"
        return results[:limit], summary

    def aggregate_tenant_metrics(self, group_by: str = "package", min_count: int = 0) -> Dict[str, Any]:
        if group_by == "package":
            counts: Dict[str, int] = {}
            for r in self.model.correlated:
                pkg_name = r.get("package_name") or r.get("package_id") or "UnknownPackage"
                counts[pkg_name] = counts.get(pkg_name, 0) + 1

            if min_count > 0:
                counts = {k: v for k, v in counts.items() if v >= min_count}

            sorted_counts = dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))
            return {
                "dimension": "Package Artifact Counts",
                "counts": sorted_counts,
                "total_packages": len(sorted_counts)
            }

        if group_by == "deployment_status":
            deployed_count = sum(1 for r in self.model.correlated if r["is_deployed"])
            not_deployed_count = sum(1 for r in self.model.correlated if not r["is_deployed"])
            return {
                "dimension": "Deployment Status Counts",
                "counts": {"Deployed": deployed_count, "Not Deployed": not_deployed_count},
                "total_artifacts": len(self.model.correlated)
            }

        return {"dimension": "General Tenant Summary", "total_artifacts": len(self.model.correlated)}
