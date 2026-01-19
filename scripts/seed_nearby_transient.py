#!/usr/bin/env python3
"""
Seed MongoDB with realistic transient data - multiple observations of the same
object at slightly different positions within 1 arcsecond, simulating a
brightening and fading light curve over a week.
"""

import datetime
import random
from pymongo import MongoClient


def create_flag_dict():
    """Create a dict with all flags set to False."""
    return {f: False for f in [
        "flag_negativeVariance", "flag_calibVariance", "flag_maxIter",
        "flag_edgePix", "flag_intpPix", "flag_satPix", "flag_crPix",
        "flag_bpPix", "flag_badPix", "flag_dpPix", "flag_rcPix", "flag_nan",
        "pixelflags_intpAny", "pixelflags_intpCenter", "pixelflags_edgeAny",
        "pixelflags_edgeCenter", "pixelflags_crAny", "pixelflags_crCenter",
        "pixelflags_bpAny", "pixelflags_bpCenter", "pixelflags_satAny",
        "pixelflags_satCenter", "pixelflags_dpAny", "pixelflags_dpCenter"
    ]}


def generate_source(source_id, object_id, ra, dec, mjd, flux, flux_err, band):
    """Generate a diaSource document."""
    return {
        "diaSourceId": source_id,
        "visit": 100000 + random.randint(0, 10000),
        "detector": random.randint(0, 188),
        "diaObjectId": object_id,
        "ssObjectId": None,
        "parentDiaSourceId": None,
        "midpointMjdTai": mjd,
        "ra": ra,
        "raErr": random.uniform(0.0001, 0.0003),
        "dec": dec,
        "decErr": random.uniform(0.0001, 0.0003),
        "ra_dec_Cov": random.uniform(-1e-8, 1e-8),
        "x": random.uniform(1000, 3000),
        "xErr": random.uniform(0.1, 0.5),
        "y": random.uniform(1000, 3000),
        "yErr": random.uniform(0.1, 0.5),
        "apFlux": flux * random.uniform(0.95, 1.05),
        "apFluxErr": flux_err,
        "snr": flux / flux_err,
        "psfFlux": flux,
        "psfFluxErr": flux_err,
        "psfLnL": random.uniform(-50, -10),
        "psfChi2": random.uniform(0.8, 1.5),
        "psfNdata": random.randint(30, 80),
        "scienceFlux": flux,
        "scienceFluxErr": flux_err,
        "templateFlux": random.uniform(100, 500),
        "templateFluxErr": random.uniform(20, 50),
        "ixx": random.uniform(1.5, 2.5),
        "iyy": random.uniform(1.5, 2.5),
        "ixy": random.uniform(-0.3, 0.3),
        "ixxPSF": random.uniform(1.6, 2.0),
        "iyyPSF": random.uniform(1.6, 2.0),
        "ixyPSF": random.uniform(-0.1, 0.1),
        "extendedness": random.uniform(0.0, 0.15),
        "reliability": random.uniform(0.9, 0.99),
        "band": band,
        "timeProcessedMjdTai": mjd + 0.02,
        "timeWithdrawnMjdTai": None,
        "bboxSize": 41,
        **create_flag_dict()
    }


def generate_forced_source(forced_id, object_id, ra, dec, mjd, band):
    """Generate a diaForcedSource document (pre-transient non-detection)."""
    forced_flux = random.uniform(-200, 200)  # Noise around zero
    forced_flux_err = random.uniform(150, 300)

    return {
        "diaForcedSourceId": forced_id,
        "diaObjectId": object_id,
        "ra": ra,
        "dec": dec,
        "visit": 90000 + random.randint(0, 10000),
        "detector": random.randint(0, 188),
        "psfFlux": forced_flux,
        "psfFluxErr": forced_flux_err,
        "midpointMjdTai": mjd,
        "scienceFlux": forced_flux,
        "scienceFluxErr": forced_flux_err,
        "band": band,
        "timeProcessedMjdTai": mjd + 0.02,
        "timeWithdrawnMjdTai": None,
    }


def main():
    # Connect to MongoDB
    connstr = "mongodb://alertwriter:writer@mongodb:27017/?authSource=brokeralert"
    client = MongoClient(connstr)
    db = client["brokeralert"]
    collection = db["test_alerts"]

    # Clear existing nearby object data
    result = collection.delete_many({"msg.diaObject.diaObjectId": {"$gte": 91000000}})
    print(f"Cleared {result.deleted_count} existing documents")

    # Configuration
    base_ra = 120.0
    base_dec = 45.0
    base_mjd = 60050.0
    arcsec = 1.0 / 3600.0
    bands = ["g", "r", "i", "z"]
    peak_flux = 50000.0  # Peak brightness in nJy
    base_object_id = 91000000

    documents = []
    now = datetime.datetime.now(tz=datetime.timezone.utc)

    # Generate 5 observations simulating a transient light curve
    for i in range(5):
        object_id = base_object_id + i

        # Position within 0.5 arcsecond of base (tight clustering)
        ra_offset = random.uniform(-0.3, 0.3) * arcsec
        dec_offset = random.uniform(-0.3, 0.3) * arcsec
        ra = base_ra + ra_offset
        dec = base_dec + dec_offset

        # MJD spread over a week - ~1.5 days apart
        day_offset = i * 1.5
        mjd = base_mjd + day_offset

        # Simulate light curve - peak at day 3 (i=2)
        phase = abs(day_offset - 3) / 3.0
        flux = peak_flux * (1 - 0.7 * phase)
        flux_err = flux * random.uniform(0.02, 0.05)

        band = bands[i % len(bands)]

        # Generate previous sources (earlier observations)
        prv_sources = []
        for j in range(i):
            prv_mjd = base_mjd + j * 1.5
            prv_phase = abs((j * 1.5) - 3) / 3.0
            prv_flux = peak_flux * (1 - 0.7 * prv_phase)
            prv_flux_err = prv_flux * random.uniform(0.02, 0.05)
            prv_band = bands[j % len(bands)]
            prv_ra = ra + random.uniform(-0.1, 0.1) * arcsec
            prv_dec = dec + random.uniform(-0.1, 0.1) * arcsec

            prv_sources.append(generate_source(
                source_id=base_object_id * 10 + i * 100 + j + 10,
                object_id=object_id,
                ra=prv_ra, dec=prv_dec, mjd=prv_mjd,
                flux=prv_flux, flux_err=prv_flux_err, band=prv_band
            ))

        # Generate forced sources (pre-transient non-detections)
        prv_forced = []
        for k in range(5):
            forced_mjd = base_mjd - 10 - k * 2  # 10-20 days before
            prv_forced.append(generate_forced_source(
                forced_id=base_object_id * 100 + i * 1000 + k,
                object_id=object_id,
                ra=ra, dec=dec, mjd=forced_mjd,
                band=bands[k % len(bands)]
            ))

        # Create the alert document
        doc = {
            "topic": "alerts-test",
            "msgoffset": random.randint(0, 1000000),
            "timestamp": now,
            "savetime": now,
            "msg": {
                "diaObject": {
                    "diaObjectId": object_id,
                    "validityStartMjdTai": mjd,
                    "ra": ra,
                    "raErr": random.uniform(0.0001, 0.0003),
                    "dec": dec,
                    "decErr": random.uniform(0.0001, 0.0003),
                    "ra_dec_Cov": random.uniform(-1e-8, 1e-8),
                    "nDiaSources": i + 1,
                },
                "diaSource": generate_source(
                    source_id=base_object_id * 10 + i,
                    object_id=object_id,
                    ra=ra, dec=dec, mjd=mjd,
                    flux=flux, flux_err=flux_err, band=band
                ),
                "prvDiaSources": prv_sources,
                "prvDiaForcedSources": prv_forced,
                "cutoutScience": None,
                "cutoutTemplate": None,
                "cutoutDifference": None,
            }
        }
        documents.append(doc)

        sep_arcsec = ((ra_offset**2 + dec_offset**2)**0.5) * 3600
        print(f"Object {object_id}: band={band}, MJD={mjd:.1f}, "
              f"flux={flux:.0f}±{flux_err:.0f} nJy, sep={sep_arcsec:.3f}\", "
              f"{len(prv_sources)} prv_src, {len(prv_forced)} prv_forced")

    result = collection.insert_many(documents)
    print(f"\nInserted {len(result.inserted_ids)} alert documents")
    print(f"Simulating a transient brightening/fading over 6 days")
    print(f"\nTo import: python -m services.source_importer -p realtime -c test_alerts")


if __name__ == "__main__":
    main()
