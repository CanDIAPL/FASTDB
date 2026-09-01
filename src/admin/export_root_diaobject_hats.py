"""Export FASTDB root DIAObjects as a HATS catalog snapshot."""

import argparse
import os
import pathlib
import shutil
import tempfile


CATALOG_NAME = "root_diaobject"
MARGIN_NAME = "root_diaobject_margin"
EXPORT_QUERY = """COPY (
    SELECT id::text AS rootid, ra, dec
    FROM root_diaobject
    WHERE ra IS NOT NULL AND dec IS NOT NULL
    ORDER BY id
) TO STDOUT WITH (FORMAT CSV, HEADER)"""


def _write_input_csv(connection, destination):
    """Stream root DIAObjects from PostgreSQL without holding them in memory."""
    with connection.cursor() as cursor, destination.open("wb") as output:
        with cursor.copy(EXPORT_QUERY) as copy:
            for data in copy:
                output.write(data)


def _build_catalog(input_csv, staging_dir, pixel_threshold, margin_arcsec, workers):
    """Build the main HATS catalog and its cross-match margin catalog."""
    try:
        from hats_import import ImportArguments, MarginCacheArguments, pipeline
        from hats_import.catalog.file_readers.csv import CsvPyarrowReader
    except ImportError as exc:
        raise RuntimeError(
            "HATS export requires hats-import. Install FASTDB's HATS dependencies first."
        ) from exc

    import_args = ImportArguments(
        input_file_list=[input_csv],
        file_reader=CsvPyarrowReader(column_names=["rootid", "ra", "dec"]),
        output_path=staging_dir,
        output_artifact_name=CATALOG_NAME,
        catalog_type="object",
        ra_column="ra",
        dec_column="dec",
        sort_columns="rootid",
        pixel_threshold=pixel_threshold,
        dask_n_workers=workers,
        progress_bar=True,
        resume=False,
    )
    pipeline(import_args)

    main_catalog = staging_dir / CATALOG_NAME
    margin_args = MarginCacheArguments(
        input_catalog_path=main_catalog,
        output_path=staging_dir,
        output_artifact_name=MARGIN_NAME,
        margin_threshold=margin_arcsec,
        dask_n_workers=workers,
        progress_bar=True,
        resume=False,
    )
    pipeline(margin_args)


def export_root_diaobject_hats(
    output_dir,
    *,
    connection=None,
    pixel_threshold=1_000_000,
    margin_arcsec=5.0,
    workers=1,
):
    """Create an atomic HATS snapshot containing all positioned root DIAObjects.

    ``output_dir`` must not already exist. It receives the main catalog and a
    margin catalog suitable for correct cross-matches at partition boundaries.
    """
    output_dir = pathlib.Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"Output path already exists: {output_dir}")
    if pixel_threshold < 1:
        raise ValueError("pixel_threshold must be positive")
    if margin_arcsec <= 0:
        raise ValueError("margin_arcsec must be positive")
    if workers < 1:
        raise ValueError("workers must be positive")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent)
    )
    input_csv = staging_dir / "root_diaobject.csv"
    try:
        from db import DB

        with DB(connection) as db_connection:
            _write_input_csv(db_connection, input_csv)
        _build_catalog(input_csv, staging_dir, pixel_threshold, margin_arcsec, workers)
        input_csv.unlink()
        os.rename(staging_dir, output_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return output_dir


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Export root_diaobject as an atomic HATS catalog snapshot."
    )
    parser.add_argument("output_dir", help="New directory in which to create the snapshot")
    parser.add_argument(
        "--pixel-threshold",
        type=int,
        default=1_000_000,
        help="Approximate maximum rows per HATS partition (default: 1000000)",
    )
    parser.add_argument(
        "--margin-arcsec",
        type=float,
        default=5.0,
        help="Width of the cross-match margin catalog in arcseconds (default: 5)",
    )
    parser.add_argument("--workers", type=int, default=1, help="Dask worker processes (default: 1)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = export_root_diaobject_hats(
        args.output_dir,
        pixel_threshold=args.pixel_threshold,
        margin_arcsec=args.margin_arcsec,
        workers=args.workers,
    )
    print(f"Created HATS snapshot at {result}")


if __name__ == "__main__":
    main()
