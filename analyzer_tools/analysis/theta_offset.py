"""
Theta offset calculator for the Liquids Reflectometer (BL-4B).

Fits the specular peak on the detector and compares with the motor-log angle
to compute the theta offset. Reproduces the calculation in
NR_Reduction._fit_and_calculate_theta() without requiring lr_reduction.
"""

import csv
import datetime
import os
import xml.etree.ElementTree as ET
from typing import Optional

import click
import h5py
import numpy as np
from scipy.optimize import curve_fit

# ═══════════════════════════════════════════════════════════════════════════
# Instrument settings table (from lr_reduction/settings.json)
# Each key maps to a list of {from, value} sorted by date.
# ═══════════════════════════════════════════════════════════════════════════
_SETTINGS = {
    "sample_detector_distance": [          # metres → converted to mm below
        {"from": "2014-10-10", "value": 1.83},
        {"from": "2024-08-26", "value": 1.355},
        {"from": "2025-01-01", "value": 1.83},
    ],
    "source_detector_distance": [          # metres → converted to mm below
        {"from": "2014-10-10", "value": 15.75},
        {"from": "2024-08-26", "value": 15.282},
        {"from": "2025-01-01", "value": 15.75},
    ],
    "pixel_width": [                       # mm
        {"from": "2014-10-10", "value": 0.70},
    ],
    "xi_reference": [                      # mm
        {"from": "2014-10-10", "value": 445},
    ],
    "s1_sample_distance": [                # metres → converted to mm below
        {"from": "2014-10-10", "value": 1.485},
    ],
    "num_y_pixels": [
        {"from": "2014-10-10", "value": 304},
    ],
}


def _read_settings(start_time_iso: str) -> dict:
    """Return instrument parameters valid for *start_time_iso* (ISO-8601)."""
    timestamp = datetime.datetime.fromisoformat(start_time_iso).date()
    out = {}
    for key, entries in _SETTINGS.items():
        chosen = None
        best_delta = None
        for entry in entries:
            valid_from = datetime.date.fromisoformat(entry["from"])
            delta = valid_from - timestamp
            if best_delta is None or (delta.total_seconds() < 0 and delta > best_delta):
                best_delta = delta
                chosen = entry["value"]
        out[key] = chosen
    # Convert to mm where the rest of the code expects it
    out["sample_detector_distance"] *= 1000
    out["source_detector_distance"] *= 1000
    out["s1_sample_distance"] *= 1000
    return out


# ═══════════════════════════════════════════════════════════════════════════
# NeXus helpers
# ═══════════════════════════════════════════════════════════════════════════

def _get_log_values(fname: str) -> dict:
    """Extract the motor logs needed for theta calculation from a NeXus file."""
    with h5py.File(fname, "r") as f:
        logs = {}
        logs["thi"] = float(f["entry/DASlogs/BL4B:Mot:thi.RBV/value"][-1])
        logs["ths"] = float(f["entry/DASlogs/BL4B:Mot:ths.RBV/value"][-1])
        logs["tthd"] = float(f["entry/DASlogs/BL4B:Mot:tthd.RBV/value"][-1])
        logs["xi"] = float(f["entry/DASlogs/BL4B:Mot:xi.RBV/average_value"][0])
        logs["start_time"] = f["entry/start_time"].asstr()[0]
        logs["op_mode"] = f["entry/DASlogs/BL4B:CS:ExpPl:OperatingMode/value"][0]
        try:
            logs["coordinates"] = int(
                f["entry/DASlogs/BL4B:CS:Mode:Coordinates/value"][0]
            )
        except Exception:
            pass  # older runs don't have this PV
    return logs


def _load_y_tof(fname: str, xmin: int, xmax: int,
                n_y: int = 304, n_x: int = 256) -> np.ndarray:
    """Histogram events into (y-pixel × TOF) and collapse over x-pixels."""
    with h5py.File(fname, "r") as f:
        e_offset = np.asarray(f["entry/bank1_events/event_time_offset"][:])
        event_id = np.asarray(f["entry/bank1_events/event_id"][:])
        pcharge = np.asarray(f["entry/proton_charge"][:])

    pcharge_val = pcharge[0] if len(pcharge) == 1 else pcharge

    xvals = event_id // n_y
    yvals = event_id % n_y
    x_good = (xvals >= xmin) & (xvals <= xmax)

    tofbin = 50
    tof_array = np.arange(0, 100000, tofbin)
    d_tof = tofbin
    bin_edges = np.concatenate([[tof_array[0] - d_tof / 2], tof_array + d_tof / 2])

    e_good = e_offset[x_good]
    y_good = yvals[x_good]

    y_tof = np.zeros((n_y, len(tof_array)))
    bin_indices = np.clip(np.digitize(e_good, bin_edges) - 1, 0, len(tof_array) - 1)
    np.add.at(y_tof, (y_good, bin_indices), 1)
    y_tof /= pcharge_val
    return y_tof


# ═══════════════════════════════════════════════════════════════════════════
# Peak fitting
# ═══════════════════════════════════════════════════════════════════════════

def _gaussian(x, a, x0, sig):
    return a * np.exp(-((x - x0) ** 2) / (2 * sig ** 2))


def _gaussian_slope(x, a, x0, sig, m, b):
    return _gaussian(x, a, x0, sig) + m * x + b


def _super_gaussian(x, a, x0, sig, ex):
    return a * np.exp(-((np.abs(x - x0) / sig) ** ex))


def _super_gaussian_slope(x, a, x0, sig, ex, m, b):
    return _super_gaussian(x, a, x0, sig, ex) + m * x + b


def _fit_peak(ypix, iY, peaktype="supergauss"):
    """Fit a peak and return parameters; par[1] is the fitted centre pixel."""
    mean = np.sum(iY * ypix) / np.sum(iY)
    var = np.sum(iY * (ypix - mean) ** 2) / np.sum(iY)
    sigma = np.sqrt(var)
    A0 = float(np.max(iY))

    if peaktype == "gauss":
        p0 = [A0, mean, sigma, 0.0, 0.0]
        par, _ = curve_fit(_gaussian_slope, ypix, iY, p0=p0)
    elif peaktype == "supergauss":
        p0 = [A0, mean, sigma, 2.0, 0.0, 0.0]
        bounds = (
            [A0 / 10, -np.inf, 1e-6, 2.0, -np.inf, -np.inf],
            [A0 * 10, np.inf, np.inf, 10.0, np.inf, np.inf],
        )
        par, _ = curve_fit(_super_gaussian_slope, ypix, iY, p0=p0, bounds=bounds)
    else:
        raise ValueError(f"peaktype must be 'gauss' or 'supergauss', got {peaktype!r}")

    return par


# ═══════════════════════════════════════════════════════════════════════════
# Gravity correction  (reproduces lr_reduction.gravity_correction._theta_sample)
# ═══════════════════════════════════════════════════════════════════════════

_G  = 9.8067              # m/s²
_H  = 6.6260715e-34       # J·s  (Planck)
_MN = 1.67492749804e-27   # kg   (neutron mass)


def _gravity_offset(theta_in_deg: float, wavelengths: np.ndarray,
                    xi_reference: float, xi: float,
                    s1_sample_distance: float) -> np.ndarray:
    """Return the gravity-induced angular offset (degrees) for each wavelength.

    Parameters
    ----------
    theta_in_deg : float
        Incident angle in degrees (absolute value).
    wavelengths : np.ndarray
        Neutron wavelengths in Angstrom.
    xi_reference, xi, s1_sample_distance : float
        Instrument distances in mm (same units as settings table).
    """
    sample_si = xi_reference - xi                      # mm
    slit_dist = s1_sample_distance - sample_si         # mm

    v = _H / (_MN * wavelengths * 1e-10)               # m/s
    k = _G / (2 * v**2)                                # 1/m

    x1 = sample_si / 1000                              # m
    x2 = (sample_si + slit_dist) / 1000                # m

    theta_in_rad = theta_in_deg * np.pi / 180
    y1 = x1 * np.tan(theta_in_rad)
    y2 = x2 * np.tan(theta_in_rad)

    x0 = (y1 - y2 + k * (x1**2 - x2**2)) / (2 * k * (x1 - x2))
    y0 = y2 + k * (x2 - x0)**2
    xs = x0 - np.sqrt(y0 / k)

    theta_sample = np.arctan(2 * k * (x0 - xs)) * 180 / np.pi
    return theta_sample - theta_in_deg                 # degrees


def _mean_wavelength_from_tof(fname: str, source_det_mm: float) -> float:
    """Return the intensity-weighted mean wavelength (Å) from event TOFs."""
    with h5py.File(fname, "r") as f:
        tofs = np.asarray(f["entry/bank1_events/event_time_offset"][:])  # µs
    # λ(Å) = h · TOF(µs) · 1e-6 / (m_n · L(mm) · 1e-3 · 1e-10)
    #       = h · TOF(µs) · 1e7 / (m_n · L(mm))
    constant = 1e-7 * _MN * source_det_mm / _H   # µs → Å conversion factor
    wl = tofs / constant
    return float(np.mean(wl))


# ═══════════════════════════════════════════════════════════════════════════
# DB header reader
# ═══════════════════════════════════════════════════════════════════════════

def _load_db_meta(db_path: str):
    """Return (db_pixel, db_tthd) from a pre-processed DB file header."""
    meta = {}
    with open(db_path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s.startswith("#"):
                break
            s = s.lstrip("#").strip()
            if "=" in s:
                k, v = s.split("=", 1)
                meta[k.strip()] = v.strip()
    return float(meta["db_pixel"]), float(meta["tthd"])


def _load_db_from_nexus(
    db_path: str, xmin: int, xmax: int, peak_type: str,
) -> tuple:
    """Return (db_pixel, db_tthd) by fitting the peak in a raw DB NeXus file."""
    log_values = _get_log_values(db_path)
    settings = _read_settings(log_values["start_time"])
    n_y = int(settings["num_y_pixels"])

    y_tof = _load_y_tof(db_path, xmin, xmax, n_y=n_y)
    y_tof = np.flipud(y_tof)
    ypix = np.linspace(n_y - 1, 0, n_y)

    profile = np.sum(y_tof, axis=1)
    peak_idx = int(np.argmax(profile))
    half_width = 20
    lo = max(0, int(ypix[min(peak_idx + half_width, n_y - 1)]))
    hi = min(n_y - 1, int(ypix[max(peak_idx - half_width, 0)]))

    mask = (ypix >= lo) & (ypix <= hi)
    par = _fit_peak(ypix[mask], np.sum(y_tof[mask, :], axis=1), peaktype=peak_type)
    db_pixel = float(par[1])
    db_tthd = log_values["tthd"]
    return db_pixel, db_tthd


def _is_nexus(path: str) -> bool:
    """Return True if *path* looks like an HDF5/NeXus file."""
    return path.endswith((".h5", ".hdf5", ".nxs", ".nxs.h5"))


# ═══════════════════════════════════════════════════════════════════════════
# Template XML parser
# ═══════════════════════════════════════════════════════════════════════════

def parse_template_xml(template_path: str) -> dict:
    """Parse a reduction template XML and return run→DB mapping.

    Parameters
    ----------
    template_path : str
        Path to a ``*_auto_template.xml`` file.

    Returns
    -------
    dict
        Mapping of ``{run_id: norm_run_id}`` where *run_id* is the sample
        data-set run number and *norm_run_id* is the direct-beam (DB) run
        number used for that segment.
    """
    tree = ET.parse(template_path)
    root = tree.getroot()
    mapping = {}
    for entry in root.iter("RefLData"):
        data_sets_el = entry.find("data_sets")
        norm_el = entry.find("norm_dataset")
        if data_sets_el is not None and norm_el is not None:
            run_id = data_sets_el.text.strip()
            norm_id = norm_el.text.strip()
            mapping[run_id] = norm_id
    return mapping


# ═══════════════════════════════════════════════════════════════════════════
# Core logic
# ═══════════════════════════════════════════════════════════════════════════

def compute_theta_offset(
    nexus: str,
    db_path: str,
    ymin: Optional[int] = None,
    ymax: Optional[int] = None,
    xmin: int = 50,
    xmax: int = 200,
    peak_type: str = "supergauss",
) -> dict:
    """Compute the theta offset for a NeXus run relative to a direct-beam file.

    Parameters
    ----------
    nexus : str
        Path to the NeXus event file (.nxs.h5).
    db_path : str
        Path to the direct-beam reference.  Can be either a pre-processed
        ``.dat`` file (with ``db_pixel`` and ``tthd`` in the header) or a
        raw NeXus ``.h5`` file — in which case the peak is fitted
        automatically.
    ymin, ymax : int, optional
        Y-pixel bounds for peak fitting.  Auto-detected from the peak if
        not provided.
    xmin, xmax : int
        Low-resolution x-pixel range for event selection.
    peak_type : str
        Peak model: ``"gauss"`` or ``"supergauss"``.

    Returns
    -------
    dict
        Keys: ``run_name``, ``db_pixel``, ``rb_pixel``, ``delta_pixel``,
        ``theta_motor``, ``theta_calc``, ``offset``.
    """
    log_values = _get_log_values(nexus)
    settings = _read_settings(log_values["start_time"])

    pixel_width = settings["pixel_width"]
    sample_det = settings["sample_detector_distance"]
    source_det = settings["source_detector_distance"]
    xi_ref     = settings["xi_reference"]
    s1_sample  = settings["s1_sample_distance"]
    n_y = int(settings["num_y_pixels"])

    y_tof = _load_y_tof(nexus, xmin, xmax, n_y=n_y)
    y_tof = np.flipud(y_tof)
    ypix = np.linspace(n_y - 1, 0, n_y)

    if _is_nexus(db_path):
        db_pixel, db_tthd = _load_db_from_nexus(db_path, xmin, xmax, peak_type)
    else:
        db_pixel, db_tthd = _load_db_meta(db_path)

    # Auto-detect fitting range if not given
    profile = np.sum(y_tof, axis=1)
    peak_idx = int(np.argmax(profile))
    half_width = 20
    if ymin is None:
        ymin = max(0, int(ypix[min(peak_idx + half_width, n_y - 1)]))
    if ymax is None:
        ymax = min(n_y - 1, int(ypix[max(peak_idx - half_width, 0)]))

    mask = (ypix >= ymin) & (ypix <= ymax)
    Ydata = ypix[mask]
    Idata = np.sum(y_tof[mask, :], axis=1)

    par = _fit_peak(Ydata, Idata, peaktype=peak_type)
    rb_pixel = par[1]

    d_pix = rb_pixel - db_pixel
    d_mm = d_pix * pixel_width
    theta_calc = np.degrees(np.arcsin(d_mm / sample_det))
    theta_calc += (log_values["tthd"] - db_tthd) / 2.0

    if "coordinates" in log_values:
        mode = log_values["coordinates"]
    elif log_values.get("op_mode") == "Free Liquid":
        mode = 0
    else:
        mode = 1

    theta_motor = log_values["thi"] if mode == 0 else log_values["ths"]
    offset = theta_calc - theta_motor

    # Gravity correction
    theta_in = abs(theta_motor)
    xi_val = log_values["xi"]
    mean_wl = _mean_wavelength_from_tof(nexus, source_det)
    wl_array = np.array([mean_wl])
    grav_sign = -1 if log_values["ths"] < -0.001 else 1
    grav_dtheta = float(
        grav_sign * _gravity_offset(theta_in, wl_array, xi_ref, xi_val, s1_sample)[0]
    )

    return {
        "run_name": os.path.basename(nexus),
        "db_pixel": db_pixel,
        "rb_pixel": float(rb_pixel),
        "delta_pixel": float(d_pix),
        "theta_motor": float(theta_motor),
        "theta_calc": float(theta_calc),
        "offset": float(offset),
        "mean_wl": mean_wl,
        "gravity_dtheta": grav_dtheta,
        # Carry data needed for the diagnostic plot
        "_ypix": Ydata,
        "_profile": Idata,
        "_fit_par": par,
        "_peak_type": peak_type,
    }


def log_result(result: dict, log_file: str, db_path: str) -> None:
    """Append a theta-offset result dict to a CSV log file."""
    write_header = not os.path.isfile(log_file)
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "nexus", "db_file",
                             "db_pixel", "rb_pixel", "delta_pixel",
                             "theta_motor", "theta_calc", "offset",
                             "mean_wl", "gravity_dtheta"])
        writer.writerow([
            datetime.datetime.now().isoformat(timespec="seconds"),
            result["run_name"],
            os.path.basename(db_path),
            f"{result['db_pixel']:.2f}",
            f"{result['rb_pixel']:.2f}",
            f"{result['delta_pixel']:+.2f}",
            f"{result['theta_motor']:.4f}",
            f"{result['theta_calc']:.4f}",
            f"{result['offset']:+.4f}",
            f"{result['mean_wl']:.2f}",
            f"{result['gravity_dtheta']:+.6f}",
        ])


def save_report(result: dict, output_path: str) -> None:
    """Save a diagnostic PNG showing the peak fit and offset summary."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ypix = result["_ypix"]
    profile = result["_profile"]
    par = result["_fit_par"]
    peak_type = result["_peak_type"]

    # Evaluate the fitted curve on a fine grid
    x_fine = np.linspace(ypix.min(), ypix.max(), 500)
    if peak_type == "gauss":
        y_fit = _gaussian_slope(x_fine, *par)
    else:
        y_fit = _super_gaussian_slope(x_fine, *par)

    fig, ax = plt.subplots(figsize=(8, 4.5))

    ax.plot(ypix, profile, "o", ms=3, color="C0", label="Data")
    ax.plot(x_fine, y_fit, "-", color="C1", lw=1.5, label="Fit")
    ax.axvline(result["db_pixel"], color="C2", ls="--", lw=1, label=f"DB pixel = {result['db_pixel']:.1f}")
    ax.axvline(result["rb_pixel"], color="C3", ls="--", lw=1, label=f"RB pixel = {result['rb_pixel']:.1f}")

    ax.set_xlabel("Y pixel")
    ax.set_ylabel("Intensity (arb.)")
    ax.set_title(result["run_name"])
    ax.legend(fontsize=8)

    # Add summary text box
    text = (
        f"$\\Delta$pixel = {result['delta_pixel']:+.2f}\n"
        f"$\\theta_{{motor}}$ = {result['theta_motor']:.4f}°\n"
        f"$\\theta_{{calc}}$ = {result['theta_calc']:.4f}°\n"
        f"offset = {result['offset']:+.4f}°"
    )
    ax.text(
        0.98, 0.95, text, transform=ax.transAxes,
        fontsize=9, verticalalignment="top", horizontalalignment="right",
        bbox=dict(boxstyle="round,pad=0.4", fc="wheat", alpha=0.8),
    )

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _extract_run_id(nexus_path: str) -> str:
    """Extract the numeric run ID from a NeXus filename like REF_L_226642.nxs.h5."""
    base = os.path.basename(nexus_path)
    # Strip all known extensions
    for ext in (".nxs.h5", ".h5", ".hdf5", ".nxs"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    parts = base.split("_")
    return parts[-1]


def _resolve_db_path(db_run_id: str, search_dirs: list) -> str:
    """Find the DB NeXus file for *db_run_id* in *search_dirs*.

    Looks for files matching ``REF_L_<id>.nxs.h5`` or ``DB_<id>.dat``.
    """
    patterns = [
        f"REF_L_{db_run_id}.nxs.h5",
        f"DB_{db_run_id}.dat",
    ]
    for d in search_dirs:
        for pat in patterns:
            candidate = os.path.join(d, pat)
            if os.path.isfile(candidate):
                return candidate
    raise FileNotFoundError(
        f"Could not find DB file for run {db_run_id} "
        f"in directories: {search_dirs}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

@click.command()
@click.argument("nexus", type=click.Path(exists=True))
@click.option("--db", "db_path", type=click.Path(exists=True), default=None,
              help="Direct-beam reference: a .dat file (with db_pixel/tthd header) or a raw NeXus .h5 file.")
@click.option("--template", "template_path", type=click.Path(exists=True), default=None,
              help="Reduction template XML — auto-resolves the DB run per segment.")
@click.option("--ymin", type=int, default=None,
              help="Lower y-pixel bound for peak fitting (default: auto from peak).")
@click.option("--ymax", type=int, default=None,
              help="Upper y-pixel bound for peak fitting (default: auto from peak).")
@click.option("--xmin", type=int, default=50, help="Low-res x-pixel min [default: 50].")
@click.option("--xmax", type=int, default=200, help="Low-res x-pixel max [default: 200].")
@click.option("--peak-type", type=click.Choice(["gauss", "supergauss"]), default="supergauss",
              help="Peak model for fitting [default: supergauss].")
@click.option("--log", "log_file", type=click.Path(), default=None,
              help="Append result to this CSV file (created if missing).")
@click.option("--output-dir", "output_dir", type=click.Path(file_okay=False), default=None,
              help="Directory for report PNGs and log CSV (created if missing).")
def main(nexus, db_path, template_path, ymin, ymax, xmin, xmax, peak_type, log_file, output_dir):
    """Compute the theta offset for a NeXus run relative to a direct-beam file.

    Provide either --db (explicit DB file) or --template (auto-resolve DB from
    a reduction template XML).
    """
    if db_path is None and template_path is None:
        raise click.UsageError("Provide either --db or --template.")
    if db_path is not None and template_path is not None:
        raise click.UsageError("Provide --db or --template, not both.")

    if template_path is not None:
        run_id = _extract_run_id(nexus)
        mapping = parse_template_xml(template_path)
        if run_id not in mapping:
            raise click.UsageError(
                f"Run {run_id} not found in template. "
                f"Available runs: {', '.join(sorted(mapping))}"
            )
        db_run_id = mapping[run_id]
        search_dirs = [os.path.dirname(nexus), os.getcwd()]
        db_path = _resolve_db_path(db_run_id, search_dirs)
        click.echo(f"Template:       {os.path.basename(template_path)}")
        click.echo(f"DB run:         {db_run_id} → {os.path.basename(db_path)}")

    result = compute_theta_offset(
        nexus, db_path, ymin=ymin, ymax=ymax,
        xmin=xmin, xmax=xmax, peak_type=peak_type,
    )

    click.echo(f"Run:            {result['run_name']}")
    click.echo(f"DB pixel:       {result['db_pixel']:.2f}")
    click.echo(f"Fitted pixel:   {result['rb_pixel']:.2f}  (delta = {result['delta_pixel']:+.2f} px)")
    click.echo(f"Theta (motor):  {result['theta_motor']:.4f}°")
    click.echo(f"Theta (calc):   {result['theta_calc']:.4f}°")
    click.echo(f"Offset:         {result['offset']:+.4f}°")
    click.echo(f"Mean λ:         {result['mean_wl']:.2f} Å")
    click.echo(f"Gravity Δθ:     {result['gravity_dtheta']:+.6f}°  (at mean λ)")

    if output_dir is not None:
        os.makedirs(output_dir, exist_ok=True)

    if log_file is not None:
        if output_dir is not None and not os.path.isabs(log_file):
            log_file = os.path.join(output_dir, log_file)
        log_result(result, log_file, db_path)
        click.echo(f"Logged to:      {log_file}")

    # Save diagnostic plot
    if output_dir is not None:
        run_stem = os.path.splitext(os.path.splitext(result["run_name"])[0])[0]
        report_path = os.path.join(output_dir, f"{run_stem}_theta_offset.png")
        save_report(result, report_path)
        click.echo(f"Report:         {report_path}")


if __name__ == "__main__":
    main()
