#!/usr/bin/env python3
"""Plot deterministic paired source-allocation scenarios at manuscript width."""
import csv, hashlib, json, os, pathlib, sys
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt

ROOT=pathlib.Path(__file__).resolve().parent
SKILL=pathlib.Path(os.environ.get('NATURE_FIGURE_SKILL','/Users/karcenzheng/.codex/skills/ipcc-v3-20261021/nature-figure'))
sys.path.insert(0,str(SKILL/'scripts'))
from audit_panel_alignment import require_matplotlib_panel_alignment

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

def main():
    source=ROOT/'offline_v1/missing_source_allocation_curve.csv';out=ROOT/'figures_v1';out.mkdir(exist_ok=True)
    with source.open(newline='') as f:rows=list(csv.DictReader(f))
    # Both report rows contain the same paired difference; use AR6 once.
    selected=[r for r in rows if r['report']=='AR6']
    assert len(selected)*2==len(rows)
    mpl.rcParams.update({'font.family':'sans-serif','font.sans-serif':['DejaVu Sans'],'font.size':7,'axes.labelsize':7,'xtick.labelsize':7,'ytick.labelsize':7,
       'legend.fontsize':7,'axes.linewidth':.65,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'svg.fonttype':'none','svg.hashsalt':'ipcc-round3-20261011',
       'lines.linewidth':1.2,'xtick.major.width':.65,'ytick.major.width':.65,'xtick.major.size':3,'ytick.major.size':3})
    fig,ax=plt.subplots(figsize=(133/25.4,70/25.4));fig.subplots_adjust(left=.135,right=.975,bottom=.22,top=.79)
    styles=[('toward_largest_source','Toward largest source','#0072B2','-'),('toward_distinct_sources','Toward distinct sources','#D55E00','--')]
    for direction,label,color,linestyle in styles:
        rr=sorted((r for r in selected if r['direction']==direction),key=lambda r:float(r['alpha']));x=[float(r['alpha']) for r in rr];y=[float(r['AR6_minus_AR5_D2']) for r in rr]
        assert len(x)==21 and all(a<b for a,b in zip(x,x[1:]))
        ax.plot(x,y,label=label,color=color,linestyle=linestyle)
    ax.axhline(0,color='.55',linewidth=.6,zorder=0)
    ax.set(xlim=(0,1),ylim=(-3,82),xlabel='Allocation tilt α',ylabel='D₂ difference (AR6 − AR5)')
    ax.set_xticks([0,.25,.5,.75,1],['0','0.25','0.50','0.75','1.00']);ax.set_yticks([0,20,40,60,80])
    fig.legend(*ax.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.56,.98),ncol=2,frameon=False,handlelength=2.4,columnspacing=2)
    fig.canvas.draw()
    require_matplotlib_panel_alignment(fig,json_out=str(out/'source_allocation.alignment.json'),overlay_svg=str(out/'source_allocation.alignment.svg'),strict=True)
    for extension in ['png','pdf','svg']:
        metadata={'Creator':'ipcc_litdb analysis package 1.4.0'}
        if extension=='pdf':metadata.update(CreationDate=None,ModDate=None)
        if extension=='svg':metadata.update(Date=None)
        fig.savefig(out/('source_allocation_sensitivity.'+extension),dpi=600,metadata=metadata,facecolor='white')
    plt.close(fig)
    (out/'manifest.json').write_text(json.dumps({'analysis_package':'1.4.0','seed':20261011,'input_sha256':sha(source),'script_sha256':sha(__file__),
       'renderer':{'matplotlib':mpl.__version__},'dimensions_mm':[133,70],'PNG_dpi':600,'font':'DejaVu Sans','font_size_pt':7,'quantitative_rows':len(selected),
       'uncertainty':'Deterministic allocation scenarios, not confidence intervals.','outputs':[{'filename':p.name,'sha256':sha(p)} for p in sorted(out.glob('source_allocation_sensitivity.*')) if not p.name.startswith('._')]},indent=2)+'\n')

if __name__=='__main__':main()
