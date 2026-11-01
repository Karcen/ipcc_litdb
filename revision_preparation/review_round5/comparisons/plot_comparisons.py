#!/usr/bin/env python3
"""Nature-style descriptive displays of fixed-frame comparisons; Python only."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.transforms import ScaledTranslation
from matplotlib.patches import Rectangle, FancyArrowPatch

SEED = 20261011

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save_formats(fig, stem):
    fig.savefig(stem.with_suffix('.pdf'), dpi=600, facecolor='white')
    fig.savefig(stem.with_suffix('.svg'), dpi=600, facecolor='white')
    fig.savefig(stem.with_suffix('.png'), dpi=600, facecolor='white')
    return [{'path': stem.with_suffix('.' + ext).name,
             'sha256': sha(stem.with_suffix('.' + ext))} for ext in ['pdf', 'svg', 'png']]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=Path(__file__).resolve().parent / 'output_v1')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'figures_v1')
    parser.add_argument('--nature-scripts', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.nature_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.input / 'wg_reference_recurrence.csv').open() as f:
        wg = list(csv.DictReader(f))
    with (args.input / 'hill_profiles.csv').open() as f:
        hill = list(csv.DictReader(f))
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'],
                         'font.size': 7.5, 'axes.labelsize': 8, 'axes.titlesize': 8,
                         'xtick.labelsize': 7, 'ytick.labelsize': 7.5,
                         'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none',
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.linewidth': .65, 'lines.linewidth': 1.1,
                         'savefig.dpi': 600, 'legend.frameon': False})
    contract = {'question': 'How does the direction and denominator of reference recurrence differ across working groups?',
                'claim': 'Earlier-cohort retention and later-cohort reuse summarize different recorded opportunities; group-specific publication-date screens change the reuse denominator.',
                'archetype': 'quantitative grid', 'backend': 'python', 'seed': SEED,
                'panels': {'a': 'WGI', 'b': 'WGII', 'c': 'WGIII'},
                'uncertainty': 'None: fixed-frame descriptive proportions; no model-accuracy or population interval is claimed.',
                'membership': 'One assigned identity within each group; identities spanning groups remain in each group.',
                'cutoff_scope': 'Older reuse uses uniquely resolved publication dates and each group cutoff, not the full unmatched denominator.',
                'formats': ['png', 'pdf', 'svg'], 'width_mm': 180}
    (args.output / 'figure_contract.json').write_text(json.dumps(contract, indent=2) + '\n')
    colors = ['#406C92', '#727272', '#BD6E48']
    metrics = ['retention', 'unconditional_reuse', 'publication_by_cutoff_reuse']
    labels = ['Retention', 'Reuse, all AR6', 'Reuse, older AR6']
    fig, axes = plt.subplots(1, 3, figsize=(180 / 25.4, 66 / 25.4), sharey=True)
    fig.subplots_adjust(left=.20, right=.985, bottom=.25, top=.80, wspace=.22)
    for index, (ax, group, title) in enumerate(zip(axes, ['WG1', 'WG2', 'WG3'], ['WGI', 'WGII', 'WGIII'])):
        ax.set_gid(chr(97 + index))
        for y, metric, color in zip([2, 1, 0], metrics, colors):
            selected = [r for r in wg if r['working_group'] == group and r['metric'] == metric]
            if len(selected) != 1:
                raise ValueError('Missing or duplicate plotted estimate')
            value = 100 * float(selected[0]['fraction'])
            ax.hlines(y, 0, value, color=color, linewidth=.85, alpha=.65)
            ax.scatter([value], [y], color=color, s=24, zorder=3)
        ax.set_xlim(0, 45)
        ax.set_xticks([0, 15, 30, 45])
        ax.set_ylim(-.55, 2.55)
        ax.set_yticks([2, 1, 0], labels)
        ax.tick_params(axis='y', length=0, pad=6)
        ax.set_title(title, pad=13)
        trans = ax.transAxes + ScaledTranslation(-7 / 72, 13 / 72, fig.dpi_scale_trans)
        ax.text(0, 1, chr(97 + index), transform=trans, fontweight='bold', fontsize=8, ha='left', va='baseline')
        ax.spines['left'].set_visible(False)
    fig.text(.59, .07, 'Reference fraction (%)', ha='center', fontsize=8)
    fig.canvas.draw()
    require_matplotlib_panel_alignment(fig, json_out=str(args.output / 'wg_reference_recurrence.alignment.json'),
                                       tolerance_pt=1.5, gutter_tolerance_pt=1.5,
                                       require_panel_labels=True, strict=True)
    outputs = []
    outputs.extend(save_formats(fig, args.output / 'wg_reference_recurrence'))
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(100 / 25.4, 84 / 25.4))
    fig.subplots_adjust(left=.18, right=.97, bottom=.31, top=.95)
    for cohort, style, suffix in [('matched', '-', 'Matched'), ('matched_plus_DOI_recovered_unmatched', '--', '+ DOI recovery')]:
        for report, color, marker in [('AR5', '#406C92', 'o'), ('AR6', '#BD6E48', 's')]:
            rows = sorted([r for r in hill if r['cohort'] == cohort and r['report'] == report], key=lambda r: int(r['q']))
            if [int(r['q']) for r in rows] != [0, 1, 2]:
                raise ValueError('Incomplete Hill profile')
            if any(float(r['hill_number']) <= 0 for r in rows):
                raise ValueError('Nonpositive Hill number cannot use a log axis')
            ax.plot([int(r['q']) for r in rows], [float(r['hill_number']) for r in rows],
                    linestyle=style, marker=marker, markersize=3.5, color=color,
                    label=report + ' ' + suffix)
    ax.set_yscale('log')
    ax.set_ylim(35, 4000)
    ax.set_yticks([50, 100, 500, 1000, 3000], ['50', '100', '500', '1000', '3000'])
    ax.minorticks_off()
    ax.set_xticks([0, 1, 2])
    ax.set_xlabel('Hill order, q')
    ax.set_ylabel('Effective journal sources')
    ax.legend(loc='upper center', bbox_to_anchor=(.50, -.25), ncol=2, fontsize=6.5, columnspacing=1.0, handlelength=2.4)
    outputs.extend(save_formats(fig, args.output / 'hill_profiles'))
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(180 / 25.4, 135 / 25.4))
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    def box(x, y, width, height, text, face='#F3F5F6'):
        ax.add_patch(Rectangle((x-width/2, y-height/2), width, height,
                               facecolor=face, edgecolor='#75808A', linewidth=.65))
        ax.text(x, y, text, ha='center', va='center', fontsize=8, linespacing=1.35)
    def arrow(x1, y1, x2, y2):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle='-|>',
                                     mutation_scale=8, linewidth=.75, color='#53616C',
                                     shrinkA=1, shrinkB=1))
    centers = [.93, .79, .65, .51, .37]
    texts = ['IPCC report reference lists',
             'Extracted raw strings + catalogue locations',
             'Report and working-group membership',
             'DOI match / Title match / No WoS match',
             'Assigned work IDs + occurrence links']
    for y, text in zip(centers, texts):
        box(.5, y, .69, .078, text)
    for a, b in zip(centers, centers[1:]):
        arrow(.5, a-.039, .5, b+.039)
    box(.5, .225, .69, .10, 'Versioned correction ledger\nOriginal strings retained', '#F6EDE8')
    arrow(.5, .331, .5, .275)
    branches = [(.18, 'Reference recurrence\nReport-level sets'),
                (.50, 'Citation age\nCited year + origin'),
                (.82, 'Journal concentration\nKnown source counts')]
    for x, text in branches:
        box(x, .075, .29, .10, text, '#ECF2F6')
        arrow(.5, .175, x, .125)
    outputs.extend(save_formats(fig, args.output / 'data_construction_flow'))
    plt.close(fig)
    manifest = {'analysis_version': '1.6.0', 'seed': SEED, 'backend': 'Python/matplotlib',
                'script_sha256': sha(Path(__file__)), 'matplotlib_version': matplotlib.__version__,
                'inputs': [{'path': name, 'sha256': sha(args.input / name)} for name in ['wg_reference_recurrence.csv', 'hill_profiles.csv']],
                'outputs': outputs,
                'hill_profile_note': 'Observed full resolved-source counts, not coverage-standardized extrapolation. No intervals appear because these are deterministic cohort summaries.'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'exported': [x['path'] for x in outputs], 'seed': SEED}, indent=2))

if __name__ == '__main__':
    main()
