# Octopus benchmarks

Generate deterministic, non-sensitive datasets outside the repository:

```powershell
python -m benchmarks.generate_dataset D:\Octopus-Bench-1k --count 1000 --mode mixed
python -m benchmarks.generate_dataset D:\Octopus-Bench-100k --count 100000 --mode metadata
```

Run release gates without network AI calls:

```powershell
octopus evaluate-search --output .octopus-dev\benchmarks\search-value.json --enforce
python -m benchmarks.benchmark_transactions --counts 400 800 --repeats 5 --warmups 1 --enforce
python -m benchmarks.benchmark_incremental --repeats 5 --warmups 1 --enforce
python -m benchmarks.benchmark_retrieval --enforce
```

The versioned `datasets/search-value-v1.json` corpus covers Chinese/English DOCX and XLSX,
same-name files and a stale-index task. The wheel packages the same file as the default dataset for
`octopus evaluate-search`; use `--dataset` only to evaluate a separately versioned corpus.

Use `--output .octopus-dev\benchmarks\<name>.json` to preserve machine-readable results. Record
CPU, memory, disk and Git working-tree state alongside any result used for a release decision.

Calibrate the versioned v0.4 Windows preflight coefficients on the designated Windows 11 x64
reference machine (Python 3.12, performance power mode, local SSD):

```powershell
python -m benchmarks.benchmark_estimates --repeats 7 --output .octopus-dev\benchmarks\windows-estimates.json
```

The output records the coefficient version, OS, Python, processor, per-format P50/P95 parser time
and extracted/source size ratio. Update the conservative envelope in `octopus.onboarding` only
after reviewing this result; increment `ESTIMATE_COEFFICIENT_VERSION` whenever values change.

The retrieval gate materializes the public `octopus-retrieval-v1` corpus in a temporary directory,
indexes it with AI disabled and evaluates 60 blind judgments. Its report includes product,
algorithm and dataset versions plus per-task ranks, Hit@1/5 and MRR.
