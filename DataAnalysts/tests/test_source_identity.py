from data_analysts import extract as extract_module
from data_analysts import pipeline as pipeline_module
from data_analysts.extract import extract_family_rows_from_database


class Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        return list(self.rows)


class Database:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, name):
        return Collection(self.rows)

    def list_collection_names(self):
        return ["prices"]


def _extract(rows):
    return extract_family_rows_from_database(
        Database(rows),
        {"family_id": "daily", "collection": "prices", "source_profile": "small_snapshot"},
    )


def test_fallback_source_row_id_is_stable_across_query_order():
    rows = [{"ticker": "2330", "date": "2026-07-08"}, {"ticker": "2317", "date": "2026-07-08"}]
    forward = {row["ticker"]: row["source_row_id"] for row in _extract(rows)}
    reverse = {row["ticker"]: row["source_row_id"] for row in _extract(list(reversed(rows)))}
    assert forward == reverse
    assert all(identifier.startswith("prices:sha256:") for identifier in forward.values())


def test_mongo_source_identity_is_preserved_without_leaking_row_content():
    result = _extract([{"_id": "mongo-identity", "ticker": "SECRET-TICKER"}])[0]
    assert result["source_row_id"] == "prices:mongo-identity"
    assert "SECRET-TICKER" not in result["source_row_id"]


def test_configured_canonical_primary_key_uses_raw_aliases_and_ignores_nonkey_edits():
    family = {
        "family_id": "daily",
        "collection": "prices",
        "source_profile": "small_snapshot",
        "primary_key": ["date", "ticker"],
        "field_map": {"date": "mdate", "ticker": "coid", "close": "close_d"},
    }
    first = extract_family_rows_from_database(
        Database([{"mdate": "2026-07-08", "coid": "2330", "close_d": 100}]), family
    )[0]
    corrected = extract_family_rows_from_database(
        Database([{"coid": "2330", "close_d": 101, "mdate": "2026-07-08"}]), family
    )[0]
    changed_key = extract_family_rows_from_database(
        Database([{"mdate": "2026-07-08", "coid": "2317", "close_d": 100}]), family
    )[0]

    assert first["source_row_id"] == corrected["source_row_id"]
    assert first["source_row_id"] != changed_key["source_row_id"]
    assert ":pk-sha256:" in first["source_row_id"]


def test_family_extraction_destructively_consumes_one_payload_without_copying_rows(
    monkeypatch,
):
    payload_rows = [{"ticker": str(index)} for index in range(8)]
    original_ids = [id(row) for row in payload_rows]
    observed_lengths = []

    class TrackingList(list):
        def pop(self, *args):
            observed_lengths.append(len(self))
            return super().pop(*args)

    extracted_payload = TrackingList(payload_rows)
    monkeypatch.setattr(
        extract_module, "extract_rows_from_collection",
        lambda *args, **kwargs: extracted_payload,
    )

    result = extract_family_rows_from_database(
        Database([]),
        {"family_id": "daily", "collection": "prices", "source_profile": "small_snapshot"},
    )

    assert observed_lengths == list(range(len(payload_rows), 0, -1))
    assert extracted_payload == []
    assert [id(row) for row in result] == original_ids


def test_pipeline_field_mapping_reuses_source_list_and_row_identities(monkeypatch):
    from types import SimpleNamespace

    source_rows = [
        {
            "mdate": "2026-07-08" if index % 2 == 0 else "2026-07-07",
            "coid": f"{index:04d}",
            "raw_only": f"drop-{index}",
            "source_collection": "APISTKATTR.test",
            "source_row_id": f"stable-{index}",
            "source_dataset_id": "apistkattr.daily_tradability",
            "data_cutoff_at": "2026-07-08T12:00:00Z",
        }
        for index in range(1000)
    ]
    retained_ids = [id(row) for index, row in enumerate(source_rows) if index % 2 == 0]
    filtered_ids = {id(row) for index, row in enumerate(source_rows) if index % 2 == 1}
    family = {
        "family_id": "daily_tradability",
        "source_profile": "large_daily_panel",
        "field_map": {
            "date": "mdate",
            "ticker": "coid",
            "ticker_alias": "coid",
        },
    }
    config = SimpleNamespace(
        source_family_profiles={"families": [family]}, mongodb_sources={}
    )
    monkeypatch.setattr(
        pipeline_module, "_rows_for_family", lambda *args, **kwargs: source_rows
    )

    family_id, mapped_rows = next(
        pipeline_module._iter_family_rows(
            config,
            {"daily_tradability"},
            "2026-07-08",
            "2026-07-08",
            None,
            "bounded_backfill",
        )
    )

    assert family_id == "daily_tradability"
    assert mapped_rows is source_rows
    assert [id(row) for row in mapped_rows] == retained_ids
    assert not filtered_ids.intersection(id(row) for row in mapped_rows)
    assert [row["ticker"] for row in mapped_rows] == [
        f"{index:04d}" for index in range(0, 1000, 2)
    ]
    assert all(row["ticker"] == row["ticker_alias"] for row in mapped_rows)
    assert [row["source_row_id"] for row in mapped_rows] == [
        f"stable-{index}" for index in range(0, 1000, 2)
    ]
    assert all(row["source_collection"] == "APISTKATTR.test" for row in mapped_rows)
    assert all(
        row["source_dataset_id"] == "apistkattr.daily_tradability"
        for row in mapped_rows
    )
    assert all(
        row["data_cutoff_at"] == "2026-07-08T12:00:00Z"
        for row in mapped_rows
    )
    assert all("raw_only" not in row and "coid" not in row for row in mapped_rows)
