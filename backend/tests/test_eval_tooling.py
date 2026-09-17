"""Evaluation tooling must not silently corrupt the results log or its numbers."""

from pathlib import Path

import pytest

from evaluation import run_eval

RESULTS_MD = Path(__file__).resolve().parents[1] / "evaluation" / "RESULTS.md"


def test_tie_diagnostics_reports_random_tiebreak_expectation():
    # 40 accounts at the default top-5% gives k=2: one clear leader, then a
    # 30-way tie holding 3 fraud accounts competing for the remaining slot.
    rows = [{"account_id": "lead", "score": 9.0}]
    rows += [{"account_id": f"t{i}", "score": 1.0} for i in range(30)]
    rows += [{"account_id": f"low{i}", "score": 0.1} for i in range(9)]
    positives = {"t0", "t1", "t2"}

    ties = run_eval.tie_diagnostics(rows, "score", positives)

    assert ties["k"] == 2
    assert ties["tie_size"] == 30
    assert ties["slots_from_tie"] == 1
    assert ties["expected_tp_random_tiebreak"] == pytest.approx(3 / 30)


def test_run_eval_patch_only_rewrites_its_own_rows(tmp_path, monkeypatch):
    doc = tmp_path / "RESULTS.md"
    doc.write_text(
        "# Log\n\n| paysim | fusion | old | row |\n\n## Hand-written section\nkeep me\n"
    )
    monkeypatch.setattr(run_eval, "RESULTS_MD", doc)
    row = {
        "dataset": "paysim", "detector": "fusion", "precision": 0.5, "recall": 0.25,
        "f1": 0.333, "fpr": 0.01, "tp": 1, "fp": 1, "fn": 3, "tn": 95,
        "ground_truth_fraud_scored": 4, "accounts_evaluated": 100,
    }

    run_eval.patch_results_md([row])

    text = doc.read_text()
    assert "| paysim | fusion | 0.500 | 0.250 | 0.333 |" in text
    assert "old | row" not in text
    assert "## Hand-written section\nkeep me" in text


def test_run_eval_patch_refuses_to_invent_rows(tmp_path, monkeypatch):
    doc = tmp_path / "RESULTS.md"
    doc.write_text("# Log\n")
    monkeypatch.setattr(run_eval, "RESULTS_MD", doc)
    row = {"dataset": "paysim", "detector": "fusion", "f1": 0.0}
    with pytest.raises(SystemExit):
        run_eval.patch_results_md([row])


@pytest.mark.parametrize(
    "required",
    [
        "<!-- /multi-seed -->",
        "<!-- /isolation-forest -->",
        "<!-- /adversarial -->",
        "<!-- /perf-bench -->",
        "<!-- /ibm-aml -->",
        "### Layer 2 hub-concentration isolation",
        "## Analyst-feedback calibration",
        "## Co-hub scoring (optional, off by default)",
        "### PaySim: no detection signal (tie-break artifact)",
    ],
)
def test_results_log_keeps_every_section(required):
    """Eval scripts have wiped hand-written sections before; catch it in CI."""
    assert RESULTS_MD.read_text().count(required) == 1
