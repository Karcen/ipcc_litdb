#!/usr/bin/env python3
"""Prepare explicit source-cell claims and manuscript table fragments.

Source selection is fixed by named columns and row filters. No manuscript numbers
are inferred by finding a numerically similar value in a table.
"""
from pathlib import Path
import csv
import json
import re
import sys
import argparse

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--support-dir',type=Path,required=True)
SUPPORT=parser.parse_args().support_dir.resolve()
SUPPORT.mkdir(parents=True,exist_ok=True)
BASE='reproducibility/reference/'
AGE='revision_preparation/review_round3/age/output_v1/'
OFF='revision_preparation/review_round3/external/offline_v1/'
R2='revision_preparation/review_round2/openalex_v1/activated_attempt_03/results_v1/'
NULL='revision_preparation/review_round3/conditional_null/output_v1/'
claims=[]; constants=[]

def add(identifier,path,filters,column,fmt='fixed2',explanation=''):
    identifier=identifier.replace('.','_')
    if identifier in {x['id'] for x in claims+constants}:
        return identifier
    claims.append({'id':identifier,'source':{'path':path,'filters':filters,'column':column},'format':fmt,'explanation':explanation})
    return identifier

def design(identifier,value,fmt='integer',explanation='Fixed analysis design constant'):
    constants.append({'id':identifier.replace('.','_'),'value':value,'format':fmt,'explanation':explanation,'protocol_reference':'Review-round-3 analysis protocol; author task and fixed scripts'})

def M(identifier): return r'\Rthree{'+identifier.replace('.','_')+'}'

def write(name,text): (SUPPORT/name).write_text(text)

def rows(path): return list(csv.DictReader((REPO/path).open()))

for key,value in [('age_max',60),('age_recent',5),('boot_reps',2500),('window_years',30),('interval_percent',95),('year_central',2013),('null_bin_width',5),('annual_early',2012),('annual_late',2014),('seed_value',20261011)]:
    design(key,value)
for key,value in [('citation_bin_zero',0),('citation_bin_one',1),('citation_bin_nine',9),('citation_bin_ten',10),('citation_bin_ninety_nine',99),('citation_bin_hundred',100)]:
    design(key,value,explanation='Citation-count categories fixed in external/analyze_acquisition.py:citation_bin')

# Assigned-ID recurrence and DOI keys: each identity rule retains its denominator.
trans=BASE+'revision_identity_transitions.csv'
filters={'earlier':'AR5','later':'AR6'}
for prefix,definition in [('matched','matched'),('DOImatched','DOI_status'),('extracted','all_extracted')]:
    for suffix,col,fmt in [('n5','papers_a','integer'),('n6','papers_b','integer'),('shared','shared','integer'),('retention','retention','percent2'),('reuse','reuse','percent2')]:
        add(prefix+'_'+suffix,trans,{**filters,'definition':definition},col,fmt)
raw=BASE+'revision_identity_independent_raw_baselines.csv'
for prefix,unit in [('literal','literal_single_raw_DOI_all_statuses'),('repaired','punctuation_repaired_single_raw_DOI_all_statuses'),('string','exact_normalized_raw_reference_all_statuses')]:
    available=[r for r in rows(raw) if r['identity_unit']==unit and r['earlier']=='AR5' and r['later']=='AR6']
    if not available:
        if prefix=='string':
            available=[r for r in rows(raw) if r['earlier']=='AR5' and r['later']=='AR6' and 'string' in r['identity_unit']]
            if len(available)==1: unit=available[0]['identity_unit']
        if not available: raise ValueError('Identity rule missing '+unit)
    for suffix,col,fmt in [('n5','papers_a','integer'),('n6','papers_b','integer'),('shared','shared','integer'),('retention','retention','percent2'),('reuse','reuse','percent2')]:
        add(prefix+'_'+suffix,raw,{**filters,'identity_unit':unit},col,fmt)
for suffix,col,fmt in [('n5','AR5_unique_OpenAlex_work_IDs','integer'),('n6','AR6_unique_OpenAlex_work_IDs','integer'),('shared','shared_OpenAlex_work_IDs','integer'),('retention','retention','percent2'),('reuse','reuse','percent2')]:
    add('OA_'+suffix,R2+'openalex_work_recurrence.csv',{'cohort':'matched_assigned','working_group':'All','publication_availability_screen':'none'},col,fmt)

# Availability windows and fixed preferred-year comparisons.
cut=OFF+'cutoff_reuse_sensitivity.csv'
for wg in ['WG1','WG2','WG3']:
    for suffix,exclusion in [('', 'none'),('_noFirst','exclude_every_month_first')]:
        add('cutoff_'+wg+suffix,cut,{'cohort':'matched_assigned','working_group':wg,'date_exclusion':exclusion},'conditional_reuse','percent2')
    for mode,exclusion in [('all','none'),('noJan','exclude_january_first'),('noFirst','exclude_every_month_first')]:
        for suffix,col,fmt in [('eligible','AR6_eligible_keys','integer'),('shared','shared_eligible_keys','integer'),('reuse','conditional_reuse','percent2')]:
            add('cutoff_'+wg+'_'+mode+'_'+suffix,cut,{'cohort':'matched_assigned','working_group':wg,'date_exclusion':exclusion},col,fmt)
    add('cutoff_'+wg+'_common',cut,{'cohort':'doi_matched_common_sources','working_group':wg,'date_exclusion':'none'},'conditional_reuse','percent2')
annual=BASE+'revision_identity_eligible_reuse.csv'
for suffix,year in [('low',2014),('high',2012),('central',2013)]:
    add('annual_'+suffix,annual,{'earlier':'AR5','later':'AR6','working_group':'All','cutoff_year':str(year),'earlier_membership':'earlier_matched'},'conditional_reuse','percent2')
for prefix,cohort in [('shared','reused_matched_through_2013'),('absent','actually_absent_AR5_through_2013')]:
    for suffix,col,fmt in [('', 'distinct_papers','integer'),('_hhi','hhi_known_journals','fixed5')]:
        add('old_'+prefix+suffix,BASE+'revision_identity_same_vintage_profiles.csv',{'cohort':cohort},col,fmt)
for wg in ['WG1','WG2','WG3']:
    add('wg_'+wg+'_retention',BASE+'revision_network_retention_sensitivity.csv',{'rule':'matched','screening':'none','group':wg,'report_a':'AR5','report_b':'AR6'},'retention','percent2')

# Raw cited-year age summaries; paired point estimates and direct differences.
cohorts={'all':'all_extracted','matched':'matched_occurrences','unmatched':'unmatched_occurrences'}
origins={'source':'source_publication_year','cutoff':'documented_cutoff_year'}
for short,cohort in cohorts.items():
    for origin,age_origin in origins.items():
        for weight,weighting in [('occ','occurrence'),('equal','equal_assigned_ID')]:
            stem='age_'+short+'_'+origin+('' if weight=='occ' else '_equal')
            fs={'cohort':cohort,'age_origin':age_origin,'weighting':weighting}
            for suffix,col,fmt in [('mean5','AR5_mean_age','fixed2'),('mean6','AR6_mean_age','fixed2'),('delta','mean_age_difference','fixed2'),('delta3','mean_age_difference','fixed3'),('lo','mean_age_difference_lower_95','fixed2'),('hi','mean_age_difference_upper_95','fixed2'),('recent5','AR5_recent_le5','percent2'),('recent6','AR6_recent_le5','percent2')]:
                add(stem+'_'+suffix,AGE+'age_changes.csv',fs,col,fmt)
            for report,suffix in [('AR5','5'),('AR6','6')]:
                for q,col in [('q1','q25_inverse_ecdf'),('med','median_inverse_ecdf'),('q3','q75_inverse_ecdf')]:
                    add(stem+'_'+q+suffix,AGE+'age_summary.csv',{**fs,'report':report},col,'integer')
for suffix,col in [('matched','matched_within_change'),('unmatched','unmatched_within_change'),('coverage','matching_composition_change')]:
    add('decomp_'+suffix,AGE+'matching_status_decomposition.csv',{'age_origin':'source_publication_year','weighting':'occurrence','metric':'mean_age'},col,'fixed3')
for suffix,col in [('within','within_component_symmetric'),('composition','composition_component_symmetric')]:
    add('decomp_WG_'+suffix,AGE+'WG_kitagawa.csv',{'cohort':'matched_occurrences','age_origin':'source_publication_year','weighting':'occurrence','metric':'mean_age'},col,'fixed3')
for report,suffix in [('AR5','5'),('AR6','6')]:
    fs={'query':'broad','report':report,'window':'equal_30_annual_bins','cohort':'all','working_group':'All'}
    add('pool_age'+suffix,OFF+'equal_window_age_comparison.csv',fs,'pool_mean_age','fixed2')
    add('pool_IPCC'+suffix,OFF+'equal_window_age_comparison.csv',fs,'IPCC_mean_age','fixed3')
    add('pool_n'+suffix,OFF+'equal_window_age_comparison.csv',fs,'pool_works','integer')
for suffix,col in [('delta','AR6_minus_AR5_mean'),('lo','q025'),('hi','q975')]:
    add('D2_'+suffix,'revision_preparation/review_round3/external/journal_strict_v1/outlet_diversity_differences.csv',{'cohort':'matched','metric':'D2'},col,'fixed2')
source_rows=rows(OFF+'top18_pool_shares.csv')
for i,r in enumerate(source_rows,1):
    for report,suffix in [('AR5','5'),('AR6','6')]:
        for metric,col,fmt in [('share',report+'_share','percent2'),('poolshare',report+'_pool_share','percent2'),('ratio',report+'_IPCC_share_divided_by_pool_share','fixed2')]:
            add('source_'+str(i)+'_'+metric+suffix,OFF+'top18_pool_shares.csv',{'source_id':r['source_id']},col,fmt)
    add('source_'+str(i)+'_delta',OFF+'top18_pool_shares.csv',{'source_id':r['source_id']},'AR6_minus_AR5_share','percent2')
for metric,col,fmt in [('share','share','percent2'),('ratio','IPCC_share_divided_by_pool_share','fixed2')]:
    for report,suffix in [('AR5','5'),('AR6','6')]:
        add('GRL_'+metric+suffix,OFF+'top18_pool_shares.csv',{'source_id':'https://openalex.org/S36624081'},report+'_'+col,fmt)
# Complete source-bucket acquisition: broader primary-source population.
POOL='revision_preparation/review_round3/external/completed_pool_v1/'
for report,suffix in [('AR5','5'),('AR6','6')]:
    for metric,col,fmt in [('D2','D2_full','fixed2'),('n','pool_total','integer'),('known','known_source_works','integer'),('missing','missing_source_fraction','percent2')]:
        add('pool_'+metric+'_'+suffix,POOL+'pool_outlet_diversity.csv',{'query':'broad','report':report,'source_scope':'all_known_sources','identity':'source_id'},col,fmt)
for suffix,col in [('delta','AR6_minus_AR5_mean'),('lo','q025'),('hi','q975')]:
    add('pool_D2_'+suffix,POOL+'pool_outlet_diversity_differences.csv',{'query':'broad','source_scope':'all_known_sources','identity':'source_id','metric':'D2'},col,'fixed2')
# Unconstrained missing-label allocations are a separate, broader population.
for suffix,col in [('lo','D2_difference_min'),('hi','D2_difference_max')]:
    add('missing_D2_'+suffix,'revision_preparation/review_round2/raw_age_v1/missing_outlet_difference_envelopes.csv',{'working_group':'All','normalization':'conservative_alias'},col,'fixed2')
# Finite conditional distributions; Holm adjustment across nine comparisons.
for pair in [('WG1','WG2'),('WG1','WG3'),('WG2','WG3')]:
    fs={'report':'AR6','bin_width':'5','group_a':pair[0],'group_b':pair[1]}
    stem='null_'+pair[0]+'_'+pair[1]
    for suffix,col,fmt in [('observed','observed_shared','integer'),('expected','exact_mean_from_integer_counts','fixed2'),('excess','observed_minus_exact_mean','fixed2'),('p','conditional_two_sided_tail','sci2'),('holm','holm_nine_comparisons','sci2'),('lo','shared_lower_95_discrete','integer'),('hi','shared_upper_95_discrete','integer')]:
        add(stem+'_'+suffix,NULL+'conditional_null_summary.csv',fs,col,fmt)

SUPPORT.mkdir(exist_ok=True,parents=True)
base=r'''% Generated from prepare_claim_spec.py. Numeric cells use source-backed macros.
'''
rec=base+r'''\begin{table}[t]
\centering\footnotesize
\caption{Recurrence from AR5 to AR6 under alternative identity rules. Each row uses its own earlier and later cohorts. DOI keys come from the original reference text; OpenAlex works are uniquely resolved matched assigned identities.}
\label{tab:recurrence}
\begin{tabular}{p{0.34\textwidth}rrrrr}
\toprule
Identity rule & AR5 & AR6 & Shared & Retention (\%) & Reuse (\%) \\
\midrule
'''
for label,prefix in [('Matched assigned identities','matched'),('DOI-based matched identities','DOImatched'),('All extracted assigned identities','extracted'),('Literal original DOI','literal'),('Repaired original DOI','repaired'),('OpenAlex work','OA')]:
    rec+=label+' & '+' & '.join(M(prefix+'_'+s) for s in ['n5','n6','shared','retention','reuse'])+r' \\'+'\n'
rec+=r'''\bottomrule
\end{tabular}
\end{table}
'''
write('table_recurrence.tex',rec)

age=base+r'''\begin{table}[t]
\centering\footnotesize
\caption{Raw cited-year ages. Means and quartiles are occurrence weighted. Intervals describe sensitivity to chapter resampling. The difference is AR6 minus AR5, calculated before rounding.}
\label{tab:age}
\begin{tabular}{llrrrl}
\toprule
Origin & Cohort & AR5 mean & AR6 mean & Difference & \Rthree{interval_percent}\% interval \\
\midrule
'''
for origin,label in [('source','Publication'),('cutoff','Cutoff')]:
    for short,label2 in [('all','All extracted'),('matched','Matched'),('unmatched','Unmatched')]:
        stem='age_'+short+'_'+origin
        age+=label+' & '+label2+' & '+' & '.join(M(stem+'_'+s) for s in ['mean5','mean6','delta3'])+' & ['+M(stem+'_lo')+', '+M(stem+'_hi')+r'] \\'+'\n'
age+=r'''\midrule
\multicolumn{2}{l}{Quartiles: lower / median / upper} & \multicolumn{2}{c}{AR5} & \multicolumn{2}{c}{AR6} \\
'''
for origin,label in [('source','Publication'),('cutoff','Cutoff')]:
    for short,label2 in [('all','All extracted'),('matched','Matched'),('unmatched','Unmatched')]:
        stem='age_'+short+'_'+origin
        q5=' / '.join(M(stem+'_'+q+'5') for q in ['q1','med','q3'])
        q6=' / '.join(M(stem+'_'+q+'6') for q in ['q1','med','q3'])
        age+=label+' & '+label2+r' & \multicolumn{2}{c}{'+q5+r'} & \multicolumn{2}{c}{'+q6+r'} \\'+'\n'
age+=r'''\bottomrule
\end{tabular}
\end{table}
'''
write('table_age.tex',age)
sources=base+r'''\begin{table}[t]
\centering\scriptsize
\caption{Leading journal sources in the resolved assessment cohorts. The rows are the union of the leading sources in each report. IPCC and broad-query publication-pool shares are percentages. Ratios divide the IPCC share by the corresponding pool share. All listed sources have the OpenAlex type journal. The IPCC denominator contains all resolved articles and reviews in the corresponding annual publication window, including sources outside this list.}
\label{tab:sources}
\begin{tabular}{p{0.31\textwidth}rrrrrr}
\toprule
& \multicolumn{2}{c}{IPCC share (\%)} & \multicolumn{2}{c}{Pool share (\%)} & \multicolumn{2}{c}{Share ratio} \\
Journal & AR5 & AR6 & AR5 & AR6 & AR5 & AR6 \\
\midrule
'''
for i,r in enumerate(source_rows,1):
    name=r['source_name'].replace('&',r'\&')
    sources+=name+' & '+' & '.join(M('source_'+str(i)+'_'+metric+s) for metric in ['share','poolshare','ratio'] for s in ['5','6'])+r' \\'+'\n'
sources+=r'''\bottomrule
\end{tabular}
\end{table}
'''
write('table_sources.tex',sources)
supp=base+r'''\begin{table}[htbp]
\centering\footnotesize
\caption{Equal-identity age comparisons. Each included assigned identity has total weight one within a report; the weights are recalculated in every chapter draw.}
\label{tab:equal-age}
\begin{tabular}{llrrrl}
\toprule
Origin & Cohort & AR5 mean & AR6 mean & Difference & \Rthree{interval_percent}\% interval \\
\midrule
'''
for origin,label in [('source','Publication'),('cutoff','Cutoff')]:
    for short,label2 in [('all','All extracted'),('matched','Matched'),('unmatched','Unmatched')]:
        stem='age_'+short+'_'+origin+'_equal'
        supp+=label+' & '+label2+' & '+' & '.join(M(stem+'_'+s) for s in ['mean5','mean6','delta3'])+' & ['+M(stem+'_lo')+', '+M(stem+'_hi')+r'] \\'+'\n'
supp+=r'''\bottomrule
\end{tabular}
\end{table}

\begin{table}[htbp]
\centering\footnotesize
\caption{Conditional reuse using working-group publication cutoff dates. The exclusions address first-day dates whose monthly precision may be uncertain. Numerators and denominators are later eligible assigned identities and their recurrence in the earlier matched cohort.}
\label{tab:date-precision}
\begin{tabular}{llrrr}
\toprule
Group & Date treatment & Eligible & Shared & Reuse (\%) \\
\midrule
'''
for wg,label in [('WG1','WGI'),('WG2','WGII'),('WG3','WGIII')]:
    for mode,treatment in [('all','All recorded dates'),('noJan','Exclude first of January'),('noFirst','Exclude first of each month')]:
        supp+=label+' & '+treatment+' & '+' & '.join(M('cutoff_'+wg+'_'+mode+'_'+s) for s in ['eligible','shared','reuse'])+r' \\'+'\n'
supp+=r'''\bottomrule
\end{tabular}
\end{table}

\begin{table}[htbp]
\centering\scriptsize
\caption{AR6 conditional sharing with journal-source and publication-period strata. Tail probabilities use finite state enumeration and direct convolution. The adjustment covers the fixed family of comparisons across reports. The strata contain source labels and publication years; topic is not included.}
\label{tab:exact-sharing}
\setlength{\tabcolsep}{3pt}
\begin{tabular}{lrrrrrr}
\toprule
Groups & Observed & Expected & Excess & Central \Rthree{interval_percent}\% & Tail prob. & Holm adj. \\
\midrule
'''
for pair,label in [(('WG1','WG2'),'WGI--WGII'),(('WG1','WG3'),'WGI--WGIII'),(('WG2','WG3'),'WGII--WGIII')]:
    stem='null_'+pair[0]+'_'+pair[1]
    supp+=label+' & '+' & '.join(M(stem+'_'+s) for s in ['observed','expected','excess'])+' & ['+M(stem+'_lo')+', '+M(stem+'_hi')+'] & '+M(stem+'_p')+' & '+M(stem+'_holm')+r' \\'+'\n'
supp+=r'''\bottomrule
\end{tabular}
\end{table}
'''
write('tables_supplement.tex',supp)
fragments=re.findall(r'\\begin\{table\}.*?\\end\{table\}',supp,flags=re.S)
for filename,fragment in zip(['table_equal_age.tex','table_date_precision.tex','table_exact_sharing.tex'],fragments):
    write(filename,base+fragment+'\n')
write('tables.tex',base+r'''\input{v3_support/table_recurrence}
\input{v3_support/table_age}
\input{v3_support/table_sources}
''')

for group in ['WG1','WG2','WG3']:
    for report, suffix in [('AR5','5'),('AR6','6')]:
        add('coverage_'+group+'_'+suffix, BASE+'revision_network_coverage.csv',{'report':report,'group':group},'matched_occurrence_fraction','percent2')
for report,suffix in [('AR5','5'),('AR6','6')]:
    add('coverage_total_'+suffix,BASE+'coverage.csv',{'report_id':report},'matched_fraction','percent2')


STRICT='revision_preparation/review_round3/external/journal_strict_v1/'
for suffix,col in [('delta','AR6_minus_AR5_mean'),('lo','q025'),('hi','q975')]:
    add('D2_recovered_'+suffix,STRICT+'outlet_diversity_differences.csv',{'cohort':'matched_plus_DOI_recovered_unmatched','metric':'D2'},col,'fixed2')
for report,suffix in [('AR5','5'),('AR6','6')]:
    add('journal_n'+suffix,STRICT+'outlet_diversity.csv',{'cohort':'matched','report':report},'assigned_IDs','integer')
    add('journal_D2_'+suffix,STRICT+'outlet_diversity.csv',{'cohort':'matched','report':report},'D2_full','fixed2')
add('journal_equal_n',STRICT+'outlet_diversity_differences.csv',{'cohort':'matched','metric':'D2'},'equal_size','integer')


SOURCES='revision_preparation/review_round3/external/new_api_sources_v1/'
for query,prefix in [('broad','journal_pool'),('narrow','narrow_journal_pool')]:
    for report,suffix in [('AR5','5'),('AR6','6')]:
        filters={'query':query,'report':report,'scope':'journal_sources','identity':'source_id'}
        add(prefix+'_D2_'+suffix,SOURCES+'pool_outlet_diversity.csv',filters,'D2','fixed2')
        add(prefix+'_n'+suffix,SOURCES+'pool_outlet_diversity.csv',filters,'included_works','integer')
    for suffix,column in [('delta','AR6_minus_AR5_D2_mean'),('lo','q025'),('hi','q975')]:
        add(prefix+'_D2_'+suffix,SOURCES+'pool_outlet_diversity_differences.csv',{'query':query,'scope':'journal_sources','identity':'source_id'},column,'fixed2')


# Sample sizes and resampling counts are empirical output fields.
for origin,originlabel in [('source','source_publication_year'),('cutoff','documented_cutoff_year')]:
    for short,cohort in [('all','all_extracted'),('matched','matched_occurrences'),('unmatched','unmatched_occurrences')]:
        for report,suffix in [('AR5','5'),('AR6','6')]:
            filters={'report':report,'cohort':cohort,'age_origin':originlabel,'weighting':'occurrence'}
            for metric,column in [('occ','included_occurrences'),('ids','included_assigned_IDs'),('units','selected_chapter_clusters')]:
                add('age_count_'+origin+'_'+short+'_'+metric+suffix,AGE+'age_summary.csv',filters,column,'integer')
add('journal_draws',STRICT+'outlet_diversity_differences.csv',{'cohort':'matched','metric':'D2'},'draws','integer')
add('pool_draws',SOURCES+'pool_outlet_diversity_differences.csv',{'query':'broad','scope':'journal_sources','identity':'source_id'},'draws','integer')
INDIRECT='revision_preparation/review_round3/external/indirect_pool_v1/indirect_pool_baseline.csv'
for identifier,column,fmt in [('indirect_pool_n','sample_size','integer'),('indirect_targets','AR5_target_work_IDs','integer'),('indirect_pool_positive','at_least_one_AR5_target','integer'),('indirect_pool_fraction','fraction','percent2'),('indirect_pool_lo','wilson025','percent2'),('indirect_pool_hi','wilson975','percent2'),('indirect_pool_empty','empty_reference_arrays','integer'),('indirect_ipcc_n','IPCC_common_scope_AR6_works','integer'),('indirect_ipcc_positive','IPCC_common_scope_at_least_one_AR5_target','integer'),('indirect_ipcc_fraction','IPCC_common_scope_fraction','percent2'),('indirect_ipcc_empty','IPCC_common_scope_empty_reference_arrays','integer')]:
    add(identifier,INDIRECT,{},column,fmt)
AI='revision_preparation/annotation/ai_review_summary_v1/'
for group,prefix in [('probability_sample','ai_prob'),('targeted_supplement','ai_target')]:
    add(prefix+'_n',AI+'ai_review_id_category_counts.csv',{'sample_group':group,'category_family':'identity_label','category':'correct_merge'},'denominator_ids','integer')
    add(prefix+'_entries',AI+'ai_review_occurrence_category_counts.csv',{'sample_group':group,'metadata_judgment':'correct'},'denominator_occurrences','integer')
    for suffix,family,category in [('mixed','identity_label','false_merge'),('shared','shared_link_label','same_work'),('false_shared','shared_link_label','false_shared'),('counterpart','counterpart_proposed','yes'),('metadata','any_metadata_error','yes'),('unresolved','any_metadata_unresolved','yes')]:
        add(prefix+'_'+suffix,AI+'ai_review_id_category_counts.csv',{'sample_group':group,'category_family':family,'category':category},'n_ids','integer')
counts=base+r'\begin{table}[htbp]'+'\n'+r'\centering\footnotesize'+'\n'+r'\caption{Age sample sizes. Each cell gives included occurrences / included assigned identities / selected catalogue units. Units without an included age remain eligible for chapter resampling.}'+'\n'+r'\label{tab:age-counts}'+'\n'+r'\begin{tabular}{llrr}\toprule Origin & Cohort & AR5 & AR6\\\midrule'+'\n'
for origin,label in [('source','Publication'),('cutoff','Cutoff')]:
    for short,cohort in [('all','All extracted'),('matched','Matched'),('unmatched','Unmatched')]:
        counts+=label+' & '+cohort+' & '+' & '.join(' / '.join(M('age_count_'+origin+'_'+short+'_'+metric+suffix) for metric in ['occ','ids','units']) for suffix in ['5','6'])+r' \\'+'\n'
counts+=r'\bottomrule\end{tabular}\end{table}'+'\n';write('table_age_counts.tex',counts)
ai=base+r'\begin{table}[htbp]'+'\n'+r'\centering\footnotesize'+'\n'+r'\caption{Archived AI source-review proposals. Counts are unweighted and can overlap. These labels have no independent human adjudication and do not measure linkage accuracy.}'+'\n'+r'\label{tab:ai-proposals}'+'\n'+r'\begin{tabular}{lrr}\toprule Measure & Probability sample & Targeted supplements\\\midrule'+'\n'
for key,label in [('n','Assigned identities'),('entries','Original entries'),('mixed','Mixed-study proposals'),('shared','Shared identities inspected'),('false_shared','False-sharing proposals'),('counterpart','Proposed counterpart links'),('metadata','Identities with metadata discrepancies'),('unresolved','Identities with unresolved metadata')]:
    ai+=label+' & '+M('ai_prob_'+key)+' & '+M('ai_target_'+key)+r' \\'+'\n'
ai+=r'\bottomrule\end{tabular}\end{table}'+'\n';write('table_ai_proposals.tex',ai)


add('older_unmatched',BASE+'revision_identity_earlier_incoming_partition_summary.csv',{'AR5_status_set':'unmatched_only','AR6_status_set':'doi_only'},'IDs','integer')
RISK='revision_preparation/review_round3/external/new_api_results_v2/conditional_relative_risk.csv'
SUPPORT_RISK='revision_preparation/review_round3/external/risk_support_v1/relative_risk_support.csv'
for prefix,adjustment in [('rr_year','publication_year_source_type'),('rr_cites','publication_year_source_type_citation_bin')]:
    for suffix,column,fmt in [('point','RR_Mantel_Haenszel','fixed2'),('lo','CI025','fixed2'),('hi','CI975','fixed2'),('strata','strata','integer'),('both','overlap_strata','integer')]:
        add(prefix+'_'+suffix,RISK,{'adjustment':adjustment},column,fmt)
    for arm,short in [('exposed','exposed'),('unexposed','other')]:
        add(prefix+'_'+short+'_support',SUPPORT_RISK,{'adjustment':adjustment,'arm':arm},'sample_support_fraction','percent2')
for identifier,column in [('rr_exposed_frame','AR5_cited_population'),('rr_other_frame','AR5_not_cited_population'),('rr_exposed_n','exposed_sample'),('rr_other_n','unexposed_sample'),('rr_exposed_positive','exposed_AR6_positive_sample'),('rr_other_positive','unexposed_AR6_positive_sample')]:
    add(identifier,RISK,{'adjustment':'publication_year_source_type'},column,'integer')
for arm,short in [('exposed','exposed'),('unexposed','other')]:
    add('rr_'+short+'_weight',SUPPORT_RISK,{'adjustment':'publication_year_source_type','arm':arm},'inverse_inclusion_weight','fixed3')
for identifier,value in [('pool_first_year',1980),('citation_from',2014),('citation_to',2021)]:
    design(identifier,value,explanation='Fixed publication/citation window in the external acquisition protocol')
rr=base+r'\begin{table}[htbp]'+'\n'+r'\centering\footnotesize'+'\n'+r'\caption{Relative representation in AR6 among older journal-pool works. Risk ratios compare works cited in AR5 with works not cited there. Each calculation retains strata containing both sampled groups. Support percentages describe the samples, not the exact population coverage.}'+'\n'+r'\label{tab:rr-support}'+'\n'+r'\begin{tabular}{lrrlrr}\toprule & & & & \multicolumn{2}{c}{Sample retained (\%)}\\ Stratification & Strata used / all & RR & Interval & AR5 cited & Other\\\midrule'+'\n'
for prefix,label in [('rr_year','Publication year'),('rr_cites','Year and later citations')]:
    rr+=label+' & '+M(prefix+'_both')+' / '+M(prefix+'_strata')+' & '+M(prefix+'_point')+' & ['+M(prefix+'_lo')+', '+M(prefix+'_hi')+'] & '+M(prefix+'_exposed_support')+' & '+M(prefix+'_other_support')+r' \\'+'\n'
rr+=r'\bottomrule\end{tabular}\end{table}'+'\n';write('table_risk_support.tex',rr)

# Reference-array coverage comparison uses the same registered target set.
for identifier,column in [('indirect_pool_nonempty_fraction','fraction_among_nonempty_arrays'),('indirect_ipcc_nonempty_fraction','IPCC_fraction_among_nonempty_arrays')]:
    add(identifier,'revision_preparation/review_round3/external/indirect_pool_v1/indirect_pool_baseline.csv',{},column,'percent2')

spec={'claims':claims,'design_constants':constants,'structural_exclusions':[{'text':r'\(D_2=1/\sum_jp_j^2\)','explanation':'Definition of inverse Simpson concentration; subscripts, power and numerator are algebraic constants.'}]}
(HERE/'claim_spec.json').write_text(json.dumps(spec,indent=2)+'\n')
print(json.dumps({'claims':len(claims),'constants':len(constants),'tables':str(SUPPORT)},indent=2))
