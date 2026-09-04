import pytest

from data_analysts import raw_families
from data_analysts.raw_families import RawFamilyError, normalize_raw_family


def _registry() -> dict:
    return {
        "schema_version": "1.0",
        "families": {
            "trading_calendar": {
                "availability_field": "zdate",
                "date_normalization": "date_only",
                "logical_key": ["date", "market"],
                "revision_field": None,
                "selected_view": False,
            },
            "monthly_sales": {
                "availability_field": "annd_s",
                "date_normalization": "date_only",
                "logical_key": ["ticker", "source_period_date"],
                "revision_field": "mdate",
                "selected_view": False,
            },
            "financial_statement_raw": {
                "availability_field": "key3",
                "date_normalization": "date_only",
                "logical_key": [
                    "ticker",
                    "no",
                    "sem",
                    "curr",
                    "merg",
                    "period_end_date",
                    "source_available_date",
                    "revision_date",
                ],
                "revision_field": "mdate",
                "selected_view": False,
            },
            "financial_statement_pit_selected": {
                "availability_field": "source_available_date",
                "date_normalization": "date_only",
                "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date"],
                "revision_field": "revision_date",
                "selected_view": True,
            },
            "self_reported_numbers_raw": {
                "availability_field": "annd",
                "date_normalization": "date_only",
                "logical_key": [
                    "ticker",
                    "key3",
                    "sem",
                    "curr",
                    "merg",
                    "period_end_date",
                    "source_available_date",
                    "revision_date",
                ],
                "revision_field": "mdate",
                "selected_view": False,
            },
        },
    }


def test_trading_calendar_uses_blank_date_rmk_as_trading_day():
    result = normalize_raw_family(
        "trading_calendar",
        [
            {"zdate": "2025-01-02 00:00:00", "mkt": "TWSE", "date_rmk": "", "source_row_id": "a"},
            {"zdate": "2025-01-03", "mkt": "TWSE", "date_rmk": "休市", "source_row_id": "b"},
        ],
        _registry(),
    )
    rows = result["raw_rows"]
    assert rows[0]["date"] == "2025-01-02"
    assert rows[0]["market"] == "TWSE"
    assert rows[0]["is_trading_day"] is True
    assert rows[1]["is_trading_day"] is False
    assert result["diagnostics"]["published_row_count"] == 2
    assert result["diagnostics"]["pit_parse_failure_count"] == 0


def test_monthly_sales_normalizes_period_and_availability_dates():
    result = normalize_raw_family(
        "monthly_sales",
        [
            {
                "coid": "2330",
                "mdate": "2025-06-01",
                "annd_s": "2025-07-10 13:30:00",
                "sales": 100,
                "source_row_id": "a",
            }
        ],
        _registry(),
    )
    row = result["raw_rows"][0]
    assert row["ticker"] == "2330"
    assert row["source_period_date"] == "2025-06-01"
    assert row["source_available_date"] == "2025-07-10"
    assert row["sales"] == 100


@pytest.mark.parametrize("family_id", ["daily_tradability", "daily_chip"])
def test_daily_panel_families_use_mdate_as_canonical_date_and_source_available_date(family_id):
    registry = _registry()
    registry["families"][family_id] = {
        "availability_field": "mdate",
        "date_normalization": "date_only",
        "logical_key": ["date", "ticker"],
        "revision_field": None,
        "selected_view": False,
    }
    result = normalize_raw_family(
        family_id,
        [{"coid": "2330", "date": "2025-01-02", "mdate": "2025-01-03", "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["date"] == "2025-01-03"
    assert row["ticker"] == "2330"
    assert row["source_available_date"] == "2025-01-03"


def test_daily_chip_normalizes_mdate_to_canonical_date_and_source_available_date():
    registry = _registry()
    registry["families"]["daily_chip"] = {
        "availability_field": "mdate",
        "date_normalization": "date_only",
        "logical_key": ["date", "ticker"],
        "revision_field": None,
        "selected_view": False,
    }
    result = normalize_raw_family(
        "daily_chip",
        [{"coid": "2330", "mdate": "2026-01-05 00:00:00", "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["date"] == "2026-01-05"
    assert row["ticker"] == "2330"
    assert row["source_available_date"] == "2026-01-05"


def test_financial_statement_preserves_raw_revisions_and_selects_latest_revision():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "2330",
                "no": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "key3": "2025-08-14",
                "mdate": "2025-08-15",
                "eps": 10,
                "source_row_id": "a",
            },
            {
                "coid": "2330",
                "no": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "key3": "2025-08-14",
                "mdate": "2025-08-20",
                "eps": 11,
                "source_row_id": "b",
            },
            {
                "coid": "2330",
                "no": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "key3": "2025-09-01",
                "mdate": "2025-09-02",
                "eps": 12,
                "source_row_id": "c",
            },
        ],
        _registry(),
        decision_dates=["2025-08-31"],
    )
    assert len(result["raw_rows"]) == 3
    selected = result["selected_rows"]
    assert len(selected) == 1
    assert selected[0]["decision_date"] == "2025-08-31"
    assert selected[0]["eps"] == 11
    assert result["diagnostics"]["future_row_count"] == 1
    assert result["diagnostics"]["resolved_duplicate_count"] == 1


def test_financial_statement_raw_preserves_a_q_ttm_and_reports_selected_q_count():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "2330",
                "no": "A",
                "sem": "4",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2024-12-31",
                "key3": "2025-03-31",
                "mdate": "2025-04-01",
                "eps": 40,
                "source_row_id": "a",
            },
            {
                "coid": "2330",
                "no": "Q",
                "sem": "1",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-05-15",
                "mdate": "2025-05-16",
                "eps": 10,
                "source_row_id": "q",
            },
            {
                "coid": "2330",
                "no": "TTM",
                "sem": "1",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-05-15",
                "mdate": "2025-05-16",
                "eps": 42,
                "source_row_id": "t",
            },
        ],
        _registry(),
        decision_dates=["2025-05-31"],
    )
    assert {row["no"] for row in result["raw_rows"]} == {"A", "Q", "TTM"}
    assert result["diagnostics"]["rows_by_no"] == {"A": 1, "Q": 1, "TTM": 1}
    assert result["diagnostics"]["selected_no_q_row_count"] == 1


def test_financial_statement_selected_pit_unresolved_duplicate_fails_closed():
    with pytest.raises(RawFamilyError, match="unresolved duplicate"):
        normalize_raw_family(
            "financial_statement_raw",
            [
                {
                    "coid": "2330",
                    "no": "Q",
                    "sem": "2",
                    "curr": "TWD",
                    "merg": "Y",
                    "endd": "2025-06-30",
                    "key3": "2025-08-14",
                    "mdate": "2025-08-20",
                    "eps": 10,
                    "source_row_id": "a",
                },
                {
                    "coid": "2330",
                    "no": "Q",
                    "sem": "2",
                    "curr": "TWD",
                    "merg": "Y",
                    "endd": "2025-06-30",
                    "key3": "2025-08-14",
                    "mdate": "2025-08-20",
                    "eps": 11,
                    "source_row_id": "b",
                },
            ],
            _registry(),
            decision_dates=["2025-08-31"],
        )


def test_financial_statement_selected_pit_uses_later_raw_key3_timestamp_for_same_day_tie():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "3576",
                "no": "A",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2022-03-31",
                "key3": "2025-01-10 17:42:32.984000",
                "mdate": "2022-03-01",
                "eps": 10,
                "source_row_id": "early",
            },
            {
                "coid": "3576",
                "no": "A",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2022-03-31",
                "key3": "2025-01-10 18:22:21.502000",
                "mdate": "2022-03-01",
                "eps": 11,
                "source_row_id": "late",
            },
        ],
        _registry(),
        decision_dates=["2025-01-31"],
    )

    assert len(result["selected_rows"]) == 1
    assert result["selected_rows"][0]["source_row_id"] == "late"
    assert result["selected_rows"][0]["eps"] == 11
    assert result["diagnostics"]["resolved_same_day_source_timestamp_count"] == 1
    assert result["diagnostics"]["resolved_duplicate_count"] == 1
    assert result["diagnostics"]["unresolved_duplicate_count"] == 0


def test_financial_statement_raw_collapses_same_day_key3_timestamp_duplicate_before_publish():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "1210",
                "no": "A",
                "sem": "3",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2023-09-30",
                "key3": "2024-11-12 10:06:04.734000",
                "mdate": "2023-09-01",
                "r103": 12.35,
                "source_row_id": "early",
            },
            {
                "coid": "1210",
                "no": "A",
                "sem": "3",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2023-09-30",
                "key3": "2024-11-12 14:02:15.030000",
                "mdate": "2023-09-01",
                "r103": 12.35,
                "source_row_id": "late",
            },
        ],
        _registry(),
    )

    assert [row["source_row_id"] for row in result["raw_rows"]] == ["late"]
    assert result["diagnostics"]["raw_same_day_source_timestamp_resolved_count"] == 1
    assert result["diagnostics"]["duplicate_logical_key_count"] == 0


def test_financial_statement_selected_pit_evolves_across_multiple_decision_dates():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "3576",
                "no": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-01-10",
                "mdate": "2025-01-10",
                "eps": 10,
                "source_row_id": "jan",
            },
            {
                "coid": "3576",
                "no": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-02-01",
                "mdate": "2025-02-02",
                "eps": 20,
                "source_row_id": "feb-old-revision",
            },
            {
                "coid": "3576",
                "no": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-02-01",
                "mdate": "2025-02-04",
                "eps": 21,
                "source_row_id": "feb-new-revision",
            },
            {
                "coid": "3576",
                "no": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-03-01 09:00:00",
                "mdate": "2025-03-05",
                "eps": 30,
                "source_row_id": "mar-early-timestamp",
            },
            {
                "coid": "3576",
                "no": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-03-01 10:00:00",
                "mdate": "2025-03-05",
                "eps": 31,
                "source_row_id": "mar-late-timestamp",
            },
        ],
        _registry(),
        decision_dates=["2025-01-15", "2025-02-10", "2025-03-15"],
    )

    assert [(row["decision_date"], row["source_row_id"], row["eps"]) for row in result["selected_rows"]] == [
        ("2025-01-15", "jan", 10),
        ("2025-02-10", "feb-new-revision", 21),
        ("2025-03-15", "mar-late-timestamp", 31),
    ]
    assert result["diagnostics"]["decision_date_count"] == 3
    assert result["diagnostics"]["resolved_same_day_source_timestamp_count"] == 1
    assert result["diagnostics"]["unresolved_duplicate_count"] == 0


def test_financial_statement_state_updates_do_not_repeat_unchanged_snapshot():
    result = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "3576",
                "no": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-03-31",
                "key3": "2025-01-10",
                "mdate": "2025-01-10",
                "eps": 10,
                "source_row_id": "jan",
            }
        ],
        _registry(),
        decision_dates=["2025-01-15", "2025-02-10", "2025-03-15"],
        selected_materialization="state_updates",
    )

    assert [
        (row["decision_date"], row["source_row_id"], row["eps"])
        for row in result["selected_rows"]
    ] == [("2025-01-15", "jan", 10)]
    assert result["diagnostics"]["selected_materialization"] == "state_updates"


def test_self_reported_state_updates_do_not_repeat_unchanged_snapshot():
    result = normalize_raw_family(
        "self_reported_numbers_raw",
        [
            {
                "coid": "3576",
                "key3": "Q",
                "sem": "1",
                "curr": "NTD",
                "merg": "Y",
                "endd": "2025-06-30",
                "annd": "2025-07-20",
                "mdate": "2025-07-21",
                "source_row_id": "jul",
            }
        ],
        _registry(),
        decision_dates=["2025-07-31", "2025-08-01"],
        selected_materialization="state_updates",
    )

    assert [row["decision_date"] for row in result["selected_rows"]] == ["2025-07-31"]
    assert result["diagnostics"]["selected_materialization"] == "state_updates"


def test_financial_statement_multi_date_selection_does_not_call_selector_per_decision_date(monkeypatch):
    calls = 0
    original = raw_families.select_latest_pit_rows

    def counting_selector(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(raw_families, "select_latest_pit_rows", counting_selector)

    normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "2330",
                "no": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "key3": "2025-01-10",
                "mdate": "2025-01-11",
                "eps": 10,
                "source_row_id": "a",
            },
            {
                "coid": "2330",
                "no": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "key3": "2025-02-10",
                "mdate": "2025-02-11",
                "eps": 11,
                "source_row_id": "b",
            },
        ],
        _registry(),
        decision_dates=["2025-01-31", "2025-02-28", "2025-03-31"],
    )

    assert calls == 0


def test_self_reported_numbers_keeps_key3_as_category():
    result = normalize_raw_family(
        "self_reported_numbers_raw",
        [
            {
                "coid": "2330",
                "key3": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "annd": "2025-07-20",
                "mdate": "2025-07-21",
                "value": 1,
                "source_row_id": "a",
            }
        ],
        _registry(),
        decision_dates=["2025-07-31"],
    )
    assert result["raw_rows"][0]["key3"] == "Q"
    assert result["raw_rows"][0]["source_available_date"] == "2025-07-20"
    assert result["selected_rows"][0]["key3"] == "Q"


def test_self_reported_numbers_uses_mdate_as_period_end_when_endd_missing():
    result = normalize_raw_family(
        "self_reported_numbers_raw",
        [
            {
                "coid": "2330",
                "key3": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "annd": "2025-07-20",
                "mdate": "2025-07-21",
                "value": 1,
                "source_row_id": "afestm1-real-shape",
            }
        ],
        _registry(),
        decision_dates=["2025-07-31"],
    )

    row = result["raw_rows"][0]
    assert row["period_end_date"] == "2025-07-21"
    assert row["source_available_date"] == "2025-07-20"
    assert row["revision_date"] == "2025-07-21"
    assert result["selected_rows"][0]["period_end_date"] == "2025-07-21"


def test_self_reported_numbers_selected_pit_reports_key3_category_counts():
    result = normalize_raw_family(
        "self_reported_numbers_raw",
        [
            {
                "coid": "2330",
                "key3": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "annd": "2025-07-20",
                "mdate": "2025-07-21",
                "value": 1,
                "source_row_id": "a",
            }
        ],
        _registry(),
        decision_dates=["2025-07-31"],
    )
    assert result["diagnostics"]["selected_key3_category_counts"] == {"Q": 1}


def test_missing_required_pit_field_fails_closed():
    with pytest.raises(RawFamilyError, match="missing required PIT field"):
        normalize_raw_family("monthly_sales", [{"coid": "2330", "mdate": "2025-06-01"}], _registry())


def test_generic_governance_family_uses_mdate_as_source_available_date():
    registry = _registry()
    registry["families"]["director_supervisor_holdings"] = {
        "availability_field": "mdate",
        "date_normalization": "date_only",
        "logical_key": ["ticker", "source_date"],
        "revision_field": "mdate",
        "selected_view": False,
    }
    result = normalize_raw_family(
        "director_supervisor_holdings",
        [{"coid": "2330", "mdate": "2025-01-15 00:00:00", "shares": 10, "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["ticker"] == "2330"
    assert row["source_date"] == "2025-01-15"
    assert row["source_available_date"] == "2025-01-15"
    assert row["shares"] == 10


def test_generic_governance_family_reports_required_diagnostics():
    registry = _registry()
    registry["families"]["treasury_stock_events"] = {
        "availability_field": "mdate",
        "date_normalization": "date_only",
        "logical_key": ["ticker", "source_date"],
        "revision_field": "mdate",
        "selected_view": False,
    }
    result = normalize_raw_family(
        "treasury_stock_events",
        [
            {"coid": "2330", "mdate": "2025-01-15", "source_row_id": "a"},
            {"coid": "2330", "mdate": "2025-01-15", "source_row_id": "b"},
        ],
        registry,
    )
    diagnostics = result["diagnostics"]
    assert diagnostics["source_row_count"] == 2
    assert diagnostics["published_row_count"] == 2
    assert diagnostics["pit_null_count"] == 0
    assert diagnostics["pit_parse_failure_count"] == 0
    assert diagnostics["duplicate_logical_key_count"] == 1
    assert diagnostics["unresolved_duplicate_count"] == 0


def test_source_collection_ids_are_not_accepted_by_generic_mdate_normalizer():
    registry = _registry()
    for family_id in ["AINVFINB", "AFESTM1", "AINVFQ1", "APISHRACTW"]:
        registry["families"][family_id] = {
            "availability_field": "mdate",
            "date_normalization": "date_only",
            "logical_key": ["ticker", "source_date"],
            "revision_field": "mdate",
            "selected_view": False,
        }

    for family_id in ["AINVFINB", "AFESTM1", "AINVFQ1", "APISHRACTW"]:
        with pytest.raises(RawFamilyError):
            normalize_raw_family(
                family_id,
                [{"coid": "2330", "mdate": "2025-01-15", "source_row_id": family_id}],
                registry,
            )


def test_unknown_unregistered_family_id_fails_closed():
    with pytest.raises(RawFamilyError):
        normalize_raw_family(
            "unknown_raw_family",
            [{"coid": "2330", "mdate": "2025-01-15", "source_row_id": "a"}],
            _registry(),
        )


def test_dedicated_statement_family_ids_still_bypass_generic_mdate_normalizer():
    financial_statement = normalize_raw_family(
        "financial_statement_raw",
        [
            {
                "coid": "2330",
                "no": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "key3": "2025-08-14",
                "mdate": "2025-08-15",
                "eps": 10,
                "source_row_id": "a",
            }
        ],
        _registry(),
    )
    assert financial_statement["raw_rows"][0]["source_available_date"] == "2025-08-14"
    assert financial_statement["raw_rows"][0]["revision_date"] == "2025-08-15"

    self_reported = normalize_raw_family(
        "self_reported_numbers_raw",
        [
            {
                "coid": "2330",
                "key3": "Q",
                "sem": "2",
                "curr": "TWD",
                "merg": "Y",
                "endd": "2025-06-30",
                "annd": "2025-07-20",
                "mdate": "2025-07-21",
                "value": 1,
                "source_row_id": "b",
            }
        ],
        _registry(),
    )
    assert self_reported["raw_rows"][0]["key3"] == "Q"
    assert self_reported["raw_rows"][0]["source_available_date"] == "2025-07-20"
    assert self_reported["raw_rows"][0]["revision_date"] == "2025-07-21"


def test_futures_near_month_uses_chinese_date_field():
    registry = _registry()
    registry["families"]["taiwan_index_futures_near_month"] = {
        "availability_field": "日期",
        "date_normalization": "date_only",
        "logical_key": ["date", "contract"],
        "revision_field": None,
        "selected_view": False,
    }
    result = normalize_raw_family(
        "taiwan_index_futures_near_month",
        [{"日期": "2025-01-02", "契約": "TXF202501", "收盤價": 23000, "source_row_id": "a"}],
        registry,
    )
    row = result["raw_rows"][0]
    assert row["date"] == "2025-01-02"
    assert row["source_available_date"] == "2025-01-02"
    assert row["contract"] == "TXF202501"


def test_futures_near_month_reports_duplicate_date_contract_count():
    registry = _registry()
    registry["families"]["taiwan_index_futures_near_month"] = {
        "availability_field": "日期",
        "date_normalization": "date_only",
        "logical_key": ["date", "contract"],
        "revision_field": None,
        "selected_view": False,
    }
    result = normalize_raw_family(
        "taiwan_index_futures_near_month",
        [
            {"日期": "2025-01-02", "契約": "TXF202501", "source_row_id": "a"},
            {"日期": "2025-01-02", "契約": "TXF202501", "source_row_id": "b"},
        ],
        registry,
    )
    assert result["diagnostics"]["duplicate_date_contract_count"] == 1
