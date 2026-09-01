"""Experimental HATS storage and matching for FASTDB root DIAObjects.

PostgreSQL remains authoritative. Incremental updates in this prototype are
not transactionally coupled to PostgreSQL and must not run concurrently.
"""

import csv
import os
import pathlib
import shutil
import tempfile
import uuid

import numpy as np
import pandas as pd


CATALOG_NAME = "root_diaobject"
MARGIN_NAME = "root_diaobject_margin"
EXPORT_QUERY = """COPY (
    SELECT id::text AS rootid, ra, dec
    FROM root_diaobject
    WHERE ra IS NOT NULL AND dec IS NOT NULL
    ORDER BY id
) TO STDOUT WITH (FORMAT CSV, HEADER)"""


def _hats_imports():
    try:
        import hats
        import lsdb
        from hats.catalog import PartitionInfo
        from hats.io import paths
        from hats.io.file_io import write_fits_image
        from hats.io.skymap import read_skymap, write_skymap
        from hats.pixel_math.sparse_histogram import HistogramAggregator
        from hats_import import ImportArguments, MarginCacheArguments, pipeline
        from hats_import.catalog.file_readers.csv import CsvPyarrowReader
        from lsdb.io.common import new_provenance_properties
        from lsdb.io.to_hats import calculate_histogram, create_modified_catalog_structure
    except ImportError as exc:
        raise RuntimeError("Root HATS support requires hats-import and lsdb") from exc
    return {
        "hats": hats,
        "lsdb": lsdb,
        "PartitionInfo": PartitionInfo,
        "paths": paths,
        "write_fits_image": write_fits_image,
        "read_skymap": read_skymap,
        "write_skymap": write_skymap,
        "HistogramAggregator": HistogramAggregator,
        "ImportArguments": ImportArguments,
        "MarginCacheArguments": MarginCacheArguments,
        "pipeline": pipeline,
        "CsvPyarrowReader": CsvPyarrowReader,
        "new_provenance_properties": new_provenance_properties,
        "calculate_histogram": calculate_histogram,
        "create_modified_catalog_structure": create_modified_catalog_structure,
    }


def _import_main_catalog(
    input_csv,
    output_dir,
    *,
    artifact_name=CATALOG_NAME,
    pixel_threshold=1_000_000,
    workers=1,
    existing_pixels=None,
    highest_healpix_order=10,
    incremental_layout=False,
):
    api = _hats_imports()
    args = api["ImportArguments"](
        input_file_list=[input_csv],
        file_reader=api["CsvPyarrowReader"](column_names=["rootid", "ra", "dec"]),
        output_path=output_dir,
        output_artifact_name=artifact_name,
        catalog_type="object",
        ra_column="ra",
        dec_column="dec",
        sort_columns="rootid",
        pixel_threshold=pixel_threshold,
        highest_healpix_order=highest_healpix_order,
        existing_pixels=existing_pixels,
        npix_suffix="/" if incremental_layout else ".parquet",
        dask_n_workers=workers,
        progress_bar=True,
        resume=False,
    )
    api["pipeline"](args)


def _generate_margin(main_catalog, output_dir, *, artifact_name=MARGIN_NAME, margin_arcsec=5.0, workers=1):
    api = _hats_imports()
    args = api["MarginCacheArguments"](
        input_catalog_path=main_catalog,
        output_path=output_dir,
        output_artifact_name=artifact_name,
        margin_threshold=margin_arcsec,
        dask_n_workers=workers,
        progress_bar=True,
        resume=False,
    )
    api["pipeline"](args)


def build_catalog(input_csv, output_dir, *, pixel_threshold=1_000_000, margin_arcsec=5.0, workers=1):
    """Build a full increment-capable main catalog and margin catalog."""
    _import_main_catalog(
        input_csv,
        output_dir,
        pixel_threshold=pixel_threshold,
        workers=workers,
        incremental_layout=True,
    )
    _generate_margin(
        pathlib.Path(output_dir) / CATALOG_NAME,
        output_dir,
        margin_arcsec=margin_arcsec,
        workers=workers,
    )


def catalog_exists(snapshot_dir):
    """Return whether both the main and margin catalogs appear initialized."""
    snapshot_dir = pathlib.Path(snapshot_dir)
    return all(
        (snapshot_dir / name / "hats.properties").is_file()
        for name in (CATALOG_NAME, MARGIN_NAME)
    )


def _write_postgres_csv(connection, destination):
    with connection.cursor() as cursor, destination.open("wb") as output:
        with cursor.copy(EXPORT_QUERY) as copy:
            for data in copy:
                output.write(data)


def initialize_from_postgres(
    snapshot_dir,
    connection,
    *,
    pixel_threshold=1_000_000,
    margin_arcsec=5.0,
    workers=1,
):
    """Atomically create the first snapshot, or return False when no roots exist."""
    snapshot_dir = pathlib.Path(snapshot_dir).expanduser().resolve()
    if catalog_exists(snapshot_dir):
        return False
    if snapshot_dir.exists():
        raise FileExistsError(f"HATS snapshot path exists but is not a valid catalog: {snapshot_dir}")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM root_diaobject WHERE ra IS NOT NULL AND dec IS NOT NULL"
        )
        if cursor.fetchone()[0] == 0:
            return False

    snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{snapshot_dir.name}-", dir=snapshot_dir.parent)
    )
    input_csv = staging_dir / "root_diaobject.csv"
    try:
        _write_postgres_csv(connection, input_csv)
        build_catalog(
            input_csv,
            staging_dir,
            pixel_threshold=pixel_threshold,
            margin_arcsec=margin_arcsec,
            workers=workers,
        )
        input_csv.unlink()
        os.rename(staging_dir, snapshot_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return True


def match_roots(snapshot_dir, inputs, radius_arcsec):
    """Return nearest HATS root IDs keyed by incoming object ID."""
    if not inputs:
        return {}
    api = _hats_imports()
    snapshot_dir = pathlib.Path(snapshot_dir)
    margin_path = snapshot_dir / MARGIN_NAME
    margin = api["hats"].read_hats(margin_path)
    if float(margin.catalog_info.margin_threshold) < radius_arcsec:
        raise ValueError(
            f"HATS margin is {margin.catalog_info.margin_threshold} arcsec, "
            f"smaller than match radius {radius_arcsec} arcsec"
        )
    roots = api["lsdb"].read_hats(
        snapshot_dir / CATALOG_NAME,
        margin_cache=margin_path,
    )
    frame = pd.DataFrame(inputs, columns=["inputid", "ra", "dec"])
    incoming = api["lsdb"].from_dataframe(
        frame,
        ra_column="ra",
        dec_column="dec",
        margin_threshold=None,
    )
    result = incoming.crossmatch(
        roots,
        n_neighbors=1,
        radius_arcsec=radius_arcsec,
        require_right_margin=True,
        suffixes=("_input", "_root"),
        suffix_method="all_columns",
    ).compute()
    return {
        int(inputid): str(rootid)
        for inputid, rootid in zip(result["inputid_input"], result["rootid_root"], strict=True)
    }


def _write_rows_csv(rows, destination):
    with destination.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["rootid", "ra", "dec"])
        writer.writerows(rows)


def _write_increment_partitions(new_catalog, base_catalog, increment_name, mapping_order):
    api = _hats_imports()
    results = []
    for pixel in new_catalog.get_healpix_pixels():
        frame = new_catalog.get_partition(pixel.order, pixel.pixel).compute()
        if len(frame) == 0:
            continue
        pointer = api["paths"].new_pixel_catalog_file(
            base_catalog.catalog_path,
            pixel,
            create_dirs=True,
            npix_suffix="/",
            npix_parquet_name=f"{increment_name}.parquet",
        )
        frame.to_parquet(pointer.path, filesystem=pointer.fs)
        results.append((pixel, len(frame), api["calculate_histogram"](frame, mapping_order)))
    return (
        [row[0] for row in results],
        [row[1] for row in results],
        [row[2] for row in results],
    )


def _update_increment_metadata(base_catalog, new_pixels, new_counts, new_histograms, mapping_order):
    api = _hats_imports()
    catalog_dir = base_catalog.catalog_path

    histogram = api["HistogramAggregator"](mapping_order)
    for new_histogram in new_histograms:
        histogram.add(new_histogram)
    histogram.full_histogram += api["read_skymap"](base_catalog, mapping_order)
    api["write_fits_image"](
        histogram.full_histogram,
        map_file_pointer=api["paths"].get_point_map_file_pointer(catalog_dir),
    )
    api["write_skymap"](
        histogram.full_histogram,
        catalog_dir=catalog_dir,
        orders=base_catalog.catalog_info.skymap_alt_orders,
    )

    api["paths"].get_parquet_metadata_pointer(catalog_dir).unlink(missing_ok=True)
    pixels = set(base_catalog.get_healpix_pixels()) | set(new_pixels)
    partition_info = api["PartitionInfo"].from_healpix(list(pixels))
    partition_info.write_to_file(api["paths"].get_partition_info_pointer(catalog_dir))

    info = base_catalog.catalog_info
    structure = api["create_modified_catalog_structure"](
        base_catalog,
        catalog_dir,
        base_catalog.catalog_name,
        total_rows=int(info.total_rows) + int(np.sum(new_counts)),
        hats_max_rows=max(int(info.hats_max_rows), max(new_counts)),
        hats_order=partition_info.get_highest_order(),
        moc_sky_fraction=partition_info.calculate_fractional_coverage(),
        **api["new_provenance_properties"](catalog_dir),
    )
    structure.catalog_info.to_properties_file(catalog_dir)


def _replace_margin(snapshot_dir, margin_arcsec, workers):
    snapshot_dir = pathlib.Path(snapshot_dir)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".margin-", dir=snapshot_dir.parent))
    backup = snapshot_dir / f".{MARGIN_NAME}-old"
    target = snapshot_dir / MARGIN_NAME
    try:
        _generate_margin(
            snapshot_dir / CATALOG_NAME,
            temporary,
            margin_arcsec=margin_arcsec,
            workers=workers,
        )
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            os.rename(target, backup)
        os.rename(temporary / MARGIN_NAME, target)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if backup.exists() and not target.exists():
            os.rename(backup, target)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def append_roots(snapshot_dir, rows, *, margin_arcsec=5.0, workers=1):
    """Append ``(rootid, ra, dec)`` rows and refresh metadata and margins.

    This experimental operation must have a single writer. It is not atomic
    with the PostgreSQL transaction that created the roots.
    """
    if not rows:
        return 0
    api = _hats_imports()
    snapshot_dir = pathlib.Path(snapshot_dir).expanduser().resolve()
    main_path = snapshot_dir / CATALOG_NAME
    base = api["hats"].read_hats(main_path)
    if base.catalog_info.npix_suffix != "/":
        raise ValueError("Root HATS catalog was not created with incremental layout")

    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".increment-", dir=snapshot_dir.parent))
    input_csv = temporary / "roots.csv"
    try:
        _write_rows_csv(rows, input_csv)
        pixels = [(pixel.order, pixel.pixel) for pixel in base.get_healpix_pixels()]
        mapping_order = int(base.catalog_info.skymap_order)
        _import_main_catalog(
            input_csv,
            temporary,
            artifact_name="increment",
            pixel_threshold=max(1, int(base.catalog_info.hats_max_rows)),
            workers=workers,
            existing_pixels=pixels,
            highest_healpix_order=mapping_order,
        )
        increment = api["lsdb"].read_hats(temporary / "increment")
        new_pixels, new_counts, histograms = _write_increment_partitions(
            increment,
            base,
            f"increment-{uuid.uuid4().hex}",
            mapping_order,
        )
        _update_increment_metadata(base, new_pixels, new_counts, histograms, mapping_order)
        _replace_margin(snapshot_dir, margin_arcsec, workers)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return len(rows)
