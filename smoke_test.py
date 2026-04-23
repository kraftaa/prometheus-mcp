#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
import time

from prometheus_mcp import server


async def _run(args: argparse.Namespace) -> int:
    now = time.time()
    start = args.start_unix if args.start_unix is not None else now - 3600
    end = args.end_unix if args.end_unix is not None else now

    try:
        instant = await server.query_instant(
            query=args.query,
            time_unix=args.time_unix,
            limit=args.limit,
        )
        print("query_instant: OK")
        print(json.dumps(instant, indent=2)[:1200])

        ranged = await server.query_range(
            query=args.query,
            start_unix=start,
            end_unix=end,
            step_seconds=args.step_seconds,
            limit=args.limit,
        )
        print("query_range: OK")
        print(json.dumps(ranged, indent=2)[:1200])

        labels = await server.list_label_names(start_unix=start, end_unix=end)
        print("list_label_names: OK")
        print(json.dumps(labels, indent=2)[:1200])

        targets = await server.list_targets(state="active")
        print("list_targets: OK")
        print(json.dumps(targets, indent=2)[:1200])

        alerts = await server.list_alerts()
        print("list_alerts: OK")
        print(json.dumps(alerts, indent=2)[:1200])

        rules = await server.list_rules()
        print("list_rules: OK")
        print(json.dumps(rules, indent=2)[:1200])

        metadata = await server.list_metric_metadata(metric=args.metric, limit=20)
        print("list_metric_metadata: OK")
        print(json.dumps(metadata, indent=2)[:1200])

        runtime = await server.get_runtime_info()
        print("get_runtime_info: OK")
        print(json.dumps(runtime, indent=2)[:1200])

        return 0
    except Exception as exc:
        print(f"smoke_test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await server._client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test prometheus-mcp request paths against a live Prometheus instance."
    )
    parser.add_argument(
        "--query",
        default="up",
        help="PromQL query for instant and range checks.",
    )
    parser.add_argument(
        "--metric",
        default="up",
        help="Metric name used for metadata lookup.",
    )
    parser.add_argument("--time-unix", type=float, help="Timestamp for instant query (unix seconds).")
    parser.add_argument("--start-unix", type=float, help="Start timestamp for range query.")
    parser.add_argument("--end-unix", type=float, help="End timestamp for range query.")
    parser.add_argument("--step-seconds", type=float, default=30.0, help="Step for range query.")
    parser.add_argument("--limit", type=int, default=20, help="Series limit for query endpoints.")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
