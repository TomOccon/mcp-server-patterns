"""FakeBackend: a synthetic in-memory 'lakehouse'. No network, no real data.

It stands in for a vendor platform API: tables, jobs, served models, a vector index and
compute clusters. Swap it for a real client that implements the same methods.
"""
from __future__ import annotations

import random
import re
from typing import Any, Iterable, Optional

from .errors import ToolError

ALL_FEATURES = frozenset({"catalog", "jobs", "model_serving", "vector_search", "compute"})

_DOCS = [
    "quarterly revenue forecast for the retail segment",
    "customer churn drivers and retention playbook",
    "data quality incident review for the orders pipeline",
    "onboarding guide for new analysts and warehouse access",
    "cluster cost optimization and autoscaling policy",
    "model monitoring checklist for drift and latency",
    "vector index rebuild runbook and rollback steps",
    "table retention policy and archival schedule",
    "access request process for restricted datasets",
    "incident postmortem template for failed batch jobs",
    "feature store naming conventions",
    "release notes for the catalog service",
]


class FakeBackend:
    MUTATING = frozenset({"drop_table", "run_job", "cancel_job", "start_cluster", "terminate_cluster"})

    def __init__(self, workspace: str, token: str, features: Optional[Iterable[str]] = None):
        self.workspace = workspace
        self._token = token
        self.features = frozenset(features) if features is not None else ALL_FEATURES
        self._clock = 0
        self.tables: dict[str, dict] = {}
        for name in ("orders", "customers", "events", "payments"):
            self._add_table(name, rows=10_000)
        for i in range(36):
            self._add_table(f"staging_{i:03d}", rows=100 + i * 37)
        statuses = ["succeeded", "failed", "running", "queued"]
        self.jobs: dict[str, dict] = {}
        for i in range(24):
            self._add_job(f"pipeline_{i % 4}", statuses[i % 4])
        self.models = {
            "sentiment-classifier": {"state": "ready", "task": "classification"},
            "churn-predictor": {"state": "ready", "task": "regression"},
            "text-embedder": {"state": "scaling", "task": "embedding"},
        }
        self.indexes = {"docs": list(_DOCS)}
        self.clusters = {
            "etl-small": {"state": "running", "nodes": 2},
            "ml-large": {"state": "terminated", "nodes": 8},
            "adhoc": {"state": "terminated", "nodes": 1},
        }

    # --- auth / capability -------------------------------------------------
    def __repr__(self) -> str:  # never include the token
        return f"FakeBackend(workspace={self.workspace!r})"

    def refresh_token(self, token: str) -> None:
        self._token = token

    def supports(self, feature: str) -> bool:
        return feature in self.features

    def _auth(self) -> None:
        if not self._token:
            raise ToolError("unauthorized", "no credentials for this workspace")

    # --- helpers ------------------------------------------------------------
    def _now(self) -> str:
        self._clock += 1
        return f"2026-01-01T00:{self._clock // 60 % 60:02d}:{self._clock % 60:02d}Z"

    def _add_table(self, name: str, rows: int) -> None:
        self.tables[name] = {
            "name": name,
            "rows": rows,
            "columns": [
                {"name": "id", "type": "int"},
                {"name": "created_at", "type": "timestamp"},
                {"name": "amount", "type": "float"},
                {"name": "region", "type": "string"},
            ],
        }

    def _add_job(self, pipeline: str, status: str) -> dict:
        job = {"job_id": f"job-{len(self.jobs) + 1:04d}", "pipeline": pipeline, "status": status, "started": self._now()}
        self.jobs[job["job_id"]] = job
        return job

    def _missing(self, kind: str, name: str, known: Iterable[str]) -> ToolError:
        names = sorted(known)[:8]
        return ToolError("not_found", f"{kind} '{name}' not found (examples: {', '.join(names)})")

    # --- catalog ------------------------------------------------------------
    def list_tables(self, name_contains: Optional[str] = None) -> list[dict]:
        self._auth()
        rows = [
            {"name": t["name"], "rows": t["rows"], "columns": len(t["columns"])}
            for t in self.tables.values()
            if not name_contains or name_contains.lower() in t["name"].lower()
        ]
        return sorted(rows, key=lambda r: r["name"])

    def get_table(self, name: str) -> dict:
        self._auth()
        if name not in self.tables:
            raise self._missing("table", name, self.tables)
        return self.tables[name]

    def sample_rows(self, name: str, n: int) -> list[dict]:
        t = self.get_table(name)
        rng = random.Random(name)
        regions = ["north", "south", "east", "west"]
        return [
            {"id": i + 1, "created_at": f"2025-06-{i % 28 + 1:02d}", "amount": round(rng.uniform(1, 500), 2),
             "region": rng.choice(regions)}
            for i in range(min(n, t["rows"]))
        ]

    def drop_table(self, name: str) -> dict:
        t = self.get_table(name)
        del self.tables[name]
        return {"dropped": name, "rows_removed": t["rows"]}

    # --- jobs ---------------------------------------------------------------
    def list_jobs(self, status: Optional[str] = None) -> list[dict]:
        self._auth()
        return [j for j in self.jobs.values() if status is None or j["status"] == status]

    def get_job(self, job_id: str) -> dict:
        self._auth()
        if job_id not in self.jobs:
            raise self._missing("job", job_id, self.jobs)
        return self.jobs[job_id]

    def run_job(self, pipeline: str) -> dict:
        self._auth()
        known = {j["pipeline"] for j in self.jobs.values()}
        if pipeline not in known:
            raise self._missing("pipeline", pipeline, known)
        return self._add_job(pipeline, "queued")

    def cancel_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job["status"] not in ("queued", "running"):
            raise ToolError("invalid_state", f"job {job_id} is {job['status']}; only queued or running jobs can be cancelled")
        job["status"] = "cancelled"
        return dict(job)

    # --- model serving ------------------------------------------------------
    def list_models(self) -> list[dict]:
        self._auth()
        return [{"name": k, **v} for k, v in sorted(self.models.items())]

    def invoke_model(self, model: str, inputs: list[str]) -> list[dict]:
        self._auth()
        if model not in self.models:
            raise self._missing("model", model, self.models)
        if self.models[model]["state"] != "ready":
            raise ToolError("invalid_state", f"model {model} is {self.models[model]['state']}, not ready")
        out = []
        for text in inputs:
            score = (sum(map(ord, text)) % 100) / 100
            out.append({"input": text, "score": score, "label": "positive" if score >= 0.5 else "negative"})
        return out

    # --- vector search ------------------------------------------------------
    def vector_search(self, index: str, query: str, top_k: int) -> list[dict]:
        self._auth()
        if index not in self.indexes:
            raise self._missing("index", index, self.indexes)
        q = set(re.findall(r"[a-z]+", query.lower()))
        scored = []
        for i, doc in enumerate(self.indexes[index]):
            d = set(re.findall(r"[a-z]+", doc.lower()))
            sim = len(q & d) / len(q | d) if q | d else 0.0
            scored.append({"doc_id": i, "score": round(sim, 4), "text": doc})
        scored.sort(key=lambda r: (-r["score"], r["doc_id"]))
        return scored[:top_k]

    # --- compute lifecycle --------------------------------------------------
    def list_clusters(self) -> list[dict]:
        self._auth()
        return [{"name": k, **v} for k, v in sorted(self.clusters.items())]

    def _cluster(self, name: str) -> dict:
        self._auth()
        if name not in self.clusters:
            raise self._missing("cluster", name, self.clusters)
        return self.clusters[name]

    def start_cluster(self, name: str) -> dict:
        c = self._cluster(name)
        c["state"] = "running"
        return {"name": name, **c}

    def terminate_cluster(self, name: str) -> dict:
        c = self._cluster(name)
        c["state"] = "terminated"
        return {"name": name, **c}


class ReadOnlyBackend:
    """Wraps a backend for dry runs: any mutating method raises instead of executing."""

    def __init__(self, inner: Any):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name in getattr(self._inner, "MUTATING", ()):
            raise ToolError("dry_run_violation", f"'{name}' would mutate state and is blocked during dry_run")
        return getattr(self._inner, name)
