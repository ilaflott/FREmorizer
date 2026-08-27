"""
``fremor config``: YAML Generator
=================================

This module powers the ``fremor config`` command, generating a CMOR YAML configuration
file that ``fremor yaml`` can consume. It scans a post-processing directory tree for
available components and time-series data, cross-references found variables against MIP
tables, and produces the structured YAML needed for CMORization.

Functions
---------
- ``cmor_config_subtool(...)``

.. note:: This module was derived from quick_script.py prototyping work.
"""

import glob
import json
import logging
import os
from pathlib import Path
import re

import yaml

from .cmor_finder import make_simple_varlist
from .cmor_constants import EXCLUDED_TABLE_SUFFIXES

fre_logger = logging.getLogger(__name__)


def _bronx_to_iso_chunk(chunk: str) -> str:
    """Convert FRE-bronx chunk syntax like ``5yr`` to CMOR YAML syntax like ``P5Y``.

    This helper intentionally only handles year-based chunks because that is the
    only chunk shape currently emitted by ``fremor config``.
    """
    if chunk.startswith('P') and chunk.endswith('Y'):
        return chunk
    match = re.fullmatch(r'(\d+)yr', str(chunk))
    if match is None:
        raise ValueError(f'chunk must be ISO8601 like P5Y or bronx-style like 5yr, got {chunk}')
    return f'P{match.group(1)}Y'


def _filter_mip_tables(mip_tables_dir: str, mip_era: str):
    """
    Glob MIP table JSON files from the given directory, filtering out
    non-variable-entry tables (grids, coordinates, etc.).

    :param mip_tables_dir: Path to directory containing MIP table JSON files.
    :type mip_tables_dir: str
    :param mip_era: MIP era string, e.g. 'cmip6', 'cmip6plus', or 'cmip7'.
    :type mip_era: str
    :return: List of paths to MIP table JSON files.
    :rtype: list[str]
    """
    era_upper = mip_era.upper()
    if era_upper != 'CMIP6PLUS':
        all_tables = glob.glob(f'{mip_tables_dir}/{era_upper}_*.json')
    else:
        all_tables = glob.glob(f'{mip_tables_dir}/MIP_*.json')

    filtered = []
    for table_path in all_tables:
        table_stem = Path(table_path).stem  # e.g. "CMIP7_ocean"
        suffix = table_stem.split('_', maxsplit=1)[1] if '_' in table_stem else ''
        if suffix not in EXCLUDED_TABLE_SUFFIXES:
            filtered.append(table_path)

    fre_logger.debug('filtered MIP tables (%d of %d): %s',
                     len(filtered), len(all_tables), filtered)
    return filtered


def _load_config_yaml(yamlfile: str) -> dict:
    """
    Load a self-contained CMOR YAML file, as written by ``cmor_config_subtool`` (``fremor
    config``), and return the resolved directories/table_targets that downstream tools
    (``fremor check``, ``fremor map``) need -- so they can derive pp_dir, the MIP tables
    directory, the MIP era, and each component's variable_list path straight from the yaml
    instead of requiring all of that to be passed as separate flags.

    :param yamlfile: Path to a CMOR YAML file produced by ``fremor config``.
    :type yamlfile: str
    :raises FileNotFoundError: If yamlfile, its pp_dir, or its table_dir do not exist.
    :raises ValueError: If yamlfile has no top-level ``cmor`` mapping.
    :return: dict with keys ``mip_era``, ``pp_dir``, ``mip_tables_dir``, ``table_targets``,
        ``start``/``stop`` (the run's year bounds, as written in the yaml -- None if absent),
        ``yaml_doc`` (the fully parsed yaml document, for callers that need to write changes
        -- e.g. a disabled flag toggled in ``fremor map`` -- back to ``yamlfile``).
    :rtype: dict
    """
    if not Path(yamlfile).is_file():
        raise FileNotFoundError(f'yamlfile does not exist: {yamlfile}')

    with open(yamlfile, 'r', encoding='utf-8') as handle:
        yaml_doc = yaml.safe_load(handle)

    if not isinstance(yaml_doc, dict) or 'cmor' not in yaml_doc:
        raise ValueError(
            f"invalid CMOR YAML file '{yamlfile}': expected a top-level mapping "
            "containing a 'cmor' section.")
    cmor_yaml_dict = yaml_doc['cmor']
    directories = cmor_yaml_dict.get('directories') or {}

    pp_dir = os.path.expandvars(directories['pp_dir'])
    if not Path(pp_dir).is_dir():
        raise FileNotFoundError(f'pp_dir from yamlfile does not exist: {pp_dir}')

    mip_tables_dir = os.path.expandvars(directories['table_dir'])
    if not Path(mip_tables_dir).is_dir():
        raise FileNotFoundError(f'mip_tables_dir from yamlfile does not exist: {mip_tables_dir}')

    return {
        'mip_era': cmor_yaml_dict['mip_era'],
        'pp_dir': pp_dir,
        'mip_tables_dir': mip_tables_dir,
        'table_targets': cmor_yaml_dict.get('table_targets') or [],
        'start': cmor_yaml_dict.get('start'),
        'stop': cmor_yaml_dict.get('stop'),
        'yaml_doc': yaml_doc,
    }


def cmor_config_subtool(
        pp_dir: str,
        mip_tables_dir: str,
        mip_era: str,
        exp_config: str,
        output_yaml: str,
        output_dir: str,
        varlist_dir: str,
        pp_comp_glob: str = '*',
        strict_varlist: bool = False,
        freq: str = 'monthly',
        chunk: str = '5yr',
        grid: str = 'g999',
        overwrite: bool = False,
        calendar_type: str = 'noleap'
):
    """
    Generate a CMOR YAML configuration file from a post-processing directory tree.

    Scans ``pp_dir`` for pp-component directories, cross-references found variables
    against MIP tables, writes per-component variable lists, and emits a structured
    YAML that ``fremor yaml`` can later consume.

    :param pp_dir: Root post-processing directory containing per-component subdirectories.
    :type pp_dir: str
    :param pp_comp_glob: glob pattern to use for selecting pp component directory names. default '*'.
    :type pp_comp_glob: str
    :param mip_tables_dir: Directory containing MIP table JSON files.
    :type mip_tables_dir: str
    :param mip_era: MIP era identifier, e.g. 'cmip6', 'cmip6plus' or 'cmip7'.
    :type mip_era: str
    :param exp_config: Path to JSON experiment/input configuration file expected by CMOR.
    :type exp_config: str
    :param output_yaml: Path to write the output CMOR YAML configuration.
    :type output_yaml: str
    :param output_dir: Root output directory for CMORized data.
    :type output_dir: str
    :param varlist_dir: Directory in which per-component variable list JSON files are written.
    :type varlist_dir: str
    :param freq: Temporal frequency string, e.g. 'monthly', 'daily'. Default 'monthly'.
    :type freq: str
    :param chunk: Time chunk string, e.g. '5yr', '10yr'. Default '5yr'.
    :type chunk: str
    :param grid: Grid label anchor name, e.g. 'g999', 'gn'. Default 'g999'.
    :type grid: str
    :param overwrite: If True, overwrite existing variable list files. Default False.
    :type overwrite: bool
    :param calendar_type: Calendar type string, e.g. 'noleap', '360_day'. Default 'noleap'.
    :type calendar_type: str
    :raises FileNotFoundError: If pp_dir or mip_tables_dir do not exist.
    :raises ValueError: If no MIP tables are found after filtering.
    :return: Path to the written output YAML file.
    :rtype: str
    """

    # ---- validate inputs ----
    if not Path(pp_dir).is_dir():
        raise FileNotFoundError(f'pp_dir does not exist: {pp_dir}')
    if not Path(mip_tables_dir).is_dir():
        raise FileNotFoundError(f'mip_tables_dir does not exist: {mip_tables_dir}')
    if not Path(exp_config).is_file():
        raise FileNotFoundError(f'exp_config does not exist: {exp_config}')
    with open(exp_config, encoding='utf-8') as handle:
        exp_config_data = json.load(handle)
    grid_desc = exp_config_data.get('grid')
    nominal_resolution = exp_config_data.get('nominal_resolution')
    chunk_iso = _bronx_to_iso_chunk(chunk)

    # ensure output directories exist
    Path(varlist_dir).mkdir(parents=True, exist_ok=True)
    Path(output_yaml).parent.mkdir(parents=True, exist_ok=True)

    # ---- gather MIP tables ----
    mip_tables = _filter_mip_tables(mip_tables_dir, mip_era)
    if not mip_tables:
        raise ValueError(
            f'no MIP tables found in {mip_tables_dir} for era {mip_era} after filtering')

    # ---- discover pp components ----
    ppcompdirs = sorted(glob.glob(f'{pp_dir}/{pp_comp_glob}'))
    fre_logger.info('found %d entries in pp_dir', len(ppcompdirs))
    if len(ppcompdirs) == 0:
        fre_logger.error('ERROR: no pp component directories found under pp_dir = %s', pp_dir)
        raise FileNotFoundError

    # ---- build YAML lines ----
    lines = [
        '',
        'cmor:',
        '  start: null',
        '  stop: null',
        '  calendar_type:',
        f"    '{calendar_type}'",
        '  mip_era:',
        f"    '{mip_era}'",
        '  exp_json:',
        f"    '{exp_config}'",
        '  directories:',
        '    pp_dir: &pp_dir',
        f"      '{pp_dir}'",
        '    table_dir: &table_dir',
        f"      '{mip_tables_dir}'",
        '    outdir:',
        f"      '{output_dir}'",
        '  table_targets:',
    ]

    era_upper = mip_era.upper()

    for mip_table in sorted(mip_tables):
        table_name = Path(mip_table).stem.split('.')[0].split('_')[1]   # e.g. CMIP7_ocean
        fre_logger.info('processing mip_table = %s', table_name)

        appended_table_header = False

        for entry in ppcompdirs:
            component_name = Path(entry).name
            fre_logger.info('making variable list for %s', component_name)
            variable_list = f'{varlist_dir}/{era_upper}_{table_name}_{component_name}.list'
            fre_logger.info('variable_list = %s', variable_list)

            # optionally regenerate
            if Path(variable_list).exists() and overwrite:
                fre_logger.debug('varlist %s exists, unlinking to recreate because overwrite=True',
                                 Path(variable_list).name)
                Path(variable_list).unlink()

            if not Path(entry).is_dir():
                fre_logger.debug('entry %s is not a directory, skipping', entry)
                continue

            # check for time-series data
            data_series_present = [
                Path(ds).name for ds in glob.glob(f'{entry}/*')
                if Path(ds).is_dir()
            ]
            if 'ts' not in data_series_present:
                fre_logger.debug('no ts directory in %s, skipping', entry)
                continue

            dir_targ = f'{entry}/ts/{freq}/{chunk}'
            if not Path(dir_targ).is_dir():
                fre_logger.debug('target dir %s does not exist, skipping', dir_targ)
                continue

            if len(glob.glob(f'{dir_targ}/*nc')) < 1:
                fre_logger.debug('no nc files in %s, skipping', dir_targ)
                continue

            try:
                make_simple_varlist(
                    dir_targ=dir_targ,
                    return_none_if_no_mip_vars=strict_varlist,
                    output_variable_list=variable_list,
                    json_mip_table=mip_table
                )

            except Exception as exc:
                fre_logger.warning(
                    'variable list creation failed for %s %s %s \nWith exception: %s',
                    dir_targ, variable_list, mip_table, exc
                )
                continue

            if Path(variable_list).exists():
                if not appended_table_header:
                    lines.append('')
                    lines.append(f"    - table_name: '{table_name}'")
                    lines.append(f"      freq: '{freq}'")
                    lines.append('      gridding:')
                    lines.append(f"        grid_label: '{grid}'")
                    lines.append(f"        grid_desc: '{grid_desc}'")
                    lines.append(f"        nom_res: '{nominal_resolution}'")
                    lines.append( '      target_components:')
                    appended_table_header = True

                lines.append(f"        - component_name: '{component_name}'")
                lines.append(f"          variable_list: '{variable_list}'")
                lines.append("          data_series_type: 'ts'")
                lines.append(f"          chunk: '{chunk_iso}'")


    # ---- write output YAML ----
    if Path(output_yaml).exists():
        Path(output_yaml).unlink()

    with open(output_yaml, 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines))

    fre_logger.info('wrote CMOR YAML configuration to %s', output_yaml)
    return output_yaml
