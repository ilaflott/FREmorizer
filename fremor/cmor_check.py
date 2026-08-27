"""
``fremor check``: Variable Mapping Coverage + Input File Sanity Checks
========================================================================

This module powers the ``fremor check`` command. It reads a self-contained CMOR YAML file
(as written by ``fremor config``) to derive pp_dir, the MIP tables directory, the MIP era,
and each component's variable list path, cross-references the per-component variable lists
against the authoritative MIP table JSON files fetched by ``fremor init`` (e.g. the
``cmip6-cmor-tables`` / ``cmip7-cmor-tables`` repos), and reports, per MIP table:

- variables the MIP table actually requires (its ``variable_entry`` keys)
  that are not mapped from any GFDL post-processing component, and
- variables mapped from more than one ``(component, GFDL diagnostic key)``
  pair, and
- values written into a varlist that don't correspond to any variable
  actually defined in that MIP table (typos / stale mappings).

The reference set of variables per table comes straight from the downloaded
MIP table JSON, not from whatever happens to already be mapped somewhere in
the varlist directory -- so this also catches variables GFDL has never
mapped at all.

By default every MIP table found in mip_tables_dir is checked; pass one or
more glob-style table-name patterns (e.g. ``Amon``, ``AER*``) to restrict
the check to a subset. Pass show_mapped=True to also report variables that
are cleanly mapped from exactly one component/diagnostic (one-to-one).

Two additional, opt-in checks look past the varlist JSON and at the actual
pp_dir input files for every cleanly (one-to-one) mapped variable:

- ``check_staging=True``: do the expected FRE time-series files exist under
  pp_dir at all, and (best-effort) are they staged/disk-resident rather than
  still sitting offline in the archive -- plus a filename-only scan for gaps
  between chunk date ranges. No file content is ever read for this check.
- ``check_dims=True``: does the input file's vertical dimension (if any)
  actually match what the MIP table declares for that variable -- e.g.
  catching a variable mapped from raw model-level (``alevel``) output when
  the table wants fixed pressure levels (``plevNN``), or vice versa -- plus
  a check that hybrid-sigma variables have their companion ``.ps.nc`` file
  alongside. Only one representative file's header is opened per variable
  (via netCDF4, metadata only, no array data is read).

Functions
---------
- ``cmor_check_subtool(...)``
"""

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional, Sequence, Union

import click
from netCDF4 import Dataset

from .cmor_config import _load_config_yaml
from .cmor_constants import ACCEPTED_VERT_DIMS, INPUT_TO_MIP_VERT_DIM
from .cmor_helpers import get_json_file_data, get_vertical_dimension, iso_to_bronx_chunk

fre_logger = logging.getLogger(__name__)

# vertical-coordinate tokens recognized on the MIP-table side of the dims check. Reuses
# ACCEPTED_VERT_DIMS (cmor_mixer's accepted *input* vertical dim names, several of which --
# plevNN, height2m, landuse -- pass straight through as MIP-table names too) plus the
# canonical MIP-table-only hybrid-sigma names that INPUT_TO_MIP_VERT_DIM maps input dims onto.
KNOWN_MIP_VERTICAL_TOKENS = set(ACCEPTED_VERT_DIMS) | {'alevel', 'alevhalf', 'olevel', 'olevhalf'}


def _reference_vars_for_table(table_path: str, mip_era: str) -> set:
    """
    Get the set of variable names a MIP table actually requires, straight
    from its ``variable_entry`` keys.

    :param table_path: path to a MIP table JSON file, e.g. ``CMIP6_Amon.json``
    :type table_path: str
    :param mip_era: MIP era string, e.g. 'cmip6' or 'cmip7'
    :type mip_era: str
    :return: set of variable names required by this table
    :rtype: set
    """
    data = get_json_file_data(table_path)
    variable_entry = data.get('variable_entry', {})
    if mip_era.lower() == 'cmip7':
        # cmip7 tables key on branded variable names, e.g. "tas_tavg-h2m-hxy-u"
        return {key.split('_')[0] for key in variable_entry.keys()}
    return set(variable_entry.keys())


def _select_table_names(table_names: Sequence[str], table_patterns: Sequence[str]) -> list:
    """
    Filter a list of MIP table names down to those matching at least one of the given
    glob-style patterns (e.g. 'Amon', 'AER*'). If no patterns are given, all names are kept.
    """
    if not table_patterns:
        return list(table_names)
    return [
        table_name for table_name in table_names
        if any(fnmatch.fnmatchcase(table_name, pattern) for pattern in table_patterns)
    ]


def _mip_table_paths(mip_tables_dir: str, mip_era: str, table_names: Sequence[str]) -> dict:
    """
    Resolve each of the given MIP table names to its MIP table JSON path in
    ``mip_tables_dir``: ``{ERA}_{table_name}.json`` for cmip6/cmip7, but
    ``MIP_{table_name}.json`` for cmip6plus, whose ``mip-cmor-tables`` repo uses a bare
    ``MIP_`` prefix instead of an era-specific one (matches ``_filter_mip_tables`` in
    cmor_config.py).

    :raises FileNotFoundError: if a table name has no corresponding MIP table JSON file.
    :return: table_name -> table_path
    :rtype: dict
    """
    era_upper = mip_era.upper()
    prefix = 'MIP' if era_upper == 'CMIP6PLUS' else era_upper
    table_paths = {}
    for table_name in table_names:
        table_path = f'{mip_tables_dir}/{prefix}_{table_name}.json'
        if not Path(table_path).is_file():
            raise FileNotFoundError(f'MIP table for {table_name} not found: {table_path}')
        table_paths[table_name] = table_path
    return table_paths


def _varlists_by_table_from_yaml(table_targets: Sequence[dict]) -> dict:
    """
    Group per-component variable lists by MIP table name, reading the ``variable_list``
    paths straight out of a cmor yaml's ``table_targets`` (as written by ``fremor config``)
    instead of globbing a directory for files matching a naming convention.

    :return: table_name -> list of (component_name, variable_list_path, data) tuples
    :rtype: dict
    """
    grouped = defaultdict(list)
    for table_target in table_targets:
        table_name = table_target['table_name']
        for comp in table_target.get('target_components') or []:
            component_name = comp['component_name']
            variable_list_path = os.path.expandvars(comp['variable_list'])
            try:
                data = get_json_file_data(variable_list_path)
            except FileNotFoundError:
                fre_logger.warning('variable_list not found, treating as empty: %s',
                                   variable_list_path)
                data = {}
            grouped[table_name].append((component_name, variable_list_path, data))
    return grouped


def _component_input_dir(pp_dir: str, table_target: dict, component: dict) -> Optional[Path]:
    """Resolve the FRE ts directory a component's files live in, mirroring the
    <pp_dir>/<component>/<data_series_type>/<freq>/<chunk> convention ``fremor config`` and
    ``fremor stage`` both use. Returns None if the table_target has no freq (nothing to check
    against, e.g. a hand-edited yaml predating ``fremor config``)."""
    freq = table_target.get('freq')
    if not freq:
        return None
    chunk_bronx = iso_to_bronx_chunk(component['chunk'])
    return Path(pp_dir) / component['component_name'] / component['data_series_type'] / freq / chunk_bronx


def _matching_input_files(input_dir: Optional[Path], local_var: str) -> list:
    """Files in input_dir matching the <something>.<daterange>.<local_var>.nc convention
    (same convention make_simple_varlist/cmor_stage assume), sorted by filename."""
    if input_dir is None or not input_dir.is_dir():
        return []
    return sorted(
        path for path in input_dir.glob('*.*.nc')
        if path.name.split('.')[-2] == local_var
    )


# ---------------------------------------------------------------------------
# staging check: are a variable's input files present under pp_dir, and are
# they staged (disk-resident) rather than still sitting offline in the
# archive? Filename-only date-range gap scan piggybacks on the same file list.
# ---------------------------------------------------------------------------

def _find_dmls_bin(dmls_bin: Optional[str] = None) -> Optional[str]:
    """Resolve the dmls binary: explicit path if given, else look it up on PATH."""
    if dmls_bin:
        return dmls_bin
    return shutil.which('dmls')


def _parse_dmls_states(stdout: str) -> dict:
    """Parse ``dmls -l`` output into ``{path: state}``, e.g. ``{'/a/b.nc': 'OFL'}`` -- 'OFL'
    (offline, archived only) and 'REG'/'DUL' (disk-resident, with 'DUL' also copied to tape)
    are the states relevant to this module."""
    states = {}
    for line in stdout.splitlines():
        match = re.search(r'\((\w+)\)\s+(\S+)\s*$', line.strip())
        if match:
            states[match.group(2)] = match.group(1).upper()
    return states


def _dmls_offline_files(paths: Sequence[Path], dmls_bin: Optional[str] = None) -> Optional[set]:
    """Best-effort, single batched ``dmls -l`` query (like ``fremor stage``'s one-shot dmget)
    for which of the given paths are offline (archived, not yet staged). Returns None --
    meaning "fall back to the stat-based heuristic" -- if dmls isn't available or its output
    can't be parsed, so a missing/misbehaving dmls never breaks this check."""
    binary = _find_dmls_bin(dmls_bin)
    if binary is None or not paths:
        return None
    try:
        result = subprocess.run(
            [binary, '-l', *(str(path) for path in paths)],
            capture_output=True, text=True, timeout=60, check=True
        )
    except (subprocess.SubprocessError, OSError):
        return None

    states = _parse_dmls_states(result.stdout)
    return {filename for filename, state in states.items() if state == 'OFL'}


def _dmls_state_for_file(path: Path, dmls_bin: Optional[str] = None) -> Optional[str]:
    """Best-effort single-file ``dmls -l`` query, returning the parsed state code (e.g.
    'REG', 'DUL', 'OFL'), or None if dmls isn't available, the call fails, or its output
    doesn't include a parseable entry for ``path``. Used by ``fremor map``'s pp-file preview
    to decide whether a file has actually been retrieved from tape yet."""
    binary = _find_dmls_bin(dmls_bin)
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, '-l', str(path)],
            capture_output=True, text=True, timeout=10, check=True
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return _parse_dmls_states(result.stdout).get(str(path))


def _is_file_staged(path: Path) -> bool:
    """Stat-only disk-residency heuristic: an offline archive stub reports fewer allocated
    disk blocks than its logical size implies. Never opens/reads the file's contents."""
    try:
        stat_result = path.stat()
    except OSError:
        return False
    if stat_result.st_size == 0:
        return True
    return stat_result.st_blocks * 512 >= stat_result.st_size


def _date_range_from_filename(path: Path) -> Optional[tuple]:
    """Parse the (first_year, last_year) chunk range out of a FRE ts filename, purely from
    its name -- no file I/O."""
    parts = path.name.split('.')
    if len(parts) < 4 or '-' not in parts[-3]:
        return None
    first_date, last_date = parts[-3].split('-', maxsplit=1)
    if not (first_date[:4].isdigit() and last_date[:4].isdigit()):
        return None
    return int(first_date[:4]), int(last_date[:4])


def _date_range_gaps(files: list) -> list:
    """Filename-only scan for gaps between consecutive chunks' year ranges (e.g. missing
    postprocessing years that would stall a run partway through)."""
    ranges = sorted(r for r in (_date_range_from_filename(f) for f in files) if r is not None)
    gaps = []
    for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
        if next_start > prev_end + 1:
            gaps.append(f'{prev_end}-{next_start}')
    return gaps


def _staging_status(files: list, dmls_bin: Optional[str] = None) -> dict:
    """Build the staging report entry for one variable's input files."""
    if not files:
        return {'status': 'missing', 'unstaged_files': [], 'gaps': []}

    offline = _dmls_offline_files(files, dmls_bin)
    if offline is not None:
        unstaged = [str(f) for f in files if str(f) in offline]
    else:
        unstaged = [str(f) for f in files if not _is_file_staged(f)]

    if not unstaged:
        status = 'staged'
    elif len(unstaged) == len(files):
        status = 'unstaged'
    else:
        status = 'partially_staged'

    return {'status': status, 'unstaged_files': unstaged, 'gaps': _date_range_gaps(files)}


# ---------------------------------------------------------------------------
# dims check: does the input file's actual vertical dimension match what the
# MIP table declares (differentiating e.g. plevNN from alevel), and -- for
# hybrid-sigma variables -- is the companion .ps.nc file present?
# ---------------------------------------------------------------------------

def _mip_table_vertical_token(mip_dims: Union[str, list]) -> Optional[str]:
    """Pick the vertical-coordinate token out of a MIP table variable's 'dimensions' entry
    (e.g. 'longitude latitude alevel time' -> 'alevel'), or None if the variable has no
    vertical dimension. 'dimensions' is a space-delimited string in CMIP6/CMIP6Plus tables
    but a JSON list in CMIP7 tables."""
    tokens = (mip_dims or '').split() if isinstance(mip_dims, str) else (mip_dims or [])
    for token in tokens:
        if token in KNOWN_MIP_VERTICAL_TOKENS or token.startswith('plev'):
            return token
    return None


def _matching_variable_keys(variable_entry: dict, var: str, mip_era: str) -> list:
    """``variable_entry`` keys that correspond to bare variable name `var`. For
    CMIP6/CMIP6Plus this is just ``[var]`` (if present); for CMIP7 a bare variable name can
    have multiple brands, each keyed as ``{var}_{brand}``, so every matching brand key is
    returned."""
    if mip_era.lower() == 'cmip7':
        return [key for key in variable_entry if key.split('_')[0] == var]
    return [var] if var in variable_entry else []


def _mip_variable_vertical_tokens(table_data: dict, var: str, mip_era: str) -> list:
    """All distinct vertical-dimension tokens declared for `var` in this MIP table. For
    CMIP6/CMIP6Plus there's exactly one entry; for CMIP7 a bare variable name can have
    multiple brands, each potentially expecting a different vertical coordinate -- so every
    brand's token is a valid match (mirrors how filter_brands disambiguates at run time)."""
    variable_entry = table_data.get('variable_entry', {})
    keys = _matching_variable_keys(variable_entry, var, mip_era)

    tokens = []
    for key in keys:
        token = _mip_table_vertical_token(variable_entry[key].get('dimensions', ''))
        if token not in tokens:
            tokens.append(token)
    return tokens


def _input_vertical_token(nc_path: str, local_var: str) -> Union[str, int]:
    """Best-effort read of the input file's vertical dimension name, mapped to its MIP-table
    equivalent (the same INPUT_TO_MIP_VERT_DIM lookup cmor_helpers.filter_brands uses for
    CMIP7 brand disambiguation). Only header metadata is inspected, never variable data.
    Returns 0 if no vertical dimension is found or the file can't be opened."""
    try:
        with Dataset(nc_path, 'r') as dataset:
            vert_dim = get_vertical_dimension(dataset, local_var)
    except Exception:  # pylint: disable=broad-except
        fre_logger.debug('could not inspect %s in %s for vertical dim', local_var, nc_path,
                         exc_info=True)
        return 0
    if vert_dim == 0:
        return 0
    return INPUT_TO_MIP_VERT_DIM.get(vert_dim.lower(), vert_dim.lower())


def _vertical_dim_finding(mip_vert_tokens: list, files: list) -> dict:
    """Build the dims report entry for one variable, comparing its MIP-table-declared
    vertical dim(s) against the actual vertical dim found in a representative input file."""
    if not files:
        return {'status': 'unknown', 'reason': 'no input files found to inspect'}

    representative = files[0]
    local_var = representative.name.split('.')[-2]
    input_vert = _input_vertical_token(str(representative), local_var)
    expects_vertical = any(token is not None for token in mip_vert_tokens)

    finding = {
        'file': str(representative),
        'mip_table_vertical_dims': [t for t in mip_vert_tokens if t is not None] or None,
        'input_vertical_dim': input_vert if input_vert != 0 else None,
    }

    if input_vert == 0:
        finding['status'] = 'missing_vertical_dim' if expects_vertical else 'ok'
    elif not expects_vertical:
        finding['status'] = 'unexpected_vertical_dim'
    elif input_vert in mip_vert_tokens:
        finding['status'] = 'ok'
    else:
        finding['status'] = 'vertical_dim_mismatch'

    if input_vert in ('alevel', 'alevhalf'):
        ps_path = representative.with_name(
            '.'.join((*representative.name.split('.')[:-2], 'ps', 'nc'))
        )
        if not ps_path.is_file():
            finding['missing_ps_file'] = str(ps_path)

    return finding


def _build_files_report(table_path: str, table_data: Optional[dict], mip_era: str,
                        table_target: dict, pp_dir: str, one_to_one_mapped: dict,
                        check_staging: bool, check_dims: bool,
                        dmls_bin: Optional[str] = None) -> dict:
    """Per one-to-one-mapped variable: staging status and/or vertical-dim consistency,
    resolved straight from pp_dir. Only variables mapped from exactly one component are
    checked -- unmapped variables have no files to look at, and multiply-mapped ones are
    already flagged as a mapping-hygiene issue by the coverage check above."""
    components_by_name = {
        comp['component_name']: comp for comp in table_target.get('target_components') or []
    }

    files_report = {}
    for var, (component_name, gfdl_key) in sorted(one_to_one_mapped.items()):
        component = components_by_name.get(component_name)
        if component is None:
            continue  # uncovered -- one_to_one_mapped is derived from these same components

        input_dir = _component_input_dir(pp_dir, table_target, component)
        files = _matching_input_files(input_dir, gfdl_key)

        var_entry = {}
        if check_staging:
            var_entry['staging'] = _staging_status(files, dmls_bin)
        if check_dims:
            mip_vert_tokens = _mip_variable_vertical_tokens(table_data or {}, var, mip_era)
            var_entry['dims'] = _vertical_dim_finding(mip_vert_tokens, files)
        files_report[var] = var_entry

    return files_report


def _build_table_report(table_path: str, mip_era: str, varlists_by_table: dict,
                        show_mapped: bool = False,
                        pp_dir: Optional[str] = None, table_target: Optional[dict] = None,
                        check_staging: bool = False, check_dims: bool = False,
                        dmls_bin: Optional[str] = None) -> dict:
    """Build the unmapped / multiply-mapped / unknown-mapped report for one MIP table."""
    table_name = Path(table_path).stem.split('.')[0].split('_')[1]
    reference_vars = _reference_vars_for_table(table_path, mip_era)

    mapped = defaultdict(list)  # cmip_var -> [(component, gfdl_diag_key), ...]
    for component, _fname, data in varlists_by_table.get(table_name, []):
        for gfdl_key, cmip_var in data.items():
            if cmip_var:
                mapped[cmip_var].append((component, gfdl_key))

    unmapped = sorted(reference_vars - set(mapped))
    multiply_mapped = {
        var: sorted(entries) for var, entries in mapped.items() if len(entries) > 1
    }
    unknown_mapped = sorted(set(mapped) - reference_vars)
    one_to_one_mapped = {
        var: entries[0] for var, entries in mapped.items()
        if len(entries) == 1 and var in reference_vars
    }

    report_entry = {
        'table_name': table_name,
        'reference_var_count': len(reference_vars),
        'unmapped': unmapped,
        'multiply_mapped': multiply_mapped,
        'unknown_mapped': unknown_mapped,
    }

    if show_mapped:
        report_entry['one_to_one_mapped'] = one_to_one_mapped

    if (check_staging or check_dims) and pp_dir is not None and table_target is not None:
        table_data = get_json_file_data(table_path) if check_dims else None
        report_entry['files'] = _build_files_report(
            table_path, table_data, mip_era, table_target, pp_dir, one_to_one_mapped,
            check_staging, check_dims, dmls_bin
        )

    return report_entry


def _print_report(report: dict, show_mapped: bool = False) -> None:
    for table_name in sorted(report):
        entry = report[table_name]
        click.echo(f'\n[{table_name}]  ({entry["reference_var_count"]} variables required by table)')

        if entry['unmapped']:
            click.echo(f'  UNMAPPED ({len(entry["unmapped"])}): variables required by the table '
                       'but not mapped from any component')
            for var in entry['unmapped']:
                click.echo(f'    - {var}')
        else:
            click.echo('  UNMAPPED: none')

        if entry['multiply_mapped']:
            click.echo(f'  MULTIPLY-MAPPED ({len(entry["multiply_mapped"])}): variables mapped from '
                       'more than one component/diagnostic')
            for var in sorted(entry['multiply_mapped']):
                locs = ', '.join(f'{comp}:{key}' for comp, key in entry['multiply_mapped'][var])
                click.echo(f'    - {var}: {locs}')
        else:
            click.echo('  MULTIPLY-MAPPED: none')

        if entry['unknown_mapped']:
            click.echo(f'  UNKNOWN ({len(entry["unknown_mapped"])}): mapped values that are not '
                       'variables in this MIP table (possible typos)')
            for var in entry['unknown_mapped']:
                click.echo(f'    - {var}')

        if show_mapped:
            one_to_one = entry.get('one_to_one_mapped', {})
            if one_to_one:
                click.echo(f'  MAPPED ({len(one_to_one)}): variables mapped from exactly one '
                           'component/diagnostic')
                for var in sorted(one_to_one):
                    comp, key = one_to_one[var]
                    click.echo(f'    - {var}: {comp}:{key}')
            else:
                click.echo('  MAPPED: none')

        files = entry.get('files')
        if files is not None:
            if not files:
                click.echo('  FILES: no one-to-one-mapped variables to check')
            for var in sorted(files):
                var_entry = files[var]
                staging = var_entry.get('staging')
                if staging is not None:
                    line = f'  FILES  {var}: staging={staging["status"]}'
                    if staging['unstaged_files']:
                        line += f' ({len(staging["unstaged_files"])} file(s) not yet staged)'
                    click.echo(line)
                    if staging['gaps']:
                        click.echo(f'           date-range gaps: {", ".join(staging["gaps"])}')
                dims = var_entry.get('dims')
                if dims is not None:
                    line = f'  FILES  {var}: dims={dims["status"]}'
                    if dims['status'] not in ('ok', 'unknown'):
                        line += (f' (table wants {dims.get("mip_table_vertical_dims")}, '
                                 f'input has {dims.get("input_vertical_dim")})')
                    click.echo(line)
                    if dims.get('missing_ps_file'):
                        click.echo(f'           missing companion ps file: {dims["missing_ps_file"]}')


def cmor_check_subtool(
        yamlfile: str,
        table_patterns: Sequence[str] = (),
        show_mapped: bool = False,
        json_output: bool = False,
        output_report: Optional[str] = None,
        check_staging: bool = False,
        check_dims: bool = False,
        dmls_bin: Optional[str] = None
) -> dict:
    """
    Cross-reference per-component varlist files against MIP table JSON files
    and report unmapped, multiply-mapped, and unrecognized variable mappings
    per MIP table.

    pp_dir, the MIP tables directory, the MIP era, and each component's variable_list path
    are all derived from ``yamlfile``, a self-contained CMOR YAML file as written by
    ``fremor config`` -- no separate varlist_dir/mip_tables_dir/mip_era flags are needed.

    :param yamlfile: path to a CMOR YAML file produced by ``fremor config``.
    :type yamlfile: str
    :param table_patterns: optional glob-style patterns (e.g. 'Amon', 'AER*') selecting which MIP
        tables to check by name. If empty, every MIP table in yamlfile's table_targets is checked.
    :type table_patterns: Sequence[str]
    :param show_mapped: if True, also report variables mapped from exactly one component/diagnostic.
    :type show_mapped: bool
    :param json_output: if True, print the report as JSON instead of a text summary.
    :type json_output: bool
    :param output_report: optional path to also write the JSON report to.
    :type output_report: str or None
    :param check_staging: if True, for every one-to-one-mapped variable also check whether its
        input files exist under pp_dir and whether they're staged/disk-resident (best-effort,
        via ``dmls`` if available else a stat-only heuristic), plus a filename-only scan for
        gaps between chunk date ranges.
    :type check_staging: bool
    :param check_dims: if True, for every one-to-one-mapped variable also check whether a
        representative input file's vertical dimension matches what the MIP table declares
        (e.g. distinguishing ``alevel`` model-level output from ``plevNN`` pressure levels),
        and whether hybrid-sigma variables have their companion ``.ps.nc`` file present.
    :type check_dims: bool
    :param dmls_bin: path to the dmls binary for the staging check. If omitted, looks for
        'dmls' on PATH; if not found either, falls back to a stat-only residency heuristic.
    :type dmls_bin: str or None
    :raises FileNotFoundError: if yamlfile, its pp_dir, or its table_dir do not exist, or a
        table_target's MIP table JSON file is missing.
    :raises ValueError: if yamlfile has no table_targets, or none of the given table_patterns
        match any table_target.
    :return: table_name -> report dict, with keys 'reference_var_count', 'unmapped',
             'multiply_mapped', 'unknown_mapped', (if show_mapped) 'one_to_one_mapped', and
             (if check_staging or check_dims) 'files'.
    :rtype: dict
    """
    cmor_yaml_ctx = _load_config_yaml(yamlfile)
    mip_era = cmor_yaml_ctx['mip_era']
    pp_dir = cmor_yaml_ctx['pp_dir']
    mip_tables_dir = cmor_yaml_ctx['mip_tables_dir']
    table_targets = cmor_yaml_ctx['table_targets']

    all_table_names = sorted({table_target['table_name'] for table_target in table_targets})
    if not all_table_names:
        raise ValueError(f'no table_targets found in {yamlfile}')

    table_names = _select_table_names(all_table_names, table_patterns)
    if not table_names:
        raise ValueError(
            f'no table_targets in {yamlfile} matched table_patterns {list(table_patterns)}')

    table_paths = _mip_table_paths(mip_tables_dir, mip_era, table_names)
    varlists_by_table = _varlists_by_table_from_yaml(table_targets)
    table_targets_by_name = {
        table_target['table_name']: table_target for table_target in table_targets
    }

    report = {}
    for table_name in table_names:
        table_entry = _build_table_report(
            table_paths[table_name], mip_era, varlists_by_table, show_mapped=show_mapped,
            pp_dir=pp_dir, table_target=table_targets_by_name.get(table_name),
            check_staging=check_staging, check_dims=check_dims, dmls_bin=dmls_bin
        )
        report[table_entry.pop('table_name')] = table_entry

    if json_output:
        click.echo(json.dumps(report, indent=2))
    else:
        _print_report(report, show_mapped=show_mapped)

    if output_report is not None:
        with open(output_report, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
        fre_logger.info('wrote check report to %s', output_report)

    return report
