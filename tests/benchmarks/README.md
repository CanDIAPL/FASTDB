# FASTDB benchmarks

These are manual development benchmarks, not part of the normal automated test
suite. They require a running FASTDB installation and may take significant
time.

## Root DIAObject matching

Compare the current PostgreSQL/Q3C root match with HATS over the same coordinate
batch:

```sh
PYTHONPATH=src python3 tests/benchmarks/benchmark_root_diaobject_matching.py \
    /data/root-diaobject-snapshot --sample-size 10000 --radius-arcsec 1 \
    --offset-arcsec 0.4 --seed 42
```

`--offset-arcsec` moves each sampled root coordinate by that distance in a
random direction, providing a more realistic incoming-object position. The
seed makes repeated benchmark runs use identical positions. Leave the offset
at its default of zero to benchmark exact root coordinates.

The benchmark reports catalog-loading and matching time separately. When the
methods select different roots, it also reports whether multiple PostgreSQL
candidates were within the radius, which method selected the closer root, and
example root IDs and angular distances. Use a HATS snapshot made from the
current database.
