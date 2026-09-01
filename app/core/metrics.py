"""
Prometheus instrumentation.

Adds request counters and a latency histogram to every route, and exposes them at
GET /metrics for the Prometheus service on this box to scrape once every 15s. The
Grafana dashboards under deploy/monitoring/ are built entirely on what this file
emits, so renaming a metric here silently empties a panel there.

Three decisions worth knowing about:

* **/metrics needs the token.** This API binds 0.0.0.0:8000 on an office LAN whose
  firewall is off (see README, "Windows Firewall"), so an unauthenticated /metrics
  would hand anyone on the network a complete list of every route that exists, how
  often each is used and when the box was last restarted. It answers 404 without a
  valid METRICS_TOKEN - the same "don't even admit it's here" treatment /health
  gets in main.py, and for the same reason.

* **No multiprocess mode.** prometheus_client keeps its counters in ordinary process
  memory, which is only correct while there is exactly one process holding them.
  The service runs `uvicorn app.main:app` with no --workers flag, so there is one.
  Add workers and every scrape starts returning whichever worker happened to answer
  it, with the numbers jumping around at random; that setup needs
  PROMETHEUS_MULTIPROC_DIR and a shared-directory registry instead.

  The registry is this module's own rather than prometheus_client's global default,
  which keeps building a second app in one process (a test fixture, say) from hitting
  a duplicate-name error on import - the storefront side had exactly that problem and
  it fails silently, leaving the dashboards blank with nothing logged.

* **Untemplated paths are grouped.** A label value that comes from the URL creates a
  new time series per distinct value, and Prometheus keeps them all in memory - so a
  bot walking /wp-admin, /.env, /phpmyadmin ... would otherwise cost one permanent
  series per guess. `should_group_untemplated` folds everything that matched no
  route into a single "none" handler, which is all a 404 flood is worth anyway.
"""
import secrets

from fastapi import FastAPI, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from app.config import settings

# Tuned for a LAN app talking to a local Postgres, where the interesting question is
# "did this get slow", not "how slow". The default set spends most of its buckets
# under 100ms, which is where nearly every response here already lands - so the whole
# distribution collapses into one bar and a p95 computed from it is meaningless. This
# keeps resolution around the 50-500ms band the PDF and report endpoints actually
# live in, and tops out at 10s because anything slower is a bug, not a measurement.
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))

REGISTRY = CollectorRegistry()


def setup_metrics(app: FastAPI) -> None:
    """Instrument `app` and register the scrape endpoint.

    A no-op when METRICS_TOKEN is unset, which is the default: an unconfigured or
    development deployment then carries no instrumentation overhead and publishes no
    /metrics route at all, rather than one that answers 404 to everybody forever.
    """
    if not settings.METRICS_TOKEN:
        return

    (
        Instrumentator(
            # Exact status codes rather than "2xx"/"5xx" buckets. The grouped form
            # cannot tell a wave of 401s (someone's token expired) from 404s (a
            # scanner) or 422s (a client sending the wrong shape), and those are three
            # completely different problems that all look like "4xx went up".
            should_group_status_codes=False,
            should_group_untemplated=True,
            # Scrapes are traffic against this API, and counting them would put a
            # permanent 4-per-minute floor under every request-rate panel.
            excluded_handlers=["/metrics"],
            should_instrument_requests_inprogress=True,
            inprogress_labels=False,
            registry=REGISTRY,
        )
        # Each metric needs the registry passed explicitly too - these default to
        # prometheus_client's global one, so instrumenting into a private registry
        # while the metrics themselves land in the global default would produce a
        # /metrics response that is always empty.
        .add(metrics.latency(buckets=LATENCY_BUCKETS, registry=REGISTRY))
        .add(metrics.requests(registry=REGISTRY))
        .add(metrics.response_size(registry=REGISTRY))
        .instrument(app)
    )

    @app.get("/metrics", include_in_schema=False, tags=["Monitoring"])
    def prometheus_metrics(request: Request) -> Response:
        _require_metrics_token(request)
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def _require_metrics_token(request: Request) -> None:
    """404 unless the caller presented `Authorization: Bearer <METRICS_TOKEN>`.

    compare_digest, not ==, so the token can't be recovered a character at a time by
    timing the responses - the same care core/security.py takes with the others.
    """
    header = request.headers.get("authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        credentials, settings.METRICS_TOKEN
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
