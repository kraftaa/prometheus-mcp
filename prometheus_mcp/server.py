import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
HTTP_TIMEOUT = float(os.environ.get("PROMETHEUS_HTTP_TIMEOUT", "30"))

DEFAULT_SERVICE_LABEL = os.environ.get("PROMETHEUS_SERVICE_LABEL", "service")
DEFAULT_REQUEST_TOTAL_METRIC = os.environ.get("PROMETHEUS_REQUEST_TOTAL_METRIC", "http_requests_total")
DEFAULT_REQUEST_DURATION_BUCKET_METRIC = os.environ.get(
    "PROMETHEUS_REQUEST_DURATION_BUCKET_METRIC",
    "http_request_duration_seconds_bucket",
)
DEFAULT_ERROR_COUNTER_METRIC = os.environ.get("PROMETHEUS_ERROR_COUNTER_METRIC", "app_errors_total")
DEFAULT_ERROR_TYPE_LABEL = os.environ.get("PROMETHEUS_ERROR_TYPE_LABEL", "error_type")
DEFAULT_STATUS_LABEL = os.environ.get("PROMETHEUS_STATUS_LABEL", "status")


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


def _promql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_iso8601_to_unix(timestamp: str) -> float:
    raw = timestamp.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone information, e.g. 2026-04-23T10:00:00Z")
    return dt.astimezone(timezone.utc).timestamp()


def _window_bounds(end_unix: float, window_minutes: int) -> dict[str, dict[str, float]]:
    seconds = float(window_minutes * 60)
    current_start = end_unix - seconds
    prev_end = current_start
    prev_start = prev_end - seconds
    return {
        "current": {"start_unix": current_start, "end_unix": end_unix},
        "previous": {"start_unix": prev_start, "end_unix": prev_end},
    }


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _extract_vector(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    result = data.get("result")
    if not isinstance(result, list):
        return []
    return [item for item in result if isinstance(item, dict)]


def _extract_sample_value(item: dict[str, Any]) -> float | None:
    sample = item.get("value")
    if not isinstance(sample, list) or len(sample) != 2:
        return None
    return _to_float(sample[1])


def _percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        if current == 0:
            return 0.0
        return None
    return ((current - previous) / previous) * 100.0


def _comparison(current: float, previous: float, unit: str) -> dict[str, Any]:
    return {
        "previous": previous,
        "current": current,
        "absolute_change": current - previous,
        "percent_change": _percent_change(current, previous),
        "unit": unit,
    }


async def _query_vector(query: str, time_unix: float | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": query}
    if time_unix is not None:
        params["time"] = time_unix
    payload = await _api_get("/api/v1/query", params=params)
    return _extract_vector(payload)


async def _query_scalar(query: str, time_unix: float | None = None) -> float:
    items = await _query_vector(query, time_unix=time_unix)
    values = [value for value in (_extract_sample_value(item) for item in items) if value is not None]
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return sum(values)


async def _query_breakdown(query: str, label: str, time_unix: float) -> dict[str, float]:
    items = await _query_vector(query, time_unix=time_unix)
    out: dict[str, float] = {}
    for item in items:
        metric = item.get("metric")
        if not isinstance(metric, dict):
            continue
        name = str(metric.get(label, "unknown"))
        value = _extract_sample_value(item)
        if value is None:
            continue
        out[name] = out.get(name, 0.0) + value
    return out


def _build_service_queries(
    service: str,
    window_minutes: int,
    service_label: str,
    request_total_metric: str,
    request_duration_bucket_metric: str,
    status_label: str,
) -> dict[str, str]:
    selector = f'{service_label}="{_promql_escape(service)}"'
    window = f"{window_minutes}m"
    return {
        "request_count": f"sum(increase({request_total_metric}{{{selector}}}[{window}]))",
        "error_count": (
            f'sum(increase({request_total_metric}{{{selector},{status_label}=~"5.."}}[{window}]))'
        ),
        "latency_p95_seconds": (
            "histogram_quantile(0.95, "
            f"sum(rate({request_duration_bucket_metric}{{{selector}}}[{window}])) by (le)"
            ")"
        ),
    }


async def _measure_window(
    *,
    queries: dict[str, str],
    end_unix: float,
) -> dict[str, float]:
    request_count, error_count, latency_p95_seconds = await asyncio.gather(
        _query_scalar(queries["request_count"], time_unix=end_unix),
        _query_scalar(queries["error_count"], time_unix=end_unix),
        _query_scalar(queries["latency_p95_seconds"], time_unix=end_unix),
    )
    error_rate_pct = 0.0
    if request_count > 0:
        error_rate_pct = (error_count / request_count) * 100.0
    return {
        "request_count": request_count,
        "error_count": error_count,
        "error_rate_pct": error_rate_pct,
        "latency_p95_ms": latency_p95_seconds * 1000.0,
    }


async def _error_breakdowns(
    *,
    service: str,
    window_minutes: int,
    service_label: str,
    error_counter_metric: str,
    error_type_label: str,
    request_total_metric: str,
    request_status_label: str,
    end_unix: float,
    previous_end_unix: float,
    fallback_to_http_status: bool = True,
) -> tuple[dict[str, float], dict[str, float], str, list[str]]:
    warnings: list[str] = []
    selector = f'{service_label}="{_promql_escape(service)}"'
    window = f"{window_minutes}m"

    primary_query = f"sum by ({error_type_label}) (increase({error_counter_metric}{{{selector}}}[{window}]))"
    current = await _query_breakdown(primary_query, label=error_type_label, time_unix=end_unix)
    previous = await _query_breakdown(primary_query, label=error_type_label, time_unix=previous_end_unix)
    used_label = error_type_label

    if (current or previous) or not fallback_to_http_status:
        if not (current or previous):
            warnings.append(
                f"No results from {error_counter_metric}; provide a metric with label {error_type_label}."
            )
        return current, previous, used_label, warnings

    fallback_query = (
        f"sum by ({request_status_label}) "
        f"(increase({request_total_metric}{{{selector},{request_status_label}=~\"5..\"}}[{window}]))"
    )
    current = await _query_breakdown(fallback_query, label=request_status_label, time_unix=end_unix)
    previous = await _query_breakdown(
        fallback_query,
        label=request_status_label,
        time_unix=previous_end_unix,
    )
    used_label = request_status_label
    warnings.append(
        "Fell back to HTTP 5xx status-code diff because ERROR_COUNTER_METRIC returned no samples."
    )
    return current, previous, used_label, warnings


def _new_errors(
    current: dict[str, float],
    previous: dict[str, float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for error_type, current_count in sorted(current.items(), key=lambda x: x[1], reverse=True):
        prev_count = previous.get(error_type, 0.0)
        delta = current_count - prev_count
        if delta > 0:
            out.append(
                {
                    "error_type": error_type,
                    "current_count": current_count,
                    "previous_count": prev_count,
                    "delta": delta,
                }
            )
    return out


def _likely_cause(
    *,
    error_rate_delta_pct_points: float,
    latency_delta_ms: float,
    new_error_types: list[str],
) -> str:
    joined = " ".join(new_error_types).lower()
    if any(token in joined for token in ("invalid", "payload", "missing field", "validation", "parse")):
        return "Likely application input/validation regression."
    if any(token in joined for token in ("timeout", "connection", "upstream", "database", "db")):
        return "Likely dependency or database instability."
    if error_rate_delta_pct_points > 1.0 and latency_delta_ms > 100:
        return "Likely backend saturation or slow downstream dependency."
    if error_rate_delta_pct_points > 1.0:
        return "Likely application/server-side error regression."
    if latency_delta_ms > 100:
        return "Likely performance regression."
    return "No strong regression signal detected."


def _status_and_confidence(
    *,
    error_rate_delta_pct_points: float,
    latency_delta_ms: float,
    new_error_count: int,
) -> tuple[str, float]:
    score = 0.0
    if error_rate_delta_pct_points > 0.5:
        score += min(0.45, error_rate_delta_pct_points / 5.0)
    if latency_delta_ms > 50:
        score += min(0.35, latency_delta_ms / 800.0)
    if new_error_count > 0:
        score += min(0.2, new_error_count / 10.0)

    confidence = min(0.99, max(0.5, 0.5 + score))
    if score >= 0.55:
        return "degraded", confidence
    return "healthy", confidence


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
async def investigate_service(
    service: str,
    window_minutes: int = 15,
    service_label: str = DEFAULT_SERVICE_LABEL,
    request_total_metric: str = DEFAULT_REQUEST_TOTAL_METRIC,
    request_status_label: str = DEFAULT_STATUS_LABEL,
    request_duration_bucket_metric: str = DEFAULT_REQUEST_DURATION_BUCKET_METRIC,
    error_counter_metric: str = DEFAULT_ERROR_COUNTER_METRIC,
    error_type_label: str = DEFAULT_ERROR_TYPE_LABEL,
) -> dict[str, Any]:
    """Deterministically compare service behavior in current vs previous window.

    Returns request volume, error rate, latency p95, and new error signatures
    without requiring an LLM.
    """
    if not service.strip():
        raise ValueError("service must not be empty")
    _validate_positive_int("window_minutes", window_minutes)

    end_unix = datetime.now(timezone.utc).timestamp()
    bounds = _window_bounds(end_unix=end_unix, window_minutes=window_minutes)
    current_end = bounds["current"]["end_unix"]
    previous_end = bounds["previous"]["end_unix"]

    queries = _build_service_queries(
        service=service,
        window_minutes=window_minutes,
        service_label=service_label,
        request_total_metric=request_total_metric,
        request_duration_bucket_metric=request_duration_bucket_metric,
        status_label=request_status_label,
    )
    current_metrics, previous_metrics = await asyncio.gather(
        _measure_window(queries=queries, end_unix=current_end),
        _measure_window(queries=queries, end_unix=previous_end),
    )

    current_errors, previous_errors, used_error_label, warnings = await _error_breakdowns(
        service=service,
        window_minutes=window_minutes,
        service_label=service_label,
        error_counter_metric=error_counter_metric,
        error_type_label=error_type_label,
        request_total_metric=request_total_metric,
        request_status_label=request_status_label,
        end_unix=current_end,
        previous_end_unix=previous_end,
    )
    new_error_patterns = _new_errors(current=current_errors, previous=previous_errors)

    metrics = {
        "request_count": _comparison(
            current=current_metrics["request_count"],
            previous=previous_metrics["request_count"],
            unit="requests_per_window",
        ),
        "error_rate_pct": _comparison(
            current=current_metrics["error_rate_pct"],
            previous=previous_metrics["error_rate_pct"],
            unit="percent",
        ),
        "latency_p95_ms": _comparison(
            current=current_metrics["latency_p95_ms"],
            previous=previous_metrics["latency_p95_ms"],
            unit="milliseconds",
        ),
    }

    error_delta = metrics["error_rate_pct"]["absolute_change"]
    latency_delta_ms = metrics["latency_p95_ms"]["absolute_change"]
    likely_cause = _likely_cause(
        error_rate_delta_pct_points=error_delta,
        latency_delta_ms=latency_delta_ms,
        new_error_types=[item["error_type"] for item in new_error_patterns],
    )
    status, confidence = _status_and_confidence(
        error_rate_delta_pct_points=error_delta,
        latency_delta_ms=latency_delta_ms,
        new_error_count=len(new_error_patterns),
    )

    return {
        "service": service,
        "window_minutes": window_minutes,
        "time_windows": bounds,
        "metrics": metrics,
        "new_error_patterns": new_error_patterns,
        "used_error_label": used_error_label,
        "status": status,
        "confidence": confidence,
        "likely_cause": likely_cause,
        "summary": [
            f"error_rate: {metrics['error_rate_pct']['previous']:.3f}% -> {metrics['error_rate_pct']['current']:.3f}%",
            f"latency_p95: {metrics['latency_p95_ms']['previous']:.1f}ms -> {metrics['latency_p95_ms']['current']:.1f}ms",
            f"request_count: {metrics['request_count']['previous']:.1f} -> {metrics['request_count']['current']:.1f}",
        ],
        "warnings": warnings,
    }


@mcp.tool()
async def investigate_deploy(
    service: str,
    deploy_time: str,
    window_minutes: int = 15,
    service_label: str = DEFAULT_SERVICE_LABEL,
    request_total_metric: str = DEFAULT_REQUEST_TOTAL_METRIC,
    request_status_label: str = DEFAULT_STATUS_LABEL,
    request_duration_bucket_metric: str = DEFAULT_REQUEST_DURATION_BUCKET_METRIC,
    error_counter_metric: str = DEFAULT_ERROR_COUNTER_METRIC,
    error_type_label: str = DEFAULT_ERROR_TYPE_LABEL,
) -> dict[str, Any]:
    """Compare service behavior 15m before and 15m after deploy timestamp."""
    if not service.strip():
        raise ValueError("service must not be empty")
    _validate_positive_int("window_minutes", window_minutes)

    deploy_unix = _parse_iso8601_to_unix(deploy_time)
    seconds = float(window_minutes * 60)
    before_end = deploy_unix
    after_end = deploy_unix + seconds

    queries = _build_service_queries(
        service=service,
        window_minutes=window_minutes,
        service_label=service_label,
        request_total_metric=request_total_metric,
        request_duration_bucket_metric=request_duration_bucket_metric,
        status_label=request_status_label,
    )
    after_metrics, before_metrics = await asyncio.gather(
        _measure_window(queries=queries, end_unix=after_end),
        _measure_window(queries=queries, end_unix=before_end),
    )

    current_errors, previous_errors, used_error_label, warnings = await _error_breakdowns(
        service=service,
        window_minutes=window_minutes,
        service_label=service_label,
        error_counter_metric=error_counter_metric,
        error_type_label=error_type_label,
        request_total_metric=request_total_metric,
        request_status_label=request_status_label,
        end_unix=after_end,
        previous_end_unix=before_end,
    )
    new_error_patterns = _new_errors(current=current_errors, previous=previous_errors)

    metrics = {
        "error_rate_pct": _comparison(
            current=after_metrics["error_rate_pct"],
            previous=before_metrics["error_rate_pct"],
            unit="percent",
        ),
        "latency_p95_ms": _comparison(
            current=after_metrics["latency_p95_ms"],
            previous=before_metrics["latency_p95_ms"],
            unit="milliseconds",
        ),
        "request_count": _comparison(
            current=after_metrics["request_count"],
            previous=before_metrics["request_count"],
            unit="requests_per_window",
        ),
    }

    error_delta = metrics["error_rate_pct"]["absolute_change"]
    latency_delta_ms = metrics["latency_p95_ms"]["absolute_change"]
    likely_cause = _likely_cause(
        error_rate_delta_pct_points=error_delta,
        latency_delta_ms=latency_delta_ms,
        new_error_types=[item["error_type"] for item in new_error_patterns],
    )
    status, confidence = _status_and_confidence(
        error_rate_delta_pct_points=error_delta,
        latency_delta_ms=latency_delta_ms,
        new_error_count=len(new_error_patterns),
    )

    return {
        "service": service,
        "deploy_time": deploy_time,
        "window_minutes": window_minutes,
        "comparison_windows": {
            "before_deploy": {"start_unix": before_end - seconds, "end_unix": before_end},
            "after_deploy": {"start_unix": after_end - seconds, "end_unix": after_end},
        },
        "metrics": metrics,
        "new_error_patterns": new_error_patterns,
        "used_error_label": used_error_label,
        "status": status,
        "confidence": confidence,
        "conclusion": likely_cause,
        "summary": [
            f"After deploy error_rate delta: {metrics['error_rate_pct']['absolute_change']:+.3f} percentage points",
            f"After deploy latency_p95 delta: {metrics['latency_p95_ms']['absolute_change']:+.1f} ms",
            f"New error patterns: {len(new_error_patterns)}",
        ],
        "warnings": warnings,
    }


@mcp.tool()
async def find_new_errors(
    service: str,
    window_minutes: int = 15,
    service_label: str = DEFAULT_SERVICE_LABEL,
    error_counter_metric: str = DEFAULT_ERROR_COUNTER_METRIC,
    error_type_label: str = DEFAULT_ERROR_TYPE_LABEL,
    request_total_metric: str = DEFAULT_REQUEST_TOTAL_METRIC,
    request_status_label: str = DEFAULT_STATUS_LABEL,
) -> dict[str, Any]:
    """Diff error signatures between current and previous windows."""
    if not service.strip():
        raise ValueError("service must not be empty")
    _validate_positive_int("window_minutes", window_minutes)

    end_unix = datetime.now(timezone.utc).timestamp()
    bounds = _window_bounds(end_unix=end_unix, window_minutes=window_minutes)
    current_end = bounds["current"]["end_unix"]
    previous_end = bounds["previous"]["end_unix"]

    current, previous, used_error_label, warnings = await _error_breakdowns(
        service=service,
        window_minutes=window_minutes,
        service_label=service_label,
        error_counter_metric=error_counter_metric,
        error_type_label=error_type_label,
        request_total_metric=request_total_metric,
        request_status_label=request_status_label,
        end_unix=current_end,
        previous_end_unix=previous_end,
    )
    new_error_patterns = _new_errors(current=current, previous=previous)

    return {
        "service": service,
        "window_minutes": window_minutes,
        "time_windows": bounds,
        "used_error_label": used_error_label,
        "new_error_patterns": new_error_patterns,
        "current_window_errors": current,
        "previous_window_errors": previous,
        "warnings": warnings,
    }


@mcp.tool()
async def investigate_and_score(
    service: str,
    window_minutes: int = 15,
    service_label: str = DEFAULT_SERVICE_LABEL,
    request_total_metric: str = DEFAULT_REQUEST_TOTAL_METRIC,
    request_status_label: str = DEFAULT_STATUS_LABEL,
    request_duration_bucket_metric: str = DEFAULT_REQUEST_DURATION_BUCKET_METRIC,
    error_counter_metric: str = DEFAULT_ERROR_COUNTER_METRIC,
    error_type_label: str = DEFAULT_ERROR_TYPE_LABEL,
) -> dict[str, Any]:
    """Run investigation and return deterministic status + confidence score."""
    base = await investigate_service(
        service=service,
        window_minutes=window_minutes,
        service_label=service_label,
        request_total_metric=request_total_metric,
        request_status_label=request_status_label,
        request_duration_bucket_metric=request_duration_bucket_metric,
        error_counter_metric=error_counter_metric,
        error_type_label=error_type_label,
    )
    metrics = base["metrics"]
    error_delta = metrics["error_rate_pct"]["absolute_change"]
    latency_delta_ms = metrics["latency_p95_ms"]["absolute_change"]
    status, confidence = _status_and_confidence(
        error_rate_delta_pct_points=error_delta,
        latency_delta_ms=latency_delta_ms,
        new_error_count=len(base["new_error_patterns"]),
    )

    return {
        "service": service,
        "status": status,
        "confidence": confidence,
        "reason": base["likely_cause"],
        "window_minutes": window_minutes,
        "signals": {
            "error_rate_delta_pct_points": error_delta,
            "latency_p95_delta_ms": latency_delta_ms,
            "new_error_pattern_count": len(base["new_error_patterns"]),
        },
        "investigation": base,
    }


@mcp.tool()
async def get_runtime_info() -> dict[str, Any]:
    """Return Prometheus runtime info (storage, goroutines, GOMAXPROCS, etc.)."""
    return await _api_get("/api/v1/status/runtimeinfo")


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
