import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
HTTP_TIMEOUT = float(os.environ.get("PROMETHEUS_HTTP_TIMEOUT", "30"))


class PrometheusAPIError(RuntimeError):
    """Raised when Prometheus returns an API-level or HTTP-level failure."""


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {}

    bearer_token = os.environ.get("PROMETHEUS_BEARER_TOKEN")
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    raw_headers = os.environ.get("PROMETHEUS_HEADERS_JSON")
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
        except json.JSONDecodeError as exc:
            raise ValueError("PROMETHEUS_HEADERS_JSON must be valid JSON") from exc

        if not isinstance(parsed, dict):
            raise ValueError("PROMETHEUS_HEADERS_JSON must be a JSON object")

        for key, value in parsed.items():
            headers[str(key)] = str(value)

    return headers


mcp = FastMCP(
    "prometheus",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "3020")),
)

_client = httpx.AsyncClient(
    base_url=PROMETHEUS_URL,
    timeout=HTTP_TIMEOUT,
    headers=_build_headers(),
)


def _validate_positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be > 0")


def _apply_time_range(params: dict[str, Any], start: float | None, end: float | None) -> None:
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    if start is not None and end is not None and end <= start:
        raise ValueError("end must be greater than start")


def _apply_matchers(params: dict[str, Any], matchers: list[str] | None) -> None:
    if matchers is None:
        return
    filtered = [matcher.strip() for matcher in matchers if matcher.strip()]
    if not filtered:
        raise ValueError("matchers must include at least one non-empty selector")
    params["match[]"] = filtered


async def _api_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = await _client.get(path, params=params)

    payload: Any = None
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.status_code >= 400:
        detail = response.text
        if isinstance(payload, dict):
            error_type = payload.get("errorType")
            error = payload.get("error")
            if error_type or error:
                detail = f"{error_type or 'error'}: {error or 'unknown error'}"
        raise PrometheusAPIError(
            f"Prometheus API {path} returned {response.status_code}: {detail}"
        )

    if not isinstance(payload, dict):
        raise PrometheusAPIError("Prometheus API returned a non-JSON response")

    if payload.get("status") != "success":
        error_type = payload.get("errorType", "error")
        error = payload.get("error", "unknown error")
        raise PrometheusAPIError(f"Prometheus API {path} failed: {error_type}: {error}")

    return payload


@mcp.tool()
async def query_instant(
    query: str,
    time_unix: float | None = None,
    timeout: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run an instant PromQL query.

    `time_unix` is optional UNIX seconds. If omitted, Prometheus uses now.
    `timeout` follows Prometheus duration syntax, e.g. '30s' or '2m'.
    `limit` caps returned series for vector/matrix results.
    """
    params: dict[str, Any] = {"query": query}
    if time_unix is not None:
        params["time"] = time_unix
    if timeout is not None:
        params["timeout"] = timeout
    if limit is not None:
        _validate_positive_int("limit", limit)
        params["limit"] = limit
    return await _api_get("/api/v1/query", params=params)


@mcp.tool()
async def query_range(
    query: str,
    start_unix: float,
    end_unix: float,
    step_seconds: float,
    timeout: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run a range PromQL query across [start_unix, end_unix].

    `step_seconds` is the query resolution step in seconds and must be > 0.
    `timeout` follows Prometheus duration syntax, e.g. '30s' or '2m'.
    `limit` caps returned series for vector/matrix results.
    """
    if step_seconds <= 0:
        raise ValueError("step_seconds must be > 0")

    params: dict[str, Any] = {
        "query": query,
        "start": start_unix,
        "end": end_unix,
        "step": step_seconds,
    }
    if end_unix <= start_unix:
        raise ValueError("end_unix must be greater than start_unix")
    if timeout is not None:
        params["timeout"] = timeout
    if limit is not None:
        _validate_positive_int("limit", limit)
        params["limit"] = limit
    return await _api_get("/api/v1/query_range", params=params)


@mcp.tool()
async def list_label_names(
    start_unix: float | None = None,
    end_unix: float | None = None,
    matchers: list[str] | None = None,
) -> dict[str, Any]:
    """List all label names, optionally scoped by time range and selectors.

    `matchers` is a list of Prometheus series selectors, e.g. ['up', 'http_requests_total{job="api"}'].
    """
    params: dict[str, Any] = {}
    _apply_time_range(params, start_unix, end_unix)
    _apply_matchers(params, matchers)
    return await _api_get("/api/v1/labels", params=params)


@mcp.tool()
async def list_label_values(
    label_name: str,
    start_unix: float | None = None,
    end_unix: float | None = None,
    matchers: list[str] | None = None,
) -> dict[str, Any]:
    """List values for one label, optionally scoped by time range and selectors."""
    clean_label = label_name.strip()
    if not clean_label:
        raise ValueError("label_name must not be empty")

    params: dict[str, Any] = {}
    _apply_time_range(params, start_unix, end_unix)
    _apply_matchers(params, matchers)
    return await _api_get(f"/api/v1/label/{clean_label}/values", params=params)


@mcp.tool()
async def list_series(
    matchers: list[str],
    start_unix: float | None = None,
    end_unix: float | None = None,
) -> dict[str, Any]:
    """List time series matching one or more selectors (`matchers`)."""
    params: dict[str, Any] = {}
    _apply_time_range(params, start_unix, end_unix)
    _apply_matchers(params, matchers)
    return await _api_get("/api/v1/series", params=params)


@mcp.tool()
async def list_targets(state: str | None = None) -> dict[str, Any]:
    """Return scrape targets and health status.

    `state` may be 'active', 'dropped', or 'any'.
    """
    params: dict[str, Any] = {}
    if state is not None:
        normalized = state.strip().lower()
        allowed = {"active", "dropped", "any"}
        if normalized not in allowed:
            raise ValueError("state must be one of: active, dropped, any")
        params["state"] = normalized
    return await _api_get("/api/v1/targets", params=params)


@mcp.tool()
async def list_alerts() -> dict[str, Any]:
    """Return all active alerts."""
    return await _api_get("/api/v1/alerts")


@mcp.tool()
async def list_rules(rule_type: str | None = None) -> dict[str, Any]:
    """Return loaded alerting and recording rules.

    `rule_type` may be 'alert' or 'record'.
    """
    params: dict[str, Any] = {}
    if rule_type is not None:
        normalized = rule_type.strip().lower()
        allowed = {"alert", "record"}
        if normalized not in allowed:
            raise ValueError("rule_type must be one of: alert, record")
        params["type"] = normalized
    return await _api_get("/api/v1/rules", params=params)


@mcp.tool()
async def list_metric_metadata(
    metric: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return metric metadata (type/help/unit) for one metric or all metrics."""
    params: dict[str, Any] = {}
    if metric is not None:
        clean_metric = metric.strip()
        if not clean_metric:
            raise ValueError("metric must not be empty")
        params["metric"] = clean_metric
    if limit is not None:
        _validate_positive_int("limit", limit)
        params["limit"] = limit
    return await _api_get("/api/v1/metadata", params=params)


@mcp.tool()
async def get_runtime_info() -> dict[str, Any]:
    """Return Prometheus runtime info (storage, goroutines, GOMAXPROCS, etc.)."""
    return await _api_get("/api/v1/status/runtimeinfo")


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
