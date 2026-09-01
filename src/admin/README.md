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
