import logging
import datetime
from typing import Dict, Any, List, Optional
from backend.services.cpi_monitoring_model import CPIMonitoringModel, parse_relative_date_range

logger = logging.getLogger(__name__)

# CPI Monitoring Tool Definitions & Schemas for LLM Reasoning
CPI_MONITORING_TOOLS_SCHEMA = [
    {
        "name": "search_mpl",
        "description": "Searches SAP CPI Message Processing Logs (MPL) by iFlow name, execution status (FAILED, COMPLETED), or relative date range (e.g. 'today', 'last 24 hours', 'last 7 days').",
        "parameters": {
            "type": "object",
            "properties": {
                "iflow_name": {"type": "string", "description": "Filter logs for a specific iFlow name or keyword"},
                "status": {"type": "string", "description": "Filter logs by execution status: 'FAILED' or 'COMPLETED'"},
                "date_expression": {"type": "string", "description": "Relative date expression: 'today', 'last 24 hours', 'last 7 days', 'last 30 days'"}
            }
        }
    },
    {
        "name": "get_failure_statistics",
        "description": "Computes message volume metrics including total messages, successful messages, failed messages, failure rates (%), and highest failure iFlows over a specified date range.",
        "parameters": {
            "type": "object",
            "properties": {
                "iflow_name": {"type": "string", "description": "Optional iFlow name filter"},
                "date_expression": {"type": "string", "description": "Relative date range: 'today', 'last 24 hours', 'last 7 days', 'last 30 days'"},
                "min_failure_rate": {"type": "number", "description": "Filter iFlows having a failure rate percentage greater than min_failure_rate (e.g. 10.0 for > 10%)"}
            }
        }
    },
    {
        "name": "analyze_error_patterns",
        "description": "Analyzes failed Message Processing Logs and groups errors by category (e.g., SFTP Auth Error: 24, Mapping Error: 11, HTTP 500: 7). Identifies top root cause categories.",
        "parameters": {
            "type": "object",
            "properties": {
                "date_expression": {"type": "string", "description": "Relative date range: 'today', 'last 24 hours', 'last 7 days'"}
            }
        }
    },
    {
        "name": "get_keystore_entries",
        "description": "Queries SAP CPI Security Keystore entries and certificates. Returns days remaining until expiration, risk status (CRITICAL <= 7 days, WARNING <= 30 days, EXPIRED <= 0 days), and subject/issuer info.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_days_to_expiry": {"type": "integer", "description": "Filter certificates expiring within N days (e.g. 7 or 30)"},
                "risk_status": {"type": "string", "description": "Filter by risk level: 'CRITICAL', 'WARNING', 'EXPIRED', 'OK'"}
            }
        }
    },
    {
        "name": "compare_failure_trends",
        "description": "Compares current period message failures against previous period message failures (e.g. this week vs last week) and calculates percentage change and trend assessment.",
        "parameters": {
            "type": "object",
            "properties": {
                "iflow_name": {"type": "string", "description": "Optional iFlow name to evaluate specific trend for"}
            }
        }
    },
    {
        "name": "generate_tenant_health_report",
        "description": "Generates a high-level CPI Tenant Health Summary report compiling Critical items (🔴 expiring certs <= 7 days, failure rate > 20%), Warning items (🟠 failure rate 5-20%, certs expiring <= 30 days), and Healthy items.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
]


class CPIMonitoringToolRegistry:
    """Executes analytical monitoring and health evaluation queries over the normalized CPI monitoring model."""

    def __init__(self, model: CPIMonitoringModel):
        self.model = model

    def search_mpl(self, iflow_name: str = None, status: str = None, date_expression: str = "last 7 days") -> List[Dict[str, Any]]:
        start_dt, end_dt, label = parse_relative_date_range(date_expression)
        logs = self.model.mpl_records

        # Filter by Date
        logs = [l for l in logs if start_dt <= l["log_dt"] <= end_dt]

        # Filter by iFlow name
        if iflow_name:
            if_lower = iflow_name.lower().strip()
            logs = [l for l in logs if if_lower in l["iflow_name"].lower()]

        # Filter by Status
        if status:
            st = status.upper().strip()
            logs = [l for l in logs if l["status"] == st]

        return logs

    def get_failure_statistics(self, iflow_name: str = None, date_expression: str = "last 7 days", min_failure_rate: float = 0.0) -> Dict[str, Any]:
        logs = self.search_mpl(iflow_name=iflow_name, date_expression=date_expression)
        
        total_msgs = len(logs)
        failed_msgs = sum(1 for l in logs if l["status"] == "FAILED")
        success_msgs = total_msgs - failed_msgs
        overall_failure_rate = (failed_msgs / total_msgs * 100.0) if total_msgs > 0 else 0.0

        # Group by iFlow
        iflow_stats: Dict[str, Dict[str, Any]] = {}
        for l in logs:
            ifname = l["iflow_name"]
            if ifname not in iflow_stats:
                iflow_stats[ifname] = {"total": 0, "failed": 0, "success": 0}
            iflow_stats[ifname]["total"] += 1
            if l["status"] == "FAILED":
                iflow_stats[ifname]["failed"] += 1
            else:
                iflow_stats[ifname]["success"] += 1

        top_failing_iflows = []
        for ifname, stats in iflow_stats.items():
            tot = stats["total"]
            fail = stats["failed"]
            rate = (fail / tot * 100.0) if tot > 0 else 0.0
            if rate >= min_failure_rate:
                top_failing_iflows.append({
                    "iflow_name": ifname,
                    "total": tot,
                    "failed": fail,
                    "success": stats["success"],
                    "failure_rate": round(rate, 2)
                })

        top_failing_iflows = sorted(top_failing_iflows, key=lambda x: (x["failed"], x["failure_rate"]), reverse=True)

        return {
            "period": date_expression,
            "total_messages": total_msgs,
            "successful_messages": success_msgs,
            "failed_messages": failed_msgs,
            "overall_failure_rate_pct": round(overall_failure_rate, 2),
            "iflow_breakdown": top_failing_iflows
        }

    def analyze_error_patterns(self, date_expression: str = "last 7 days") -> Dict[str, Any]:
        failed_logs = self.search_mpl(status="FAILED", date_expression=date_expression)
        
        pattern_counts: Dict[str, int] = {}
        for l in failed_logs:
            cat = l["error_category"]
            pattern_counts[cat] = pattern_counts.get(cat, 0) + 1

        sorted_patterns = [
            {"category": cat, "failure_count": cnt}
            for cat, cnt in sorted(pattern_counts.items(), key=lambda item: item[1], reverse=True)
        ]

        return {
            "period": date_expression,
            "total_failures_analyzed": len(failed_logs),
            "error_patterns": sorted_patterns
        }

    def get_keystore_entries(self, max_days_to_expiry: Optional[int] = None, risk_status: str = None) -> List[Dict[str, Any]]:
        certs = list(self.model.keystore_entries)

        if max_days_to_expiry is not None:
            certs = [c for c in certs if c["days_remaining"] <= max_days_to_expiry]

        if risk_status:
            rs = risk_status.upper().strip()
            certs = [c for c in certs if c["risk_status"] == rs]

        return certs

    def compare_failure_trends(self, iflow_name: str = None) -> Dict[str, Any]:
        logs_current = self.search_mpl(iflow_name=iflow_name, date_expression="last 7 days")
        logs_previous = self.search_mpl(iflow_name=iflow_name, date_expression="last 30 days")

        current_fails = sum(1 for l in logs_current if l["status"] == "FAILED")
        # Subtract current 7 days from last 30 days to get previous period
        previous_fails = max(0, sum(1 for l in logs_previous if l["status"] == "FAILED") - current_fails)

        pct_change = 0.0
        if previous_fails > 0:
            pct_change = ((current_fails - previous_fails) / previous_fails) * 100.0

        assessment = "Stable failure rate"
        if pct_change > 20.0:
            assessment = f"Significant increase in failures (+{round(pct_change, 1)}%)"
        elif pct_change < -20.0:
            assessment = f"Significant reduction in failures ({round(pct_change, 1)}%)"

        return {
            "iflow_target": iflow_name or "Tenant-wide",
            "current_period_failures": current_fails,
            "previous_period_failures": previous_fails,
            "percentage_change": round(pct_change, 1),
            "assessment": assessment
        }

    def generate_tenant_health_report(self) -> Dict[str, Any]:
        stats_today = self.get_failure_statistics(date_expression="today")
        stats_week = self.get_failure_statistics(date_expression="last 7 days")
        expiring_critical_certs = self.get_keystore_entries(max_days_to_expiry=7)
        expiring_warning_certs = self.get_keystore_entries(max_days_to_expiry=30)
        patterns = self.analyze_error_patterns(date_expression="today")

        critical_items = []
        warning_items = []
        healthy_items = []

        # Certs evaluation
        if expiring_critical_certs:
            critical_items.append(f"{len(expiring_critical_certs)} certificate(s) expire within 7 days (or expired)")
        elif expiring_warning_certs:
            warning_items.append(f"{len(expiring_warning_certs)} certificate(s) expire within 30 days")

        # High Failure Rate evaluation
        high_fail_iflows = [b for b in stats_week["iflow_breakdown"] if b["failure_rate"] >= 20.0]
        mod_fail_iflows = [b for b in stats_week["iflow_breakdown"] if 5.0 <= b["failure_rate"] < 20.0]

        if high_fail_iflows:
            critical_items.append(f"{len(high_fail_iflows)} integration(s) have >20% failure rate: " + ", ".join([f["iflow_name"] for f in high_fail_iflows[:3]]))
        if mod_fail_iflows:
            warning_items.append(f"{len(mod_fail_iflows)} integration(s) experienced 5-20% failure rate")

        if not critical_items and not warning_items:
            healthy_items.append("Runtime message processing & certificate health status normal")

        overall_status = "🔴 CRITICAL" if critical_items else ("🟠 WARNING" if warning_items else "🟢 HEALTHY")

        top_attention_items = []
        for c in expiring_critical_certs:
            top_attention_items.append(f"Certificate '{c['alias']}' - Expires in {c['days_remaining']} days ({c['risk_status']})")
        for h in high_fail_iflows:
            top_attention_items.append(f"iFlow '{h['iflow_name']}' - {h['failure_rate']}% failure rate ({h['failed']} failures)")

        return {
            "overall_status": overall_status,
            "critical_items": critical_items,
            "warning_items": warning_items,
            "healthy_items": healthy_items,
            "top_attention_items": top_attention_items[:5],
            "messages_today": stats_today["total_messages"],
            "failures_today": stats_today["failed_messages"],
            "error_patterns": patterns["error_patterns"][:3]
        }
