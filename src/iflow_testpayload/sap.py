from __future__ import annotations

import base64
import io
import json
import ssl
import time
import zipfile
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener, urlopen


MAX_ARTIFACT_BYTES = 100 * 1024 * 1024


class SapCpiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SapArtifact:
    id: str
    name: str
    version: str
    package_id: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "version": self.version, "package_id": self.package_id}


class SapCpiClient:
    """Small read-only client for SAP Cloud Integration design-time APIs."""

    def __init__(
        self,
        tenant_url: str,
        principal: str,
        secret: str,
        auth_type: str = "basic",
        token_url: str = "",
        timeout: int = 45,
    ):
        self.service_root = self._service_root(tenant_url)
        self.principal = principal
        self.secret = secret
        self.auth_type = auth_type.lower()
        self.token_url = self._normalize_token_url(token_url) if token_url else ""
        self.timeout = timeout
        self._bearer_token = ""
        if not principal or not secret:
            raise SapCpiError("Username/client ID and password/client secret are required")
        if self.auth_type not in {"basic", "oauth"}:
            raise SapCpiError("Authentication type must be basic or oauth")
        if self.auth_type == "oauth" and not self.token_url:
            raise SapCpiError("OAuth token URL is required for client-credentials authentication")

    @classmethod
    def _normalize_token_url(cls, value: str) -> str:
        normalized = cls._https_url(value, "OAuth token URL")
        parsed = urlsplit(normalized)
        path = parsed.path.rstrip("/")
        if not path or path == "/":
            path = "/oauth/token"
        elif not path.endswith("/oauth/token"):
            if path.endswith("/oauth"):
                path = f"{path}/token"
            else:
                path = f"{path}/oauth/token"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    @classmethod
    def _fetch_oauth_token(cls, token_url: str, client_id: str, client_secret: str, timeout: int = 45) -> str:
        client_id = client_id.strip().strip('"').strip("'")
        client_secret = client_secret.strip().strip('"').strip("'")
        token_url = token_url.strip().strip('"').strip("'")
        normalized_url = cls._normalize_token_url(token_url)

        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "iflow-testpayload/0.1",
        }
        body_data = urlencode({"grant_type": "client_credentials"}).encode("utf-8")
        request = Request(normalized_url, data=body_data, headers=headers, method="POST")
        ctx = ssl._create_unverified_context()

        try:
            with urlopen(request, timeout=timeout, context=ctx) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise cls._friendly_error(exc, f"OAuth token request failed for `{normalized_url}`") from exc
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise cls._friendly_error(exc, f"OAuth token request failed for `{normalized_url}`") from exc

        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise SapCpiError(f"OAuth token response from `{normalized_url}` did not contain access_token")
        return str(token)

    @classmethod
    def _service_root(cls, value: str) -> str:
        normalized = cls._https_url(value, "SAP CPI tenant URL")
        parsed = urlsplit(normalized)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/v1"):
            service_path = path
        elif path.endswith("/api"):
            service_path = f"{path}/v1"
        elif path in {"", "/"}:
            service_path = "/api/v1"
        else:
            raise SapCpiError("Tenant URL must be the tenant host or end with /api/v1")
        return urlunsplit((parsed.scheme, parsed.netloc, service_path, "", ""))

    @staticmethod
    def _https_url(value: str, label: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SapCpiError(f"{label} must be an HTTPS URL without embedded credentials")
        if parsed.query or parsed.fragment:
            raise SapCpiError(f"{label} must not contain a query string or fragment")
        return value

    def list_artifacts(self) -> list[SapArtifact]:
        query = urlencode({"$format": "json", "$select": "Id,Name,Version,PackageId", "$top": "500"})
        try:
            payload = self._request_json(f"{self.service_root}/IntegrationDesigntimeArtifacts?{query}")
            raw_results = self._odata_results(payload)
            artifacts = self._artifacts_from_results(raw_results)
        except SapCpiError as exc:
            if exc.status not in {404, 501}:
                raise
            try:
                artifacts = self._list_artifacts_by_package(exc)
            except SapCpiError:
                rt_payload = self._request_json(f"{self.service_root}/IntegrationRuntimeArtifacts?{query}")
                raw_results = self._odata_results(rt_payload)
                artifacts = self._artifacts_from_results(raw_results, "DeployedRuntime")
        return sorted(artifacts, key=lambda item: (item.name.lower(), item.version.lower()))

    @staticmethod
    def _odata_results(payload: dict) -> list:
        raw_results = payload.get("d", {}).get("results", []) if isinstance(payload.get("d"), dict) else payload.get("value", [])
        if not isinstance(raw_results, list):
            raise SapCpiError("SAP CPI returned an unexpected artifact-list response")
        return raw_results

    @staticmethod
    def _artifacts_from_results(raw_results: list, package_id: str = "") -> list[SapArtifact]:
        artifacts: list[SapArtifact] = []
        for item in raw_results:
            if not isinstance(item, dict) or not item.get("Id"):
                continue
            artifacts.append(
                SapArtifact(
                    id=str(item["Id"]),
                    name=str(item.get("Name") or item["Id"]),
                    version=str(item.get("Version") or "active"),
                    package_id=str(item.get("PackageId") or package_id),
                )
            )
        return artifacts

    def _list_artifacts_by_package(self, original_error: SapCpiError) -> list[SapArtifact]:
        package_query = urlencode({"$format": "json", "$select": "Id,Name", "$top": "200"})
        try:
            packages = self._odata_results(self._request_json(f"{self.service_root}/IntegrationPackages?{package_query}"))
            artifacts: list[SapArtifact] = []
            artifact_query = urlencode({"$format": "json", "$select": "Id,Name,Version", "$top": "500"})
            for package in packages:
                if not isinstance(package, dict) or not package.get("Id"):
                    continue
                package_id = str(package["Id"])
                escaped_package = quote(package_id.replace("'", "''"), safe="")
                url = f"{self.service_root}/IntegrationPackages('{escaped_package}')/IntegrationDesigntimeArtifacts?{artifact_query}"
                results = self._odata_results(self._request_json(url))
                artifacts.extend(self._artifacts_from_results(results, package_id))
            return artifacts
        except SapCpiError as fallback_error:
            raise SapCpiError(
                f"Global and package-scoped artifact listing both failed. API root: {self.service_root}. "
                f"Global error: {original_error}. Package fallback: {fallback_error}",
                fallback_error.status,
            ) from fallback_error

    def download_artifact(self, artifact_id: str, version: str = "active") -> bytes:
        if not artifact_id:
            raise SapCpiError("Artifact ID is required")
        target_version = version.strip() if version else "active"
        escaped_id = quote(artifact_id.replace("'", "''"), safe="")

        # 1. Query designtime metadata to discover actual Version string (commit 15a5f23)
        if target_version.lower() in {"active", ""}:
            try:
                info_url = f"{self.service_root}/IntegrationDesigntimeArtifacts(Id='{escaped_id}',Version='active')?$format=json"
                info_payload = self._request_json(info_url)
                discovered_ver = info_payload.get("d", {}).get("Version") or info_payload.get("value", {}).get("Version")
                if discovered_ver:
                    target_version = str(discovered_ver)
            except Exception as ex_ver:
                print(f"[SapCpiClient] Designtime Version discovery note for `{artifact_id}`: {ex_ver}", flush=True)

        escaped_version = quote(target_version.replace("'", "''"), safe="")
        url = f"{self.service_root}/IntegrationDesigntimeArtifacts(Id='{escaped_id}',Version='{escaped_version}')/$value"

        try:
            content = self._request(url, accept="application/zip, application/octet-stream")
        except SapCpiError as download_err:
            if target_version != "1.0.0":
                # Fallback to Version '1.0.0'
                fb_url = f"{self.service_root}/IntegrationDesigntimeArtifacts(Id='{escaped_id}',Version='1.0.0')/$value"
                try:
                    content = self._request(fb_url, accept="application/zip, application/octet-stream")
                except SapCpiError:
                    raise download_err from None
            else:
                raise download_err from None

        if len(content) > MAX_ARTIFACT_BYTES:
            raise SapCpiError(f"Artifact {artifact_id} exceeds the 100 MB analysis limit")
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise SapCpiError(f"SAP CPI did not return a ZIP for artifact {artifact_id} ({target_version})")
        return content

    def resolve_artifact(self, identifier_or_name: str, version: str) -> SapArtifact:
        requested = identifier_or_name.strip().casefold()
        candidates = [
            artifact
            for artifact in self.list_artifacts()
            if artifact.id.casefold() == requested or artifact.name.casefold() == requested
        ]
        if not candidates:
            raise SapCpiError(f"No IFlow found with ID or name `{identifier_or_name}`")
        version_matches = [artifact for artifact in candidates if artifact.version.casefold() == version.strip().casefold()]
        if version_matches:
            return version_matches[0]
        available = ", ".join(dict.fromkeys(artifact.version for artifact in candidates))
        raise SapCpiError(f"IFlow `{identifier_or_name}` was found, but version `{version}` was not. Available: {available}")

    def _authorization(self) -> str:
        if self.auth_type == "basic":
            token = base64.b64encode(f"{self.principal}:{self.secret}".encode()).decode()
            return f"Basic {token}"
        if not self._bearer_token:
            try:
                self._bearer_token = self._fetch_oauth_token(self.token_url, self.principal, self.secret, self.timeout)
                return f"Bearer {self._bearer_token}"
            except SapCpiError as oauth_error:
                self._oauth_error = oauth_error
                basic_token = base64.b64encode(f"{self.principal}:{self.secret}".encode()).decode()
                return f"Basic {basic_token}"
        return f"Bearer {self._bearer_token}"

    def _request_json(self, url: str) -> dict:
        content = self._request(url, accept="application/json")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SapCpiError("SAP CPI returned a non-JSON artifact-list response") from exc
        if not isinstance(payload, dict):
            raise SapCpiError("SAP CPI returned an unexpected JSON response")
        return payload

    def _request(self, url: str, accept: str) -> bytes:
        auth_header = self._authorization()
        request = Request(
            url,
            headers={"Authorization": auth_header, "Accept": accept, "User-Agent": "iflow-testpayload/0.1"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout, context=ssl.create_default_context()) as response:
                content = response.read(MAX_ARTIFACT_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            if hasattr(self, "_oauth_error") and self._oauth_error:
                raise SapCpiError(
                    f"Authentication failed via both OAuth and direct Basic Auth. "
                    f"1) OAuth token endpoint (`{self.token_url}`): {self._oauth_error}. "
                    f"2) Direct CPI OData endpoint (`{url}`): {self._friendly_error(exc, 'CPI OData GET failed')}"
                ) from exc
            raise self._friendly_error(exc, "SAP CPI request failed") from exc
        return content

    @staticmethod
    def _friendly_error(exc: BaseException, prefix: str) -> SapCpiError:
        if isinstance(exc, HTTPError):
            error_details = ""
            try:
                raw_err = exc.read().decode("utf-8", errors="ignore")
                if raw_err:
                    err_json = json.loads(raw_err)
                    if isinstance(err_json, dict) and "error_description" in err_json:
                        error_details = f": {err_json['error_description']}"
                    elif isinstance(err_json, dict) and "error" in err_json:
                        error_details = f": {err_json['error']}"
                    else:
                        error_details = f": {raw_err[:150]}"
            except Exception:
                pass

            if "OAuth token" in prefix:
                return SapCpiError(
                    f"{prefix} (HTTP {exc.code}{error_details}). Verify clientid, clientsecret, and tokenurl from your BTP Service Key JSON.",
                    exc.code,
                )
            messages = {
                401: "authentication failed - verify OAuth credentials or Basic auth username/password",
                403: (
                    "API permission denied (HTTP 403). "
                    "Troubleshooting checklist: "
                    "1) BTP Service Key Plan: Ensure the service key was generated under the 'api' (Process Integration API) service plan. Runtime service keys ('integration-flow' / 'it-rt') only have messaging access (ESBMessaging.send) and cannot access design-time OData APIs. "
                    "2) Host URL: Ensure Tenant URL is the '-api' host URL (e.g. https://<tenant>-api.cfapps.<region>.hana.ondemand.com). "
                    "3) Roles: BTP Cockpit User role collections (e.g. AuthGroup_IntegrationDeveloper) do NOT automatically apply to Service Keys; the Service Key itself requires API grants."
                ),
                404: "resource not found - check IFlow technical ID, version, and tenant URL",
                429: "rate limit exceeded",
                501: "the requested OData route is not implemented on this host (HTTP 501)",
            }
            return SapCpiError(f"{prefix}: {messages.get(exc.code, f'HTTP {exc.code}')}{error_details}", exc.code)
        if isinstance(exc, URLError):
            return SapCpiError(f"{prefix}: could not reach the tenant ({exc.reason})")
        return SapCpiError(f"{prefix}: {exc}")


@dataclass(frozen=True)
class RuntimeCallResult:
    status: int
    reason: str
    headers: dict[str, str]
    body: str
    elapsed_ms: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "headers": self.headers,
            "body": self.body,
            "elapsed_ms": self.elapsed_ms,
        }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RuntimeHttpClient:
    """Invokes one HTTPS IFlow sender endpoint without persisting credentials."""

    BLOCKED_HEADERS = {"authorization", "host", "content-length", "connection", "cookie", "proxy-authorization"}
    MAX_RESPONSE_BYTES = 10 * 1024 * 1024

    def __init__(self, principal: str, secret: str, auth_type: str = "basic", token_url: str = "", timeout: int = 60):
        self.principal = principal
        self.secret = secret
        self.auth_type = auth_type.lower()
        self.token_url = SapCpiClient._normalize_token_url(token_url) if token_url else ""
        self.timeout = timeout
        if not principal or not secret:
            raise SapCpiError("Runtime username/client ID and password/client secret are required")
        if self.auth_type not in {"basic", "oauth"}:
            raise SapCpiError("Runtime authentication type must be basic or oauth")
        if self.auth_type == "oauth" and not self.token_url:
            raise SapCpiError("Runtime OAuth token URL is required")

    def call(self, endpoint: str, xml_body: str, headers: dict[str, str] | None = None) -> RuntimeCallResult:
        endpoint = self._endpoint_url(endpoint)
        request_headers = {"Accept": "application/xml, text/xml, */*", "Content-Type": "application/xml; charset=utf-8", "User-Agent": "iflow-testpayload/0.1"}
        for name, value in (headers or {}).items():
            normalized = str(name).strip()
            if not normalized or normalized.lower() in self.BLOCKED_HEADERS or "\n" in normalized or "\r" in normalized:
                raise SapCpiError(f"Request header `{name}` is not allowed")
            text_value = str(value)
            if "\n" in text_value or "\r" in text_value:
                raise SapCpiError(f"Request header `{name}` contains an invalid newline")
            request_headers[normalized] = text_value
        request_headers["Authorization"] = self._authorization()
        request = Request(endpoint, data=xml_body.encode("utf-8"), headers=request_headers, method="POST")
        opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
        started = time.monotonic()
        try:
            with opener.open(request, timeout=self.timeout) as response:
                status = int(response.status)
                reason = str(response.reason or "")
                response_headers = dict(response.headers.items())
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            status = exc.code
            reason = str(exc.reason or "")
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            body = exc.read(self.MAX_RESPONSE_BYTES + 1)
        except (URLError, OSError) as exc:
            raise SapCpiClient._friendly_error(exc, "Runtime IFlow request failed") from exc
        if len(body) > self.MAX_RESPONSE_BYTES:
            raise SapCpiError("Runtime response exceeds the 10 MB display limit")
        return RuntimeCallResult(status, reason, response_headers, body.decode("utf-8", errors="replace"), round((time.monotonic() - started) * 1000))

    def _authorization(self) -> str:
        if self.auth_type == "basic":
            encoded = base64.b64encode(f"{self.principal}:{self.secret}".encode()).decode()
            return f"Basic {encoded}"
        token = SapCpiClient._fetch_oauth_token(self.token_url, self.principal, self.secret, self.timeout)
        return f"Bearer {token}"

    @staticmethod
    def _endpoint_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise SapCpiError("Runtime endpoint must be an HTTPS URL without embedded credentials or fragments")
        return value.strip()
