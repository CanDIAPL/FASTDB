"""Compare FASTDB's Q3C root matching with a HATS snapshot."""

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

        diagnostics_started = time.perf_counter()
        cursor.execute(
            """SELECT input.inputid::text,
                      input.rootid::text,
                      q3c_dist(input.ra, input.dec, selected.ra, selected.dec) * 3600.0,
                      count(candidate.id)
               FROM benchmark_root_match input
               LEFT JOIN root_diaobject selected ON selected.id=input.rootid
               LEFT JOIN root_diaobject candidate
                 ON q3c_radial_query(
                     candidate.ra, candidate.dec, input.ra, input.dec, %s
                 )
               GROUP BY input.inputid, input.rootid, input.ra, input.dec,
                        selected.ra, selected.dec""",
            (radius_arcsec / 3600.0,),
        )
        rows = cursor.fetchall()
        diagnostics_elapsed = time.perf_counter() - diagnostics_started

    matches = {row[0]: row[1] for row in rows}
    distances = {row[0]: row[2] for row in rows}
    candidate_counts = {row[0]: row[3] for row in rows}
    return matches, distances, candidate_counts, elapsed, diagnostics_elapsed


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
    distances = dict(zip(result["inputid_input"], result["_dist_arcsec"], strict=True))
    return matches, distances, load_elapsed, elapsed


def _classify_matches(q3c_matches, hats_matches, candidate_counts, q3c_distances, hats_distances):
    classifications = {
        "agreements": 0,
        "different_roots": 0,
        "ambiguous_differences": 0,
        "unambiguous_differences": 0,
        "hats_closer": 0,
        "q3c_closer": 0,
        "q3c_only": 0,
        "hats_only": 0,
        "unmatched_by_both": 0,
        "difference_examples": [],
    }
    for inputid in sorted(q3c_matches.keys() | hats_matches.keys()):
        q3c_root = q3c_matches.get(inputid)
        hats_root = hats_matches.get(inputid)
        if q3c_root is None and hats_root is None:
            classifications["unmatched_by_both"] += 1
        elif hats_root is None:
            classifications["q3c_only"] += 1
        elif q3c_root is None:
            classifications["hats_only"] += 1
        elif q3c_root == hats_root:
            classifications["agreements"] += 1
        else:
            classifications["different_roots"] += 1
            ambiguity_key = (
                "ambiguous_differences"
                if candidate_counts.get(inputid, 0) > 1
                else "unambiguous_differences"
            )
            classifications[ambiguity_key] += 1
            q3c_distance = q3c_distances.get(inputid)
            hats_distance = hats_distances.get(inputid)
            classifications["difference_examples"].append(
                {
                    "inputid": inputid,
                    "candidates": candidate_counts.get(inputid, 0),
                    "q3c_root": q3c_root,
                    "q3c_distance": q3c_distance,
                    "hats_root": hats_root,
                    "hats_distance": hats_distance,
                }
            )
            if q3c_distance is not None and hats_distance is not None:
                if hats_distance < q3c_distance:
                    classifications["hats_closer"] += 1
                elif q3c_distance < hats_distance:
                    classifications["q3c_closer"] += 1
    return classifications


def benchmark(snapshot_dir, sample_size=10_000, radius_arcsec=1.0, connection=None):
    """Run both matchers over the same database-derived coordinate batch."""
    from db import DB

    with DB(connection) as db_connection:
        inputs = _sample_inputs(db_connection, sample_size)
        if not inputs:
            raise RuntimeError("root_diaobject contains no positioned rows")
        (
            q3c_matches,
            q3c_distances,
            candidate_counts,
            q3c_elapsed,
            diagnostics_elapsed,
        ) = _match_with_q3c(db_connection, inputs, radius_arcsec)

    hats_matches, hats_distances, hats_load_elapsed, hats_elapsed = _match_with_hats(
        snapshot_dir, inputs, radius_arcsec
    )
    classifications = _classify_matches(
        q3c_matches, hats_matches, candidate_counts, q3c_distances, hats_distances
    )
    return classifications | {
        "input_rows": len(inputs),
        "q3c_seconds": q3c_elapsed,
        "diagnostics_seconds": diagnostics_elapsed,
        "hats_load_seconds": hats_load_elapsed,
        "hats_match_seconds": hats_elapsed,
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
    print(f"Diagnostics:      {results['diagnostics_seconds']:.3f} s (not included above)")
    print(f"HATS catalog load:{results['hats_load_seconds']:9.3f} s")
    print(f"HATS match:       {results['hats_match_seconds']:.3f} s")
    print(f"Agreements:       {results['agreements']}")
    print(f"Different roots:  {results['different_roots']}")
    print(f"  ambiguous:      {results['ambiguous_differences']}")
    print(f"  unambiguous:    {results['unambiguous_differences']}")
    print(f"  HATS closer:    {results['hats_closer']}")
    print(f"  Q3C closer:     {results['q3c_closer']}")
    print(f"Q3C only:         {results['q3c_only']}")
    print(f"HATS only:        {results['hats_only']}")
    print(f"Unmatched by both:{results['unmatched_by_both']:9}")
    if results["difference_examples"]:
        print("Different-root examples (distances in arcseconds):")
        for example in results["difference_examples"][:10]:
            print(
                f"  {example['inputid']}: candidates={example['candidates']}, "
                f"Q3C={example['q3c_root']} at {example['q3c_distance']:.6f}, "
                f"HATS={example['hats_root']} at {example['hats_distance']:.6f}"
            )


if __name__ == "__main__":
    main()
