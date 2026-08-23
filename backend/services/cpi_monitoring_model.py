import os
import re
import json
import logging
import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def parse_relative_date_range(expression: str) -> tuple[datetime.datetime, datetime.datetime, str]:
    """
    Converts relative date expressions into concrete UTC bounds (start_time, end_time, label).
    Supported: 'today', 'yesterday', 'last 24 hours', 'last 7 days', 'last 30 days',
               'this week', 'last week', 'this month', 'last month', 'older than 30 days'.
    """
    expr_clean = expression.lower().strip()
    now = datetime.datetime.now(datetime.timezone.utc)

    if "24 hours" in expr_clean or "last 24h" in expr_clean:
        start = now - datetime.timedelta(hours=24)
        return start, now, f"Last 24 Hours ({start.strftime('%Y-%m-%d %H:%M')} to {now.strftime('%Y-%m-%d %H:%M')} UTC)"

    if "today" in expr_clean:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, f"Today ({start.strftime('%Y-%m-%d')})"

    if "yesterday" in expr_clean:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - datetime.timedelta(days=1)
        return start, end, f"Yesterday ({start.strftime('%Y-%m-%d')})"

    if "7 days" in expr_clean or "last week" in expr_clean or "this week" in expr_clean:
        start = now - datetime.timedelta(days=7)
        return start, now, f"Last 7 Days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"

    if "30 days" in expr_clean or "month" in expr_clean:
        start = now - datetime.timedelta(days=30)
        return start, now, f"Last 30 Days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"

    if "older than 30 days" in expr_clean or "older than 1 month" in expr_clean:
        end = now - datetime.timedelta(days=30)
        start = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
        return start, end, f"Older than 30 Days (Before {end.strftime('%Y-%m-%d')})"

    # Default to 7 days if unspecified
    start = now - datetime.timedelta(days=7)
    return start, now, f"Last 7 Days ({start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')})"


def parse_iso_dt(date_val: Any) -> Optional[datetime.datetime]:
    if not date_val or not isinstance(date_val, str):
        return None
    
    # OData /Date(1700000000000)/ format
    odata_match = re.search(r'/Date\((\d+)\)/', date_val)
    if odata_match:
        ts_ms = int(odata_match.group(1))
        return datetime.datetime.fromtimestamp(ts_ms / 1000.0, tz=datetime.timezone.utc)

    try:
        clean_str = date_val.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        try:
            dt = datetime.datetime.strptime(date_val[:10], "%Y-%m-%d")
            return dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            return None


class CPIMonitoringModel:
    """
    Normalized data model for SAP CPI Message Processing Logs (MPL)
    and Security Keystore Entries / Certificates.
    """

    def __init__(self, raw_mpl: List[Dict[str, Any]], raw_keystore: List[Dict[str, Any]]):
        self.mpl_records = self._normalize_mpl(raw_mpl)
        self.keystore_entries = self._normalize_keystore(raw_keystore)

    def _normalize_mpl(self, raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for item in raw_list:
            guid = item.get("MessageGuid") or item.get("id") or ""
            if not guid:
                continue
            
            status = (item.get("Status") or item.get("status") or "COMPLETED").upper()
            iflow = item.get("IntegrationFlowName") or item.get("IntegrationArtifact") or item.get("iflow_name") or "Unknown_iFlow"
            
            log_start_raw = item.get("LogStart") or item.get("log_start") or item.get("LogEnd") or ""
            log_dt = parse_iso_dt(log_start_raw) or datetime.datetime.now(datetime.timezone.utc)
            
            err_text = item.get("ErrorProlog") or item.get("LastError") or item.get("error_message") or ""
            
            normalized.append({
                "message_guid": guid,
                "correlation_id": item.get("CorrelationId") or "",
                "iflow_name": iflow,
                "status": status,  # COMPLETED, FAILED, PROCESSING, DISPATCHED, ESCALATED
                "log_start": log_dt.isoformat(),
                "log_dt": log_dt,
                "sender": item.get("Sender") or "",
                "receiver": item.get("Receiver") or "",
                "error_message": err_text,
                "error_category": self._categorize_error(err_text, status)
            })
        return normalized

    def _normalize_keystore(self, raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        now = datetime.datetime.now(datetime.timezone.utc)

        for k in raw_list:
            alias = k.get("Alias") or k.get("alias") or k.get("Id") or ""
            if not alias:
                continue

            valid_until_raw = k.get("ValidUntil") or k.get("NotAfter") or k.get("valid_until") or ""
            exp_dt = parse_iso_dt(valid_until_raw)
            
            days_remaining = None
            risk_status = "OK"  # OK, WARNING, CRITICAL, EXPIRED

            if exp_dt:
                delta = exp_dt - now
                days_remaining = delta.days
                if days_remaining <= 0:
                    risk_status = "EXPIRED"
                elif days_remaining <= 7:
                    risk_status = "CRITICAL"
                elif days_remaining <= 30:
                    risk_status = "WARNING"

            normalized.append({
                "alias": alias,
                "type": k.get("Type") or k.get("type") or "KeyPair",
                "owner": k.get("Owner") or k.get("owner") or "SAP CPI Tenant",
                "valid_until": exp_dt.isoformat() if exp_dt else "Unknown",
                "days_remaining": days_remaining if days_remaining is not None else 999,
                "risk_status": risk_status,
                "subject_dn": k.get("SubjectDN") or "",
                "issuer_dn": k.get("IssuerDN") or ""
            })

        return sorted(normalized, key=lambda x: x["days_remaining"])

    def _categorize_error(self, err_text: str, status: str) -> str:
        if status != "FAILED":
            return "SUCCESS"

        err_lower = err_text.lower()
        if "sftp" in err_lower or "ssh" in err_lower or "host key" in err_lower:
            return "SFTP Authentication / Host Error"
        if "mapping" in err_lower or "xslt" in err_lower or "xml" in err_lower:
            return "Payload Mapping Error"
        if "500" in err_lower or "internal server" in err_lower:
            return "HTTP 500 Internal Server Error"
        if "401" in err_lower or "403" in err_lower or "unauthorized" in err_lower:
            return "HTTP 401/403 Authentication Error"
        if "timeout" in err_lower or "connection refused" in err_lower:
            return "Network Timeout / Connection Refused"
        if "script" in err_lower or "groovy" in err_lower:
            return "Groovy Script Exception"
        
        return "General Runtime Exception"
