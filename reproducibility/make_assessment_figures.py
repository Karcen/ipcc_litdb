"""Render four source-backed figures for the revised assessment-corpus analysis.

Requires matplotlib, NumPy, PyMuPDF and the Nature Figure QA scripts.
All numerical inputs are existing result tables. This script does not fit models,
change identities, resample records or modify analysis inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.transforms import ScaledTranslation
import numpy as np

ROOT = Path(__file__).resolve().parent
WIDTH_MM = 133
BLUE = "#0072B2"
ORANGE = "#D55E00"
TEAL = "#007F73"
GREY = "#8C9299"
DARK = "#222222"
PALE = "#EEF0F2"
REPORTS = ["AR4", "AR5", "AR6"]
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7.4, "axes.labelsize": 7.4, "axes.titlesize": 7.8,
    "xtick.labelsize": 7.2, "ytick.labelsize": 7.2, "legend.fontsize": 7.2,
    "pdf.fonttype": 42, "svg.fonttype": "none", "axes.linewidth": 0.65,
    "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "legend.handlelength": 2.1,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "savefig.facecolor": "white", "figure.facecolor": "white",
})


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(directory, name):
    with (directory / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def choose(rows, **keys):
    selected = [row for row in rows if all(row[key] == value for key, value in keys.items())]
    if len(selected) != 1:
        raise ValueError(f"Expected one row for {keys}; found {len(selected)}")
    return selected[0]


def number(row, key):
    value = float(row[key])
    if not np.isfinite(value):
        raise ValueError(f"Nonfinite {key}")
    return value


def panel_labels(fig, axes):
    for index, axis in enumerate(axes):
        offset = ScaledTranslation(-17 / 72, 9 / 72, fig.dpi_scale_trans)
        axis.text(0, 1, chr(97 + index), transform=axis.transAxes + offset,
                  ha="left", va="bottom", fontsize=8.5, fontweight="bold")


def stacked_canvas(height_mm, rows=2, ratios=None, left=0.24, bottom=0.085,
                   top=0.91, gap=0.6):
    fig, axes = plt.subplots(rows, 1, figsize=(WIDTH_MM / 25.4, height_mm / 25.4),
                             gridspec_kw={"height_ratios": ratios or [1] * rows})
    axes = list(np.atleast_1d(axes))
    fig.subplots_adjust(left=left, right=0.96, bottom=bottom, top=top, hspace=gap)
    panel_labels(fig, axes)
    return fig, axes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", "--results", dest="results", type=Path, default=ROOT / "reference")
    parser.add_argument("--output-dir", "--output", dest="output", type=Path, default=ROOT / "figures")
    parser.add_argument("--qc-dir", "--qc-output", dest="qc_output", type=Path, default=ROOT / "qc")
    parser.add_argument("--skill-directory", type=Path,
                        default=Path.home() / ".codex" / "skills" / "nature-figure")
    args = parser.parse_args()
    skill = args.skill_directory
    required = ["audit_panel_alignment.py", "audit_pdf_text.py", "audit_figure_collisions.py",
                "validate_figure.py"]
    if not all((skill / "scripts" / name).is_file() for name in required):
        parser.error("Supply --skill-directory containing the Nature Figure QA scripts")
    sys.path.insert(0, str(skill / "scripts"))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    from audit_pdf_text import audit_pdf
    args.output.mkdir(parents=True, exist_ok=True)
    args.qc_output.mkdir(parents=True, exist_ok=True)

    names = ["revision_identity_status_coverage.csv", "revision_identity_raw_consistency_census.csv",
             "revision_identity_transitions.csv", "revision_identity_independent_raw_baselines.csv",
             "revision_age_summary.csv", "revision_age_ecdf.csv",
             "revision_outlet_hill_numbers.csv", "revision_outlet_rarefaction_differences.csv",
             "revision_network_retention_sensitivity.csv", "revision_network_conditional_null.csv"]
    hashes = {name: digest(args.results / name) for name in names}
    data = {name: read_csv(args.results, name) for name in names}
    contracts = []
    records = []
    caption_data = {}

    def save(fig, index, contract, insets=()):
        stem = f"revision_figure{index}"
        fig.canvas.draw()
        alignment = require_matplotlib_panel_alignment(
            fig, json_out=args.qc_output / f"{stem}_alignment.json",
            overlay_svg=args.qc_output / f"{stem}_alignment.svg",
            tolerance_pt=1.5, gutter_tolerance_pt=1.5, require_panel_labels=True,
            exclude_axes=insets, strict=True,
        )
        # Fixed dimensions keep the PDF/SVG page and PNG canvas at 133 mm.
        fig.savefig(args.output / f"{stem}.pdf")
        fig.savefig(args.output / f"{stem}.svg")
        fig.savefig(args.output / f"{stem}.png", dpi=600)
        text = audit_pdf((args.output / f"{stem}.pdf").read_bytes(), minimum_pt=7)
        (args.qc_output / f"{stem}_text.json").write_text(json.dumps(text, indent=2) + "\n")
        collision_path = args.qc_output / f"{stem}_collisions.json"
        outcome = subprocess.run(
            [sys.executable, str(skill / "scripts" / "audit_figure_collisions.py"),
             str(args.output / f"{stem}.pdf"), "--json-out", str(collision_path),
             "--overlay-pdf", str(args.qc_output / f"{stem}_collisions.pdf")],
            text=True, capture_output=True,
        )
        collision = json.loads(collision_path.read_text())
        record = {"figure": stem, "alignment": alignment["verdict"],
                  "pdf_text_auditable": text["auditable"],
                  "glyphs_below_7pt": text["below_minimum_count"],
                  "collision_exit": outcome.returncode, "collision_verdict": collision["verdict"],
                  "collision_findings": collision["findings"],
                  "width_mm": WIDTH_MM, "height_mm": float(fig.get_size_inches()[1] * 25.4),
                  "png_dpi": 600,
                  "files": {f"figures/{stem}.{suffix}": digest(args.output / f"{stem}.{suffix}")
                            for suffix in ["pdf", "svg", "png"]}}
        records.append(record)
        contracts.append({"figure": stem, "archetype": "quantitative grid",
                          "backend": "Python/matplotlib", "width_mm": WIDTH_MM,
                          "font_floor_pt": 7, "adaptation": "Build anew from revised source tables",
                          "inset_exclusion": "Magnified ECDF axes are not comparable main panels" if insets else None,
                          **contract})
        print(stem, record["alignment"], "text below 7 pt:", record["glyphs_below_7pt"],
              "collision:", record["collision_verdict"], flush=True)
        plt.close(fig)

    # 1. Workflow coverage precedes any interpretation of cohort retention.
    coverage = data["revision_identity_status_coverage.csv"]
    consistency = data["revision_identity_raw_consistency_census.csv"]
    transitions = data["revision_identity_transitions.csv"]
    raw_baselines = data["revision_identity_independent_raw_baselines.csv"]
    fig, (a, b, c) = stacked_canvas(153, rows=3, ratios=[1, 1, 1.25], left=0.25, top=0.91, gap=0.82)
    totals = [choose(coverage, report=r, working_group="All", status="All") for r in REPORTS]
    y = np.arange(3)
    left = np.zeros(3)
    for status, label, color, hatch in [
        ("matched_doi", "DOI match", BLUE, None),
        ("matched_title", "Title match", ORANGE, None),
        ("unmatched", "Unmatched", GREY, None),
    ]:
        counts = np.array([int(choose(coverage, report=r, working_group="All", status=status)["occurrences"])
                           for r in REPORTS])
        fraction = counts / np.array([int(t["occurrences"]) for t in totals]) * 100
        a.barh(y, fraction, left=left, height=0.46, color=color, hatch=hatch,
               edgecolor="white", linewidth=0.4, label=label)
        left += fraction
    assert np.allclose(left, 100)
    a.set_yticks(y, [f"{r} ({int(t['occurrences']):,})" for r, t in zip(REPORTS, totals)])
    a.invert_yaxis(); a.set_xlim(0, 100); a.set_xticks([0, 25, 50, 75, 100])
    a.set_xlabel("Reference occurrences (%)")
    a.legend(loc="upper left", bbox_to_anchor=(0, 1.29), ncol=3,
             columnspacing=1.15, handlelength=1.3, borderaxespad=0)
    raw_counts = [sum(int(choose(consistency, report=r, working_group="All", status=s)["raw_doi_available"])
                      for s in ["matched_doi", "matched_title", "unmatched"]) for r in REPORTS]
    raw_fractions = np.array(raw_counts) / np.array([int(t["occurrences"]) for t in totals]) * 100
    b.hlines(y, 0, raw_fractions, color=PALE, linewidth=3)
    b.plot(raw_fractions, y, "o", color=DARK, markersize=4)
    for yy, value in zip(y, raw_fractions):
        b.annotate(f"{value:.2f}%", (value, yy), xytext=(6, 0), textcoords="offset points", va="center")
    b.set_yticks(y, [f"{r} ({n:,})" for r, n in zip(REPORTS, raw_counts)])
    b.set_ylim(2.5, -0.5); b.set_xlim(0, 78); b.set_xticks([0, 20, 40, 60])
    b.set_xlabel("Occurrences with a usable raw DOI (%)")
    b.set_title("Literal raw DOI coverage", loc="left", pad=14)
    variants = [("all_extracted", "Extracted IDs", GREY, "s", "--"),
                ("matched", "Matched IDs", BLUE, "o", "-"),
                ("DOI_status", "DOI-status IDs", ORANGE, "^", ":"),
                ("literal_single_raw_DOI_all_statuses", "Raw DOI keys", TEAL, "D", "-.")]
    retention_rows = []
    for idx, (definition, label, color, marker, style) in enumerate(variants):
        selected = [choose(raw_baselines, earlier=u, later=v, identity_unit=definition)
                    if definition == "literal_single_raw_DOI_all_statuses"
                    else choose(transitions, earlier=u, later=v, definition=definition)
                    for u, v in [("AR4", "AR5"), ("AR5", "AR6")]]
        xx = np.array([0, 1]) + (idx - 1.5) * 0.048
        c.plot(xx, [number(r, "retention") * 100 for r in selected], marker=marker,
               color=color, linestyle=style, markersize=3.8, linewidth=1.15, label=label)
        retention_rows += selected
    c.set_xticks([0, 1], ["AR4 → AR5", "AR5 → AR6"])
    c.set_xlim(-0.16, 1.16); c.set_ylim(0, 23); c.set_yticks([0, 5, 10, 15, 20])
    c.set_ylabel("Earlier identity cohort retained (%)")
    c.legend(loc="upper left", bbox_to_anchor=(0, 1.35), ncol=2, borderaxespad=0,
             columnspacing=1.0)
    caption_data["revision_figure1"] = {"occurrence_totals": totals, "literal_raw_DOI_counts": raw_counts,
                                         "literal_raw_DOI_percentages": raw_fractions.tolist(),
                                         "retention_rows": retention_rows}
    save(fig, 1, {"question": "How does workflow coverage qualify cross-report identity retention?",
                  "conclusion": "Workflow matching and literal raw DOI coverage change sharply across reports, so retention depends on the identity cohort.",
                  "panels": {"a": "Setup: full occurrence counts and workflow status composition.",
                             "b": "Independent coverage context: at least one usable unfragmented DOI in raw entries, without punctuation repair.",
                             "c": "Boundary: extracted, matched and DOI-status database-ID cohorts, plus independent literal raw DOI keys across all workflow statuses."},
                  "statistics": "Finite-corpus enumeration. Occurrence, unique assigned-ID and independent DOI-key denominators are distinct; no uncertainty bars. Independent keys require exactly one usable DOI in a raw entry and use no enriched paper DOI or assigned ID.",
                  "source_data": names[:4]})

    # 2. The complete age distributions expose the role of weighting.
    ecdf = data["revision_age_ecdf.csv"]
    ages = data["revision_age_summary.csv"]
    fig, axes = stacked_canvas(130, left=0.15, gap=0.65, top=0.85, bottom=0.095)
    insets = []
    age_caption = []
    for ax, weighting, title in zip(axes, ["occurrence", "equal_paper_id"],
                                     ["Occurrence-weighted age", "Equal-ID-weighted age"]):
        inset = ax.inset_axes([0.53, 0.14, 0.43, 0.51])
        insets.append(inset)
        for report, color in [("AR5", BLUE), ("AR6", ORANGE)]:
            for cohort in ["extracted", "matched"]:
                style = {("AR5", "extracted"): "-", ("AR5", "matched"): "--",
                         ("AR6", "extracted"): "-.", ("AR6", "matched"): ":"}[(report, cohort)]
                selected = [r for r in ecdf if r["report"] == report and r["cohort"] == cohort
                            and r["basis"] == "source_year" and r["weighting"] == weighting]
                selected.sort(key=lambda r: number(r, "age"))
                xx = np.array([number(r, "age") for r in selected]); yy = np.array([number(r, "ecdf") for r in selected])
                assert len(xx) == 61 and np.all(np.diff(xx) > 0) and np.all(np.diff(yy) >= -1e-12)
                assert np.isclose(yy[-1], 1)
                for target in [ax, inset]:
                    target.step(xx, yy, where="post", color=color, linestyle=style, linewidth=1.15,
                                label=f"{report}, {'extracted' if cohort == 'extracted' else 'matched'}")
                age_caption.append(choose(ages, report=report, cohort=cohort, basis="source_year", weighting=weighting))
        ax.set_xlim(0, 60); ax.set_ylim(0, 1.02); ax.set_xticks([0, 10, 20, 30, 40, 50, 60])
        ax.set_yticks([0, 0.5, 1]); ax.set_ylabel("Cumulative fraction"); ax.set_xlabel("Age (years)")
        median_note = "Median: AR5 = 4; AR6 = 4 years" if weighting == "occurrence" else "Median: AR5 = 4; AR6 = 5 years"
        ax.set_title(f"{title}  |  {median_note}", loc="left", pad=14)
        inset.set_xlim(0, 12); inset.set_ylim(0, 1.02); inset.set_xticks([0, 4, 8, 12]); inset.set_yticks([0, 0.5, 1])
        inset.tick_params(axis="both", labelsize=7, length=2)
        inset.set_title("First 12 years", fontsize=7.2, loc="left", pad=3)
    axes[0].legend(loc="upper left", bbox_to_anchor=(0, 1.43), ncol=2, borderaxespad=0, columnspacing=1.2)
    caption_data["revision_figure2"] = {"age_summary_rows": age_caption,
                                        "support": "All integer ages 0–60; insets magnify 0–12 without removing tail observations"}
    save(fig, 2, {"question": "Does citation age change when repeated citations stop receiving extra weight?",
                  "conclusion": "Occurrence medians remain four years; equal-ID medians rise from four to five years in both extracted and matched cohorts.",
                  "panels": {"a": "Primary distribution under occurrence weights, including the complete accepted age support.",
                             "b": "Weighting discriminator: every included ID has total weight one across its occurrences."},
                  "statistics": "Weighted empirical distributions and inverse-ECDF medians; no population confidence bands. Source-year ages include only 0–60; exclusion counts are in the source summary.",
                  "source_data": ["revision_age_ecdf.csv", "revision_age_summary.csv"]}, insets=insets)

    # 3. Concentration and equal-size controls answer one outlet question.
    hill = data["revision_outlet_hill_numbers.csv"]
    differences = data["revision_outlet_rarefaction_differences.csv"]
    fig, (a, b) = stacked_canvas(142, ratios=[1, 2.1], left=0.32, top=0.90, gap=0.55)
    concentration_rows = []
    for cohort, label, color, marker, style in [("raw", "Extracted IDs", GREY, "s", "--"),
                                               ("matched", "Matched IDs", BLUE, "o", "-")]:
        selected = [choose(hill, report=r, cohort=cohort, restriction="all", source_field="preferred_label",
                           normalization="casefold_whitespace") for r in REPORTS]
        a.plot(np.arange(3), [number(r, "q2") for r in selected], color=color, marker=marker,
               linestyle=style, markersize=4, linewidth=1.25, label=label)
        concentration_rows += selected
    a.set_xticks([0, 1, 2], REPORTS); a.set_xlim(-0.12, 2.12); a.set_ylim(0, 230); a.set_yticks([0, 100, 200])
    a.set_ylabel("Effective outlets (q = 2)")
    a.legend(loc="upper left", bbox_to_anchor=(0, 1.35), ncol=2, borderaxespad=0, columnspacing=1.2)
    specifications = [
        ("raw|all|preferred_label|casefold_whitespace", "Extracted labels"),
        ("matched|all|preferred_label|casefold_whitespace", "Matched labels"),
        ("matched|all|preferred_label|conservative_title_tokens", "Matched aliases"),
        ("doi_only|all|preferred_label|conservative_title_tokens", "DOI-match aliases"),
        ("matched|all|registered_wos_source|conservative_title_tokens", "WoS aliases"),
        ("matched|publication_2000_2013|preferred_label|conservative_title_tokens", "2000–13: preferred"),
        ("matched|publication_2000_2013|registered_wos_source|conservative_title_tokens", "2000–13: WoS"),
        ("matched|common_outlet_support|preferred_label|conservative_title_tokens", "Shared outlet support"),
    ]
    sensitivity_rows = [choose(differences, comparison=s, metric="q2") for s, _ in specifications]
    yy = np.arange(len(sensitivity_rows))
    means = np.array([number(r, "mean_difference_ar6_minus_ar5") for r in sensitivity_rows])
    low = np.array([number(r, "p025") for r in sensitivity_rows]); high = np.array([number(r, "p975") for r in sensitivity_rows])
    assert np.all(low <= means) and np.all(means <= high)
    b.errorbar(means, yy, xerr=np.array([means - low, high - means]), fmt="o", color=BLUE,
               markersize=4, capsize=2.5, linewidth=1.2)
    b.axvline(0, color=GREY, linestyle="--", linewidth=0.65)
    b.set_yticks(yy, [label for _, label in specifications]); b.invert_yaxis()
    b.set_ylim(len(yy) - 0.4, -0.8); b.set_xlim(-2, 90); b.set_xticks([0, 20, 40, 60, 80])
    b.set_xlabel("AR6 − AR5 effective outlets (q = 2)")
    b.set_title("Equal-size sampling and label/window controls", loc="left", pad=14)
    caption_data["revision_figure3"] = {"hill_number_rows": concentration_rows,
                                        "sensitivity_rows": sensitivity_rows,
                                        "specification_labels": dict(specifications)}
    save(fig, 3, {"question": "Does the rise in effective outlet diversity persist at equal ID counts?",
                  "conclusion": "AR6 has more effective outlets than AR5 under equal-size sampling, label variants, a shared publication window and shared outlet support.",
                  "panels": {"a": "Primary context: inverse HHI of nonblank preferred outlet labels, with extracted and matched ID cohorts separated.",
                             "b": "Stress tests: eight equal-ID-size AR6–AR5 contrasts under explicit label and inclusion specifications."},
                  "statistics": "q2 = 1 / sum(p_j squared). Dots in b are mean paired differences across 2,000 independent without-replacement subsampling pairs; whiskers are their 2.5th–97.5th percentiles, not population confidence intervals.",
                  "source_data": ["revision_outlet_hill_numbers.csv", "revision_outlet_rarefaction_differences.csv"]})

    # 4. Within-group persistence differs from allocation of fixed sharing.
    retention = data["revision_network_retention_sensitivity.csv"]
    null = data["revision_network_conditional_null.csv"]
    fig, (a, b) = stacked_canvas(175, ratios=[1.24, 1], left=0.31, top=0.91, gap=0.5, bottom=0.075)
    rules = [("extracted", "none", "Extracted"), ("matched", "none", "Matched"),
             ("matched_doi", "none", "DOI match"), ("matched", "source_year", "Date-screened")]
    retention_caption = []
    labels = []; values_a = []; values_b = []
    for wg, numeral in [("WG1", "I"), ("WG2", "II"), ("WG3", "III")]:
        for rule, screening, label in rules:
            first = choose(retention, group=wg, rule=rule, screening=screening, report_a="AR4", report_b="AR5")
            second = choose(retention, group=wg, rule=rule, screening=screening, report_a="AR5", report_b="AR6")
            labels.append(f"{numeral}: {label}"); values_a.append(number(first, "retention") * 100)
            values_b.append(number(second, "retention") * 100); retention_caption += [first, second]
    yy = np.arange(len(labels))
    for index, (first, second) in enumerate(zip(values_a, values_b)):
        a.plot([first, second], [index, index], color=GREY, linewidth=0.8)
    a.plot(values_a, yy, "o", markersize=4.1, markerfacecolor="white", markeredgecolor=BLUE,
           markeredgewidth=1.1, linestyle="none", label="AR4 → AR5")
    a.plot(values_b, yy, "^", color=BLUE, markersize=4.3, linestyle="none", label="AR5 → AR6")
    for boundary in [3.5, 7.5]:
        a.axhline(boundary, color=PALE, linewidth=0.8)
    a.set_yticks(yy, labels); a.set_ylim(len(labels) - 0.5, -0.75)
    a.set_xlim(0, 23); a.set_xticks([0, 5, 10, 15, 20]); a.set_xlabel("Earlier WG cohort retained (%)")
    a.legend(loc="upper left", bbox_to_anchor=(0, 1.15), ncol=2, borderaxespad=0, columnspacing=1.6)
    pairs = [("WG1", "WG2", "I–II"), ("WG1", "WG3", "I–III"), ("WG2", "WG3", "II–III")]
    null_rows = [choose(null, report=r, bin_width="5", group_a=u, group_b=v)
                 for r in REPORTS for u, v, _ in pairs]
    yy = np.arange(9)
    expected = np.array([number(r, "expected_shared_exact") for r in null_rows])
    assert np.all(expected > 0)
    low = np.array([number(r, "shared_lower_95") for r in null_rows]) / expected
    high = np.array([number(r, "shared_upper_95") for r in null_rows]) / expected
    observed = np.array([number(r, "observed_shared") for r in null_rows]) / expected
    b.errorbar(np.ones(9), yy, xerr=np.array([1 - low, high - 1]), fmt="o", color=BLUE,
               capsize=2.5, markersize=3.5, linewidth=1.2, label="Conditional expectation / range")
    b.plot(observed, yy, "D", color=DARK, markersize=3.7, linestyle="none", label="Observed sharing")
    b.axvline(1, color=GREY, linewidth=0.65, linestyle="--")
    for boundary in [2.5, 5.5]:
        b.axhline(boundary, color=PALE, linewidth=0.8)
    b.set_yticks(yy, [f"{r}: {label}" for r in REPORTS for _, _, label in pairs])
    b.set_ylim(8.5, -0.75); b.set_xlim(0.70, 1.34); b.set_xticks([0.8, 1.0, 1.2])
    b.set_xlabel("Shared IDs / conditional expected count")
    b.legend(loc="upper left", bbox_to_anchor=(0, 1.23), borderaxespad=0)
    caption_data["revision_figure4"] = {"retention_rows": retention_caption, "conditional_null_rows": null_rows}
    save(fig, 4, {"question": "How do within-group retention and the allocation of cross-group sharing differ?",
                  "conclusion": "Within-group retention varies by group and cohort rule; observed pairwise sharing deviates in both directions from a benchmark that fixes total sharing.",
                  "panels": {"a": "Stratification: two adjacent assessment transitions within each working group and four identity/screening definitions.",
                             "b": "Conditional comparison: observed pair allocations relative to exact expected counts and conditional null ranges."},
                  "statistics": "Panel a enumerates database IDs; connecting segments mark two transitions, not uncertainty. Panel b uses 2,500 independent conditional null states per report with five-year publication bins and normalized outlet strata. Null ranges are 2.5th–97.5th percentiles. Individual row degrees and stratum group column sums are fixed, so the total of pairwise shared counts is fixed. This is not a test of total integration.",
                  "source_data": ["revision_network_retention_sensitivity.csv", "revision_network_conditional_null.csv"]})

    assert hashes == {name: digest(args.results / name) for name in names}, "Input results changed during rendering"
    (args.qc_output / "revision_figure_contracts.json").write_text(json.dumps(contracts, indent=2) + "\n")
    (args.qc_output / "revision_figure_caption_data.json").write_text(json.dumps(caption_data, indent=2) + "\n")
    validation = subprocess.run([sys.executable, str(skill / "scripts" / "validate_figure.py"),
                                 str(Path(__file__)), "--json"], capture_output=True, text=True)
    source_validation = json.loads(validation.stdout)
    (args.qc_output / "revision_figure_source_validation.json").write_text(json.dumps(source_validation, indent=2) + "\n")
    report = {"source_script_sha256": digest(Path(__file__)), "input_sha256": hashes,
              "source_tables_unchanged": True, "width_mm": WIDTH_MM, "font_floor_pt": 7,
              "exports": ["PDF", "SVG", "PNG"], "png_dpi": 600,
              "figures": records, "source_validation": source_validation,
              "rendered_visual_inspection": "Pending separate panel-by-panel review"}
    (args.qc_output / "revision_figure_export_audit.json").write_text(json.dumps(report, indent=2) + "\n")
    failures = [r["figure"] for r in records if not r["pdf_text_auditable"]
                or r["glyphs_below_7pt"] or r["collision_exit"] != 0]
    if failures or validation.returncode != 0:
        raise SystemExit(f"Fix figure QA findings before delivery: {failures}; source validation exit {validation.returncode}")


if __name__ == "__main__":
    main()
