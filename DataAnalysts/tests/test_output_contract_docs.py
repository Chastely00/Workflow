import json
import re
from pathlib import Path

import pytest


CONTRACTS = Path(__file__).parents[1] / "contracts"
CONTRACT = CONTRACTS / "OUTPUT_CONTRACT.md"
CLI_CONTRACT = CONTRACTS / "CLI_CONTRACT.md"
README = Path(__file__).parents[1] / "README.md"
EXIT_MATRIX_HEADING = "## Canonical CLI Exit Matrix"
REQUIRED_EXIT_COLUMNS = {
    "surface_id",
    "scope",
    "stage",
    "outcome",
    "exit_code",
    "job_result",
    "artifact_commitment",
    "stream",
}
CURRENT_EXIT_SURFACE_CORE = {
    "common.legacy_root_rejection": ("all", "pre_parser", "rejection"),
    "pipeline.parser_rejection": ("pipeline", "parser", "rejection"),
    "pipeline.preflight_failure": ("pipeline", "preflight", "failure"),
    "pipeline.execution_blocked": ("pipeline", "execution", "blocked"),
    "pipeline.planning_no_op": ("pipeline", "planning", "no_op"),
    "pipeline.executed_success": ("pipeline", "execution", "success"),
    "maintenance.parser_rejection": ("maintenance", "parser", "rejection"),
    "maintenance.handler_failure": ("maintenance", "handler", "failure"),
    "maintenance.success": ("maintenance", "handler", "success"),
}
CURRENT_EXIT_SURFACE_COMMITMENTS = {
    "pipeline.execution_blocked": ("blocked", "blocked_result"),
    "pipeline.executed_success": ("ready", "command_defined"),
    "maintenance.success": ("none", "selected_manifests"),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def raw_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, heading
    return match.group("body")


def markdown_table(text: str, heading: str) -> list[dict[str, str]]:
    body = raw_section(text, heading)
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    assert len(lines) >= 3, heading

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    headers = cells(lines[0])
    separator = cells(lines[1])
    assert len(headers) == len(set(headers))
    assert len(separator) == len(headers)
    assert all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)

    rows: list[dict[str, str]] = []
    for line in lines[2:]:
        values = cells(line)
        assert len(values) == len(headers)
        rows.append(dict(zip(headers, values, strict=True)))
    return rows


def validate_exit_matrix(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    assert rows
    assert all(REQUIRED_EXIT_COLUMNS <= row.keys() for row in rows)
    by_id = {row["surface_id"]: row for row in rows}
    assert len(by_id) == len(rows)

    allowed = {
        "scope": {"all", "pipeline", "maintenance"},
        "stage": {"pre_parser", "parser", "preflight", "planning", "execution", "handler"},
        "outcome": {"rejection", "failure", "blocked", "no_op", "success"},
        "job_result": {"none", "blocked", "ready"},
        "artifact_commitment": {
            "none",
            "blocked_result",
            "command_defined",
            "selected_manifests",
        },
        "stream": {"stderr", "stdout"},
    }
    stage_outcomes = {
        "pre_parser": {"rejection"},
        "parser": {"rejection"},
        "preflight": {"failure"},
        "planning": {"no_op"},
        "execution": {"blocked", "success"},
        "handler": {"failure", "success"},
    }
    for row in rows:
        for column, values in allowed.items():
            assert row[column] in values
        assert row["outcome"] in stage_outcomes[row["stage"]]
        exit_code = int(row["exit_code"])
        expected_exit = 0 if row["outcome"] in {"no_op", "success"} else 1
        if row["stage"] == "parser":
            expected_exit = 2
        assert exit_code == expected_exit
        assert row["stream"] == ("stdout" if exit_code == 0 else "stderr")
        if row["stage"] in {"parser", "preflight"}:
            assert row["job_result"] == "none"
            assert row["artifact_commitment"] == "none"
        if row["outcome"] in {"rejection", "failure"}:
            assert row["job_result"] == "none"
            assert row["artifact_commitment"] == "none"
        if row["outcome"] == "blocked":
            assert row["job_result"] == "blocked"
            assert row["artifact_commitment"] == "blocked_result"
        if row["artifact_commitment"] == "blocked_result":
            assert row["outcome"] == "blocked"
            assert row["job_result"] == "blocked"
        if row["outcome"] == "no_op":
            assert row["job_result"] == "none"
            assert row["artifact_commitment"] == "none"
        if row["job_result"] == "ready":
            assert row["outcome"] == "success"
        if row["job_result"] == "blocked":
            assert row["outcome"] == "blocked"
    return by_id


def fenced_block_after(text: str, marker: str, language: str) -> str:
    match = re.search(
        rf"{re.escape(marker)}\s*```{re.escape(language)}\s*\n"
        rf"(?P<body>.*?)\n```",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, marker
    return match.group("body")


def test_output_contract_defines_both_data_store_publisher_modes() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    assert "- `path_mode: project_relative | external_unrecorded`" in text
    assert "`project_relative`" in text
    assert "`external_unrecorded`" in text
    assert "`data_store_root: null`" in text
    assert "`--data-store <absolute-path>`" in text
    assert "不得把 external store 偽裝成 project-relative path" in text


def test_cli_exit_matrix_is_structured() -> None:
    rows = markdown_table(read(CLI_CONTRACT), EXIT_MATRIX_HEADING)
    assert rows
    assert REQUIRED_EXIT_COLUMNS <= rows[0].keys()


def test_output_contract_keeps_artifact_paths_store_relative_in_both_modes() -> None:
    text = read(CONTRACT)
    normalized = " ".join(text.split())

    assert (
        "兩種 publisher mode 的 `artifact_paths` 都必須維持 data-store-relative"
        in normalized
    )


def test_manifest_fingerprint_contract_is_targeted_and_incremental() -> None:
    cli = read(CLI_CONTRACT)
    output = read(CONTRACT)

    assert "repair-manifest-fingerprints" in cli
    assert "--artifact-id" in cli
    assert "不得預設處理全部 manifests" in cli
    assert '"schema_version": "1.1"' in output
    assert "artifact_fingerprints" in output
    assert "SHA-256" in output


def test_root_workspace_repair_command_uses_project_root_relative_data_store() -> None:
    for document in (README, CLI_CONTRACT):
        text = read(document)
        match = re.search(
            r"repair-manifest-fingerprints\s+`\s*\r?\n"
            r"\s*--project-root\s+\.\\DataAnalysts\s+`\s*\r?\n"
            r"\s*--data-store\s+(?P<data_store>\S+)",
            text,
        )

        assert match is not None, document
        assert match.group("data_store") == ".\\data_store"
        assert "--data-store .\\DataAnalysts\\data_store" not in text


def test_cli_exit_matrix_matches_current_surface_minimums() -> None:
    by_id = validate_exit_matrix(
        markdown_table(read(CLI_CONTRACT), EXIT_MATRIX_HEADING)
    )

    for surface_id, expected in CURRENT_EXIT_SURFACE_CORE.items():
        row = by_id[surface_id]
        assert tuple(row[column] for column in ("scope", "stage", "outcome")) == expected
    for surface_id, expected in CURRENT_EXIT_SURFACE_COMMITMENTS.items():
        row = by_id[surface_id]
        assert tuple(
            row[column] for column in ("job_result", "artifact_commitment")
        ) == expected


def test_cli_exit_matrix_allows_extra_columns_and_surfaces() -> None:
    synthetic = """## Canonical CLI Exit Matrix

| owner | stream | artifact_commitment | outcome | surface_id | stage | job_result | scope | exit_code |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| data_platform | stderr | none | failure | maintenance.future_validation_failure | handler | none | maintenance | 1 |
| data_platform | stdout | selected_manifests | success | maintenance.success | handler | none | maintenance | 0 |
"""

    rows = markdown_table(synthetic, EXIT_MATRIX_HEADING)
    by_id = validate_exit_matrix(rows)

    assert by_id["maintenance.future_validation_failure"]["owner"] == "data_platform"


def test_cli_exit_matrix_rejects_success_ready_blocked_result() -> None:
    contradictory_row = {
        "surface_id": "pipeline.contradictory_success",
        "scope": "pipeline",
        "stage": "execution",
        "outcome": "success",
        "exit_code": "0",
        "job_result": "ready",
        "artifact_commitment": "blocked_result",
        "stream": "stdout",
    }

    with pytest.raises(AssertionError):
        validate_exit_matrix([contradictory_row])


def test_schema_1_1_manifest_example_is_complete_and_validator_compatible() -> None:
    output = read(CONTRACT)
    minimum_fields = {
        line.strip()
        for line in fenced_block_after(output, "最低欄位：", "text").splitlines()
        if line.strip()
    }
    example = json.loads(
        fenced_block_after(output, "schema `1.1` manifest 範例：", "json")
    )

    assert example["schema_version"] == "1.1"
    assert minimum_fields <= example.keys()

    artifact_paths = example["artifact_paths"]
    artifact_fingerprints = example["artifact_fingerprints"]
    assert artifact_paths == [
        fingerprint["artifact_path"] for fingerprint in artifact_fingerprints
    ]
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", fingerprint["sha256"]) is not None
        for fingerprint in artifact_fingerprints
    )
