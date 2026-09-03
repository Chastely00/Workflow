from __future__ import annotations


def test_materialize_security_master_preserves_industry_and_derives_latest_market() -> None:
    from data_analysts.security_master_materialization import materialize_security_master_rows

    rows = materialize_security_master_rows([
        {
            "coid": "6906", "stk_name": "Groundhog", "enm": "Groundhog Inc.",
            "list_date": "2024-01-15", "delist_date": None,
            "tse_date": "2024-01-15", "otc_date": None,
            "main_ind_c": "M3600 資訊服務", "main_ind_e": "M3600 Digital and Cloud Services",
            "sub_ind_c": "", "sub_ind_e": "",
        },
        {
            "coid": "1235", "stk_name": "Example", "enm": "Example Inc.",
            "list_date": "2000-09-11", "delist_date": None,
            "tse_date": "2000-09-11", "otc_date": "1993-01-11",
            "main_ind_c": "M1100 食品", "main_ind_e": "M1100 Food",
            "sub_ind_c": "M1101 食品", "sub_ind_e": "M1101 Food",
        },
        {
            "coid": "7415", "stk_name": "Emerging", "enm": "Emerging Inc.",
            "list_date": "2026-06-17", "delist_date": None,
            "tse_date": None, "otc_date": None,
            "main_ind_c": "", "main_ind_e": "", "sub_ind_c": "", "sub_ind_e": "",
        },
    ], data_cutoff_at="2026-09-03T00:00:00Z")

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["6906"]["market"] == "TWSE"
    assert by_ticker["6906"]["market_identity_source"] == "APISTOCK_TSE_OTC_DATE"
    assert by_ticker["6906"]["main_industry_c"] == "M3600 資訊服務"
    assert by_ticker["6906"]["main_industry_e"] == "M3600 Digital and Cloud Services"
    assert by_ticker["6906"]["main_industry"] == "M3600 Digital and Cloud Services"
    assert by_ticker["1235"]["market"] == "TWSE"
    assert by_ticker["7415"]["market"] == "EMERGING"
