"""The theta-offset --json stdout contract."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from analyzer_tools.analysis import theta_offset


def test_theta_offset_json_stdout(tmp_path: Path, monkeypatch) -> None:
    nexus = tmp_path / "REF_L_226642.nxs.h5"
    nexus.write_text("")  # existence only; compute is mocked
    db = tmp_path / "DB_226559.dat"
    db.write_text("")

    fake = {
        "run_name": "REF_L_226642.nxs.h5",
        "db_pixel": 150.0, "rb_pixel": 152.0, "delta_pixel": 2.0,
        "theta_motor": 0.600, "theta_calc": 0.610, "offset": 0.010,
        "mean_wl": 5.0, "gravity_dtheta": 0.0003,
        # private keys that must NOT leak into the JSON payload
        "_ypix": [1, 2, 3], "_profile": [4, 5, 6], "_fit_par": [1.0],
        "_peak_type": "supergauss",
    }
    monkeypatch.setattr(theta_offset, "compute_theta_offset", lambda *a, **k: fake)

    result = CliRunner().invoke(
        theta_offset.main, [str(nexus), "--db", str(db), "--json"]
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output.strip())
    assert payload["offset"] == 0.010
    assert payload["run_name"] == "REF_L_226642.nxs.h5"
    assert not any(k.startswith("_") for k in payload)  # private keys stripped
