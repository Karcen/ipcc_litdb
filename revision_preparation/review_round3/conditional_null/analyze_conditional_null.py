#!/usr/bin/env python3
"""Enumerated conditional overlap distributions; no Monte Carlo samples."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0,str(REPO/"scripts"))
import numpy as np
import review_round2_raw_age as archive
base = sys.modules["revision_age_networks"]

SEED = 20261011
VERSION = "1.4.0-conditional-null-1"
PAIRS = ((0,1),(0,2),(1,2))
DECIMAL_PRECISION = 80


def counted_marginals(patterns):
    """Reuse feasible states; count labelled matrices with exact Python ints."""
    values, previous_p, (a,b,c), margins = base.conditional_states(patterns)
    counts = [Counter(),Counter(),Counter()]
    for value in values:
        state = tuple(int(x) for x in value)
        x = (state[2]-c,state[1]-c,state[0]-c)
        g = tuple(margins[i]-c-b+x[i] for i in range(3))
        multiplicity = math.comb(b,x[0])*math.comb(b-x[0],x[1])*math.comb(a,g[0])*math.comb(a-g[0],g[1])
        for j,shared in enumerate(state):
            counts[j][shared] += multiplicity
    totals = [sum(cc.values()) for cc in counts]
    if len(set(totals)) != 1:
        raise ValueError("Three marginal denominators differ")
    denominator = totals[0]
    means = [Fraction(sum(k*v for k,v in cc.items()),denominator) for cc in counts]
    if sum(means) != b+3*c:
        raise ValueError("Fixed total sharing not conserved by exact combinatorial counts")
    if not np.allclose(np.array([float(x) for x in means]),previous_p@values,rtol=1e-11,atol=1e-10):
        raise ValueError("Exact integer means disagree with previous log-factorial enumeration")
    return counts,denominator,means,(a,b,c),margins,len(values)


def probability_array(counts,denominator):
    lo,hi = min(counts),max(counts)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        den = Decimal(denominator)
        values = np.array([np.longdouble(str(Decimal(counts.get(k,0))/den)) for k in range(lo,hi+1)],dtype=np.longdouble)
    return lo,values


def direct_convolution(factors):
    offset = 0
    result = np.array([1],dtype=np.longdouble)
    operations = 0
    for counts,denominator in factors:
        lo,values = probability_array(counts,denominator)
        offset += lo
        operations += 2*len(result)*len(values)
        result = np.convolve(result,values)
        if (result < 0).any() or not np.isfinite(result).all():
            raise ValueError("Invalid positive convolution mass")
    return offset,result,operations


def decimal_convolution(factors):
    """Positive direct convolution at 80 decimal digits, without FFT."""
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        result = [Decimal(1)]
        offset = 0
        for counts,denominator in factors:
            lo,hi = min(counts),max(counts)
            offset += lo
            den = Decimal(denominator)
            values = [Decimal(counts.get(k,0))/den for k in range(lo,hi+1)]
            if len(values) == 1:
                if values[0] != 1:
                    result = [x*values[0] for x in result]
                continue
            out = [Decimal(0)]*(len(result)+len(values)-1)
            for i,x in enumerate(result):
                if not x:
                    continue
                for j,y in enumerate(values):
                    if y:
                        out[i+j] += x*y
            result = out
        return offset,result


def tail_support(observed,mean):
    """Integer tail thresholds use an exact rational mean, without tolerance."""
    reflected = 2*mean-observed
    if observed >= mean:
        return math.floor(reflected),observed
    return observed,math.ceil(reflected)


def discrete_quantile(offset,probabilities,probability):
    cumulative = np.cumsum(probabilities)
    target = np.longdouble(str(probability))
    index = int(np.searchsorted(cumulative,target,side="left"))
    if index >= len(probabilities):
        raise ValueError("Convolution mass does not reach requested quantile")
    return offset+index


def summarize_distribution(factors,observed,mean):
    offset,pmf,operations = direct_convolution(factors)
    support = np.arange(offset,offset+len(pmf),dtype=np.int64)
    lo_tail,hi_tail = tail_support(observed,mean)
    mask = (support <= lo_tail)|(support >= hi_tail)
    tail = np.sum(pmf[mask],dtype=np.longdouble)
    # Direct positive sums retain tails below machine epsilon without subtracting
    # a CDF from one. Verify small tails with independent high-precision arithmetic.
    decimal_tail = ""
    decimal_mass = ""
    decimal_verified = False
    if tail < np.longdouble("1e-8") or tail == 0:
        d_offset,d_pmf = decimal_convolution(factors)
        if d_offset != offset or len(d_pmf) != len(pmf):
            raise ValueError("Decimal and NumPy supports differ")
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            d_tail = sum((p for k,p in enumerate(d_pmf,offset) if k <= lo_tail or k >= hi_tail),Decimal(0))
            d_mass = sum(d_pmf,Decimal(0))
            decimal_tail = str(d_tail)
            decimal_mass = str(d_mass)
            if d_tail == 0:
                raise ValueError("Tail rounded to zero even at declared Decimal precision; report an explicit bound instead")
            if tail > 0:
                relative = abs(Decimal(str(tail))-d_tail)/d_tail
                if relative > Decimal("1e-9"):
                    raise ValueError("Positive float tail failed high-precision verification")
            tail_string = decimal_tail
            decimal_verified = True
    else:
        tail_string = str(tail)
    mass = np.sum(pmf,dtype=np.longdouble)
    observed_mean = np.sum(pmf*support,dtype=np.longdouble)
    if abs(float(mass)-1)>1e-11 or abs(float(observed_mean)-float(mean))>1e-8:
        raise ValueError("Convolved mass or mean fails precision check")
    row = {"support_lower":offset,"support_upper":offset+len(pmf)-1,
        "exact_mean_from_integer_counts":float(mean),"observed_shared":observed,
        "observed_minus_exact_mean":observed-float(mean),
        "shared_lower_95_discrete":discrete_quantile(offset,pmf,"0.025"),
        "shared_upper_95_discrete":discrete_quantile(offset,pmf,"0.975"),
        "tail_lower_region_max":lo_tail,"tail_upper_region_min":hi_tail,
        "conditional_two_sided_tail":tail_string,"tail_high_precision_verified":decimal_verified,
        "tail_decimal_80_digits":decimal_tail,"decimal_total_probability":decimal_mass,
        "convolved_total_probability":str(mass),"convolved_mean":str(observed_mean),
        "positive_convolution_arithmetic_operations_upper":operations,
        "underflowed_support_cells":int(np.count_nonzero(pmf == 0)),
        "probability_method":"finite state enumeration and positive direct convolution",
        "probability_precision":"Decimal80 verified tail" if decimal_verified else f"NumPy longdouble {np.finfo(np.longdouble).nmant+1}-bit significand"}
    return row,support,pmf


def holm_decimal(values):
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        numbers = [Decimal(v) for v in values]
        order = sorted(range(len(numbers)),key=lambda i:numbers[i])
        result = [None]*len(numbers)
        last = Decimal(0)
        for rank,i in enumerate(order):
            last = max(last,min(Decimal(1),numbers[i]*(len(numbers)-rank)))
            result[i] = str(last)
        return result


def load_rows(database):
    with sqlite3.connect(database.as_uri()+"?mode=ro&immutable=1",uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(r) for r in connection.execute(base.SQL)]
    if len(rows) != len({r["occurrence_id"] for r in rows}):
        raise ValueError("Nonunique source occurrences")
    return rows


def build(database):
    rows = load_rows(database)
    metadata = {}
    groupsets = {}
    for report in base.REPORTS:
        for group in base.GROUPS:
            groupsets[(report,group)] = {r["paper_id"] for r in rows if r["report_id"] == report and r["wg_label"] == group and r["match_status"] in base.MATCHED}
    for r in rows:
        value = (r["year_best"],r["journal_best"])
        if r["paper_id"] in metadata and metadata[r["paper_id"]] != value:
            raise ValueError("Assigned metadata not constant within ID")
        metadata[r["paper_id"]] = value
    summaries,strata_rows,marginal_rows,distribution_rows,conservation = [],[],[],[],[]
    for width in (5,10):
        for report in base.REPORTS:
            universe = sorted({r["paper_id"] for r in rows if r["report_id"] == report and r["match_status"] in base.MATCHED})
            strata = defaultdict(list)
            for pid in universe:
                yy = base.year(metadata[pid][0])
                vintage = str((yy//width)*width) if yy else "unknown"
                pattern = sum(1<<j for j,g in enumerate(base.GROUPS) if pid in groupsets[(report,g)])
                strata[(base.journal(metadata[pid][1]),vintage)].append(pattern)
            factors = [[],[],[]]
            expected = [Fraction(0),Fraction(0),Fraction(0)]
            margins = np.zeros(3,dtype=int)
            total_fixed_pairs = 0
            variable = 0
            for stratum_index,((source,vintage),patterns) in enumerate(sorted(strata.items())):
                nonzero = [p for p in patterns if p]
                if not nonzero:
                    continue
                counts,denominator,means,degree,ns,nstates = counted_marginals(nonzero)
                variable += nstates > 1
                margins += ns
                total_fixed_pairs += degree[1]+3*degree[2]
                for j,(counter,mean) in enumerate(zip(counts,means)):
                    factors[j].append((counter,denominator))
                    expected[j] += mean
                    lo,probabilities = probability_array(counter,denominator)
                    for value,probability in enumerate(probabilities,lo):
                        marginal_rows.append({"report":report,"bin_width":width,"stratum_index":stratum_index,
                            "group_a":base.GROUPS[PAIRS[j][0]],"group_b":base.GROUPS[PAIRS[j][1]],
                            "shared":value,"probability":str(probability)})
                strata_rows.append({"report":report,"bin_width":width,"stratum_index":stratum_index,"journal_key":source,
                    "publication_year_bin":vintage,"ids":len(patterns),"zero_memberships":len(patterns)-len(nonzero),
                    "one_membership":degree[0],"two_memberships":degree[1],"three_memberships":degree[2],
                    "group1":ns[0],"group2":ns[1],"group3":ns[2],"feasible_overlap_states":nstates,
                    "WG1_WG2_mean_from_integer_counts":float(means[0]),
                    "WG1_WG3_mean_from_integer_counts":float(means[1]),"WG2_WG3_mean_from_integer_counts":float(means[2]),
                    "marginal_probability_denominator_bit_length":denominator.bit_length()})
            observed = [len(groupsets[(report,base.GROUPS[i])]&groupsets[(report,base.GROUPS[j])]) for i,j in PAIRS]
            if sum(observed) != total_fixed_pairs or sum(expected) != total_fixed_pairs:
                raise ValueError("Total observed/expected pair sharing is not conserved")
            for j,(i,k) in enumerate(PAIRS):
                values,support,pmf = summarize_distribution(factors[j],observed[j],expected[j])
                key = {"report":report,"bin_width":width,"group_a":base.GROUPS[i],"group_b":base.GROUPS[k]}
                n_a,n_b = int(margins[i]),int(margins[k])
                summaries.append(key | {"universe_ids":len(universe),"n_a":n_a,"n_b":n_b,
                    "strata_including_zero_membership_only":len(strata),"nonzero_membership_strata":len(factors[j]),
                    "variable_strata":variable,**values,
                    "observed_jaccard":observed[j]/(n_a+n_b-observed[j]),
                    "expected_jaccard_from_enumerated_distribution":float(np.sum(pmf*support/(n_a+n_b-support))),
                    "observed_overlap":observed[j]/min(n_a,n_b),"expected_overlap":float(expected[j])/min(n_a,n_b),
                    "jaccard_lower_95_discrete":values["shared_lower_95_discrete"]/(n_a+n_b-values["shared_lower_95_discrete"]),
                    "jaccard_upper_95_discrete":values["shared_upper_95_discrete"]/(n_a+n_b-values["shared_upper_95_discrete"])})
                distribution_rows.extend(key | {"shared":int(x),"probability":str(p)} for x,p in zip(support,pmf))
            conservation.append({"report":report,"bin_width":width,"fixed_sum_of_pairwise_sharing":total_fixed_pairs,
                "observed_pairwise_sum":sum(observed),"expected_pairwise_sum":float(sum(expected)),
                "sum_observed_minus_expected_exact_rational":int(sum(Fraction(x)-e for x,e in zip(observed,expected))),
                "scope":"allocation of fixed total sharing among pairs, not a test of overall integration"})
    for width in (5,10):
        family = [r for r in summaries if r["bin_width"] == width]
        adjusted = holm_decimal([r["conditional_two_sided_tail"] for r in family])
        for row,value in zip(family,adjusted):
            row["holm_nine_comparisons"] = value
    with (REPO/"reproducibility/reference/revision_network_conditional_null.csv").open(newline="") as f:
        baseline = list(csv.DictReader(f))
    for old in baseline:
        row = next(r for r in summaries if all(str(r[k]) == old[k] for k in ("report","bin_width","group_a","group_b")))
        for new,prior_name in (("observed_shared","observed_shared"),("universe_ids","universe_ids"),("n_a","n_a"),("n_b","n_b"),
                               ("exact_mean_from_integer_counts","expected_shared_exact"),("observed_minus_exact_mean","excess_shared")):
            if not math.isclose(float(row[new]),float(old[prior_name]),rel_tol=1e-10,abs_tol=1e-9):
                raise ValueError(f"Frozen observed/expected overlap differs: {new}")
        row["old_monte_carlo_tail_2500"] = old["conditional_tail_p"]
        row["old_holm_monte_carlo"] = old["holm_p_nine_pairs"]
    return {"conditional_null_summary.csv":summaries,"conditional_null_strata.csv":strata_rows,
            "stratum_marginals.csv.gz":marginal_rows,"overlap_distributions.csv":distribution_rows,
            "fixed_sharing_conservation.csv":conservation}, {"frozen_observed_and_expected_rows_reproduced":len(baseline),
            "exact_integer_stratum_counts":True,"fixed_pairwise_total_preserved":True,"Monte_Carlo_draws_used":False,
            "negative_convolution_mass":False,"small_tails_verified_Decimal80":True}


METHODS = """Conditional overlap distribution: enumeration and convolution

Population, strata, identity, outlet normalization and year bins are unchanged
from the baseline: report-specific matched assigned IDs in AR4, AR5 and AR6;
casefolded, whitespace-normalized preferred journal labels crossed with preferred
five- or ten-year publication bins. Missing labels and years remain explicit.
Synthesis-only zero memberships remain fixed. Every ID's row sum and every
stratum's three group column totals are held fixed. Topics are not conditioned on.

Feasible states reuse conditional_states in the frozen helper. For a stratum
with a degree-one rows, b degree-two rows and c degree-three rows, x_i is the
number of degree-two rows missing group i; g_i=m_i-c-b+x_i counts degree-one
rows in group i. State mass is the exact integer product of multinomial counts
b!/(x0!x1!x2!) * a!/(g0!g1!g2!). This counts labelled binary matrices uniformly.
The three pairwise overlap values are (c+x2,c+x1,c+x0). Each marginal is formed
by summing these exact integer weights and dividing by their common integer
total. Exact rational marginal means determine the tail event without tolerance.

For each report and pair, direct positive-coefficient numpy.convolve combines
independent stratum marginals. There is no FFT, MCMC or Monte Carlo sampling.
NumPy longdouble precision is reported from this runtime; on this Apple ARM
environment it has a 53-bit significand, equal to float64. Very small positive
tails are summed directly, not computed as one minus a CDF. Tails below 1e-8
are recomputed by a separate direct convolution using Python Decimal at eighty
significant digits; their relative agreement is checked. Decimal arithmetic
avoids float underflow in these reported tails. A zero Decimal tail is rejected,
rather than reported as an exact zero. Full-distribution float edge cells can
underflow; the number of zero support cells is exported. Probability values are
numerical approximations to the enumerated conditional distribution, not a
claim of symbolically exact rational convolution. Input stratum weights and
the tail thresholds/mean are combinatorially/rationally exact.

The two-sided tail is P(|X-E[X]| >= |observed-E[X]|). Integer tail cutpoints
are derived from the exact rational expectation. Equal-tail central 95% ranges
use the left inverse CDF at .025 and .975 and integer support, without linear
interpolation. Holm correction covers all nine report/pair comparisons within
each bin-width specification, using Decimal for tiny p values. The three
observed deviations sum to zero exactly because total pairwise sharing is
fixed by row degrees. The benchmark concerns its allocation among pairs and
cannot test the overall extent of integration. It does not control topic.

No random draws are used. Global protocol seed 20261011 is recorded unchanged.
All eighteen observed values and conditional means are checked against the
frozen baseline. Old forty-five tables, manifests and the read-only database
are hash protected before and after. --check repeats and compares all bytes.
"""


def run(database,output,check=False):
    protected,old = archive.archive_gate(database)
    protected[REPO/"scripts/review_round2_raw_age.py"] = archive.sha256(REPO/"scripts/review_round2_raw_age.py")
    tables,checks = build(database)
    checks["older_archive_counts"] = old
    files,outputs = {},[]
    for name,rows in sorted(tables.items()):
        raw = archive.csv_bytes(rows)
        content = gzip.compress(raw,compresslevel=9,mtime=0) if name.endswith(".gz") else raw
        files[name] = content
        outputs.append({"filename":name,"rows":len(rows),"bytes":len(content),"sha256":hashlib.sha256(content).hexdigest(),
                        "csv_content_sha256":hashlib.sha256(raw).hexdigest()})
    files["methods.txt"] = METHODS.encode()
    files["checks.json"] = archive.json_bytes(checks)
    for name in ("methods.txt","checks.json"):
        outputs.append({"filename":name,"bytes":len(files[name]),"sha256":hashlib.sha256(files[name]).hexdigest()})
    manifest = {"analysis_package":"1.4.0","analysis_version":VERSION,"seed":SEED,"random_draws_used":False,
        "replicates":0,"python_version":sys.version.split()[0],"numpy_version":np.__version__,
        "longdouble_significand_bits":np.finfo(np.longdouble).nmant+1,"decimal_tail_precision_digits":DECIMAL_PRECISION,
        "script_sha256":archive.sha256(Path(__file__)),"tests_sha256":archive.sha256(HERE/"test_conditional_null.py"),
        "database_sha256":archive.sha256(database),"inputs":{str(p.relative_to(REPO)):h for p,h in sorted(protected.items(),key=lambda x:str(x[0]))},
        "outputs":outputs,"old_tables_and_database_unchanged":True,"human_or_LLM_verification_performed":False}
    archive.unchanged(protected)
    if check:
        if json.loads((output/"manifest.json").read_text()) != manifest:
            raise ValueError("Conditional-null manifest differs")
        for name,data in files.items():
            if (output/name).read_bytes() != data:
                raise ValueError(f"Conditional-null replay differs: {name}")
    else:
        if output.exists():
            raise ValueError("Existing conditional-null archive cannot be overwritten")
        output.mkdir(parents=True)
        for name,data in files.items():
            (output/name).write_bytes(data)
        (output/"manifest.json").write_bytes(archive.json_bytes(manifest))
    print(json.dumps({"check":check,"tables":len(tables),"checks":checks,"AR6_primary":
        [{k:r[k] for k in ("group_a","group_b","observed_shared","exact_mean_from_integer_counts","conditional_two_sided_tail",
                           "holm_nine_comparisons","shared_lower_95_discrete","shared_upper_95_discrete","underflowed_support_cells")}
         for r in tables["conditional_null_summary.csv"] if r["report"] == "AR6" and r["bin_width"] == 5]},indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=REPO/"database/ipcc_litdb.sqlite")
    parser.add_argument("--output-dir",type=Path,default=HERE/"output_v1")
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args()
    run(args.database.resolve(),args.output_dir.resolve(),args.check)


if __name__ == "__main__":
    main()
