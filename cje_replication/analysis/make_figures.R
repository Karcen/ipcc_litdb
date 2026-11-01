## Figures for the CJE revision. Default: all four figures.
## Run --figures=F3,F4 to update only the classification and decomposition plots.
suppressPackageStartupMessages({library(ggplot2); library(data.table)})
script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_file <- normalizePath(gsub("~+~", " ", sub("^--file=", "", script_arg[1]), fixed = TRUE))
package_root <- dirname(dirname(script_file))
ROOT <- Sys.getenv("CJE_REPLICATION_OUTPUT", unset = file.path(package_root, "output"))
T    <- file.path(ROOT, "tables"); F <- file.path(ROOT, "figures")
dir.create(F, showWarnings = FALSE, recursive = TRUE)
QA <- file.path(ROOT, "figure_sources")
dir.create(QA, showWarnings = FALSE, recursive = TRUE)
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 1L || (length(args) == 1L && !startsWith(args, "--figures="))) {
  stop("Usage: Rscript analysis/make_figures.R [--figures=F3,F4]")
}
requested <- if (length(args)) strsplit(sub("^--figures=", "", args), ",", fixed = TRUE)[[1]] else paste0("F", 1:4)
stopifnot(length(requested) > 0L, all(requested %in% paste0("F", 1:4)))
save_pub <- function(plot, name, height) {
  if (!requireNamespace("svglite", quietly = TRUE) ||
      !requireNamespace("ragg", quietly = TRUE)) {
    stop("PDF/SVG/PNG export requires the svglite and ragg R packages.")
  }
  ggsave(file.path(F, paste0(name, ".pdf")), plot, device = grDevices::cairo_pdf,
         width = 6.6, height = height, units = "in", bg = "white")
  ggsave(file.path(F, paste0(name, ".svg")), plot, device = svglite::svglite,
         width = 6.6, height = height, units = "in", bg = "white")
  ggsave(file.path(F, paste0(name, ".png")), plot, device = ragg::agg_png,
         width = 6.6, height = height, units = "in", dpi = 600, bg = "white")
}
th <- theme_bw(base_size = 10, base_family = "Helvetica") +
  theme(panel.grid.minor = element_blank(), legend.position = "top",
        legend.title = element_blank(), plot.title = element_text(face = "bold", size = 10),
        plot.caption = element_text(size = 7, hjust = 0))
glab <- c(WG1 = "Working Group I", WG2 = "Working Group II", WG3 = "Working Group III")
flab <- c(W = "WoS frame", D = "Any-metadata frame", A = "Extracted (no frame)")

## F1: persistence under three frames ------------------------------------
fr <- fread(file.path(T, "AUDIT_frame_alternatives.csv"))[group != "report"]
fr[, group := factor(glab[group], levels = glab)]
fr[, frame := factor(flab[frame], levels = flab)]
fr[, se := sqrt((retention_pct/100)*(1-retention_pct/100)/earlier_n)*100]
p1 <- ggplot(fr, aes(group, retention_pct, fill = frame)) +
  geom_col(position = position_dodge(.78), width = .72, colour = "grey25") +
  geom_errorbar(aes(ymin = retention_pct - 1.96*se, ymax = retention_pct + 1.96*se),
                position = position_dodge(.78), width = .18) +
  scale_fill_grey(start = .25, end = .85) +
  labs(x = NULL, y = "Matched retention (per cent)",
       title = "The same reports, three observation frames",
       caption = "Retention = shared assigned identities / earlier assigned identities in the frame.\nBars are 95 per cent binomial intervals. Frames differ only in what counts as an observed work.") + th
if ("F1" %in% requested) ggsave(file.path(F, "F1_three_frames.pdf"), p1, width = 6.6, height = 4.0)

## F2: coverage under the two metadata frames ---------------------------
cv <- fr[frame != "Extracted (no frame)"]
p2 <- ggplot(cv, aes(group, later_coverage_pct, fill = frame)) +
  geom_col(position = position_dodge(.7), width = .62, colour = "grey25") +
  scale_fill_grey(start = .35, end = .8) +
  labs(x = NULL, y = "Later-report coverage (per cent)",
       title = "The coverage gradient is mostly a Web of Science gradient",
       caption = "Coverage = assigned identities in the frame / all extracted assigned identities, AR6.") + th
if ("F2" %in% requested) ggsave(file.path(F, "F2_coverage_by_frame.pdf"), p2, width = 6.6, height = 3.8)

## F3: document type composition and match rate ------------------------
if ("F3" %in% requested) {
dist <- fread(file.path(T, "A_document_type_distribution.csv"))
ty <- dist[unit == "occurrence" & cycle == "AR5" & wg %in% names(glab)]
stopifnot(nrow(ty) == 18L, all(ty[, sum(n) == unique(denominator), by = wg]$V1))
ty[, variable := fifelse(category == "journal_article", "Journal article",
                        fifelse(category == "uncertain", "Uncertain", "Non-journal"))]
long <- ty[, .(n = sum(n), denominator = unique(denominator)), by = .(wg, variable)]
long[, value := 100 * n / denominator]
long[, group := factor(glab[wg], levels = glab)]
long[, variable := factor(variable, levels = c("Journal article", "Non-journal", "Uncertain"))]
ar5_plot_n <- ty[, sum(unique(denominator)), by = wg][, sum(V1)]
ar5_all_n <- unique(dist[unit == "occurrence" & cycle == "AR5" & wg == "ALL", denominator])
ar5_syr_n <- unique(dist[unit == "occurrence" & cycle == "AR5" & wg == "SYR", denominator])
stopifnot(ar5_plot_n + ar5_syr_n == ar5_all_n)
unc <- dist[unit == "occurrence" & cycle %in% c("AR5", "AR6") & wg == "ALL"]
unc_n <- unc[category == "uncertain", sum(n)]
unc_den <- unc[, sum(unique(denominator)), by = cycle][, sum(V1)]
unc_pct <- 100 * unc_n / unc_den
p3 <- ggplot(long, aes(group, value, fill = variable)) +
  geom_col(width = .62, colour = "grey25") +
  scale_fill_grey(start = .8, end = .25) +
  labs(x = NULL, y = "Share of extracted occurrences (per cent)",
       title = "What the working groups cite, coded from raw reference strings",
       caption = sprintf("Shares are of extracted occurrences in AR5, n = %s; excludes %s Synthesis Report entries.\nNon-journal includes reports, books, working papers and other sources.\nThe identity-frame shares quoted in the text differ slightly. Across AR5 and AR6, %.2f per cent of occurrences remain uncertain.",
                         format(ar5_plot_n, big.mark = ",", trim = TRUE), ar5_syr_n, unc_pct)) + th
save_pub(p3, "F3_type_composition", height = 4.1)
jsonlite::write_json(list(
  figure = "F3", source = "tables/A_document_type_distribution.csv",
  filter = "unit=occurrence; cycle=AR5; wg in WG1,WG2,WG3",
  grouping = "Journal article; Non-journal=book_or_chapter+institutional_report+working_paper+other; Uncertain",
  bars = long[, .(wg, category = as.character(variable), n, denominator, share_pct = value)],
  plotted_n = ar5_plot_n, excluded_synthesis_n = ar5_syr_n, report_all_n = ar5_all_n,
  uncertainty = list(filter = "unit=occurrence; cycle in AR5,AR6; wg=ALL", numerator = unc_n,
                     denominator = unc_den, share_pct = unc_pct),
  inference = "Descriptive composition of the extracted occurrence set; no sampling interval.",
  alignment = "Not applicable: one plot area."),
  file.path(QA, "F3_source_data.json"), auto_unbox = TRUE, pretty = TRUE)
}

## F4: decomposition ladder --------------------------------------------
if ("F4" %in% requested) {
dd <- fread(file.path(T, "D_decomposition.csv"))[task == "D2_gap"]
getq <- function(wanted_spec, wanted_quantity) {
  rows <- dd[(spec == paste0("spec=", wanted_spec) |
              startsWith(spec, paste0("spec=", wanted_spec, ";"))) &
             quantity == wanted_quantity, estimate]
  stopifnot(length(rows) == 1L, is.finite(rows))
  rows
}
full_values <- unique(dd[quantity == "full_sample_crude_gap_pp", estimate])
stopifnot(length(full_values) == 1L, is.finite(full_values))
lad <- data.table(
  step = factor(c("Full-sample\ncrude gap","Common-support\ncrude gap","Common-support\nstandardised gap"),
                levels = c("Full-sample\ncrude gap","Common-support\ncrude gap","Common-support\nstandardised gap")),
  value = c(full_values,
            getq("year_x_venue","crude_common_gap_pp"),
            getq("year_x_venue","standardised_common_gap_pp")))
stopifnot(nrow(lad) == 3L, all(is.finite(lad$value)), all(lad$value >= 0))
support_pct <- getq("year_x_venue", "support_share_pct")
support_g1 <- getq("year_x_venue", "sample_support_share_g1_pct")
support_g3 <- getq("year_x_venue", "sample_support_share_g3_pct")
p4 <- ggplot(lad, aes(step, value)) +
  geom_col(fill = "grey55", colour = "grey20", width = .58) +
  geom_text(aes(label = sprintf("%.2f", value)), vjust = -.4, size = 3.2) +
  scale_y_continuous(expand = expansion(mult = c(0, .1))) +
  labs(x = NULL, y = "Working Group I minus III gap (percentage points)",
       title = "Separating the sample restriction from the reweighting",
       caption = sprintf("Common support retains %.2f per cent of pooled eligible records (WGI %.2f, WGIII %.2f).\nThe standardised gap is sensitive to how the long tail of venue labels is pooled.",
                         support_pct, support_g1, support_g3)) + th +
  theme(panel.grid.major = element_blank())
save_pub(p4, "F4_decomposition_ladder", height = 3.9)
jsonlite::write_json(list(
  figure = "F4", source = "tables/D_decomposition.csv", filter = "task=D2_gap; group=WG1_vs_WG3",
  full_quantity = "full_sample_crude_gap_pp; repeated identical values collapsed",
  common_spec = "spec=year_x_venue", common_quantities = c("crude_common_gap_pp", "standardised_common_gap_pp"),
  bars = data.frame(step = as.character(lad$step), gap_pp = lad$value),
  support_pct = support_pct, support_g1_pct = support_g1, support_g3_pct = support_g3,
  bug = "The former getq argument spec was masked by the data.table spec column; two selectors returned NA.",
  inference = "Fixed-corpus accounting quantities; no sampling intervals.", alignment = "Not applicable: one plot area."),
  file.path(QA, "F4_source_data.json"), auto_unbox = TRUE, pretty = TRUE)
}
cat("figures written:", paste(requested, collapse = ", "), "\n")
