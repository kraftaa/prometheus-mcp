# prometheus-mcp

MCP server exposing a curated surface of the Prometheus HTTP API to LLM agents over Streamable HTTP transport.

## Why this is better than a direct Quickwit-style clone

- Centralized API error parsing with explicit `PrometheusAPIError` messages.
- Input validation for risky parameters (`end > start`, `step_seconds > 0`, non-empty matchers).
- Optional auth/header controls for secured Prometheus deployments.
- Curated high-signal tools for investigations (queries + labels + series + targets + alerts + rules + metadata + runtime).

## Tools

- `query_instant(query, time_unix?, timeout?, limit?)` → instant PromQL query
- `query_range(query, start_unix, end_unix, step_seconds, timeout?, limit?)` → range PromQL query
- `list_label_names(start_unix?, end_unix?, matchers?)` → label names
- `list_label_values(label_name, start_unix?, end_unix?, matchers?)` → values for one label
- `list_series(matchers, start_unix?, end_unix?)` → matching series labels
- `list_targets(state?)` → scrape targets and health
- `list_alerts()` → active alerts
- `list_rules(rule_type?)` → alerting/recording rules
- `list_metric_metadata(metric?, limit?)` → metric metadata
- `get_runtime_info()` → Prometheus runtime details
- `investigate_service(service, window_minutes?, ...)` → compares current vs previous window and returns deterministic regression signals
- `investigate_deploy(service, deploy_time, window_minutes?, ...)` → compares before/after deploy window
- `find_new_errors(service, window_minutes?, ...)` → diffs new error signatures in current window
- `investigate_and_score(service, window_minutes?, ...)` → returns `{status, confidence, reason}` based on deterministic scoring

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus base URL |
| `PROMETHEUS_HTTP_TIMEOUT` | `30` | HTTP timeout (seconds) |
| `PROMETHEUS_BEARER_TOKEN` | unset | Optional bearer token |
| `PROMETHEUS_HEADERS_JSON` | unset | Extra headers as JSON object |
| `MCP_HOST` | `0.0.0.0` | Bind host |
| `MCP_PORT` | `3020` | Bind port |
| `PROMETHEUS_SERVICE_LABEL` | `service` | Service label key used by investigation tools |
| `PROMETHEUS_REQUEST_TOTAL_METRIC` | `http_requests_total` | Counter metric for request/error calculations |
| `PROMETHEUS_STATUS_LABEL` | `status` | HTTP status label key used for 5xx detection/fallback |
| `PROMETHEUS_REQUEST_DURATION_BUCKET_METRIC` | `http_request_duration_seconds_bucket` | Histogram bucket metric for p95 latency |
| `PROMETHEUS_ERROR_COUNTER_METRIC` | `app_errors_total` | Error counter used for `find_new_errors` |
| `PROMETHEUS_ERROR_TYPE_LABEL` | `error_type` | Label key used to diff error signatures |

`PROMETHEUS_HEADERS_JSON` example:

```bash
export PROMETHEUS_HEADERS_JSON='{"X-Scope-OrgID":"tenant-a"}'
```

## Run locally

```bash
make install
make dev
```

## Install from PyPI

```bash
pip install prometheus-mcp-investigator
prometheus-mcp
```

MCP Streamable HTTP endpoint: `http://localhost:3020/mcp`

Optional smoke test (uses same request paths as the tools):

```bash
make smoke
make smoke QUERY='rate(http_requests_total[5m])' STEP_SECONDS=15
```

Verify MCP handshake:

```bash
make handshake
make handshake MCP_URL=http://localhost:3020/mcp
```

Example deterministic investigation call:

```json
{
  "tool": "investigate_service",
  "arguments": {
    "service": "api",
    "window_minutes": 15
  }
}
```

## Docker

Image publishing is automated by GitHub Actions (`.github/workflows/publish-image.yml`):
- Push a tag `v*` to publish a version image (for example `v0.1.3`)
- `workflow_dispatch` can be used for manual publish runs
- Every publish also includes a short SHA tag

Release example:

```bash
git tag v0.1.3
git push origin v0.1.3
```

PyPI publishing is automated by GitHub Actions (`.github/workflows/publish-pypi.yml`) using Trusted Publisher (OIDC, no API token). The workflow validates that the git tag matches `pyproject.toml` version before publishing.

```bash
docker build -t prometheus-mcp:local .
docker run --rm -p 3020:3020 -e PROMETHEUS_URL=http://host.docker.internal:9090 prometheus-mcp:local
```

## Kubernetes quickstart

See [`kube/`](kube/):
- `kube/namespace.yaml`
- `kube/prometheus-mcp-single.yaml`
- `kube/README.md`

Apply:

```bash
kubectl apply -f kube/namespace.yaml
kubectl apply -f kube/prometheus-mcp-single.yaml
kubectl -n mcp rollout status deploy/prometheus-mcp
```

## MCP client config example

```json
{
  "mcp": {
    "prometheus": {
      "type": "remote",
      "url": "http://prometheus-mcp.mcp.svc.cluster.local:3020/mcp",
      "enabled": true
    }
  }
}
```

## License

MIT.
