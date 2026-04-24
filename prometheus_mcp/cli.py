#!/usr/bin/env python3
"""Small investigation CLI on top of prometheus_mcp.server tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _fmt_percent(value: float) -> str:
    return f"{value:.3f}%"


def _fmt_ms(value: float) -> str:
    return f"{value:.1f}ms"


def _fmt_signed(value: float, decimals: int = 3) -> str:
    return f"{value:+.{decimals}f}"


def _print_investigate_service(payload: dict[str, Any]) -> None:
    metrics = payload["metrics"]
    err = metrics["error_rate_pct"]
    lat = metrics["latency_p95_ms"]
    print(f"status: {payload['status']} (confidence={payload['confidence']:.2f})")
    print(
        "error_rate_delta_pp: "
        f"{_fmt_signed(err['absolute_change'])} "
        f"({_fmt_percent(err['previous'])} -> {_fmt_percent(err['current'])})"
    )
    print(
        "latency_p95_delta_ms: "
        f"{_fmt_signed(lat['absolute_change'], decimals=1)} "
        f"({_fmt_ms(lat['previous'])} -> {_fmt_ms(lat['current'])})"
    )
    print(f"reason: {payload['likely_cause']}")


def _print_investigate_deploy(payload: dict[str, Any]) -> None:
    metrics = payload["metrics"]
    err = metrics["error_rate_pct"]
    lat = metrics["latency_p95_ms"]
    print(f"status: {payload['status']} (confidence={payload['confidence']:.2f})")
    print(f"deploy_time: {payload['deploy_time']}")
    print(f"error_rate_delta_pp: {_fmt_signed(err['absolute_change'])}")
    print(f"latency_p95_delta_ms: {_fmt_signed(lat['absolute_change'], decimals=1)}")
    print(f"conclusion: {payload['conclusion']}")


def _print_find_new_errors(payload: dict[str, Any]) -> None:
    patterns = payload["new_error_patterns"]
    if not patterns:
        print("new_error_types: none")
        return
    print(f"new_error_types: {len(patterns)}")
    for item in patterns:
        print(
            f"- {item['error_type']}: "
            f"delta={_fmt_signed(item['delta'], decimals=1)} "
            f"(prev={item['previous_count']:.1f}, curr={item['current_count']:.1f})"
        )


def _print_status(payload: dict[str, Any]) -> None:
    print(f"status: {payload['status']}")
    print(f"confidence: {payload['confidence']:.2f}")
    print(f"reason: {payload['reason']}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prometheus MCP investigation CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    service = sub.add_parser("investigate-service", help="Compare current vs previous window.")
    service.add_argument("service", help="Service name label value.")
    service.add_argument("--window", type=int, default=15, help="Window in minutes (default: 15).")
    service.add_argument("--json", action="store_true", help="Print full JSON response.")

    deploy = sub.add_parser("investigate-deploy", help="Compare before vs after a deploy.")
    deploy.add_argument("service", help="Service name label value.")
    deploy.add_argument("--deploy-time", required=True, help="ISO8601 UTC timestamp, e.g. 2026-04-24T10:00:00Z.")
    deploy.add_argument("--window", type=int, default=15, help="Window in minutes (default: 15).")
    deploy.add_argument("--json", action="store_true", help="Print full JSON response.")

    errors = sub.add_parser("find-new-errors", help="Diff new error types in current vs previous window.")
    errors.add_argument("service", help="Service name label value.")
    errors.add_argument("--window", type=int, default=15, help="Window in minutes (default: 15).")
    errors.add_argument("--json", action="store_true", help="Print full JSON response.")

    status = sub.add_parser("status", help="Quick health/degraded status with reason.")
    status.add_argument("service", help="Service name label value.")
    status.add_argument("--window", type=int, default=15, help="Window in minutes (default: 15).")
    status.add_argument("--json", action="store_true", help="Print full JSON response.")

    score = sub.add_parser("score", help="Return status/confidence/reason as JSON.")
    score.add_argument("service", help="Service name label value.")
    score.add_argument("--window", type=int, default=15, help="Window in minutes (default: 15).")

    return parser


async def _run(args: argparse.Namespace) -> int:
    srv = None
    try:
        from prometheus_mcp import server as srv

        if args.command == "investigate-service":
            payload = await srv.investigate_service(service=args.service, window_minutes=args.window)
            if args.json:
                _print_json(payload)
            else:
                _print_investigate_service(payload)
            return 0

        if args.command == "investigate-deploy":
            payload = await srv.investigate_deploy(
                service=args.service,
                deploy_time=args.deploy_time,
                window_minutes=args.window,
            )
            if args.json:
                _print_json(payload)
            else:
                _print_investigate_deploy(payload)
            return 0

        if args.command == "find-new-errors":
            payload = await srv.find_new_errors(service=args.service, window_minutes=args.window)
            if args.json:
                _print_json(payload)
            else:
                _print_find_new_errors(payload)
            return 0

        if args.command == "status":
            payload = await srv.investigate_and_score(service=args.service, window_minutes=args.window)
            short = {
                "service": args.service,
                "status": payload["status"],
                "confidence": payload["confidence"],
                "reason": payload["reason"],
            }
            if args.json:
                _print_json(short)
            else:
                _print_status(short)
            return 0

        if args.command == "score":
            payload = await srv.investigate_and_score(service=args.service, window_minutes=args.window)
            short = {
                "service": args.service,
                "status": payload["status"],
                "confidence": payload["confidence"],
                "reason": payload["reason"],
            }
            _print_json(short)
            return 0

        raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(f"cli failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if srv is not None:
            await srv._client.aclose()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
