"""Development-only comparison of cache-hit and forced cache-miss redirects."""

import argparse
import math
import os
import statistics
import time
import uuid

import httpx
from redis import Redis


def percentile(samples: list[float], percentile_value: float) -> float:
    ordered = sorted(samples)
    index = max(math.ceil(percentile_value * len(ordered)) - 1, 0)
    return ordered[index]


def measure_redirect(client: httpx.Client, url: str) -> float:
    started = time.perf_counter()
    response = client.get(url)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code != 307:
        raise RuntimeError(
            f"Expected a 307 from {url}, got {response.status_code}: {response.text}"
        )
    return elapsed_ms


def summarize(label: str, samples: list[float]) -> None:
    print(
        f"{label:<20} n={len(samples):>4} "
        f"mean={statistics.fmean(samples):>8.2f} ms "
        f"p50={percentile(samples, 0.50):>8.2f} ms "
        f"p95={percentile(samples, 0.95):>8.2f} ms"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    parser.add_argument("--requests", type=int, default=30, help="Requests per scenario")
    parser.add_argument("--email", default="benchmark@example.com")
    parser.add_argument("--password", default="linkforge-local-benchmark-only")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.requests < 1:
        raise SystemExit("--requests must be positive")

    base_url = args.base_url.rstrip("/")
    unique = uuid.uuid4().hex[:12]
    hit_alias = f"bench-hit-{unique}"
    miss_alias = f"bench-miss-{unique}"
    redis = Redis.from_url(args.redis_url, decode_responses=True)

    with httpx.Client(base_url=base_url, follow_redirects=False, timeout=10) as client:
        registration = client.post(
            "/api/v1/auth/register",
            json={"email": args.email, "password": args.password},
        )
        if registration.status_code not in {201, 409}:
            registration.raise_for_status()
        login = client.post(
            "/api/v1/auth/login",
            data={"username": args.email, "password": args.password},
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created_links: list[dict[str, str]] = []
        try:
            for alias in (hit_alias, miss_alias):
                response = client.post(
                    "/api/v1/urls",
                    json={"original_url": "https://example.com/benchmark", "custom_alias": alias},
                    headers=headers,
                )
                response.raise_for_status()
                created_links.append(response.json())

            redis.delete(f"link:{hit_alias}", f"link:{miss_alias}")
            measure_redirect(client, f"/{hit_alias}")
            cache_hits = [measure_redirect(client, f"/{hit_alias}") for _ in range(args.requests)]

            cache_misses: list[float] = []
            for _ in range(args.requests):
                # Eviction is intentionally outside the timed HTTP request.
                redis.delete(f"link:{miss_alias}")
                cache_misses.append(measure_redirect(client, f"/{miss_alias}"))

            print("LinkForge local redirect benchmark (development diagnostic only)")
            summarize("Redis cache hit", cache_hits)
            summarize("PostgreSQL miss", cache_misses)
        finally:
            for link in created_links:
                client.delete(f"/api/v1/urls/{link['id']}", headers=headers)
            redis.close()


if __name__ == "__main__":
    main()
