import pytest

from benchmark_root_diaobject_matching import _classify_matches, parse_args


def test_classify_matches():
    q3c = {
        "same": "root-a",
        "ambiguous": "root-b",
        "unambiguous": "root-d",
        "q3c-only": "root-f",
        "neither": None,
    }
    hats = {
        "same": "root-a",
        "ambiguous": "root-c",
        "unambiguous": "root-e",
        "hats-only": "root-g",
    }
    candidates = {"same": 1, "ambiguous": 2, "unambiguous": 1, "q3c-only": 1}
    q3c_distances = {"ambiguous": 0.8, "unambiguous": 0.2}
    hats_distances = {"ambiguous": 0.1, "unambiguous": 0.4}

    assert _classify_matches(
        q3c, hats, candidates, q3c_distances, hats_distances
    ) == {
        "agreements": 1,
        "different_roots": 2,
        "ambiguous_differences": 1,
        "unambiguous_differences": 1,
        "hats_closer": 1,
        "q3c_closer": 1,
        "q3c_only": 1,
        "hats_only": 1,
        "unmatched_by_both": 1,
        "difference_examples": [
            {
                "inputid": "ambiguous",
                "candidates": 2,
                "q3c_root": "root-b",
                "q3c_distance": 0.8,
                "hats_root": "root-c",
                "hats_distance": 0.1,
            },
            {
                "inputid": "unambiguous",
                "candidates": 1,
                "q3c_root": "root-d",
                "q3c_distance": 0.2,
                "hats_root": "root-e",
                "hats_distance": 0.4,
            },
        ],
    }


def test_benchmark_arguments():
    args = parse_args(["snapshot", "--sample-size", "25", "--radius-arcsec", "2"])
    assert args.sample_size == 25
    assert args.radius_arcsec == 2

    with pytest.raises(SystemExit):
        parse_args(["snapshot", "--sample-size", "0"])
    with pytest.raises(SystemExit):
        parse_args(["snapshot", "--radius-arcsec", "0"])
