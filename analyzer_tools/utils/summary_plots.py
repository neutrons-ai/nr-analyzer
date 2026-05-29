import sys
import os
import json
import numpy as np
from matplotlib import pyplot as plt
import matplotlib.lines as mlines

from matplotlib.path import Path
from matplotlib.patches import PathPatch

try:
    import bumps
    from bumps import dream

    HAS_BUMPS = True
except Exception:
    print("Summary_plot could not import bumps")
    HAS_BUMPS = False

if HAS_BUMPS:
    from . import model_utils
    from refl1d.names import FitProblem
    from bumps.serialize import load_file
    from refl1d.bumps_interface import fitplugin


def load_problem(json_file: str) -> FitProblem:
    bumps.cli.install_plugin(fitplugin)
    return load_file(json_file)


def plot_sld(profile_file, label, show_cl=True, z_offset=0.0):
    """
    :param profile_file: File containing the SLD profile.
    :param label: Label for the plot.
    :param show_cl: Show the confidence limits.
    :param z_offset: Offset to apply to the z-axis when plotting.
    """
    if not os.path.isfile(profile_file):
        print("Could not find %s" % profile_file)
        return

    pre_sld = np.loadtxt(profile_file).T
    linewidth = 1 if show_cl else 2

    expt_file = profile_file.replace("-profile.dat", "-expt.json")
    if "-1-" in profile_file:
        profile_file = profile_file.replace("-1-", "-")

    def _plot_sld():
        plt.plot(
            pre_sld[0][-1] - pre_sld[0] + z_offset,
            pre_sld[1],
            markersize=4,
            label=label,
        linewidth=linewidth,
    )

    if show_cl and HAS_BUMPS:
        # Sanity check
        mc_file = profile_file.replace("-profile.dat", "-chain.mc")
        if not os.path.isfile(mc_file):
            mc_file = profile_file.replace("-profile.dat", "-chain.mc.gz")
        if not os.path.isfile(mc_file):
            print("Could not find: %s" % mc_file)
            _plot_sld()
            return

        # Load the model that was used for fitting
        expt = model_utils.expt_from_json_file(expt_file, set_ranges=True)
        problem = FitProblem(expt)
        model_path = profile_file.replace("-profile.dat", "")
        state = dream.state.load_state(model_path)

        z, best, low, high = model_utils.get_sld_contour(
            problem, state, cl=90, align=-1
        )[0]

        # Find the starting point of the distribution
        for i in range(len(best) - 1, 0, -1):
            if np.fabs(best[i] - best[i - 1]) > 0.001:
                break

        _z = z[i] - z + z_offset
        plt.plot(
            _z[:i],
            best[:i],
            markersize=4,
            label=label,
            linewidth=linewidth,
        )
        plt.fill_between(
            _z[:i], low[:i], high[:i], alpha=0.2, color=plt.gca().lines[-1].get_color()
        )
    else:
        _plot_sld()



def plot_dyn_data(
    dynamic_run,
    initial_state,
    final_state,
    first_index=0,
    last_index=-1,
    dyn_data_dir=None,
    dyn_fit_dir=None,
    model_name="__model",
    scale=1,
    model_id=1,
):
    """
    Plot the dynamic data for a given run, and display the initial and final states.
    """
    # Fit results
    pre_fit = None
    if os.path.isfile(initial_state):
        pre_fit = np.loadtxt(initial_state).T

    post_fit = None
    if os.path.isfile(final_state):
        post_fit = np.loadtxt(final_state).T

    # Dynamic data
    _file_list = sorted(os.listdir(dyn_data_dir))
    fig, ax = plt.subplots(dpi=150, figsize=(5, 8))
    plt.subplots_adjust(left=0.15, right=0.95, top=0.98, bottom=0.1)

    if pre_fit is not None:
        idx = pre_fit[3] < pre_fit[2]
        plt.errorbar(
            pre_fit[0][idx],
            pre_fit[2][idx],
            yerr=pre_fit[3][idx],
            linewidth=1,
            markersize=2,
            marker=".",
            linestyle="",
            color="darkgreen",
            label="Pre cycle 1",
        )
        plt.plot(
            pre_fit[0],
            pre_fit[4],
            linewidth=1,
            markersize=2,
            marker="",
            color="black",
            zorder=400,
        )

    # Get only the files for the run we're interested in
    _good_files = [_f for _f in _file_list if _f.startswith("r%d_t" % dynamic_run)]

    print(len(_good_files))

    scale = 1.0
    multiplier = 10
    file_list = []

    # Check timing
    first_time = int(
        os.path.splitext(_good_files[first_index])[0].replace("r%d_t" % dynamic_run, "")
    )
    second_time = int(
        os.path.splitext(_good_files[first_index + 1])[0].replace(
            "r%d_t" % dynamic_run, ""
        )
    )
    delta_t = second_time - first_time

    for _file in _good_files[first_index:last_index]:
        if _file.startswith("r%d_t" % dynamic_run):
            scale *= 1
            _data = np.loadtxt(os.path.join(dyn_data_dir, _file)).T
            _data_name, _ = os.path.splitext(_file)
            _time = int(_data_name.replace("r%d_t" % dynamic_run, ""))
            _label = "%d < t < %d s" % (_time, _time + delta_t)

            # Get fit if it exists
            fit_file = os.path.join(
                dyn_fit_dir, _data_name, f"{model_name}-{model_id}-refl.dat"
            )

            if os.path.isfile(fit_file):
                fit_data = np.loadtxt(fit_file).T
                plt.plot(
                    fit_data[0],
                    fit_data[4] * scale,
                    markersize=2,
                    marker="",
                    linewidth=1,
                    color="black",
                )

            if len(_data) > 1:
                idx = _data[2] < _data[1]
                plt.errorbar(
                    _data[0][idx],
                    _data[1][idx] * scale,
                    yerr=_data[2][idx] * scale,
                    linewidth=1,
                    markersize=2,
                    marker=".",
                    linestyle="",
                    label=_label,
                )

                scale *= multiplier
                file_list.append([_time, _data_name, _data_name])

    final_scale = scale / multiplier
    if post_fit is not None:
        idx = post_fit[3] < post_fit[2]
        plt.errorbar(
            post_fit[0][idx],
            post_fit[2][idx] * final_scale,
            yerr=post_fit[3][idx] * final_scale,
            linewidth=1,
            markersize=2,
            marker=".",
            linestyle="",
            color="darkgreen",
            label="Post cycle 1",
        )
        plt.plot(
            post_fit[0], post_fit[4] * final_scale, linewidth=1, color="darkblue"
        )  # , label='final')

    handles, labels = ax.get_legend_handles_labels()
    plt.legend(handles[::-1], labels[::-1], frameon=False, prop={"size": 7})
    plt.xlabel(r"Q ($1/\AA$)", fontsize=15)
    plt.ylabel("Reflectivity", fontsize=15)
    plt.yscale("log")
    plt.xscale("log")
    ax.yaxis.labelpad = 1

    plt.show()
    return file_list


def plot_dyn_sld(
    file_list,
    initial_state,
    final_state,
    dyn_fit_dir=None,
    model_name="__model",
    show_cl=True,
    legend_font_size=6,
    max_z=None,
    reverse=True,
    sld_range=None,
    initial_z_offset=0,
    final_z_offset=0,
    model_id=1,
):
    fig, ax = plt.subplots(dpi=200, figsize=(5, 4.1))
    plt.subplots_adjust(left=0.15, right=0.95, top=0.95, bottom=0.15)

    # Plot initial state
    if initial_state is not None:
        plot_sld(
            initial_state, "Initial state", show_cl=False, z_offset=initial_z_offset
        )

    _file_list = reversed(file_list) if reverse else file_list
    delta_t = int(file_list[1][0]) - int(file_list[0][0])

    for _file in _file_list:
        profile_file = os.path.join(
            dyn_fit_dir, str(_file[2]), f"{model_name}-{model_id}-profile.dat"
        )
        plot_sld(
            profile_file,
            "%d < t < %d s" % (int(_file[0]), int(_file[0]) + delta_t),
            show_cl=HAS_BUMPS and show_cl,
        )

    # Plot final OCP
    if final_state is not None:
        plot_sld(final_state, "Final state", show_cl=False, z_offset=final_z_offset)

    handles, labels = ax.get_legend_handles_labels()
    plt.legend(
        handles[::-1],
        labels[::-1],
        loc="lower right",
        frameon=False,
        fontsize=legend_font_size,
    )
    if max_z is not None:
        plt.xlim(-20, max_z)
    if sld_range is not None:
        plt.ylim(sld_range[0], sld_range[1])
    plt.xlabel(r"z ($\AA$)", fontsize=14)
    plt.ylabel(r"SLD ($10^{-6}/\AA^2$)", fontsize=14)
    plt.show()


def trend_data(
    file_list,
    initial_state,
    final_state,
    label="",
    fit_dir=None,
    dyn_data_dir=None,
    dyn_fit_dir=None,
    model_name="__model",
    model_file=None,
    newplot=True,
    plot_chi2=False,
    add_plot=0,
):
    """
        sei_thick.append(item['sei thickness'][which])
    sei_dthick.append(item['sei thickness']['std'])
    """
    # Get the varying parameters, which are assumed to be the same for all data sets
    par_file = os.path.join(dyn_fit_dir, str(file_list[0][2]), "%s.par" % model_name)
    if not os.path.isfile(par_file):
        par_file = os.path.join(
            dyn_fit_dir, str(file_list[-1][2]), "%s.par" % model_name
        )

    trend_data = dict()
    trend_err = dict()
    chi2 = []  # TODO: NOT FILLED YET WITH REFL1D V1
    timestamp = []

    with open(par_file, "r") as fd:
        for line in fd.readlines():
            par = " ".join(line.split(" ")[0:2])
            if "intensity" not in par:
                trend_data[par] = []
                trend_err[par] = []

    # Go through each file and retrieve the parameters
    # 'which' defines the value to select. It can either be 'mean' of 'best'.
    which = "best"
    for _file in file_list:
        err_file = os.path.join(dyn_fit_dir, str(_file[2]), "%s.err" % model_name)
        err_json = os.path.join(dyn_fit_dir, str(_file[2]), "%s-err.json" % model_name)

        if os.path.isfile(err_json):
            with open(err_json) as fd:
                m = json.load(fd)
                for par in trend_data.keys():
                    trend_data[par].append(m[par][which])
                    trend_err[par].append(m[par]["std"])

            timestamp.append(float(_file[0]))

    # Read initial and final states
    steady_values = dict()
    steady_err = dict()
    steady_times = dict()
    for par in trend_data.keys():
        steady_values[par] = []
        steady_err[par] = []
        steady_times[par] = []

    t_offset = (timestamp[-1] - timestamp[0]) / len(timestamp) * 5
    if os.path.isfile(initial_state):
        expt = model_utils.expt_from_json_file(initial_state, keep_original_ranges=True)
        for par in trend_data.keys():
            for layer in expt.sample.layers:
                rho = layer.material.rho
                irho = layer.material.irho
                thickness = layer.thickness
                interface = layer.interface
                for p in [rho, irho, thickness, interface]:
                    if p.name == par:
                        print("Initial state: %s = %g" % (par, p.value))
                        steady_values[par].append(p.value)
                        
                        steady_times[par].append(timestamp[0] - t_offset)
                        

    if os.path.isfile(final_state):
        expt = model_utils.expt_from_json_file(final_state, keep_original_ranges=True)
        for par in trend_data.keys():
            for layer in expt.sample.layers:
                rho = layer.material.rho
                irho = layer.material.irho
                thickness = layer.thickness
                interface = layer.interface
                for p in [rho, irho, thickness, interface]:
                    if p.name == par:
                        steady_values[par].append(p.value)
                        steady_times[par].append(timestamp[-1] + t_offset)

    # Plot trend data
    n_tot = len(trend_data.keys()) + add_plot
    if plot_chi2:
        n_tot += 1

    if newplot:
        ysize = len(trend_data.keys()) * 2 + 6
        fig, axs = plt.subplots(n_tot, 1, dpi=100, figsize=(6, ysize), sharex=True)
        plt.subplots_adjust(left=0.15, right=0.95, top=0.98, bottom=0.1)

    n_current = 1
    for i, par in enumerate(trend_data.keys()):
        ax = plt.subplot(n_tot, 1, i + 1)
        plt.errorbar(
            timestamp,
            trend_data[par],
            yerr=trend_err[par],
            label=par,
            marker=".",
            markersize=8,
            linestyle="--",
        )
        # plt.xlabel('seconds')

        if par in steady_values:
            plt.plot(
                steady_times[par],
                steady_values[par],
                linestyle="",
                marker="*",
                markersize=10,
            )

        plt.ylabel(par)
        # plt.legend(frameon=False)

    if plot_chi2:
        ax = plt.subplot(n_tot, 1, n_tot)
        plt.plot(timestamp, chi2, label=r"$\chi^2$")
        plt.ylabel(r"$\chi^2$")
        plt.legend(frameon=False)

    plt.xlabel("Time (seconds)")

    # Trend output
    trend_file = os.path.join(dyn_fit_dir, "trend-%s.json" % model_name)
    with open(trend_file, "w") as fp:
        print("Output saved to", trend_file)
        json.dump([timestamp, trend_data, trend_err, chi2], fp)

    return trend_data, trend_err, timestamp


def write_md_table(trend_data_file):
    """
    The trend data file is saved as:
        data[0] is the array of times
        data[1] is a dict of parameter values
        data[2] is the corresponding dict of uncertainties
    """
    with open(trend_data_file) as fd:
        data = json.load(fd)

        output_file = trend_data_file.replace(".json", "-table.md")
        with open(output_file, "w") as output:
            # Write header
            headers = data[1].keys()
            header = "| Time | " + "|".join(headers) + "| chi2 |\n"
            header += "| " + "|".join((len(headers) + 2) * ["---"]) + "|\n"
            output.write(header)

            for i in range(len(data[0])):
                entry = "| %g " % (data[0][i])
                for k in data[1].keys():
                    entry += "| %4.2f ± %4.2f " % (data[1][k][i], data[2][k][i])
                entry += "| %g |\n" % data[3][i]
                output.write(entry)


def detect_changes(dynamic_run, dyn_data_dir, first=0, last=-1, out_array=None):
    compiled_array = []
    compiled_times = []

    _file_list = sorted(os.listdir(dyn_data_dir))

    # Get only the files for the run we're interested in
    _good_files = [_f for _f in _file_list if _f.startswith("r%d_t" % dynamic_run)]

    print(len(_good_files))
    chi2 = []
    asym = []
    t = []
    skipped = 0
    previous = None
    previous_q = None
    previous_err = None

    min_q = 0.0154
    for _file in _good_files[first:last]:
        if _file.startswith("r%d_t" % dynamic_run):
            _data = np.loadtxt(os.path.join(dyn_data_dir, _file)).T
            if len(_data) == 0:
                continue
            idx = _data[0] >= min_q
            _data_name, _ = os.path.splitext(_file)
            _time = int(_data_name.replace("r%d_t" % dynamic_run, ""))
            compiled_array.append([_data[0][idx], _data[1][idx], _data[2][idx]])
            compiled_times.append(_time)

            if previous is not None:
                if len(_data[1]) == len(previous):
                    delta = np.mean(
                        (_data[1] - previous) ** 2 / (_data[2] ** 2 + previous_err**2)
                    )
                    chi2.append(delta)
                    _asym = np.mean((_data[1] - previous) / (_data[1] + previous))
                    asym.append(_asym)
                    t.append(_time)

                elif True:
                    old_r = []
                    old_err = []
                    new_r = []
                    new_err = []

                    for i, q in enumerate(_data[0]):
                        idx = np.argwhere(previous_q == q)
                        # print(idx)
                        if len(idx) > 0:
                            new_r.append(_data[1][i])
                            new_err.append(_data[2][i])
                            old_r.append(previous[idx[0][0]])
                            old_err.append(previous_err[idx[0][0]])

                    old_r = np.asarray(old_r)
                    old_err = np.asarray(old_err)
                    new_r = np.asarray(new_r)
                    new_err = np.asarray(new_err)

                    delta = np.mean((new_r - old_r) ** 2 / (new_err**2 + old_err**2))
                    # delta = np.mean((new_r - old_r)**2 / (new_err**2))
                    chi2.append(delta)
                    _asym = np.mean((new_r - old_r) / (new_r + old_r))
                    asym.append(_asym)
                    t.append(_time)

                previous_q = _data[0]
                previous = _data[1]
                previous_err = _data[2]

            # print("Unequal length: %s" % _file)
            else:
                print("Ref %s" % _file)
                previous_q = _data[0]
                previous = _data[1]
                previous_err = _data[2]

    if out_array:
        # np.save(out_array, np.asarray(compiled_array))
        # np.save(out_array+'_times', np.asarray(compiled_times))
        np.savetxt(out_array + "_chi2.txt", t)
        np.savetxt(out_array + "_times.txt", t)
    print("Skipped: %s" % skipped)
    fig = plt.figure(dpi=100, figsize=[8, 4])
    plt.plot(t, chi2, markersize=10, marker=".", linestyle="--", label=r"$\chi^2$")
    # plt.plot(t, 10*np.asarray(asym), label='Asym [x10]')
    # plt.legend(frameon=False)
    plt.ylabel(r"$\chi^2$")
    plt.xlabel("Time (sec)")
    return t, chi2


def package_data(
    dynamic_run,
    dyn_data_dir,
    first=0,
    last=-1,
    qmin=0,
    qmax=1,
    max_len=None,
    out_array=None,
):
    compiled_array = []
    compiled_times = []
    data_array = []

    _file_list = sorted(os.listdir(dyn_data_dir))

    # Get only the files for the run we're interested in
    _good_files = [_f for _f in _file_list if _f.startswith("r%d_t" % dynamic_run)]

    print(len(_good_files))
    asym = []
    t = []
    skipped = 0
    previous = None

    min_q = qmin
    max_q = qmax

    for i, _file in enumerate(_good_files[first:last]):
        if _file.startswith("r%d_t" % dynamic_run):
            print(_file)
            _data = np.loadtxt(os.path.join(dyn_data_dir, _file)).T
            if np.min(_data[0]) > min_q:
                min_q = np.min(_data[0])
            if np.max(_data[0]) < max_q:
                max_q = np.max(_data[0])

            _data_name, _ = os.path.splitext(_file)
            _time = int(_data_name.replace("r%d_t" % dynamic_run, ""))
            data_array.append([_data_name, _time, _data])

    for i, _data in enumerate(data_array):
        idx = (_data[2][0] >= min_q) & (_data[2][0] < max_q)
        if max_len is not None and len(_data[2][0][idx]) > max_len:
            data2 = _data[2][0][idx]
            print(data2[-max_len:])
            compiled_array.append(
                [
                    data2[-max_len:],
                    _data[2][1][idx][-max_len:],
                    _data[2][2][idx][-max_len:],
                ]
            )
            compiled_times.append(_data[1])
        else:
            compiled_array.append(
                [_data[2][0][idx], _data[2][1][idx], _data[2][2][idx]]
            )
            compiled_times.append(_data[1])

    compiled_array = np.asarray(compiled_array)
    compiled_times = np.asarray(compiled_times)
    print(compiled_array.shape)
    print(np.max(compiled_array[0][0]))
    if out_array:
        np.save(out_array, compiled_array)
        np.save(out_array + "_times", compiled_times)
    return compiled_times, compiled_array


def package_json_data(dynamic_run, dyn_data_dir, out_array=None):
    compiled_array = []
    compiled_times = []
    data_array = []

    _file_list = sorted(os.listdir(dyn_data_dir))

    # Get only the files for the run we're interested in
    _good_files = [_f for _f in _file_list if _f.startswith("r%d_t" % dynamic_run)]

    for i, _file in enumerate(_good_files):
        if _file.startswith("r%d_t" % dynamic_run):
            _data = np.loadtxt(os.path.join(dyn_data_dir, _file)).T
            print(i, _file, len(_data[0]))
            _data_name, _ = os.path.splitext(_file)
            _time = int(_data_name.replace("r%d_t" % dynamic_run, ""))
            compiled_array.append(_data.tolist())
            compiled_times.append(_time)

    if out_array:
        with open(out_array, "w") as fp:
            json.dump(dict(times=compiled_times, data=compiled_array), fp)

    return compiled_times, compiled_array


def main(
    dynamic_run,
    dyn_data_dir,
    model_file,
    initial_state,
    final_state,
    results_dir,
    first_item=0,
    last_item=-1,
):
    initial_refl = initial_state.replace("expt.json", "refl.dat")
    final_refl = final_state.replace("expt.json", "refl.dat")
    model_name = os.path.basename(model_file).replace(".py", "")

    # Generate plot of the reflectivity data
    plotted_data = plot_dyn_data(
        dynamic_run,
        initial_refl,
        final_refl,
        dyn_data_dir=dyn_data_dir,
        dyn_fit_dir=results_dir,
        model_name=model_name,
        first_index=first_item,
        last_index=last_item,
    )
    plt.savefig(os.path.join(results_dir, "dyn-%d.png" % dynamic_run))
    plt.savefig(os.path.join(results_dir, "dyn-%d.svg" % dynamic_run))

    # Generate plot of the SLD profiles
    initial_sld = initial_state.replace("expt.json", "profile.dat")
    final_sld = final_state.replace("expt.json", "profile.dat")

    plot_dyn_sld(
        plotted_data,
        initial_sld,
        final_sld,
        dyn_fit_dir=results_dir,
        show_cl=True,
        model_name=model_name,
        legend_font_size=8,
    )
    plt.savefig(os.path.join(results_dir, "sld-%d.png" % dynamic_run))
    plt.savefig(os.path.join(results_dir, "sld-%d.svg" % dynamic_run))
