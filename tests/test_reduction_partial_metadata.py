"""Tests for the ``# Meta:`` header parser used to recover the true
first-run-of-set from a partial reflectivity file.

This is the authoritative source for ``sequence_id`` — ``workflow.reduce()``'s
return value cannot be trusted when a run joins an existing sequence.
"""

from __future__ import annotations

from analyzer_tools.reduction.reduction import _read_partial_metadata


_REAL_HEADER = """\
# Experiment IPTS-36897 Run 226644
# Reduction 2.10.0.dev6
# Run title: Sample5_OCV-226642-3.
# Run start time: 2026-03-30T03:24:52.349394667
# Reduction time: Wed May 13 15:22:56 2026
# Q summing: False
# TOF weighted: False
# Bck in Q: False
# Theta offset: 0.0
# Stitching type: None
# Meta:{"wl_min": 2.68, "q_min": 0.08, "sequence_number": 3, "sequence_id": 226642, "run_number": "226644"}
# DataRun   NormRun   TwoTheta(deg)
# 226644    226561    7.00026
# Q [1/Angstrom]
0.08  0.0004  0.00005  0.0017
"""


def test_extracts_sequence_id(tmp_path):
    f = tmp_path / "REFL_226642_3_226644_partial.txt"
    f.write_text(_REAL_HEADER)
    meta = _read_partial_metadata(str(f))
    assert meta is not None
    assert meta["sequence_id"] == 226642
    assert meta["sequence_number"] == 3
    assert meta["run_number"] == "226644"


def test_returns_none_when_no_meta_line(tmp_path):
    f = tmp_path / "no_meta.txt"
    f.write_text("# Experiment IPTS-foo\n# DataRun\n0.08 0.0004\n")
    assert _read_partial_metadata(str(f)) is None


def test_returns_none_for_malformed_json(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("# Meta:{not valid json}\n")
    assert _read_partial_metadata(str(f)) is None


def test_returns_none_for_missing_file(tmp_path):
    assert _read_partial_metadata(str(tmp_path / "nope.txt")) is None


def test_stops_at_first_non_comment_line(tmp_path):
    """A # Meta: that appears AFTER the header isn't picked up — header only."""
    f = tmp_path / "after_data.txt"
    f.write_text(
        "# Experiment IPTS-foo\n"
        "0.08 0.0004\n"             # data section starts here
        "# Meta:{\"sequence_id\": 999}\n"  # not in header, must be ignored
    )
    assert _read_partial_metadata(str(f)) is None
