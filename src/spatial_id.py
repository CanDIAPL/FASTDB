"""
Spatial UUID Generation for Multi-Master Conflict-Free Root Object Identification.

This module generates deterministic 128-bit UUIDs that encode spatial position,
timestamp, processing version, and data release. These UUIDs replace random
rootid values to enable conflict-free multi-master replication.

UUID Structure (128 bits, Big-Endian):
======================================

    Bit Position:
    0                                                                        127
    +------------------------------------------------------------------------+
    | Reserved(2) | HEALPix (62)        | MJD_ms (43) | ProcVer (16) | DR(5) |
    +------------------------------------------------------------------------+

Field Ordering Rationale:
-------------------------
Fields are ordered to enable hierarchical filtering:
    1. Position (HEALPix): Filter by sky location first
    2. Time (MJD): Then narrow by observation time
    3. ProcVer: Then by processing version
    4. DataRelease: Finally by data release

This allows efficient range queries like:
    - All objects at position X → mask HEALPix bits
    - Objects at X observed in time range → add MJD bits
    - Specific processing version at X,T → add ProcVer bits

Field Capacities:
-----------------
    - Reserved:   2 bits (always 0, for future use)
    - HEALPix:    62 bits, NSIDE=2^29, ~0.0004" (0.4 milliarcsec) resolution
    - MJD_ms:     43 bits = 278 years at 1ms precision from epoch
    - ProcVer:    16 bits = 65,536 processing versions
    - DataRelease: 5 bits = 32 data releases (0=realtime, 1-31=DR1-DR31)

Byte Layout:
------------
    - high64 (bytes 0-7):  [Reserved:2][HEALPix:62] - HEALPix uses at most 62 bits
    - low64 (bytes 8-15):  [MJD_ms:43][ProcVer:16][DataRelease:5]

Grouping:
---------
For SQL compatibility (BIGINT is 64 bits), spatial grouping uses:
    - Top 48 bits of HEALPix = 48 bits (fits in BIGINT)
    - This provides ~0.05" grouping resolution (still sub-arcsecond)

MJD Epoch:
----------
MJD values are stored relative to MJD_EPOCH = 40000.0 (1968-05-23).
This extends the representable range to MJD 40000-141851, covering
all astronomical surveys through year 2280+.

Precision:
----------
    - Spatial: ~0.0004" (0.4 milliarcseconds) - exceeds any telescope precision
    - Temporal: 1 millisecond
    - Coordinate recovery: Exact to HEALPix pixel center (~0.0004")
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
# NSIDE = 2^29 is the maximum supported by healpy.
# This provides ~0.0004" (0.4 milliarcsecond) resolution.
# This exceeds the precision of any current ground-based or space telescope.
#
# Total HEALPix pixels = 12 * NSIDE^2 = 12 * 2^58 ≈ 3.5 x 10^18
# This fits in 62 bits (max 2^62 ≈ 4.6 x 10^18)
#
# Resolution = 206265" / NSIDE = 206265 / 2^29 ≈ 0.000385"
NSIDE = 2**29

# Bit Field Layout (128 bits total, big-endian)
# ----------------------------------------------
# high64: [Reserved:2][HEALPix:62] - HEALPix values fit in 62 bits
# low64:  [MJD_ms:43][ProcVer:16][DataRelease:5]
#
# Effective bit positions (0 = MSB):
#   Bits 0-1:    Reserved (always 0)
#   Bits 2-63:   HEALPix (62 bits)
#   Bits 64-106: MJD_ms (43 bits)
#   Bits 107-122: ProcVer (16 bits)
#   Bits 123-127: DataRelease (5 bits)
RESERVED_BITS = 2
HEALPIX_BITS = 62
PROCVER_BITS = 16
MJD_BITS = 43
DATARELEASE_BITS = 5

# Compile-time assertion: ensure NSIDE doesn't exceed bit capacity
assert 12 * NSIDE**2 <= (1 << HEALPIX_BITS), \
    f"NSIDE={NSIDE} produces HEALPix indices that exceed {HEALPIX_BITS}-bit capacity"

# Verify total bits = 128
assert RESERVED_BITS + HEALPIX_BITS + PROCVER_BITS + MJD_BITS + DATARELEASE_BITS == 128, \
    "Bit fields must sum to 128"

# Derived maximum values for validation
_MAX_HEALPIX = (1 << HEALPIX_BITS) - 1        # 2^62 - 1 ≈ 4.6 x 10^18
_MAX_PROCVER = (1 << PROCVER_BITS) - 1        # 65,535
_MAX_MJD_MS = (1 << MJD_BITS) - 1             # 8,796,093,022,207
_MAX_DATARELEASE = (1 << DATARELEASE_BITS) - 1  # 31 (0=realtime, 1-31=DR1-DR31)

# MJD Epoch
# ---------
# Reference MJD for timestamp encoding. Using 40000.0 (1968-05-23) extends
# the representable range to MJD 40000-141851 (through year 2280+).
# All astronomical survey data falls within this range.
MJD_EPOCH = 40000.0

# Milliseconds per day (for MJD conversion)
_MS_PER_DAY = 86_400_000

# Spatial grouping uses top 48 bits of HEALPix
# This provides ~0.05" grouping resolution (NSIDE=2^22 equivalent)
# ProcVer is NOT included in spatial grouping (allows grouping across versions)
_GROUPING_HEALPIX_BITS = 48


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
        Data release identifier [0, 31] (0=realtime, 1=DR1, etc.)

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
    # Convert to Python int (healpy returns numpy int64)
    hpix = int(hp.ang2pix(NSIDE, ra, dec, nest=True, lonlat=True))

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
    # Layout: [HEALPix:62][MJD_ms:43][ProcVer:16][DataRelease:5]
    #
    # high64: HEALPix (62 bits, with 2 reserved bits at top)
    # low64:  MJD_ms (43) | ProcVer (16) | DataRelease (5)

    high64 = hpix
    low64 = (mjd_ms << 21) | (procver_compact << 5) | data_release

    # Mask to 64 bits (Python integers are unbounded, struct.pack needs bounded)
    high64 = high64 & 0xFFFFFFFFFFFFFFFF
    low64 = low64 & 0xFFFFFFFFFFFFFFFF

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
        HEALPix index (NESTED scheme at NSIDE=2^29)
    """
    high64 = struct.unpack(">Q", spatial_id.bytes[:8])[0]
    return high64


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
    low64 = struct.unpack(">Q", spatial_id.bytes[8:])[0]
    return (low64 >> 5) & 0xFFFF


def extract_data_release(spatial_id: uuid.UUID) -> int:
    """
    Extract data release ID from spatial_id.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    int
        Data release ID [0, 31] (0=realtime, 1=DR1, etc.)
    """
    low64 = struct.unpack(">Q", spatial_id.bytes[8:])[0]
    return low64 & 0x1F


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
    mjd_ms = (low64 >> 21) & 0x7FFFFFFFFFF  # 43 bits
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

    healpix = high64
    mjd_ms = (low64 >> 21) & 0x7FFFFFFFFFF  # 43 bits
    procver = (low64 >> 5) & 0xFFFF  # 16 bits
    data_release = low64 & 0x1F  # 5 bits
    mjd = (mjd_ms / _MS_PER_DAY) + MJD_EPOCH

    return healpix, procver, data_release, mjd


# =============================================================================
# Grouping Functions
# =============================================================================

def spatial_group_int(spatial_id: uuid.UUID) -> int:
    """
    Extract spatial grouping prefix as 64-bit integer for SQL compatibility.

    Uses top 48 bits of HEALPix only (NOT including ProcVer).
    This provides ~0.05" grouping resolution while fitting in SQL BIGINT.

    Objects with the same spatial_group_int are at the same sky position
    (regardless of observation time, processing version, or data release).
    This enables queries like "find all observations of this object".

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    int
        48-bit spatial grouping prefix (top 48 bits of HEALPix)

    Examples
    --------
    >>> sid1 = generate_spatial_id(180.0, 0.0, 60000.0, 1, 0)
    >>> sid2 = generate_spatial_id(180.0, 0.0, 60001.0, 2, 1)  # Different time/procver/dr
    >>> spatial_group_int(sid1) == spatial_group_int(sid2)
    True
    """
    high64 = struct.unpack(">Q", spatial_id.bytes[:8])[0]

    # Top 48 bits of HEALPix (shift right 16 to get top 48)
    # This gives ~0.05" spatial resolution for grouping
    return high64 >> 16


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
        True if both IDs share the same spatial group prefix
    """
    return spatial_group_int(sid1) == spatial_group_int(sid2)


# =============================================================================
# Coordinate Recovery
# =============================================================================

def healpix_to_radec(healpix: int) -> Tuple[float, float]:
    """
    Convert HEALPix index back to RA/Dec coordinates.

    With NSIDE=2^29, precision is ~0.0004" (0.4 milliarcseconds),
    which exceeds the measurement precision of any current telescope.

    Parameters
    ----------
    healpix : int
        HEALPix index (NESTED scheme at NSIDE=2^29)

    Returns
    -------
    tuple
        (ra, dec) in degrees
    """
    ra, dec = hp.pix2ang(NSIDE, healpix, nest=True, lonlat=True)
    return float(ra), float(dec)


def extract_approx_radec(spatial_id: uuid.UUID) -> Tuple[float, float]:
    """
    Extract RA/Dec from spatial_id.

    With NSIDE=2^29, the recovered coordinates have ~0.0004" precision,
    which exceeds the measurement precision of any current telescope.

    Parameters
    ----------
    spatial_id : uuid.UUID
        Spatial identifier

    Returns
    -------
    tuple
        (ra, dec) in degrees (center of HEALPix pixel)
    """
    healpix = extract_healpix(spatial_id)
    return healpix_to_radec(healpix)
