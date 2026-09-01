"""Compare FASTDB's current Q3C root matching with a HATS snapshot."""

import argparse
import pathlib
import time

import pandas as pd


def _sample_inputs(connection, sample_size):
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT id::text AS inputid, ra, dec
               FROM root_diaobject
               WHERE ra IS NOT NULL AND dec IS NOT NULL
               ORDER BY id
               LIMIT %s""",
            (sample_size,),
        )
        return cursor.fetchall()


def _match_with_q3c(connection, inputs, radius_arcsec):
    with connection.cursor() as cursor:
        cursor.execute(
            """CREATE TEMP TABLE benchmark_root_match (
                   inputid UUID PRIMARY KEY,
                   ra DOUBLE PRECISION,
                   dec DOUBLE PRECISION,
                   rootid UUID
               ) ON COMMIT DROP"""
        )
        cursor.executemany(
            "INSERT INTO benchmark_root_match(inputid, ra, dec) VALUES (%s, %s, %s)",
            inputs,
        )

        started = time.perf_counter()
        cursor.execute(
            """UPDATE benchmark_root_match input SET rootid=root.id
               FROM root_diaobject root
               WHERE q3c_radial_query(
                   root.ra, root.dec, input.ra, input.dec, %s
               )""",
            (radius_arcsec / 3600.0,),
        )
        elapsed = time.perf_counter() - started

        cursor.execute("SELECT inputid::text, rootid::text FROM benchmark_root_match")
        matches = dict(cursor.fetchall())
    return matches, elapsed


def _match_with_hats(snapshot_dir, inputs, radius_arcsec):
    try:
        import lsdb
    except ImportError as exc:
        raise RuntimeError("The matching benchmark requires lsdb") from exc

    snapshot_dir = pathlib.Path(snapshot_dir)
    catalog_path = snapshot_dir / "root_diaobject"
    margin_path = snapshot_dir / "root_diaobject_margin"

    load_started = time.perf_counter()
    roots = lsdb.read_hats(catalog_path, margin_cache=margin_path)
    load_elapsed = time.perf_counter() - load_started

    input_frame = pd.DataFrame(inputs, columns=["inputid", "ra", "dec"])
    incoming = lsdb.from_dataframe(
        input_frame,
        ra_column="ra",
        dec_column="dec",
        margin_threshold=None,
    )
    started = time.perf_counter()
    result = incoming.crossmatch(
        roots,
        n_neighbors=1,
        radius_arcsec=radius_arcsec,
        require_right_margin=True,
        suffixes=("_input", "_root"),
        suffix_method="all_columns",
    ).compute()
    elapsed = time.perf_counter() - started

    matches = dict(zip(result["inputid_input"], result["rootid_root"], strict=True))
    return matches, load_elapsed, elapsed


def _comparison_counts(q3c_matches, hats_matches):
    keys = q3c_matches.keys() | hats_matches.keys()
    agreements = sum(q3c_matches.get(key) == hats_matches.get(key) for key in keys)
    return agreements, len(keys) - agreements


def benchmark(snapshot_dir, sample_size=10_000, radius_arcsec=1.0, connection=None):
    """Run both matchers over the same database-derived coordinate batch."""
    from db import DB

    with DB(connection) as db_connection:
        inputs = _sample_inputs(db_connection, sample_size)
        if not inputs:
            raise RuntimeError("root_diaobject contains no positioned rows")
        q3c_matches, q3c_elapsed = _match_with_q3c(db_connection, inputs, radius_arcsec)

    hats_matches, hats_load_elapsed, hats_elapsed = _match_with_hats(
        snapshot_dir, inputs, radius_arcsec
    )
    agreements, disagreements = _comparison_counts(q3c_matches, hats_matches)
    return {
        "input_rows": len(inputs),
        "q3c_seconds": q3c_elapsed,
        "hats_load_seconds": hats_load_elapsed,
        "hats_match_seconds": hats_elapsed,
        "agreements": agreements,
        "disagreements": disagreements,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_dir", help="Snapshot made by export_root_diaobject_hats.py")
    parser.add_argument("--sample-size", type=int, default=10_000)
    parser.add_argument("--radius-arcsec", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.sample_size < 1:
        parser.error("--sample-size must be positive")
    if args.radius_arcsec <= 0:
        parser.error("--radius-arcsec must be positive")
    return args


def main(argv=None):
    args = parse_args(argv)
    results = benchmark(args.snapshot_dir, args.sample_size, args.radius_arcsec)
    print(f"Input rows:       {results['input_rows']}")
    print(f"Q3C match:        {results['q3c_seconds']:.3f} s")
    print(f"HATS catalog load:{results['hats_load_seconds']:9.3f} s")
    print(f"HATS match:       {results['hats_match_seconds']:.3f} s")
    print(f"Agreements:       {results['agreements']}")
    print(f"Disagreements:    {results['disagreements']}")


if __name__ == "__main__":
    main()
