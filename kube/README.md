# Kubernetes Examples

This folder contains generic manifests for running `prometheus-mcp` in Kubernetes.

Files:
- `namespace.yaml`: Namespace example (`mcp`).
- `prometheus-mcp-single.yaml`: Single MCP instance + Service.

Update placeholders before apply:
- `image: ghcr.io/kraftaa/prometheus-mcp-server:<tag>`
- `PROMETHEUS_URL`, for example:
  - `http://<prometheus-service>.<prometheus-namespace>.svc.cluster.local:9090`

Apply examples:

```bash
kubectl apply -f kube/namespace.yaml
kubectl apply -f kube/prometheus-mcp-single.yaml
```

Verify MCP handshake:

```bash
kubectl -n mcp run tmp-curl --rm -it --restart=Never --image=curlimages/curl -- \
  curl -i -N \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}' \
    http://prometheus-mcp.mcp.svc.cluster.local:3020/mcp
```
