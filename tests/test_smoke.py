from __future__ import annotations

from retail_agent.data.bq_client import BigQueryRunner


def test_bigquery_runner_is_importable() -> None:
    assert BigQueryRunner.__init__ is not None
