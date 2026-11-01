#!/usr/bin/env Rscript
# Coverage-standardized source diversity using the unmodified iNEXT estimator.
args <- commandArgs(trailingOnly=TRUE)
if (length(args) != 2) stop("Usage: Rscript analyze_coverage.R source_counts.csv new_output_dir")
if (!requireNamespace("iNEXT", quietly=TRUE) || !requireNamespace("jsonlite", quietly=TRUE)) stop("Install iNEXT 3.0.2 and jsonlite in an isolated R library first")
if (as.character(packageVersion("iNEXT")) != "3.0.2") stop("This registry pins iNEXT 3.0.2")
if (dir.exists(args[2])) stop("Output directory already exists")
dir.create(args[2], recursive=TRUE)
SEED <- 20261011L
NBOOT <- 200L
RNGkind("Mersenne-Twister", "Inversion", "Rejection")
set.seed(SEED)
d <- read.csv(args[1], stringsAsFactors=FALSE)
stopifnot(all(c("cohort","report","source_id","assigned_IDs") %in% names(d)))
results <- list(); info <- list(); changes <- list()
for (cohort in sort(unique(d$cohort))) {
  x <- lapply(c("AR5","AR6"), function(report) {
    y <- d[d$cohort == cohort & d$report == report, ]
    z <- tapply(y$assigned_IDs, y$source_id, sum)
    stopifnot(length(z)>1, all(is.finite(z)), all(z>0), all(z==as.integer(z)))
    unname(z[order(names(z))])
  })
  names(x) <- c("AR5","AR6")
  di <- iNEXT::DataInfo(x, datatype="abundance")
  di$cohort <- cohort
  info[[cohort]] <- di
  observed_exact <- vapply(x, function(z) iNEXT::estimateD(list(z), q=2, datatype="abundance", base="size", level=sum(z), nboot=0)$SC[1], numeric(1))
  common_observed <- floor(min(observed_exact) * 1e6) / 1e6
  # DataInfo rounds SC to four decimals. Floor the exact estimate at six
  # decimals so the interpolation target never exceeds observed coverage.
  for (rule in c("minimum_observed_coverage", "minimum_double_sample_coverage")) {
    level <- if (rule == "minimum_observed_coverage") common_observed else NULL
    z <- iNEXT::estimateD(x, q=c(0,1,2), datatype="abundance", base="coverage", level=level, nboot=NBOOT, conf=.95)
    z$cohort <- cohort
    z$coverage_rule <- rule
    z$seed <- SEED
    z$bootstrap_draws <- NBOOT
    z$interval_scope <- "iNEXT marginal abundance-bootstrap intervals for observed resolved journal sources"
    results[[paste(cohort,rule)]] <- z
    for (q in c(0,1,2)) {
      early <- z[z$Assemblage=="AR5" & z$Order.q==q, ]
      late <- z[z$Assemblage=="AR6" & z$Order.q==q, ]
      stopifnot(nrow(early)==1, nrow(late)==1, abs(early$SC-late$SC)<1e-10)
      changes[[paste(cohort,rule,q)]] <- data.frame(cohort=cohort, coverage_rule=rule, q=q,
        common_coverage=early$SC, AR5=early$qD, AR6=late$qD, difference=late$qD-early$qD,
        AR5_method=early$Method, AR6_method=late$Method,
        interval_for_difference="Not computed; marginal bootstrap intervals are not a difference interval")
    }
  }
}
write.csv(do.call(rbind,results), file.path(args[2],"coverage_standardized_hill.csv"), row.names=FALSE, na="")
write.csv(do.call(rbind,info), file.path(args[2],"reference_sample_coverage.csv"), row.names=FALSE, na="")
write.csv(do.call(rbind,changes), file.path(args[2],"coverage_standardized_differences.csv"), row.names=FALSE, na="")
packages <- c("iNEXT","Rcpp","reshape2","plyr","ggplot2","jsonlite")
jsonlite::write_json(list(analysis_version="1.6.0", seed=SEED, bootstrap_draws=NBOOT,
  R_version=R.version.string, RNG=RNGkind(), packages=setNames(lapply(packages, function(p) as.character(packageVersion(p))),packages),
  methodology="Minimum exact observed sample coverage rounded down to six decimals and minimum coverage attained at twice each reference sample size, per estimateD documentation; no extrapolation of unresolved source labels"),
  file.path(args[2],"runtime.json"), pretty=TRUE, auto_unbox=TRUE)
cat("Completed coverage-standardized journal comparisons.\n")
