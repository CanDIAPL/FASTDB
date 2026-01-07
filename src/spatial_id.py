"""
Spatial UUID Generation for Multi-Master Conflict-Free Root Object Identification.

This module generates deterministic 128-bit UUIDs that encode spatial position,
processing version, data release, and timestamp. These UUIDs replace random
rootid values to enable conflict-free multi-master replication.

UUID Structure (128 bits, Big-Endian):
======================================

    Bit Position:
    0                                                                        127
    +------------------------------------------------------------------------+
    | HEALPix (40) | ProcVer (16) | DataRelease (11) | MJD_ms (43) | Rsvd(18)|
    +------------------------------------------------------------------------+

Field Capacities:
-----------------
    - HEALPix:     2^40 = 1.1 x 10^12 (sufficient for NSIDE=2^18)
    - ProcVer:     2^16 = 65,536 processing versions
    - DataRelease: 2^11 = 2,048 data releases (0=realtime, 1=DR1, ...)
    - MJD_ms:      2^43 = 8.8 x 10^12 ms = ~101,000 days from epoch
    - Reserved:    18 bits for future use (zero-filled)

Note: DataRelease field spans the 64-bit boundary (8 bits in high word,
      3 bits in low word) to maintain alignment.

Grouping:
---------
Objects with the same HEALPix + ProcVer share the first 56 bits, enabling
efficient spatial grouping via bitmask: `spatial_id >> 72`

MJD Epoch:
----------
MJD values are stored relative to MJD_EPOCH = 40000.0 (1968-05-23).
This extends the representable range to MJD 40000-141851, covering
all astronomical surveys through year 2280+.
"""

import struct
import uuid
from typing import Tuple

import healpy as hp

# =============================================================================
# Configuration Constants
# =============================================================================

# HEALPix Configuration
# ---------------------
# NSIDE = 2^18 = 262,144 provides ~0.78 arcsecond resolution, matching the
# 1 arcsecond spatial matching threshold used in source_importer.py.
#
# Total HEALPix pixels = 12 * NSIDE^2 = 824,633,720,832 (~8.2 x 10^11)
# This fits in 40 bits (max 2^40 = 1.1 x 10^12)
NSIDE = 2**18

# Bit Field Layout (128 bits total, big-endian)
# ----------------------------------------------
HEALPIX_BITS = 40
PROCVER_BITS = 16
DATARELEASE_BITS = 11
MJD_BITS = 43
RESERVED_BITS = 18

# Compile-time assertion: ensure NSIDE doesn't exceed bit capacity
assert 12 * NSIDE**2 <= (1 << HEALPIX_BITS), \
    f"NSIDE={NSIDE} produces HEALPix indices that exceed {HEALPIX_BITS}-bit capacity"

# Derived maximum values for validation
_MAX_HEALPIX = (1 << HEALPIX_BITS) - 1        # 1,099,511,627,775
_MAX_PROCVER = (1 << PROCVER_BITS) - 1        # 65,535
_MAX_DATARELEASE = (1 << DATARELEASE_BITS) - 1  # 2,047
_MAX_MJD_MS = (1 << MJD_BITS) - 1             # 8,796,093,022,207

# MJD Epoch
# ---------
# Reference MJD for timestamp encoding. Using 40000.0 (1968-05-23) extends
# the representable range to MJD 40000-141851 (through year 2280+).
# All astronomical survey data falls within this range.
MJD_EPOCH = 40000.0

# Milliseconds per day (for MJD conversion)
_MS_PER_DAY = 86_400_000


# =============================================================================
# Generation Functions
# =============================================================================

def generate_spatial_id(
    ra: float,
    dec: float,
    mjd: float,
    procver_compact: int,
    data_release: int,
) -> uuid.UUID:
    """
    Generate a deterministic spatial UUID from astronomical coordinates.

    This function produces identical UUIDs for identical inputs across all
    database instances, enabling conflict-free multi-master replication.

    Parameters
    ----------
    ra : float
        Right ascension in degrees [0, 360)
    dec : float
        Declination in degrees [-90, 90]
    mjd : float
        Modified Julian Date (must be >= MJD_EPOCH = 40000.0)
    procver_compact : int
        Processing version compact ID [0, 65535]
    data_release : int
        Data release identifier [0, 2047] (0=realtime, 1=DR1, etc.)

    Returns
    -------
    uuid.UUID
        Deterministic 128-bit spatial identifier

    Raises
    ------
    ValueError
        If any input is out of valid range

    Examples
    --------
    >>> sid = generate_spatial_id(ra=180.0, dec=0.0, mjd=60000.0,
    ...                           procver_compact=1, data_release=0)
    >>> extract_procver(sid)
    1
    """
    # Validate inputs
    if not (0 <= ra < 360):
        raise ValueError(f"ra must be in [0, 360), got {ra}")
    if not (-90 <= dec <= 90):
        raise ValueError(f"dec must be in [-90, 90], got {dec}")
    if mjd < MJD_EPOCH:
        raise ValueError(f"mjd must be >= {MJD_EPOCH}, got {mjd}")
    if not (0 <= procver_compact <= _MAX_PROCVER):
        raise ValueError(
            f"procver_compact must be in [0, {_MAX_PROCVER}], got {procver_compact}"
        )
    if not (0 <= data_release <= _MAX_DATARELEASE):
        raise ValueError(
            f"data_release must be in [0, {_MAX_DATARELEASE}], got {data_release}"
        )

    # Compute HEALPix index (NESTED scheme for spatial locality)
    hpix = hp.ang2pix(NSIDE, ra, dec, nest=True, lonlat=True)

    # Validate HEALPix fits in allocated bits (defensive check)
    if hpix > _MAX_HEALPIX:
        raise ValueError(
            f"HEALPix index {hpix} exceeds maximum for {HEALPIX_BITS} bits"
        )

    # Convert MJD to milliseconds relative to epoch
    mjd_ms = int((mjd - MJD_EPOCH) * _MS_PER_DAY)

    # Validate MJD_ms fits in allocated bits
    if mjd_ms > _MAX_MJD_MS:
        raise ValueError(
            f"MJD {mjd} exceeds maximum representable value "
            f"(max MJD = {MJD_EPOCH + _MAX_MJD_MS / _MS_PER_DAY:.1f})"
        )

    # Pack into 128 bits (big-endian)
    # Layout: [HEALPix:40][ProcVer:16][DataRelease:11][MJD_ms:43][Reserved:18]
    #
    # high64 contains: HEALPix (40) + ProcVer (16) + DataRelease high 8 bits
    # low64 contains:  DataRelease low 3 bits + MJD_ms (43) + Reserved (18)

    high64 = (hpix << 24) | (procver_compact << 8) | (data_release >> 3)
    low64 = ((data_release & 0x7) << 61) | (mjd_ms << 18)

    # Convert to bytes (big-endian) and create UUID
    uuid_bytes = struct.pack(">QQ", high64, low64)
    return uuid.UUID(bytes=uuid_bytes)


# =============================================================================
# Extraction Functions
# =============================================================================

def extract_healpix(spatial_id: uuid.UUID) -> int:
    """
    Extract HEALPix index from spatial_id.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    int
        HEALPix index (NESTED scheme at NSIDE=2^18)
    """
    high64 = struct.unpack(">Q", spatial_id.bytes[:8])[0]
    return high64 >> 24


def extract_procver(spatial_id: uuid.UUID) -> int:
    """
    Extract processing version compact ID from spatial_id.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    int
        Processing version compact ID [0, 65535]
    """
    high64 = struct.unpack(">Q", spatial_id.bytes[:8])[0]
    return (high64 >> 8) & 0xFFFF


def extract_data_release(spatial_id: uuid.UUID) -> int:
    """
    Extract data release ID from spatial_id.

    Note: DataRelease spans the 64-bit boundary (8 bits high, 3 bits low).

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    int
        Data release ID [0, 2047] (0=realtime, 1=DR1, etc.)
    """
    high64, low64 = struct.unpack(">QQ", spatial_id.bytes)
    return ((high64 & 0xFF) << 3) | (low64 >> 61)


def extract_mjd(spatial_id: uuid.UUID) -> float:
    """
    Extract MJD (Modified Julian Date) from spatial_id.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    float
        Modified Julian Date
    """
    low64 = struct.unpack(">Q", spatial_id.bytes[8:])[0]
    mjd_ms = (low64 >> 18) & 0x7FFFFFFFFFF  # 43 bits
    return (mjd_ms / _MS_PER_DAY) + MJD_EPOCH


def extract_all(spatial_id: uuid.UUID) -> Tuple[int, int, int, float]:
    """
    Extract all components from spatial_id.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    tuple
        (healpix, procver_compact, data_release, mjd)
    """
    high64, low64 = struct.unpack(">QQ", spatial_id.bytes)

    healpix = high64 >> 24
    procver = (high64 >> 8) & 0xFFFF
    data_release = ((high64 & 0xFF) << 3) | (low64 >> 61)
    mjd_ms = (low64 >> 18) & 0x7FFFFFFFFFF
    mjd = (mjd_ms / _MS_PER_DAY) + MJD_EPOCH

    return healpix, procver, data_release, mjd


# =============================================================================
# Grouping Functions
# =============================================================================

def spatial_group_int(spatial_id: uuid.UUID) -> int:
    """
    Extract grouping prefix (HEALPix + ProcVer, 56 bits) as integer.

    Objects with the same spatial_group_int are in the same spatial group
    and should be considered related (e.g., same astronomical object across
    different observations).

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    int
        56-bit grouping prefix (HEALPix + ProcVer)

    Examples
    --------
    >>> sid1 = generate_spatial_id(180.0, 0.0, 60000.0, 1, 0)
    >>> sid2 = generate_spatial_id(180.0, 0.0, 60001.0, 1, 0)  # Different time
    >>> spatial_group_int(sid1) == spatial_group_int(sid2)
    True
    """
    high64 = struct.unpack(">Q", spatial_id.bytes[:8])[0]
    return high64 >> 8  # Top 56 bits


def same_spatial_group(sid1: uuid.UUID, sid2: uuid.UUID) -> bool:
    """
    Check if two spatial IDs belong to the same spatial group.

    Parameters
    ----------
    sid1, sid2 : uuid.UUID
        Spatial identifiers to compare

    Returns
    -------
    bool
        True if both IDs share the same HEALPix + ProcVer prefix
    """
    return spatial_group_int(sid1) == spatial_group_int(sid2)


# =============================================================================
# Coordinate Recovery
# =============================================================================

def healpix_to_radec(healpix: int) -> Tuple[float, float]:
    """
    Convert HEALPix index back to approximate RA/Dec coordinates.

    Note: This returns the center of the HEALPix pixel, not the original
    coordinates. Precision is limited to ~0.78 arcseconds (NSIDE=2^18).

    Parameters
    ----------
    healpix : int
        HEALPix index (NESTED scheme at NSIDE=2^18)

    Returns
    -------
    tuple
        (ra, dec) in degrees
    """
    ra, dec = hp.pix2ang(NSIDE, healpix, nest=True, lonlat=True)
    return float(ra), float(dec)


def extract_approx_radec(spatial_id: uuid.UUID) -> Tuple[float, float]:
    """
    Extract approximate RA/Dec from spatial_id.

    Note: Precision is limited to ~0.78 arcseconds due to HEALPix quantization.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    tuple
        (ra, dec) in degrees (approximate, center of HEALPix pixel)
    """
    healpix = extract_healpix(spatial_id)
    return healpix_to_radec(healpix)
