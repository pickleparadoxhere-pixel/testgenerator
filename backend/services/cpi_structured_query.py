import re
import json
import logging
import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class StructuredTimeRange:
    expression: str
    start_time: datetime.datetime
    end_time: datetime.datetime
    label: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expression": self.expression,
            "start_time_iso": self.start_time.isoformat(),
            "end_time_iso": self.end_time.isoformat(),
            "label": self.label
        }


def calculate_exact_time_range(expression: str) -> StructuredTimeRange:
    """
    Deterministically computes exact UTC start_time and end_time for any natural language relative date expression.
    Supports:
      - 'last hour', 'last 6 hours', 'last 24 hours' / 'last 24h' / 'past day', 'last 48 hours'
      - 'today', 'yesterday'
      - 'last 7 days' / 'this week', 'last week'
      - 'last 30 days' / 'this month', 'last month'
      - 'older than 30 days', 'recently'
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    expr_clean = expression.lower().strip()

    # 1. Exact Hours parsing (e.g. 'last 24 hours', 'last 6 hours', '24h', 'past 12 hours')
    hours_match = re.search(r'(?:last|past)\s+(\d+)\s*(?:hour|hours|h)\b', expr_clean)
    if not hours_match:
        hours_match = re.search(r'\b(\d+)\s*(?:hour|hours|h)\b', expr_clean)

    if hours_match:
        num_hours = int(hours_match.group(1))
        start = now - datetime.timedelta(hours=num_hours)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Last {num_hours} Hours ({start.strftime('%Y-%m-%d %H:%M')} to {now.strftime('%Y-%m-%d %H:%M')} UTC)"
        )

    # 2. Days / Past Day / Day Parsing
    days_match = re.search(r'(?:last|past)\s+(\d+)\s*(?:day|days|d)\b', expr_clean)
    if days_match:
        num_days = int(days_match.group(1))
        start = now - datetime.timedelta(days=num_days)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Last {num_days} Days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')} UTC)"
        )

    if "past day" in expr_clean or "last day" in expr_clean or "previous 24" in expr_clean:
        start = now - datetime.timedelta(hours=24)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Last 24 Hours ({start.strftime('%Y-%m-%d %H:%M')} to {now.strftime('%Y-%m-%d %H:%M')} UTC)"
        )

    if "today" in expr_clean:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Today ({start.strftime('%Y-%m-%d')} UTC)"
        )

    if "yesterday" in expr_clean:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - datetime.timedelta(days=1)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=end,
            label=f"Yesterday ({start.strftime('%Y-%m-%d')} UTC)"
        )

    if "last week" in expr_clean:
        end = now - datetime.timedelta(days=7)
        start = end - datetime.timedelta(days=7)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=end,
            label=f"Last Week ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')} UTC)"
        )

    if "this week" in expr_clean or "last 7 days" in expr_clean:
        start = now - datetime.timedelta(days=7)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Last 7 Days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')} UTC)"
        )

    if "last month" in expr_clean:
        end = now - datetime.timedelta(days=30)
        start = end - datetime.timedelta(days=30)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=end,
            label=f"Last Month ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')} UTC)"
        )

    if "this month" in expr_clean or "last 30 days" in expr_clean:
        start = now - datetime.timedelta(days=30)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Last 30 Days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')} UTC)"
        )

    if "older than 30 days" in expr_clean or "older than 1 month" in expr_clean:
        end = now - datetime.timedelta(days=30)
        start = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=end,
            label=f"Older than 30 Days (Before {end.strftime('%Y-%m-%d')})"
        )

    if "recently" in expr_clean or "recent" in expr_clean:
        start = now - datetime.timedelta(days=14)
        return StructuredTimeRange(
            expression=expression,
            start_time=start,
            end_time=now,
            label=f"Recently (Last 14 Days: {start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"
        )

    # Defaultfallback: last 7 days
    start = now - datetime.timedelta(days=7)
    return StructuredTimeRange(
        expression=expression or "last 7 days",
        start_time=start,
        end_time=now,
        label=f"Default (Last 7 Days: {start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"
    )


@dataclass
class CPIIntentQuery:
    domain: str        # MESSAGE_PROCESSING, CERTIFICATE, ARTIFACT_SEARCH, PACKAGE_SEARCH, TENANT_HEALTH
    operation: str     # COUNT, LIST, BREAKDOWN, RANKING, PERCENTAGE, TREND, HEALTH_REPORT
    time_range: StructuredTimeRange
    filters: Dict[str, Any] = field(default_factory=dict)
    group_by: Optional[str] = None
    sort_by: Optional[str] = None
    sort_order: str = "DESC"
    limit: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "operation": self.operation,
            "time_range": self.time_range.to_dict(),
            "filters": self.filters,
            "group_by": self.group_by,
            "sort_by": self.sort_by,
            "sort_order": self.sort_order,
            "limit": self.limit
        }


class CPIIntentClassifier:
    """Classifies natural language questions into formal CPIIntentQuery objects."""

    @staticmethod
    def classify(query_text: str) -> CPIIntentQuery:
        q_lower = query_text.lower().strip()

        # Extract Time Range
        time_range = calculate_exact_time_range(q_lower)

        # 1. Health Report Intent
        if any(h in q_lower for h in ["health", "worry", "attention", "top 5 problems", "tenant report"]):
            return CPIIntentQuery(
                domain="TENANT_HEALTH",
                operation="HEALTH_REPORT",
                time_range=time_range
            )

        # 2. Certificate Intent
        if any(c in q_lower for c in ["certificate", "keystore", "expiry", "expire", "expired"]):
            max_days = 30
            if "7 days" in q_lower or "critical" in q_lower:
                max_days = 7
            elif "expired" in q_lower:
                max_days = 0

            return CPIIntentQuery(
                domain="CERTIFICATE",
                operation="COUNT" if ("how many" in q_lower or "count" in q_lower) else "LIST",
                time_range=time_range,
                filters={"max_days_to_expiry": max_days}
            )

        # 3. Message Failure / Processing Log Intent
        if any(m in q_lower for m in ["failure", "failed", "error", "mpl", "messages", "trend", "rate"]):
            operation = "LIST"
            
            # Simple count query e.g. "how many failures in last 24 hours?"
            if any(cnt_word in q_lower for cnt_word in ["how many", "failure count", "count of", "number of failures"]):
                operation = "COUNT"
            elif "trend" in q_lower or "compare" in q_lower:
                operation = "TREND"
            elif "pattern" in q_lower or "common" in q_lower or "root cause" in q_lower:
                operation = "BREAKDOWN"
                group_by = "error_category"
            elif "rate" in q_lower or "percentage" in q_lower:
                operation = "PERCENTAGE"
                group_by = "iflow_name"
            elif "each" in q_lower or "breakdown" in q_lower or "top" in q_lower or "most" in q_lower:
                operation = "RANKING"
                group_by = "iflow_name"

            limit = None
            top_match = re.search(r'top\s+(\d+)', q_lower)
            if top_match:
                limit = int(top_match.group(1))

            # Check if specific iFlow name is mentioned
            filters = {"status": "FAILED"}
            for keyword in ["ordersync", "customersync", "invoicesync", "horizon", "supernova", "sftp_customer_sync"]:
                if keyword in q_lower:
                    filters["iflow_name"] = keyword

            # Check for adapter filter
            for adapter in ["sftp", "https", "soap", "odata", "idoc"]:
                if adapter in q_lower:
                    filters["adapter_type"] = adapter.upper()

            # Check for package filter
            for pkg in ["customer", "finance", "logistics"]:
                if pkg in q_lower:
                    filters["package_term"] = pkg

            return CPIIntentQuery(
                domain="MESSAGE_PROCESSING",
                operation=operation,
                time_range=time_range,
                filters=filters,
                group_by=filters.get("group_by", "iflow_name"),
                sort_by="failure_count",
                sort_order="DESC",
                limit=limit
            )

        # 4. Package Search Intent
        if "package" in q_lower and ("list" in q_lower or "show" in q_lower or "what" in q_lower):
            pkg_term = None
            for pkg in ["customer", "finance", "logistics", "sales"]:
                if pkg in q_lower:
                    pkg_term = pkg
            return CPIIntentQuery(
                domain="PACKAGE_SEARCH",
                operation="LIST",
                time_range=time_range,
                filters={"search_term": pkg_term} if pkg_term else {}
            )

        # 5. Artifact Search Intent (Fallback for genuine artifact queries)
        filters = {}
        for adapter in ["sftp", "https", "soap", "odata", "idoc"]:
            if adapter in q_lower:
                filters["adapter_type"] = adapter.upper()

        for pkg in ["customer", "finance", "logistics"]:
            if pkg in q_lower:
                filters["package_term"] = pkg

        if "not deployed" in q_lower or "design time" in q_lower:
            filters["is_deployed"] = False
        elif "deploy" in q_lower or "running" in q_lower:
            filters["is_deployed"] = True

        return CPIIntentQuery(
            domain="ARTIFACT_SEARCH",
            operation="LIST",
            time_range=time_range,
            filters=filters
        )
