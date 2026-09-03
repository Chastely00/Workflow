from datetime import date, datetime

import pytest

from data_analysts.pit import PitError, normalize_date, select_latest_pit_rows


def test_normalize_date_strips_time_component():
    assert normalize_date("2025-03-31 00:00:00") == "2025-03-31"
    assert normalize_date("2025-03-31T13:45:01") == "2025-03-31"
    assert normalize_date(datetime(2025, 3, 31, 13, 45, 1)) == "2025-03-31"
    assert normalize_date(date(2025, 3, 31)) == "2025-03-31"


def test_normalize_date_rejects_unparseable_text():
    with pytest.raises(PitError, match="unsupported date value"):
        normalize_date("not-a-date")


def test_select_latest_pit_rows_excludes_future_key3_and_uses_latest_mdate_for_same_key3():
    rows = [
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-01",
            "eps": 10,
        },
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-02",
            "eps": 11,
        },
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-09-01",
            "revision_date": "2025-06-03",
            "eps": 12,
        },
    ]

    selected, diagnostics = select_latest_pit_rows(
        rows,
        logical_key=["ticker", "no", "sem", "curr", "merg", "period_end_date"],
        availability_field="source_available_date",
        revision_field="revision_date",
        decision_date="2025-08-31",
    )

    assert len(selected) == 1
    assert selected[0]["eps"] == 11
    assert diagnostics["input_row_count"] == 3
    assert diagnostics["eligible_row_count"] == 2
    assert diagnostics["future_row_count"] == 1
    assert diagnostics["resolved_duplicate_count"] == 1
    assert diagnostics["unresolved_duplicate_count"] == 0


def test_select_latest_pit_rows_fails_when_latest_revision_is_still_ambiguous():
    rows = [
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-02",
            "source_row_id": "a",
        },
        {
            "ticker": "2330",
            "no": "Q",
            "sem": "2",
            "curr": "TWD",
            "merg": "Y",
            "period_end_date": "2025-06-30",
            "source_available_date": "2025-08-14",
            "revision_date": "2025-06-02",
            "source_row_id": "b",
        },
    ]

    with pytest.raises(PitError, match="unresolved duplicate"):
        select_latest_pit_rows(
            rows,
            logical_key=["ticker", "no", "sem", "curr", "merg", "period_end_date"],
            availability_field="source_available_date",
            revision_field="revision_date",
            decision_date="2025-08-31",
        )


@pytest.mark.parametrize(
    ("row_override", "message"),
    [
        ({"source_available_date": None}, "missing availability_field"),
        ({"source_available_date": ""}, "blank availability_field"),
    ],
)
def test_select_latest_pit_rows_fails_closed_on_missing_or_blank_availability(row_override, message):
    row = {
        "ticker": "2330",
        "source_available_date": "2025-08-14",
    }
    if row_override["source_available_date"] is None:
        row.pop("source_available_date")
    else:
        row.update(row_override)

    with pytest.raises(PitError, match=message):
        select_latest_pit_rows(
            [row],
            logical_key=["ticker"],
            availability_field="source_available_date",
            revision_field=None,
            decision_date="2025-08-31",
        )


@pytest.mark.parametrize(
    ("row_override", "message"),
    [
        ({"ticker": None}, "missing logical_key"),
        ({"ticker": ""}, "blank logical_key"),
    ],
)
def test_select_latest_pit_rows_fails_closed_on_missing_or_blank_logical_key(row_override, message):
    row = {
        "ticker": "2330",
        "source_available_date": "2025-08-14",
    }
    if row_override["ticker"] is None:
        row.pop("ticker")
    else:
        row.update(row_override)

    with pytest.raises(PitError, match=message):
        select_latest_pit_rows(
            [row],
            logical_key=["ticker"],
            availability_field="source_available_date",
            revision_field=None,
            decision_date="2025-08-31",
        )


@pytest.mark.parametrize(
    ("row_override", "message"),
    [
        ({"revision_date": None}, "missing revision_field"),
        ({"revision_date": ""}, "blank revision_field"),
    ],
)
def test_select_latest_pit_rows_fails_closed_on_missing_or_blank_revision(row_override, message):
    row = {
        "ticker": "2330",
        "source_available_date": "2025-08-14",
        "revision_date": "2025-06-02",
    }
    if row_override["revision_date"] is None:
        row.pop("revision_date")
    else:
        row.update(row_override)

    with pytest.raises(PitError, match=message):
        select_latest_pit_rows(
            [row],
            logical_key=["ticker"],
            availability_field="source_available_date",
            revision_field="revision_date",
            decision_date="2025-08-31",
        )
