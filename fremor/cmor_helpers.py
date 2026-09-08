"""
``fremor``: helper/utility python functions
===========================================

This module provides helper functions for the CMORization workflow in the FRE (Flexible Runtime Environment)
CLI, specifically for use in the cmor_mixer submodule. The utilities here support a variety of common
tasks including:

- Logging and min/max value inspection for masked arrays.
- Extraction and manipulation of variables from netCDF4 datasets.
- File path and directory utilities tailored to FRE conventions.
- Construction of boundary arrays for vertical levels.
- Extraction and filtering of ISO datetime ranges from filenames.
- Detection of ocean grid conventions in datasets.
- Determination of vertical dimension names in datasets.
- Creation of temporary output directories for CMOR products.
- Reading and updating experiment configuration JSON files.

Functions
---------
- ``print_data_minmax(ds_variable, desc)``
- ``from_ds_get_this(from_ds, var_name)``
- ``find_statics_file(bronx_file_path)``
- ``create_lev_bnds(bound_these, with_these)``
- ``get_iso_datetime_ranges(var_filenames, iso_daterange_arr, start, stop)``
- ``check_dataset_for_ocean_grid(ds)``
- ``get_vertical_dimension(ds, target_var)``
- ``create_tmp_dir(outdir, json_exp_config)``
- ``get_json_file_data(json_file_path)``
- ``update_grid_and_label(json_file_path, new_grid_label, new_grid, new_nom_res, output_file_path)``
- ``update_calendar_type(json_file_path, new_calendar_type, output_file_path)``
- ``check_path_existence(some_path)``
- ``resolve_mip_era_table_resource(json_table_config, mip_era, resource)``
- ``iso_to_bronx_chunk(cmor_chunk_in)``
- ``conv_mip_to_bronx_freq(cmor_table_freq)``
- ``get_bronx_freq_from_mip_table(json_table_config)``
- ``filter_brands(brands, target_var, mip_var_cfgs, has_time_bnds, input_vert_dim)``

Notes
-----
These functions aim to encapsulate frequently repeated logic in the CMOR workflow, improving code
readability, maintainability, and robustness.
"""

import glob
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional, List, Union

import numpy as np
from netCDF4 import Dataset, Variable

from .cmor_constants import ( ARCHIVE_GOLD_DATA_DIR, CMIP7_GOLD_OCEAN_FILE_STUB, # CMIP6_GOLD_OCEAN_FILE_STUB,
                              INPUT_TO_MIP_VERT_DIM,
                              MIP_ERA_RESOURCES, MIP_ERA_RESOURCE_FALLBACKS )

fre_logger = logging.getLogger(__name__)

# CF calendar aliases mapped to their canonical equivalents
CF_CALENDAR_ALIASES = {
    'noleap': '365_day',
    'all_leap': '366_day',
    'standard': 'gregorian',
}


def normalize_calendar(calendar: Optional[str]) -> Optional[str]:
    """
    Normalize a calendar string to its canonical CF name, resolving common aliases.

    :param calendar: Calendar string (e.g., 'noleap', '365_day', 'standard').
    :type calendar: str, optional
    :return: Canonical calendar string or None if input is None.
    :rtype: str or None
    """
    if calendar is None:
        return None
    calendar_lc = str(calendar).lower()
    return CF_CALENDAR_ALIASES.get(calendar_lc, calendar_lc)

def calendars_are_equivalent(cal1: Optional[str], cal2: Optional[str]) -> bool:
    """
    Return True if two CF calendar names refer to the same calendar, accounting for aliases.

    :param cal1: First CF calendar name (case-insensitive).
    :type cal1: str, optional
    :param cal2: Second CF calendar name (case-insensitive).
    :type cal2: str, optional
    :return: True if both names refer to the same calendar, False otherwise.
    :rtype: bool
    """
    return normalize_calendar(cal1) == normalize_calendar(cal2)

def get_time_calendar_value(time_var) -> Optional[str]:
    """
    Read a time variable's calendar/calendar_type attribute and normalize aliases.

    :param time_var: netCDF-like time variable with optional calendar attributes.
    :type time_var: Any
    :return: Normalized calendar string or None if not present.
    :rtype: str or None
    """
    calendar_val = None
    try:
        calendar_val = str(time_var.calendar).lower()
    except Exception:
        fre_logger.debug('could not find calendar attribute on time axis. moving on.')

    if calendar_val is None:
        try:
            calendar_val = str(time_var.calendar_type).lower()
        except Exception:
            fre_logger.debug('could not find calendar_type attribute on time axis. moving on.')

    return normalize_calendar(calendar_val)


def print_data_minmax( ds_variable: Optional[np.ma.core.MaskedArray] = None,
                       desc: Optional[str] = None) -> None:
    """
    Log the minimum and maximum values of a numpy MaskedArray along with a description.

    :param ds_variable: The data array whose min/max is to be logged.
    :type ds_variable: numpy.ma.core.MaskedArray, optional
    :param desc: Description of the data.
    :type desc: str, optional

    :return: None
    :rtype: None

    .. note:: If the data cannot be logged, a warning is issued.
    """
    try:
        fre_logger.info('info for \n desc = %s \n %s', desc, type(ds_variable))
        fre_logger.info('%s < %s < %s', ds_variable.min(), desc, ds_variable.max())
    except Exception:
        fre_logger.warning('could not print min/max entries for desc = %s', desc)


def from_ds_get_this( from_ds: Dataset,
                      var_name: str) -> Optional[np.ndarray]:
    """
    Retrieve and return a copy of a variable from a netCDF4.Dataset-like object.

    :param from_ds: The source dataset object.
    :type from_ds: netCDF4.Dataset
    :param var_name: The variable name to extract from the dataset.
    :type var_name: str
    :return: A copy of the requested variable's data, or None if not found.
    :rtype: np.ndarray or None

    .. note:: Logs a warning if the variable is not found.
    """
    try:
        return from_ds[var_name][:].copy()
    except Exception:
        fre_logger.exception('could not retrieve variable: %s\n returning None!\n', var_name)
        return None


def find_gold_ocean_statics_file(put_copy_here: Optional[str] = None) -> Optional[str]:
    """
    Locate (and if necessary copy) the gold-standard OM5_025 ocean_static.nc file
    from the GFDL archive into a user-writable directory.

    :param put_copy_here: Directory root under which a mirror of the archive
        sub-path will be created and the file copied into.
    :type put_copy_here: str or None
    :return: Absolute path to the local working copy of ocean_static.nc,
        or None if the file could not be obtained.
    :rtype: str or None

    .. note:: The archive path is hard-coded to the OM5_025 dataset on GFDL systems.
    """
    archive_gold_file = (
        f'{ARCHIVE_GOLD_DATA_DIR}/{CMIP7_GOLD_OCEAN_FILE_STUB}'
    )
    fre_logger.debug('ARCHIVE_GOLD_DATA_DIR=%s', ARCHIVE_GOLD_DATA_DIR)
    fre_logger.debug('archive_gold_file=%s', archive_gold_file)

    if not Path(archive_gold_file).exists():
        fre_logger.error('ERROR gold archive file does not exist: %s', archive_gold_file)
        fre_logger.error('ERROR this file should probably exist.')
        fre_logger.warning('WARNING i will fallback to using buggy ocean_statics'
                           ' files in pp directories out of desperation')
        return None


    if put_copy_here is None:
        fre_logger.warning('put_copy_here is None, cannot stage gold ocean statics file')
        return None

    # mirror the archive sub-path under put_copy_here
    # e.g.  /archive/gold/datasets/OM5_025/…  ->  datasets/OM5_025/…
    #try:
    new_dir_tree = CMIP7_GOLD_OCEAN_FILE_STUB # '/'.join(archive_gold_file.split('/')[3:])
    fre_logger.debug('new_dir_tree=%s', new_dir_tree)
    #except Exception:
    #    fre_logger.error('could not derive sub-path from archive_gold_file')
    #    return None

    working_copy_dir = f'{put_copy_here}/{Path(new_dir_tree).parent}'
    Path(working_copy_dir).mkdir(parents=True, exist_ok=True)
    working_copy = f'{working_copy_dir}/{Path(archive_gold_file).name}'

    # guard: if a stale directory exists where the file should be (from a prior buggy mkdir),
    # remove it so the copy can succeed
    if Path(working_copy).exists():
        if Path(working_copy).is_dir():
            fre_logger.warning('prior buggy mkdir suspected- removing directory instead of the expected file')
            shutil.rmtree(working_copy)
            fre_logger.warning('dir removed, moving on')
        else:
            fre_logger.warning('a previous copy of the ocean statics file exists, not re-copying!')
            return working_copy

    if not Path(working_copy).is_file():
        fre_logger.info('copying archived golden statics file to\n  %s', working_copy)
        try:
            subprocess.run(['cp', archive_gold_file, working_copy], shell=False, check=True)
        except subprocess.CalledProcessError as exc:
            fre_logger.warning('cp of gold statics file failed: %s', exc)
            return None

    if Path(working_copy).is_file():
        fre_logger.info('gold ocean statics file available at %s', working_copy)
        return working_copy

    fre_logger.warning('gold ocean statics file not available after copy attempt')
    return None

# note, the awkward spacing of the docstring below is for the way sphinx renders reStructuredText, do not change!
def find_statics_file( bronx_file_path: str) -> Optional[str]:
    """
    Attempt to find the corresponding statics file given the path to a FRE-bronx output file. The code assumes
    the output file is in a FRE-bronx directory structure when trying to access the statics file. The structure is
    mocked in this package within the `fre/tests/test_files/ascii_files/mock_archive` directory structure. `cd`'ing
    there and using the command `tree` will reveal the mocked directory structure, something like:


    <STEM>/<EXP_NAME>/<PLATFORM>-<TARGET>/

    └── pp

        ├── component

            ├── realm_frequency.static.nc

            └── ts

                └── frequency

                    └── chunk_size

                        └── component.YYYYMM-YYYYMM.var.nc


    :param bronx_file_path: File path to use as a reference for statics file location.
    :type bronx_file_path: str
    :return: Path to the statics file if found, else None.
    :rtype: str or None

    .. note:: The function searches upward in the directory structure until it finds a 'pp' directory, then globs
              for '*static*.nc' files.
    """
    bronx_file_path_elem = bronx_file_path.split('/')
    num_elem = len(bronx_file_path_elem)
    fre_logger.debug('bronx_file_path_elem = \n%s\n', bronx_file_path_elem)
    while bronx_file_path_elem[num_elem-2] != 'pp':
        bronx_file_path_elem.pop()
        num_elem = num_elem-1
    statics_path = '/'.join(bronx_file_path_elem)
    fre_logger.debug('going to glob the following path for a statics file: \n%s\n', statics_path)
    fre_logger.debug('the call is going to be:')
    fre_logger.debug('\n glob.glob(%s)  \n', statics_path+'/*static*.nc')

    statics_file_glob = glob.glob(statics_path+'/*static*.nc') # update to use component TODO
    fre_logger.debug('the output glob looks like: %s', statics_file_glob)
    if len(statics_file_glob) == 1:
        return statics_file_glob[0]

    fre_logger.warning('no statics file found, returning None')
    return None


def create_lev_bnds( bound_these: Variable = None,
                     with_these: Variable = None) -> np.ndarray:
    """
    Create a vertical level bounds array for a set of levels.

    :param bound_these: netCDF4 Variable with a numpy array representing vertical levels
    :type bound_these: netCDF4.Variable
    :param with_these: netCDF4 Variable with a numpy array representing level bounds, one longer than bound_these
    :type with_these: netCDF4.Variable
    :raises ValueError: If the length of with_these is not len(bound_these) + 1.
    :return: Array of shape (len(bound_these), 2), where each row gives the bounds for a level.
    :rtype: np.ndarray

    .. note:: Logs debug information about the input and output arrays.
    """
    if len(with_these) != (len(bound_these) + 1):
        raise ValueError('failed creating bnds on-the-fly :-(')
    fre_logger.debug('bound_these = \n%s', bound_these)
    fre_logger.debug('with_these = \n%s', with_these)

    the_bnds = np.arange(len(bound_these)*2).reshape(len(bound_these), 2)
    for i in range(0, len(bound_these)):
        the_bnds[i][0] = with_these[i]
        the_bnds[i][1] = with_these[i+1]
    fre_logger.info('the_bnds = \n%s', the_bnds)
    return the_bnds


def get_iso_datetime_ranges( var_filenames: List[str],
                             iso_daterange_arr: Optional[List[str]] = None,
                             start: Optional[str] = None,
                             stop: Optional[str] = None) -> None:
    """
    Extract and append ISO datetime ranges from filenames, filtered by start/stop years if specified.

    :param var_filenames: Filenames, some of which contain ISO datetime ranges (e.g. 'YYYYMMDD-YYYYMMDD').
    :type var_filenames: list of str
    :param iso_daterange_arr: List to append found datetime ranges to; modified in-place.
    :type iso_daterange_arr: list of str
    :param start: Start year in 'YYYY' format; only ranges within/after this year are included.
    :type start: str, optional
    :param stop: Stop year in 'YYYY' format; only ranges within/before this year are included.
    :type stop: str, optional
    :raises ValueError: If iso_daterange_arr is not provided or if no datetime ranges are found.
    :return: None
    :rtype: None

    .. note:: This function modifies iso_daterange_arr in-place.
    """
    fre_logger.debug('start = %s', start)
    fre_logger.debug('stop = %s', stop)
    start_stop_filter = False
    stop_yr_int, start_yr_int = None, None
    if start is not None and len(start) == 4:
        start_yr_int = int(start)
        start_stop_filter = True
    if stop is not None and len(stop) == 4:
        stop_yr_int = int(stop)
        start_stop_filter = True
    fre_logger.debug('start_yr_int = %s', start_yr_int)
    fre_logger.debug(' stop_yr_int = %s', stop_yr_int)

    if iso_daterange_arr is None:
        raise ValueError(
            'this function requires the list one desires to fill with datetime ranges from filenames')

    for filename in var_filenames:
        fre_logger.debug('filename = %s', filename)
        iso_daterange = filename.split('.')[-3]  # '????????-????????'
        fre_logger.debug('iso_daterange = %s', iso_daterange)

        if start_stop_filter:
            iso_datetimes = iso_daterange.split('-')
            fre_logger.debug('iso_datetimes = %s', iso_datetimes)
            if start is not None and int(iso_datetimes[0][0:4]) < start_yr_int:
                continue
            if stop is not None and int(iso_datetimes[1][0:4]) > stop_yr_int:
                continue

        if iso_daterange not in iso_daterange_arr:
            iso_daterange_arr.append(iso_daterange)

    iso_daterange_arr.sort()

    if len(iso_daterange_arr) < 1:
        raise ValueError('iso_daterange_arr has length 0! i need to find at least one datetime range!')


def check_dataset_for_ocean_grid( ds: Dataset) -> bool:
    """
    Check if a netCDF4.Dataset uses an ocean grid (i.e., contains 'xh' or 'yh' variables).

    :param ds: Dataset to be checked.
    :type ds: netCDF4.Dataset
    :return: True if ocean grid variables are present, otherwise False.
    :rtype: bool

    .. note:: Logs a warning if an ocean grid is detected.
    """
    ds_var_keys = list(ds.variables.keys())
    uses_ocean_grid = any(['xh' in ds_var_keys, 'yh' in ds_var_keys])
    if uses_ocean_grid:
        fre_logger.warning(
            '\n----------------------------------------------------------------------------------\n'
            ' \'xh\' found in var_list: ocean grid req\'d\n'
            '     sometimes i don\'t cmorize right! check me!\n'
            '----------------------------------------------------------------------------------\n'
        )
    return uses_ocean_grid


def get_vertical_dimension( ds: Dataset,
                            target_var: str) -> Union[str, int]:
    """
    Determine the vertical dimension for a variable in a netCDF4.Dataset.

    :param ds: Dataset containing variables.
    :type ds: netCDF4.Dataset
    :param target_var: Name of the variable to inspect.
    :type target_var: str
    :return: Name of the vertical dimension if found, otherwise 0.
    :rtype: str or int

    .. note:: Returns 0 if no vertical dimension is detected.
    """
    vert_dim = 0
    for name, variable in ds.variables.items():
        if name != target_var:
            continue
        dims = variable.dimensions
        for dim in dims:
            if dim.lower() == 'landuse':
                vert_dim = dim
                break
            dim_attrs = ds[dim].ncattrs()
            axis_attr = None
            if 'axis' in dim_attrs:
                axis_attr = ds[dim].axis
                fre_logger.debug('dim %s has axis=%s', dim, axis_attr)
            elif 'cartesian_axis' in dim_attrs:
                axis_attr = ds[dim].cartesian_axis
                fre_logger.debug('dim %s has cartesian_axis=%s', dim, axis_attr)
            else:
                fre_logger.debug('dim %s has neither axis nor cartesian_axis attr, skipping', dim)
            if not (axis_attr and axis_attr == 'Z'):
                continue
            vert_dim = dim
    return vert_dim


def resolve_mip_era_table_resource(json_table_config: str,
                                   mip_era: str,
                                   resource: str) -> str:
    """
    Locate an auxiliary CMOR table that belongs with ``json_table_config``.

    CMOR resolves the CV, coordinate and formula-terms tables relative to the directory of
    the MIP table being loaded (``cmor_load_table`` builds ``dirname(<table>)/<name>``), so
    the grids table is looked up the same way here. Where a table repo keeps its auxiliary
    tables in a sibling directory -- CMIP6Plus's ``mip-cmor-tables`` puts them in
    ``Auxillary_files/`` while the variable tables live in ``Tables/`` -- the name carries the
    ``../`` prefix, exactly as it must in the experiment config.

    :param json_table_config: Path to the MIP table CMOR is loading.
    :type json_table_config: str
    :param mip_era: MIP era of the experiment config, e.g. 'CMIP6', 'CMIP6PLUS', 'CMIP7'.
    :type mip_era: str
    :param resource: Which resource to resolve: 'cv', 'coordinate', 'formula_terms' or 'grids'.
    :type resource: str
    :raises ValueError: If mip_era or resource is not recognized.
    :raises FileNotFoundError: If no candidate name exists next to the MIP table.
    :return: Absolute path to the resource.
    :rtype: str
    """
    era = mip_era.upper()
    if era not in MIP_ERA_RESOURCES:
        raise ValueError(f'unrecognized mip_era = {mip_era}. '
                         f'expected one of {sorted(MIP_ERA_RESOURCES)}')
    if resource not in MIP_ERA_RESOURCES[era]:
        raise ValueError(f'unrecognized resource = {resource}. '
                         f'expected one of {sorted(MIP_ERA_RESOURCES[era])}')

    table_dir = Path(json_table_config).parent
    candidates = [MIP_ERA_RESOURCES[era][resource]]
    candidates += [ cand
                    for cand in MIP_ERA_RESOURCE_FALLBACKS.get(era, {}).get(resource, [])
                    if cand not in candidates ]

    tried, unusable = [], []
    for candidate in candidates:
        candidate_path = table_dir / candidate
        tried.append(str(candidate_path))
        if not candidate_path.exists():
            continue
        # cmor.set_table() only accepts a table that declares Header/table_id; without it CMOR
        # fails with the opaque "Invalid table: N , not loaded yet!". mip-cmor-tables ships its
        # grids table with no Header at all, so keep looking when that is what we found.
        if resource == 'grids' and not _table_declares_table_id(candidate_path):
            unusable.append(str(candidate_path))
            fre_logger.warning('%s declares no Header/table_id, so cmor.set_table cannot use it. '
                               'looking for another %s table.', candidate_path, resource)
            continue
        fre_logger.info('resolved %s %s table: %s', era, resource, candidate_path)
        return str(candidate_path.resolve())

    message = (f'could not find a usable {era} {resource} table alongside {json_table_config}.\n'
               '  tried:\n    ' + '\n    '.join(tried) + '\n')
    if unusable:
        message += ('  found but unusable (no Header/table_id):\n    '
                    + '\n    '.join(unusable) + '\n')
    if era == 'CMIP6PLUS' and resource == 'grids':
        message += ('  PCMDI/mip-cmor-tables ships no grids table CMOR can set: its\n'
                    '  Auxillary_files/MIP_grids.json has no Header. Copy CMIP6_grids.json from\n'
                    '  pcmdi/cmip6-cmor-tables next to your MIP tables -- fremor processes\n'
                    '  CMIP6Plus as a CMIP6 case, and its projected-grid axes are the same.\n')
    else:
        message += f'  check that your MIP tables are a complete {era} table set.\n'
    raise FileNotFoundError(message)


def _table_declares_table_id(table_path: Path) -> bool:
    """
    Return True if a CMOR table JSON declares ``Header/table_id``.

    :param table_path: Path to the table JSON.
    :type table_path: Path
    :return: Whether the table can be handed to ``cmor.set_table``.
    :rtype: bool
    """
    try:
        with open(table_path, 'r', encoding='utf-8') as table_file:
            return bool(json.load(table_file).get('Header', {}).get('table_id'))
    except (OSError, ValueError) as exc:
        fre_logger.warning('could not read %s (%s)', table_path, exc)
        return False


def create_tmp_dir( outdir: str,
                    json_exp_config: Optional[str] = None) -> str:
    """
    Create a temporary directory for output, possibly informed by a JSON experiment config.

    :param outdir: Base output directory.
    :type outdir: str
    :param json_exp_config: Path to a JSON config file with an 'outpath' key.
    :type json_exp_config: str, optional
    :raises OSError: If the temporary directory cannot be created.
    :return: Path to the created temporary directory.
    :rtype: str

    .. note:: If json_exp_config is provided and contains 'outpath', a subdirectory is also created.
    """
    outdir_from_exp_config = None
    if json_exp_config is not None:
        with open(json_exp_config, 'r', encoding='utf-8') as table_config_file:
            try:
                outdir_from_exp_config = json.load(table_config_file)['outpath']
            except Exception:
                fre_logger.warning(
                    'could not read outdir from json_exp_config. the cmor module will throw a toothless warning')

    tmp_dir = str(Path(f'{outdir}/CMOR_tmp/').resolve())
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        if outdir_from_exp_config is not None:
            fre_logger.info('attempting to create %s dir in tmp_dir targ', outdir_from_exp_config)
            try:
                os.makedirs(tmp_dir + '/' + outdir_from_exp_config, exist_ok=True)
            except Exception:
                fre_logger.info('attempting to create %s dir in tmp_dir targ did not work', outdir_from_exp_config)
                fre_logger.info('... attempt to avoid a toothless cmor warning failed... moving on')
    except Exception as exc:
        raise OSError(f'problem creating tmp output directory {tmp_dir}. stop.') from exc

    return tmp_dir


def get_json_file_data( json_file_path: Optional[str] = None) -> dict:
    """
    Load and return the contents of a JSON file.

    :param json_file_path: Path to the JSON file.
    :type json_file_path: str
    :raises FileNotFoundError: If the file cannot be opened.
    :return: Parsed data from the JSON file.
    :rtype: dict
    """
    try:
        with open(json_file_path, 'r', encoding='utf-8') as json_config_file:
            return json.load(json_config_file)
    except FileNotFoundError:
        fre_logger.error(
            'ERROR: a file was not found at json_file_path.\n'
            '       json_file_path = %s', json_file_path
        )
        raise
    except json.JSONDecodeError:
        fre_logger.error(
            'ERROR: json file at %s likely has a syntax error', json_file_path
        )
        raise
    except Exception:
        fre_logger.error(
            'unknown exception'
        )
        raise


def update_grid_and_label( json_file_path: str,
                           new_grid_label: str,
                           new_grid: str,
                           new_nom_res: str,
                           output_file_path: Optional[str] = None) -> None:
    """
    Update the 'grid_label', 'grid', and 'nominal_resolution' fields in a JSON experiment config.

    :param json_file_path: Path to the input JSON file.
    :type json_file_path: str
    :param new_grid_label: New value for the 'grid_label' field.
    :type new_grid_label: str
    :param new_grid: New value for the 'grid' field.
    :type new_grid: str
    :param new_nom_res: New value for the 'nominal_resolution' field.
    :type new_nom_res: str
    :param output_file_path: Path to save the updated JSON file. If None, overwrites the original file.
    :type output_file_path: str, optional
    :raises FileNotFoundError: If the input JSON file does not exist.
    :raises KeyError: If a required field is not found in the JSON file.
    :raises ValueError: If any input value is None.
    :raises json.JSONDecodeError: If the JSON file cannot be decoded.
    :return: None
    :rtype: None

    .. note:: The function logs before and after values, and overwrites the input file unless an output path is given.
    """
    if None in [new_grid_label, new_grid, new_nom_res]:
        fre_logger.error(
            'grid/grid_label/nom_res updating requested for exp_config file, but one of them is None\n'
            'bailing...!')
        raise ValueError

    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)

        try:
            fre_logger.info('Original grid: %s', data['grid'])
            data['grid'] = new_grid
            fre_logger.info('Updated grid: %s', data['grid'])
        except KeyError as e:
            fre_logger.error('Failed to update grid: %s', e)
            raise KeyError('Error while updating grid. Ensure the field exists and is modifiable.') from e

        try:
            fre_logger.info('Original grid_label: %s', data['grid_label'])
            data['grid_label'] = new_grid_label
            fre_logger.info('Updated grid_label: %s', data['grid_label'])
        except KeyError as e:
            fre_logger.error('Failed to update grid_label: %s', e)
            raise KeyError('Error while updating grid_label. Ensure the field exists and is modifiable.') from e

        try:
            fre_logger.info('Original nominal_resolution: %s', data['nominal_resolution'])
            data['nominal_resolution'] = new_nom_res
            fre_logger.info('Updated nominal_resolution: %s', data['nominal_resolution'])
        except KeyError as e:
            fre_logger.error('Failed to update nominal_resolution: %s', e)
            raise KeyError('Error updating nominal_resolution. Ensure the field exists and is modifiable.') from e

        output_file_path = output_file_path or json_file_path

        with open(output_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)

        fre_logger.info('Successfully updated fields and saved to %s', output_file_path)

    except FileNotFoundError:
        fre_logger.error('The file %s does not exist.', json_file_path)
        raise
    except json.JSONDecodeError:
        fre_logger.error('Failed to decode JSON from the file %s.', json_file_path)
        raise
    except Exception as e:
        fre_logger.error('An unexpected error occurred: %s', e)
        raise


def update_calendar_type( json_file_path: str,
                          new_calendar_type: str,
                          output_file_path: Optional[str] = None) -> None:
    """
    Update the 'calendar' field in a JSON experiment config file.

    :param json_file_path: Path to the input JSON file.
    :type json_file_path: str
    :param new_calendar_type: New value for the 'calendar' field.
    :type new_calendar_type: str
    :param output_file_path: Path to save the updated JSON file. If None, overwrites the original file.
    :type output_file_path: str, optional
    :raises FileNotFoundError: If the input JSON file does not exist.
    :raises KeyError: If the 'calendar' field is not found in the JSON file.
    :raises ValueError: If new_calendar_type is None.
    :raises json.JSONDecodeError: If the JSON file cannot be decoded.
    :return: None
    :rtype: None

    .. note:: The function logs before and after values, and overwrites the input file unless an output path is given.
              CF calendar aliases (e.g., 'noleap') are normalized to their canonical CF names (e.g., '365_day').
    """
    if new_calendar_type is None:
        fre_logger.error(
            'calendar_type updating requested for exp_config file, but one of them is None\n'
            'bailing...!')
        raise ValueError

    normalized_calendar_type = normalize_calendar(new_calendar_type)

    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)

        try:
            fre_logger.info('Original calendar: %s', data['calendar'])
            if normalized_calendar_type != str(new_calendar_type).lower():
                fre_logger.info('Normalizing calendar alias %s to %s',
                                new_calendar_type, normalized_calendar_type)
            data['calendar'] = normalized_calendar_type
            fre_logger.info('Updated calendar: %s', data['calendar'])
        except KeyError as e:
            fre_logger.error('Failed to update calendar: %s', e)
            raise KeyError('Error while updating calendar. Ensure the field exists and is modifiable.') from e

        output_file_path = output_file_path or json_file_path

        with open(output_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4)

        fre_logger.info('Successfully updated fields and saved to %s', output_file_path)

    except FileNotFoundError:
        fre_logger.error('The file %s does not exist.', json_file_path)
        raise
    except json.JSONDecodeError:
        fre_logger.error('Failed to decode JSON from the file %s.', json_file_path)
        raise
    except Exception as e:
        fre_logger.error('An unexpected error occurred: %s', e)
        raise

def check_path_existence(some_path: str):
    """
    Check if the given path exists, raising FileNotFoundError if not.

    :param some_path: A string representing a filesystem path (relative or absolute).
    :type some_path: str
    :raises FileNotFoundError: If the path does not exist.
    """
    if not Path(some_path).exists():
        raise FileNotFoundError(f'does not exist:  {some_path}')

def iso_to_bronx_chunk(cmor_chunk_in: str) -> str:
    """
    Convert an ISO8601 duration string (e.g., 'P5Y') to FRE-bronx-style chunk string (e.g., '5yr').

    :param cmor_chunk_in: ISO8601 formatted string representing a time interval (must start with 'P' and end with 'Y').
    :type cmor_chunk_in: str
    :raises ValueError: If the input does not follow the expected ISO format.
    :return: FRE-bronx chunk string.
    :rtype: str
    """
    fre_logger.debug('cmor_chunk_in = %s', cmor_chunk_in)
    if cmor_chunk_in[0] == 'P' and cmor_chunk_in[-1] == 'Y':
        bronx_chunk = f'{cmor_chunk_in[1:-1]}yr'
    else:
        raise ValueError('problem with converting to bronx chunk from the cmor chunk. check cmor_yamler.py')
    fre_logger.debug('bronx_chunk = %s', bronx_chunk)
    return bronx_chunk

def conv_mip_to_bronx_freq(cmor_table_freq: str) -> Optional[str]:
    """
    Convert a MIP table frequency string to its FRE-bronx equivalent using a lookup table.

    :param cmor_table_freq: Frequency string as found in a MIP table (e.g., 'mon', 'day', 'yr', etc.).
    :type cmor_table_freq: str
    :raises KeyError: If the frequency string is not recognized as valid.
    :return: FRE-bronx frequency string, or None if not mappable.
    :rtype: str or None
    """
    cmor_to_bronx_dict = {
        '1hr'    : '1hr',
        '1hrCM'  : None,
        '1hrPt'  : None,
        '3hr'    : '3hr',
        '3hrPt'  : None,
        '6hr'    : '6hr',
        '6hrPt'  : None,
        'day'    : 'daily',
        'dec'    : None,
        'fx'     : None,
        'mon'    : 'monthly',
        'monC'   : None,
        'monPt'  : None,
        'subhrPt': None,
        'yr'     : 'annual',
        'yrPt'   : None
    }
    bronx_freq = cmor_to_bronx_dict.get(cmor_table_freq)
    if bronx_freq is None:
        fre_logger.warning('MIP table frequency = %s does not have a FRE-bronx equivalent', cmor_table_freq)
    if cmor_table_freq not in cmor_to_bronx_dict:
        raise KeyError(f'MIP table frequency = {cmor_table_freq} is not a valid MIP frequency')
    return bronx_freq

def get_bronx_freq_from_mip_table(json_table_config: str) -> str:
    """
    Extract the frequency of data from a CMIP MIP table (JSON), returning its FRE-bronx equivalent.

    :param json_table_config: Path to a JSON MIP table file with 'variable_entry' metadata.
    :type json_table_config: str
    :raises KeyError: If the frequency cannot be found or mapped.
    :return: FRE-bronx frequency string.
    :rtype: str
    """
    table_freq = None
    with open(json_table_config, 'r', encoding='utf-8') as table_config_file:
        table_config_data = json.load(table_config_file)
        for var_entry in table_config_data['variable_entry']:
            try:
                table_freq = table_config_data['variable_entry'][var_entry]['frequency']
                break
            except Exception as exc:
                raise KeyError('no frequency in table under variable_entry. this may be a CMIP7 table.') from exc

    bronx_freq = conv_mip_to_bronx_freq(table_freq)
    return bronx_freq


def filter_brands( brands: list,
                   target_var: str,
                   mip_var_cfgs: dict,
                   has_time_bnds: bool,
                   input_vert_dim: Union[str, int],
                   cell_methods: Optional[str] = None ) -> str:
    """
    Disambiguate multiple CMIP7 variable brands by comparing input data
    properties against each candidate brand's MIP dimension list.

    Two filters are applied in sequence:

    1. **Time type**: The presence or absence of time bounds in the input data
       is compared to whether the brand's MIP dimensions contain ``time``
       (time-mean, has bounds) or ``time1`` (instantaneous, no bounds).
    2. **Vertical coordinate**: The input data's vertical dimension name is
       mapped to the corresponding MIP dimension name (via
       ``INPUT_TO_MIP_VERT_DIM``) and brands whose MIP dimensions do not
       include it are excluded.

    :param brands: List of candidate brand strings to filter.
    :type brands: list[str]
    :param target_var: The base variable name (before the brand suffix).
    :type target_var: str
    :param mip_var_cfgs: The full MIP table config dict (must contain
        ``'variable_entry'``).
    :type mip_var_cfgs: dict
    :param has_time_bnds: Whether the input dataset contains ``time_bnds``.
    :type has_time_bnds: bool
    :param input_vert_dim: The vertical dimension name from the input dataset,
        or ``0`` if no vertical dimension is present.
    :type input_vert_dim: str or int
    :raises ValueError: If zero or more than one brand survives filtering.
    :return: The single brand string that survived disambiguation.
    :rtype: str
    """
    # map input vertical dim to MIP equivalent
    expected_mip_vert = None
    if input_vert_dim != 0:
        expected_mip_vert = INPUT_TO_MIP_VERT_DIM.get(
            input_vert_dim.lower(), input_vert_dim.lower())

    filtered_brands = []
    for brand in brands:
        mip_key = f'{target_var}_{brand}'
        mip_dims = mip_var_cfgs['variable_entry'][mip_key]['dimensions']

        # time filter
        if has_time_bnds and 'time1' in mip_dims:
            fre_logger.debug('filtering out brand %s: MIP dims contain time1 '
                             'but input data has time_bnds', brand)
            continue
        if not has_time_bnds and 'time' in mip_dims and 'time1' not in mip_dims:
            fre_logger.debug('filtering out brand %s: MIP dims contain time (mean) '
                             'but input data lacks time_bnds', brand)
            continue

        # vertical filter
        if expected_mip_vert is not None and expected_mip_vert not in mip_dims:
            fre_logger.debug('filtering out brand %s: expected MIP vert dim %s '
                             'not found in %s', brand, expected_mip_vert, mip_dims)
            continue

        # cell_methods temporal filter: match brand temporal prefix to input cell_methods
        if cell_methods is not None:
            time_method = None
            if 'time: mean' in cell_methods:
                time_method = 'tavg'
            elif 'time: maximum' in cell_methods:
                time_method = 'tmax'
            elif 'time: minimum' in cell_methods:
                time_method = 'tmin'
            elif 'time: point' in cell_methods:
                time_method = 'tpt'

            if time_method is not None:
                brand_temporal = brand.split('-')[0]
                if brand_temporal != time_method:
                    fre_logger.debug('filtering out brand %s: temporal prefix %s '
                                     'does not match cell_methods %s',
                                     brand, brand_temporal, cell_methods)
                    continue

        filtered_brands.append(brand)

    if len(filtered_brands) == 1:
        fre_logger.info('cmip7 brand disambiguation successful, selected brand: %s',
                        filtered_brands[0])
        return filtered_brands[0]

    if len(filtered_brands) == 0:
        fre_logger.error('cmip7 brand disambiguation eliminated all candidates '
                         'from %s', brands)
        raise ValueError(
            f'multiple brands {brands} found for {target_var}, '
            f'but none survived disambiguation filtering')

    fre_logger.error('cmip7 brand disambiguation could not resolve between '
                     '%s', filtered_brands)
    raise ValueError(
        f'multiple brands {filtered_brands} remain for {target_var} after '
        f'disambiguation \u2014 cannot determine which brand to use')
