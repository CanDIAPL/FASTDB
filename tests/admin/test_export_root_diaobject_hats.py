import csv
import pathlib
import uuid

import hats
import lsdb
import pytest

from admin.export_root_diaobject_hats import (
    CATALOG_NAME,
    MARGIN_NAME,
    _build_catalog,
    export_root_diaobject_hats,
)


def test_build_catalog_and_spatial_match(tmp_path):
    input_csv = tmp_path / "input.csv"
    ids = [uuid.uuid4() for _ in range(3)]
    with input_csv.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["rootid", "ra", "dec"])
        writer.writerow([ids[0], 10.0, -20.0])
        writer.writerow([ids[1], 10.0001, -20.0])
        writer.writerow([ids[2], 200.0, 30.0])

    _build_catalog(input_csv, tmp_path, pixel_threshold=2, margin_arcsec=5.0, workers=1)

    catalog_path = tmp_path / CATALOG_NAME
    margin_path = tmp_path / MARGIN_NAME
    catalog = hats.read_hats(catalog_path)
    assert catalog.catalog_info.total_rows == 3
    assert catalog.catalog_info.ra_column == "ra"
    assert catalog.catalog_info.dec_column == "dec"
    assert hats.read_hats(margin_path).catalog_info.catalog_type == "margin"

    roots = lsdb.read_hats(catalog_path, margin_cache=margin_path)
    near_first = roots.cone_search(10.0, -20.0, radius_arcsec=1.0).compute()
    assert set(near_first["rootid"]) == {str(ids[0]), str(ids[1])}


def test_export_refuses_to_replace_snapshot(tmp_path):
    output_dir = tmp_path / "existing"
    output_dir.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        export_root_diaobject_hats(output_dir, connection=object())


def test_export_argument_validation(tmp_path):
    output_dir = tmp_path / "snapshot"

    with pytest.raises(ValueError, match="pixel_threshold"):
        export_root_diaobject_hats(output_dir, connection=object(), pixel_threshold=0)
    with pytest.raises(ValueError, match="margin_arcsec"):
        export_root_diaobject_hats(output_dir, connection=object(), margin_arcsec=0)
    with pytest.raises(ValueError, match="workers"):
        export_root_diaobject_hats(output_dir, connection=object(), workers=0)
