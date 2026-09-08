"""Discover and stage archive-resident inputs for a YAML-driven fremor run."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Optional, Sequence

import yaml

from .cmor_helpers import get_bronx_freq_from_mip_table, iso_to_bronx_chunk


fre_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StageConfig:
    """Resolved configuration used while collecting staging paths."""

    document: dict
    pp_dir: Path
    table_dir: Path
    mip_era: str
    start: Optional[int]
    stop: Optional[int]


def _year_bound(value: Optional[str], name: str) -> Optional[int]:
    """Validate and convert an optional four-digit year bound."""
    if value is None:
        return None
    value_text = str(value)
    if len(value_text) != 4 or not value_text.isdigit():
        raise ValueError(f'{name} must be a four-digit year (YYYY), got {value!r}')
    return int(value_text)


def _in_year_range(path: Path, start: Optional[int], stop: Optional[int]) -> bool:
    """Return whether a FRE time-series filename is wholly within the bounds."""
    if start is None and stop is None:
        return True

    parts = path.name.split('.')
    try:
        date_range = parts[-3]
        first_date, last_date = date_range.split('-', maxsplit=1)
        first_year = int(first_date[:4])
        last_year = int(last_date[:4])
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(
            f'cannot read a start-end date range from input filename {path}'
        ) from exc

    return not ((start is not None and first_year < start) or
                (stop is not None and last_year > stop))


def _table_json_prefix(mip_era: str) -> str:
    """MIP table JSON filename prefix: 'MIP' for cmip6plus, whose mip-cmor-tables repo uses
    a bare 'MIP_' prefix instead of an era-specific one, else the era itself. Matches
    _mip_table_paths in cmor_check.py."""
    return 'MIP' if mip_era == 'CMIP6PLUS' else mip_era


def _table_local_variables(table_path: Path, variable_list_path: Path,
                           mip_era: str) -> set[str]:
    """Return local variables whose targets occur in the selected MIP table."""
    if not table_path.is_file():
        raise FileNotFoundError(f'MIP table does not exist: {table_path}')
    if not variable_list_path.is_file():
        raise FileNotFoundError(f'variable list does not exist: {variable_list_path}')

    with table_path.open(encoding='utf-8') as handle:
        table = json.load(handle)
    with variable_list_path.open(encoding='utf-8') as handle:
        variable_list = json.load(handle)

    table_variables = table.get('variable_entry')
    if not isinstance(table_variables, dict):
        raise ValueError(f'MIP table has no variable_entry mapping: {table_path}')
    if not isinstance(variable_list, dict):
        raise ValueError(f'variable list must contain a JSON mapping: {variable_list_path}')

    if mip_era == 'CMIP7':
        valid_targets = {name.split('_')[0] for name in table_variables}
    else:
        valid_targets = set(table_variables)

    return {
        local_var for local_var, target_var in variable_list.items()
        if isinstance(local_var, str) and isinstance(target_var, str) and
        target_var in valid_targets
    }


def _frequency(table_target: dict, table_path: Path, mip_era: str) -> str:
    """Resolve a YAML table target's FRE directory frequency."""
    freq = table_target.get('freq')
    if freq is not None:
        return freq
    if mip_era == 'CMIP7':
        raise ValueError(
            f"freq is required for CMIP7 table target {table_target.get('table_name')!r}"
        )
    freq = get_bronx_freq_from_mip_table(str(table_path))
    if freq is None:
        raise ValueError(
            f"could not derive freq for table target {table_target.get('table_name')!r}"
        )
    return freq


def _load_stage_config(yamlfile: str, start: Optional[str], stop: Optional[str]) -> _StageConfig:
    """Load and validate the shared configuration needed for file discovery."""
    yaml_path = Path(yamlfile)
    if not yaml_path.is_file():
        raise FileNotFoundError(f'YAML file does not exist: {yamlfile}')

    with yaml_path.open(encoding='utf-8') as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict) or not isinstance(document.get('cmor'), dict):
        raise ValueError(
            f"invalid CMOR YAML file '{yamlfile}': expected a top-level cmor mapping"
        )

    config = document['cmor']
    directories = config.get('directories')
    if not isinstance(directories, dict):
        raise ValueError(f"invalid CMOR YAML file '{yamlfile}': missing directories mapping")

    pp_dir = Path(os.path.expandvars(directories['pp_dir']))
    table_dir = Path(os.path.expandvars(directories['table_dir']))
    if not pp_dir.is_dir():
        raise FileNotFoundError(f'pp_dir does not exist: {pp_dir}')
    if not table_dir.is_dir():
        raise FileNotFoundError(f'MIP table directory does not exist: {table_dir}')

    mip_era = str(config['mip_era']).upper()
    if mip_era not in {'CMIP6', 'CMIP6PLUS', 'CMIP7'}:
        raise ValueError(f'unsupported mip_era: {mip_era}')

    start_year = _year_bound(start if start is not None else config.get('start'), 'start')
    stop_year = _year_bound(stop if stop is not None else config.get('stop'), 'stop')
    if start_year is not None and stop_year is not None and start_year > stop_year:
        raise ValueError(f'start year {start_year} is later than stop year {stop_year}')
    return _StageConfig(config, pp_dir, table_dir, mip_era, start_year, stop_year)


def _component_stage_files(input_dir: Path, local_variables: set[str],
                           start: Optional[int], stop: Optional[int]) -> set[Path]:
    """Collect mapped primary and auxiliary files from one component directory."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f'input directory does not exist: {input_dir}')

    input_files = set()
    for path in input_dir.glob('*.nc'):
        parts = path.name.split('.')
        if len(parts) < 4 or parts[-2] not in local_variables:
            continue
        if not _in_year_range(path, start, stop):
            continue

        input_files.add(path.resolve())
        ps_path = path.with_name('.'.join((*parts[:-2], 'ps', 'nc')))
        if ps_path.is_file():
            input_files.add(ps_path.resolve())
    return input_files


def collect_stage_files(yamlfile: str, start: Optional[str] = None,
                        stop: Optional[str] = None) -> list[str]:
    """Collect the unique input files that a YAML-driven fremor run can consume.

    The returned paths include mapped primary variables and an existing same-date
    ``ps`` file, which is an auxiliary input used for hybrid vertical coordinates.
    Year bounds follow ``fremor yaml`` semantics: a chunk is selected only when its
    complete filename date range falls within the requested bounds.
    """
    stage_config = _load_stage_config(yamlfile, start, stop)

    input_files: set[Path] = set()
    for table_target in stage_config.document.get('table_targets') or []:
        if table_target.get('disabled'):
            fre_logger.info('skipping disabled MIP table %s', table_target.get('table_name'))
            continue
        table_name = table_target['table_name']
        table_path = (stage_config.table_dir /
                     f'{_table_json_prefix(stage_config.mip_era)}_{table_name}.json')
        freq = _frequency(table_target, table_path, stage_config.mip_era)

        for component in table_target.get('target_components') or []:
            variable_list_path = Path(os.path.expandvars(component['variable_list']))
            local_variables = _table_local_variables(
                table_path, variable_list_path, stage_config.mip_era
            )
            input_dir = (
                stage_config.pp_dir / component['component_name'] /
                component['data_series_type'] /
                freq / iso_to_bronx_chunk(component['chunk'])
            )
            input_files.update(
                _component_stage_files(
                    input_dir, local_variables, stage_config.start, stage_config.stop
                )
            )

    return sorted(str(path) for path in input_files)


def cmor_stage_subtool(yamlfile: str, start: Optional[str] = None,
                       stop: Optional[str] = None, dmget_bin: str = 'dmget',
                       dry_run: bool = False) -> list[str]:
    """Collect YAML run inputs and submit all of them in one ``dmget`` call."""
    input_files = collect_stage_files(yamlfile=yamlfile, start=start, stop=stop)
    if not input_files:
        raise ValueError(f'no mapped input files found for {yamlfile}')

    fre_logger.info('found %d unique input files to stage', len(input_files))
    if dry_run:
        return input_files

    command: Sequence[str] = [dmget_bin, *input_files]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f'dmget executable not found: {dmget_bin!r}'
        ) from exc
    return input_files
