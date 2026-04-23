SHELL := /bin/zsh

VENV ?= .venv
PYTHON ?= python3
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

PROMETHEUS_URL ?= http://localhost:9090
QUERY ?= up
STEP_SECONDS ?= 30
MCP_URL ?= http://localhost:3020/mcp

.PHONY: help venv install check dev smoke handshake

help:
	@echo "Available targets:"
	@echo "  make install                     # Create venv and install editable package"
	@echo "  make check                       # Compile-time syntax check"
	@echo "  make dev                         # Run MCP server locally"
	@echo "  make smoke                       # Run smoke tests against PROMETHEUS_URL"
	@echo "  make handshake                   # Send MCP initialize request to MCP_URL"
	@echo ""
	@echo "Variable overrides:"
	@echo "  PROMETHEUS_URL=http://host:9090  # Prometheus base URL"
	@echo "  QUERY='rate(http_requests_total[5m])'"
	@echo "  STEP_SECONDS=15"
	@echo "  MCP_URL=http://localhost:3020/mcp"

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(PIP) install -e .

check:
	$(PYTHON) -m compileall prometheus_mcp smoke_test.py

dev: install
	PROMETHEUS_URL=$(PROMETHEUS_URL) $(VENV)/bin/prometheus-mcp

smoke: install
	PROMETHEUS_URL=$(PROMETHEUS_URL) $(PY) smoke_test.py --query "$(QUERY)" --step-seconds $(STEP_SECONDS)

handshake:
	curl -i -N \
	  -H "Accept: application/json, text/event-stream" \
	  -H "Content-Type: application/json" \
	  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}' \
	  $(MCP_URL)
