# Benchmark results

k6 writes `k6-summary.json` here by default. JSON result files are ignored so a
developer does not accidentally commit an unexplained local benchmark.

For a published result, copy the summary to a descriptive filename and document
the hardware, commit, image, configuration, process counts, workload, and any
observed bottleneck alongside it. A benchmark without that context is not
comparable or reproducible.
