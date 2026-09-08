"""
Shared fixtures for fremor/tests CLI integration tests.
"""

from datetime import date
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import fremor


# ── path constants ──────────────────────────────────────────────────────────
ROOTDIR = Path(fremor.__file__).parent / 'tests' / 'test_files'

CMIP6_TABLE_CONFIG     = ROOTDIR / 'cmip6-cmor-tables' / 'Tables' / 'CMIP6_Omon.json'
CMIP6PLUS_TABLE_CONFIG = ROOTDIR / 'mip-cmor-tables'   / 'Tables' / 'MIP_OPmon.json'
CMIP7_TABLE_CONFIG     = ROOTDIR / 'cmip7-cmor-tables' / 'tables' / 'CMIP7_ocean.json'

INDIR = ROOTDIR / 'ocean_sos_var_file'
VARLIST = ROOTDIR / 'varlist'
VARLIST_DIFF = ROOTDIR / 'varlist_local_target_vars_differ'
VARLIST_MAPPED = ROOTDIR / 'varlist_mapped'
EXP_CONFIG = ROOTDIR / 'CMOR_input_example.json'
EXP_CONFIG_CMIP7 = ROOTDIR / 'CMOR_CMIP7_input_example.json'
EXP_CONFIG_CMIP6PLUS = ROOTDIR / 'CMOR_CMIP6PLUS_input_example.json'

SOS_NC_FILENAME = 'reduced_ocean_monthly_1x1deg.199301-199302.sos.nc'
SOSV2_NC_FILENAME = 'reduced_ocean_monthly_1x1deg.199301-199302.sosV2.nc'
MAPPED_NC_FILENAME = 'reduced_ocean_monthly_1x1deg.199301-199302.sea_sfc_salinity.nc'

YYYYMMDD = date.today().strftime('%Y%m%d')


# ── raw experiment-config contents (kept in-code for fixture use) ───────────
# pylint: disable=line-too-long
_CMIP6_EXP_CONFIG_DATA = {
    '#note': ' **** The following are set correctly for CMIP6 and should not normally need editing',
    'source_type': 'AOGCM ISM AER',
    'experiment_id': 'piControl-withism',
    'activity_id': 'ISMIP6',
    'sub_experiment_id': 'none',
    'realization_index': '3',
    'initialization_index': '1',
    'physics_index': '1',
    'forcing_index': '1',
    'run_variant': '3rd realization',
    'parent_experiment_id': 'no parent',
    'parent_activity_id': 'no parent',
    'parent_source_id': 'no parent',
    'parent_variant_label': 'no parent',
    'parent_time_units': 'no parent',
    'branch_method': 'no parent',
    'branch_time_in_child': 59400.0,
    'branch_time_in_parent': 0.0,
    'institution_id': 'PCMDI',
    'source_id': 'PCMDI-test-1-0',
    'calendar': 'julian',
    'grid': 'FOO_BAR_PLACEHOLD',
    'grid_label': 'gr',
    'nominal_resolution': '10000 km',
    'license': 'CMIP6 model data produced by Lawrence Livermore PCMDI is licensed under a Creative Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/). Consult https://pcmdi.llnl.gov/CMIP6/TermsOfUse for terms of use governing CMIP6 output, including citation requirements and proper acknowledgment. Further information about this data, including some limitations, can be found via the further_info_url (recorded as a global attribute in this file) and at https:///pcmdi.llnl.gov/. The data producers and data providers make no warranty, either express or implied, including, but not limited to, warranties of merchantability and fitness for a particular purpose. All liabilities arising from the supply of the information (including any liability arising in negligence) are excluded to the fullest extent permitted by law.',
    '#output': 'Root directory for output (can be either a relative or full path)',
    'outpath': 'CMIP6',
    'contact ': 'Python Coder (coder@a.b.c.com)',
    'history': 'Output from archivcl_A1.nce/giccm_03_std_2xCO2_2256.',
    'comment': '',
    'references': 'Model described by Koder and Tolkien (J. Geophys. Res., 2001, 576-591).  Also see http://www.GICC.su/giccm/doc/index.html.  The ssp245 simulation is described in Dorkey et al. \'(Clim. Dyn., 2003, 323-357.)\'',
    'sub_experiment': 'none',
    'institution': '',
    'source': 'PCMDI-test 1.0 (1989)',
    '_controlled_vocabulary_file': 'CMIP6_CV.json',
    '_AXIS_ENTRY_FILE': 'CMIP6_coordinate.json',
    '_FORMULA_VAR_FILE': 'CMIP6_formula_terms.json',
    '_cmip6_option': 'CMIP6',
    'mip_era': 'CMIP6',
    'parent_mip_era': 'no parent',
    'tracking_prefix': 'hdl:21.14100',
    '_history_template': '%s ;rewrote data to be consistent with <activity_id> for variable <variable_id> found in table <table_id>.',
    '#output_path_template': 'Template for output path directory using tables keys or global attributes, these should follow the relevant data reference syntax',
    'output_path_template': '<mip_era><activity_id><institution_id><source_id><experiment_id><_member_id><table><variable_id><grid_label><version>',
    'output_file_template': '<variable_id><table><source_id><experiment_id><_member_id><grid_label>'
}

_CMIP6PLUS_EXP_CONFIG_DATA = {
    '#note': ' **** CMIP6Plus experiment config for testing ****',
    'source_type': 'AOGCM ISM AER',
    'experiment_id': 'piControl-withism',
    'activity_id': 'ISMIP6',
    'sub_experiment_id': 'none',
    'realization_index': '3',
    'initialization_index': '1',
    'physics_index': '1',
    'forcing_index': '1',
    'run_variant': '3rd realization',
    'parent_experiment_id': 'no parent',
    'parent_activity_id': 'no parent',
    'parent_source_id': 'no parent',
    'parent_variant_label': 'no parent',
    'parent_time_units': 'no parent',
    'branch_method': 'no parent',
    'branch_time_in_child': 59400.0,
    'branch_time_in_parent': 0.0,
    'institution_id': 'PCMDI',
    'source_id': 'PCMDI-test-1-0',
    'calendar': 'julian',
    'grid': 'FOO_BAR_PLACEHOLD',
    'grid_label': 'gr',
    'nominal_resolution': '10000 km',
    'license': 'CMIP6Plus model data produced by PCMDI is licensed under a Creative Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/). Consult https://pcmdi.llnl.gov/CMIP6Plus/TermsOfUse for terms of use governing CMIP6Plus output, including citation requirements and proper acknowledgment. The data producers and data providers make no warranty, either express or implied, including, but not limited to, warranties of merchantability and fitness for a particular purpose. All liabilities arising from the supply of the information (including any liability arising in negligence) are excluded to the fullest extent permitted by law.',
    '#output': 'Root directory for output (can be either a relative or full path)',
    'outpath': 'CMIP6Plus',
    'contact ': 'Python Coder (coder@a.b.c.com)',
    'history': 'Output from archivcl_A1.nce/giccm_03_std_2xCO2_2256.',
    'comment': '',
    'references': 'Model described by Koder and Tolkien (J. Geophys. Res., 2001, 576-591).  Also see http://www.GICC.su/giccm/doc/index.html.  The ssp245 simulation is described in Dorkey et al. \'(Clim. Dyn., 2003, 323-357.)\'',
    'sub_experiment': 'none',
    'institution': '',
    'source': 'PCMDI-test 1.0 (1989)',
    # CMOR resolves these relative to the MIP table's directory; PCMDI/mip-cmor-tables keeps
    # its auxiliary tables in Auxillary_files/, a sibling of Tables/, and ships no CV
    # (CMIP6Plus_CV.json comes from WCRP-CMIP/CMIP6Plus_CVs).
    '_controlled_vocabulary_file': 'CMIP6Plus_CV.json',
    '_AXIS_ENTRY_FILE': '../Auxillary_files/MIP_coordinate.json',
    '_FORMULA_VAR_FILE': '../Auxillary_files/MIP_formula_terms.json',
    '_cmip6_option': 'CMIP6',
    'mip_era': 'CMIP6Plus',
    'parent_mip_era': 'no parent',
    'tracking_prefix': 'hdl:21.14100',
    '_history_template': '%s ;rewrote data to be consistent with <activity_id> for variable <variable_id> found in table <table_id>.',
    '#output_path_template': 'Template for output path directory using tables keys or global attributes, these should follow the relevant data reference syntax',
    'output_path_template': '<mip_era><activity_id><institution_id><source_id><experiment_id><_member_id><table><variable_id><grid_label><version>',
    'output_file_template': '<variable_id><table><source_id><experiment_id><_member_id><grid_label>'
}

_CMIP7_EXP_CONFIG_DATA = {
    '#_TESTING_ONLY': ' ***** This is for unit-test functionality of NOAA-GFDL\'s fremor module for CMIP7, they do not reflect values used in actual production *****',
    'contact ': 'MIP participant mipmember@foobar.c.om',
    'comment': 'additional important information not fitting into other fields can be placed here',
    'license_id': 'CC-BY-4.0',
    'license_url': 'https://creativecommons.org/licenses/by/4.0',
    'license': 'CC-BY-4.0; CMIP7 data produced by NOAA-GFDL is licensed under a Creative Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0). Consult https://wcrp-cmip.github.io/cmip7-guidance/docs/CMIP7/Guidance_for_users/#2-terms-of-use-and-citations-requirements for terms of use governing CMIP7 output, including citation requirements and proper acknowledgment. The data producers and data providers make no warranty, either express or implied, including, but not limited to, warranties of merchantability and fitness for a particular purpose. All liabilities arising from the supply of the information (including any liability arising in negligence) are excluded to the fullest extent permitted by law.',
    '#_TRACKING': '***** anything to do with citing this data, accreditation, licensing, and references go here *****',
    'references': 'Model described by Koder and Tolkien (J. Geophys. Res., 2001, 576-591).  Also see http://www.GICC.su/giccm/doc/index.html.  The ssp245 simulation is described in Dorkey et al. \'(Clim. Dyn., 2003, 323-357.)\'',
    'drs_specs': 'MIP-DRS7',
    'archive_id': 'WCRP',
    'tracking_prefix': 'hdl:21.14107',
    '#_MIP_DETAILS': '***** anything to do with identifying the specific MIP activity this configuration file is for *****',
    '_cmip7_option': 1,
    'mip_era': 'CMIP7',
    'parent_mip_era': 'CMIP7',
    'activity_id': 'CMIP',
    'parent_activity_id': 'CMIP',
    '#_SOURCE_SECTION': '***** anything to do with identifying this experiment, it\'s relationships to other experiments, the producers, and their institution *****',
    'institution_id': 'NOAA-GFDL',
    'source': 'GFDL-ESM4p5: aerosol: gfdl-am4p5-aerosol; atmosphere: gfdl-am4p5; land-surface: gfdl-lm4p5; ocean-biogeochemistry: cobaltv3p1; ocean: gfdl-om4p5; sea-ice: sis2',
    'parent_source_id': 'GFDL-ESM4p5',
    'source_id': 'GFDL-ESM4p5',
    'source_type': 'AOGCM ISM AER',
    'parent_experiment_id': 'piControl',
    'experiment_id': 'historical',
    'sub_experiment': 'none',
    'sub_experiment_id': 'none',
    '#_INDICES': '***** changed from ints to strings for CMIP7 *****',
    'realization_index': 'r3',
    'initialization_index': 'i1',
    'physics_index': 'p1',
    'forcing_index': 'f3',
    'run_variant': '3rd realization',
    'parent_variant_label': 'r3i1p1f3',
    '#_TEMPORAL_INFO': '***** anything to do with describing temporal aspects of the experiment *****',
    'parent_time_units': 'days since 1850-01-01',
    'branch_method': 'no parent',
    'branch_time_in_child': 59400.0,
    'branch_time_in_parent': 0.0,
    'calendar': 'julian',
    '#_SPATIAL_INFO': '***** anything to do with describing physical aspects of the experiment *****',
    'grid': 'FOO_BAR_PLACEHOLD',
    'grid_label': 'g999',
    'frequency': 'mon',
    'region': 'glb',
    'nominal_resolution': '10000 km',
    '#_HISTORY_METADATA': 'history attribute string and template, to create history field for output file',
    'history': 'Output from archivcl_A1.nce/giccm_03_std_2xCO2_2256.',
    '_history_template': '%s ;rewrote data to be consistent with <activity_id> for variable <variable_id> found in table <table_id>.',
    '#_OUTPUT_PATHS': '***** pathing/templates for output files *****',
    '#_output_template_NOTE': '***** PCMDI/cmor 4e7f1f3d731077b7f65c188edefac924cc3e2779 Test/test_cmor_CMIP7.py L47 *****',
    '#_output': '***** Root directory for output (can be either a relative or full path) *****',
    'outpath': '.',
    '#_output_path_template': '***** Template for output path directory using tables keys or global attributes, these should follow the relevant data reference syntax *****',
    'output_path_template': '<activity_id><source_id><experiment_id><member_id><variable_id><branding_suffix><grid_label>',
    '#_output_file_template': '***** Template for output filename using tables keys or global attributes, these should follow the relevant data reference syntax *****',
    'output_file_template': '<variable_id><branding_suffix><frequency><region><grid_label><source_id><experiment_id><variant_label>',
    '#_INPUT_CONFIG_PATHS': '***** pathing/templates for input configuration files holding controlled vocabularies *****',
    '_controlled_vocabulary_file': '../tables-cvs/cmor-cvs.json',
    '_AXIS_ENTRY_FILE': 'CMIP7_coordinate.json',
    '_FORMULA_VAR_FILE': 'CMIP7_formula_terms.json'
}
# pylint: enable=line-too-long


# ── experiment-config fixtures ──────────────────────────────────────────────
@pytest.fixture(autouse=True, scope='session')
def _write_exp_configs():
    """Write both experiment-config JSONs to ROOTDIR at the start of every session.

    The JSON data lives in this module (_CMIP6_EXP_CONFIG_DATA /
    _CMIP7_EXP_CONFIG_DATA) so the on-disk files are no longer tracked by git.
    This session-scoped autouse fixture materializes fresh copies before any
    test that needs them runs, and cleans them up afterwards.
    """
    EXP_CONFIG.write_text(json.dumps(_CMIP6_EXP_CONFIG_DATA, indent=4))
    EXP_CONFIG_CMIP7.write_text(json.dumps(_CMIP7_EXP_CONFIG_DATA, indent=4))
    EXP_CONFIG_CMIP6PLUS.write_text(json.dumps(_CMIP6PLUS_EXP_CONFIG_DATA, indent=4))
    yield
    # restore pristine copies so later sessions (or re-runs) start clean
    EXP_CONFIG.write_text(json.dumps(_CMIP6_EXP_CONFIG_DATA, indent=4))
    EXP_CONFIG_CMIP7.write_text(json.dumps(_CMIP7_EXP_CONFIG_DATA, indent=4))
    EXP_CONFIG_CMIP6PLUS.write_text(json.dumps(_CMIP6PLUS_EXP_CONFIG_DATA, indent=4))


@pytest.fixture
def cmip6_exp_config(tmp_path):
    """Write the CMIP6 experiment config JSON to a temp file and return its path."""
    path = tmp_path / 'CMOR_input_example.json'
    path.write_text(json.dumps(_CMIP6_EXP_CONFIG_DATA, indent=4))
    return str(path)


@pytest.fixture
def cmip7_exp_config(tmp_path):
    """Write the CMIP7 experiment config JSON to a temp file and return its path."""
    path = tmp_path / 'CMOR_CMIP7_input_example.json'
    path.write_text(json.dumps(_CMIP7_EXP_CONFIG_DATA, indent=4))
    return str(path)


# ── ncgen helper ────────────────────────────────────────────────────────────
def ncgen(cdl_path, nc_path):
    """Run ncgen3 to convert a CDL file into a NetCDF-4 file.

    Parameters
    ----------
    cdl_path : str or Path
        Full path to the CDL source file.
    nc_path : str or Path
        Full path where the NetCDF-4 file will be written.
    """
    cdl_path = Path(cdl_path)
    nc_path = Path(nc_path)
    assert cdl_path.exists(), f'CDL file not found: {cdl_path}'

    if nc_path.exists():
        nc_path.unlink()

    subprocess.run(
        ['ncgen3', '-k', 'netCDF-4', '-o', str(nc_path), str(cdl_path)],
        check=True,
    )
    assert nc_path.exists(), f'ncgen3 failed to create {nc_path}'


def _ncgen(cdl_name, nc_path):
    """Run ncgen3 for a CDL file under ``ROOTDIR/reduced_ascii_files``."""
    ncgen(ROOTDIR / 'reduced_ascii_files' / cdl_name, nc_path)


# ── session-scoped fixtures ─────────────────────────────────────────────────
@pytest.fixture(scope='session')
def cli_sos_nc_file():
    """Generate the sos NetCDF file from CDL (session-scoped)."""
    INDIR.mkdir(parents=True, exist_ok=True)
    nc_path = INDIR / SOS_NC_FILENAME
    _ncgen('reduced_ocean_monthly_1x1deg.199301-199302.sos.cdl', nc_path)
    return str(nc_path)


@pytest.fixture(scope='session')
def cli_sosv2_nc_file(cli_sos_nc_file): # pylint: disable=redefined-outer-name
    """Create a copy of the sos file as sosV2 (session-scoped)."""
    nc_path = INDIR / SOSV2_NC_FILENAME
    if nc_path.exists():
        nc_path.unlink()
    shutil.copy(cli_sos_nc_file, str(nc_path))
    assert nc_path.exists()
    return str(nc_path)


@pytest.fixture(scope='session')
def cli_mapped_nc_file():
    """Generate the sea_sfc_salinity NetCDF file from CDL (session-scoped)."""
    INDIR.mkdir(parents=True, exist_ok=True)
    nc_path = INDIR / MAPPED_NC_FILENAME
    _ncgen('reduced_ocean_monthly_1x1deg.199301-199302.sea_sfc_salinity.cdl', nc_path)
    return str(nc_path)
