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

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus base URL |
| `PROMETHEUS_HTTP_TIMEOUT` | `30` | HTTP timeout (seconds) |
| `PROMETHEUS_BEARER_TOKEN` | unset | Optional bearer token |
| `PROMETHEUS_HEADERS_JSON` | unset | Extra headers as JSON object |
| `MCP_HOST` | `0.0.0.0` | Bind host |
| `MCP_PORT` | `3020` | Bind port |

`PROMETHEUS_HEADERS_JSON` example:

```bash
export PROMETHEUS_HEADERS_JSON='{"X-Scope-OrgID":"tenant-a"}'
```

## Run locally

```bash
pip install -e .
PROMETHEUS_URL=http://localhost:9090 prometheus-mcp
```

MCP Streamable HTTP endpoint: `http://localhost:3020/mcp`

Optional smoke test (uses same request paths as the tools):

```bash
PROMETHEUS_URL=http://localhost:9090 python smoke_test.py --query 'up' --step-seconds 30
```

## Docker

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
