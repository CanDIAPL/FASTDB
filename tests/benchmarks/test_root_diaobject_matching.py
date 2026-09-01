import pytest

from benchmark_root_diaobject_matching import _comparison_counts, parse_args


def test_comparison_counts():
    q3c = {"one": "root-a", "two": "root-b", "three": None}
    hats = {"one": "root-a", "two": "root-c"}

    assert _comparison_counts(q3c, hats) == (2, 1)


def test_benchmark_arguments():
    args = parse_args(["snapshot", "--sample-size", "25", "--radius-arcsec", "2"])
    assert args.sample_size == 25
    assert args.radius_arcsec == 2

    with pytest.raises(SystemExit):
        parse_args(["snapshot", "--sample-size", "0"])
    with pytest.raises(SystemExit):
        parse_args(["snapshot", "--radius-arcsec", "0"])
