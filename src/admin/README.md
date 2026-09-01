DiaObject.csv, DiaSource.csv, and DiaForcedSource.csv were downloaded from the following URL on 2025-03-21:

  https://sdm-schemas.lsst.io/apdb.html

Genenerated avsc files with

```
python3 csv_to_avsc.py DiaObject.csv \
  --name DiaObject \
  --namespace fastdb_test_0.1 \
  --no-null diaObjectId ra dec \
  > fastdb_test_0.1.DiaObject.avsc

python3 csv_to_avsc.py DiaSource.csv \
  --name DiaSource \
  --namespace fastdb_test_0.1 \
  --no-null diaSourceId diaObjectId ra dec band midpointMjdTai psfFlux psfFluxErr \
  > fastdb_test_0.1.DiaSource.avsc

python3 csv_to_avsc.py DiaForcedSource.csv \
  --name DiaForcedSource \
  --namespace fastdb_test_0.1 \
  --no-null diaForcedSourceId diaObjectId ra dec band midpointMjdTai psfFlux psfFluxErr \
  > fastdb_test_0.1.DiaForcedSource.avsc
```

## Root DIAObject HATS snapshot

Export all positioned rows from `root_diaobject` into a new HATS snapshot:

```sh
python3 export_root_diaobject_hats.py /data/root-diaobject-snapshot
```

The destination must not exist. It is published only after both
`root_diaobject` and its `root_diaobject_margin` catalog have been built
successfully. Use `--margin-arcsec` to set the largest cross-match radius the
margin catalog must support.

The resulting catalog uses per-pixel directories so that experimental
incremental updates can add new Parquet files without rewriting existing
partitions.

### Experimental source-importer integration

Pass the snapshot directory to `source_importer.py` to replace its Q3C root
match with an LSDB nearest-neighbor match:

```sh
python /fastdb/services/source_importer.py \
    --root-hats-dir /data/root-diaobject-snapshot \
    ...the existing source-importer arguments...
```

After the PostgreSQL and MongoDB transactions commit, newly created roots are
appended to the main catalog. Catalog metadata, sky maps, and the margin catalog
are then refreshed.

The snapshot directory may be absent on the first run. Until positioned roots
exist, source importer uses Q3C and defers initialization. After the first run
that commits positioned roots, it builds the initial HATS snapshot automatically;
later runs use and increment that snapshot.

This is a single-writer prototype. A failure after PostgreSQL commits but before
the HATS update completes can leave HATS behind PostgreSQL. PostgreSQL remains
authoritative; repair this state by building a new full snapshot. Do not run
concurrent HATS-enabled source importers against the same snapshot.
