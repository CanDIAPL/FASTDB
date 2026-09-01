"""Export FASTDB root DIAObjects as a HATS catalog snapshot."""

import argparse
import pathlib

from root_hats import initialize_from_postgres


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

    from db import DB

    with DB(connection) as db_connection:
        created = initialize_from_postgres(
            output_dir,
            db_connection,
            pixel_threshold=pixel_threshold,
            margin_arcsec=margin_arcsec,
            workers=workers,
        )
    if not created:
        raise RuntimeError("root_diaobject contains no positioned rows")

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
