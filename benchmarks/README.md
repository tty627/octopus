# Octopus v0.3 benchmarks

Generate deterministic, non-sensitive datasets outside the repository:

```powershell
python -m benchmarks.generate_dataset D:\Octopus-Bench-1k --count 1000 --mode mixed
python -m benchmarks.generate_dataset D:\Octopus-Bench-100k --count 100000 --mode metadata
```

Run release gates without network AI calls:

```powershell
python -m benchmarks.benchmark_transactions --counts 400 800 --repeats 5 --warmups 1 --enforce
python -m benchmarks.benchmark_incremental --repeats 5 --warmups 1 --enforce
```

Use `--output .octopus-dev\benchmarks\<name>.json` to preserve machine-readable results. Record
CPU, memory, disk and Git working-tree state alongside any result used for a release decision.
