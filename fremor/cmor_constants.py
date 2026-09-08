"""
``fremor`` Constants: Centralized Module-Wide Config
====================================================


Centralized constants for the ``fremor`` package.  Every hard-coded
value that was previously scattered across ``cmor_mixer``, ``cmor_helpers``,
``cmor_config``, ``cmor_finder``, and ``cmor_yamler`` now lives here so that
each module imports from a single, transparent location.

Sections
--------
- **Vertical-coordinate classification** – lists that partition the accepted
  vertical dimension names into physical categories.
- **CMOR module defaults** – arguments passed to ``cmor.setup()``.
- **MIP-era CMOR resources** – per-era CV / coordinate / formula-terms /
  grids table names, as CMOR resolves them relative to a MIP table.
- **CMIP7 brand disambiguation** – mapping from input netCDF vertical
  dimension names to MIP-table dimension names.
- **Archive / filesystem paths** – locations of gold-standard data sets.
- **MIP-table filtering** – suffixes used to exclude non-variable-entry
  tables when scanning a MIP-tables directory.
- **Output / display flags** – behavioral toggles for CLI and finder output.
"""

import cmor

# ---------------------------------------------------------------------------
# Vertical-coordinate classification (used by cmor_mixer)
# ---------------------------------------------------------------------------
ACCEPTED_VERT_DIMS = [
    'z_l', 'landuse',
    'plev39', 'plev30', 'plev19', 'plev8',
    'height2m',
    'level', 'lev', 'levhalf',
]

NON_HYBRID_SIGMA_COORDS = [
    'landuse',
    'plev39', 'plev30', 'plev19', 'plev8',
    'height2m',
]

ALT_HYBRID_SIGMA_COORDS = ['level', 'lev', 'levhalf']

DEPTH_COORDS = ['z_l']

# ---------------------------------------------------------------------------
# Horizontal-coordinate axis names (used by cmor_mixer for cmor.axis calls)
# ---------------------------------------------------------------------------
# Change these if your MIP tables use different names for the lat/lon axes.
CMOR_LAT_AXIS_NAME = 'latitude'
CMOR_LON_AXIS_NAME = 'longitude'


# ---------------------------------------------------------------------------
# CMOR module defaults (passed to cmor.setup in cmor_mixer)
# ---------------------------------------------------------------------------
CMOR_NC_FILE_ACTION = cmor.CMOR_REPLACE
CMOR_VERBOSITY      = cmor.cmor.CMOR_NORMAL#CMOR_QUIET#
CMOR_EXIT_CTL       = cmor.CMOR_EXIT_ON_WARNING#CMOR_NORMAL#
CMOR_MK_SUBDIRS     = 1
CMOR_LOG             = None

# CMOR_EXIT_ON_WARNING makes *every* CMOR message fatal, warnings included -- see
# cmor_handle_error_internal(): `if ((CMOR_MODE == CMOR_EXIT_ON_WARNING) || (level ==
# CMOR_CRITICAL)) kill(getpid(), SIGTERM)`. That is too strict for CMIP6Plus: every table in
# PCMDI/mip-cmor-tables carries a `version_metadata` section, which CMOR's table loader does
# not recognize (it is only known to the CV validator), so loading any CMIP6Plus table warns
# "unknown section: version_metadata" and, under EXIT_ON_WARNING, dies. CMOR_EXIT_ON_MAJOR
# still surfaces genuine errors -- they set CMOR's error flag and the Python wrapper raises
# CMORError -- it just does not treat a warning as fatal.
CMOR_EXIT_CTL_BY_ERA = {
    'CMIP6'    : CMOR_EXIT_CTL,
    'CMIP6PLUS': cmor.CMOR_EXIT_ON_MAJOR,
    'CMIP7'    : CMOR_EXIT_CTL,
}


# ---------------------------------------------------------------------------
# CMIP7 brand disambiguation (used by cmor_helpers.filter_brands)
# ---------------------------------------------------------------------------
# Maps input netCDF vertical dimension names to their CMIP7 MIP-table
# equivalents.  Dimensions whose names already match (e.g. plev39, height2m)
# need no entry; the look-up falls back to using the input name directly.
INPUT_TO_MIP_VERT_DIM = {
    'z_l':      'olevel',
    'level':    'alevel',
    'lev':      'alevel',
    'levhalf':  'alevhalf',
}


# ---------------------------------------------------------------------------
# Archive / filesystem paths (used by cmor_helpers)
# ---------------------------------------------------------------------------
ARCHIVE_GOLD_DATA_DIR = '/archive/gold/datasets'
# CMIP7_GOLD_OCEAN_FILE_STUB='OM5_025/ocean_mosaic_v20250916_unpacked/ocean_static.nc'
# nope, yh/xh repurposed for "mesh index"
# CMIP7_GOLD_OCEAN_FILE_STUB='OM5_025/ocean_mosaic_v20250916_unpacked/ocean_hgrid.nc'
# nope, xh/xq/yh/yq all encoded but no geolat/lon
CMIP7_GOLD_OCEAN_FILE_STUB='OM5_025/ocean_mosaic_v20250916_unpacked/ocean_static_no_basin.nc'
# surprisingly, this should work
CMIP6_GOLD_OCEAN_FILE_STUB=None #TODO

# ---------------------------------------------------------------------------
# MIP-era CMOR resources (CV + auxiliary tables)
# ---------------------------------------------------------------------------
# CMOR resolves the controlled vocabulary, coordinate ("axis entry") and
# formula-terms tables relative to the directory of the MIP table it is loading:
# cmor_load_table() builds "dirname(<table>)/<name>" for each. The grids table is
# resolved the same way by cmor_mixer when a tripolar ocean grid is in play.
#
# The three table repos lay these files out differently:
#   CMIP6     pcmdi/cmip6-cmor-tables   -- everything together in Tables/
#   CMIP6Plus PCMDI/mip-cmor-tables     -- variable tables in Tables/, auxiliary
#                                          tables in Auxillary_files/ (sic), and
#                                          NO CV at all: CMIP6Plus_CV.json lives in
#                                          WCRP-CMIP/CMIP6Plus_CVs
#   CMIP7     WCRP-CMIP/cmip7-cmor-tables -- auxiliary tables in tables/ with the
#                                          variable tables, CV in tables-cvs/
#
# Keyed by the upper-cased mip_era of the experiment config.
MIP_ERA_RESOURCES = {
    'CMIP6': {
        'cv'           : 'CMIP6_CV.json',
        'coordinate'   : 'CMIP6_coordinate.json',
        'formula_terms': 'CMIP6_formula_terms.json',
        'grids'        : 'CMIP6_grids.json',
    },
    'CMIP6PLUS': {
        'cv'           : 'CMIP6Plus_CV.json',
        'coordinate'   : '../Auxillary_files/MIP_coordinate.json',
        'formula_terms': '../Auxillary_files/MIP_formula_terms.json',
        'grids'        : '../Auxillary_files/MIP_grids.json',
    },
    'CMIP7': {
        'cv'           : '../tables-cvs/cmor-cvs.json',
        'coordinate'   : 'CMIP7_coordinate.json',
        'formula_terms': 'CMIP7_formula_terms.json',
        'grids'        : 'CMIP7_grids.json',
    },
}

# Fallback names tried, in order, when the layout above does not match the
# tables the user actually has on disk (e.g. a flattened CMIP6Plus table set
# where MIP_grids.json sits next to the variable tables).
MIP_ERA_RESOURCE_FALLBACKS = {
    'CMIP6'    : {'grids': ['CMIP6_grids.json']},
    # CMIP6Plus has no usable grids table of its own: mip-cmor-tables ships
    # Auxillary_files/MIP_grids.json with no Header, so CMOR cannot set_table() it. A CMIP6
    # grids table stands in -- fremor treats CMIP6Plus as a CMIP6 case -- if the user drops
    # one next to their tables.
    'CMIP6PLUS': {'grids': ['MIP_grids.json', 'CMIP6Plus_grids.json', 'CMIP6PLUS_grids.json',
                            'CMIP6_grids.json']},
    'CMIP7'    : {'grids': ['CMIP7_grids.json']},
}

# Upstream source for the CMIP6Plus controlled vocabulary, which is not shipped
# with the CMIP6Plus MIP tables (used by cmor_init to fetch it alongside them).
CMIP6PLUS_CV_URL = ('https://raw.githubusercontent.com/WCRP-CMIP/CMIP6Plus_CVs/'
                    'main/CVs/CMIP6Plus_CV.json')


# ---------------------------------------------------------------------------
# MIP-table filtering (used by cmor_config)
# ---------------------------------------------------------------------------
# Table-file suffixes to exclude when scanning a MIP-tables directory for
# variable-entry tables.
EXCLUDED_TABLE_SUFFIXES = [
    'long_name_overrides',
    'grids',
    'formula_terms',
    'coordinate',
    'cell_measures',
]


# ---------------------------------------------------------------------------
# Output / display flags
# ---------------------------------------------------------------------------
# cmor_finder: variable-entry keys to suppress when printing variable info.
DO_NOT_PRINT_LIST = [
    'comment',
    'ok_min_mean_abs', 'ok_max_mean_abs',
    'valid_min', 'valid_max',
]
