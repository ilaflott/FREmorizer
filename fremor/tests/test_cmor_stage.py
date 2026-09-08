"""Tests for archive input discovery and batched dmget staging."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from fremor.cmor_stage import collect_stage_files, cmor_stage_subtool


def _stage_case(tmp_path: Path, *, mip_era: str = 'CMIP6') -> tuple[Path, Path]:
    """Create a small self-contained YAML staging case."""
    pp_dir = tmp_path / 'pp'
    input_dir = pp_dir / 'atmos' / 'ts' / 'monthly' / '5yr'
    input_dir.mkdir(parents=True)
    table_dir = tmp_path / 'tables'
    table_dir.mkdir()
    varlist = tmp_path / 'varlist.json'

    table_variables = {'tas': {}}
    if mip_era == 'CMIP7':
        table_variables = {'tas_tavg-u-hxy-u': {}}
    table_prefix = 'MIP' if mip_era == 'CMIP6PLUS' else mip_era
    (table_dir / f'{table_prefix}_Amon.json').write_text(
        json.dumps({'variable_entry': table_variables}), encoding='utf-8'
    )
    varlist.write_text(json.dumps({'temp': 'tas', 'unused': 'not_in_table'}), encoding='utf-8')

    for name in (
        'atmos.199001-199412.temp.nc',
        'atmos.199501-199912.temp.nc',
        'atmos.199501-199912.ps.nc',
        'atmos.199501-199912.unused.nc',
        'atmos.200001-200412.temp.nc',
    ):
        (input_dir / name).touch()

    config = {
        'cmor': {
            'mip_era': mip_era,
            'start': '1995',
            'stop': '1999',
            'directories': {
                'pp_dir': str(pp_dir),
                'table_dir': str(table_dir),
                'outdir': str(tmp_path / 'output'),
            },
            'table_targets': [{
                'table_name': 'Amon',
                'freq': 'monthly',
                'target_components': [{
                    'component_name': 'atmos',
                    'data_series_type': 'ts',
                    'chunk': 'P5Y',
                    'variable_list': str(varlist),
                }],
            }],
        },
    }
    yamlfile = tmp_path / 'cmor.yaml'
    yamlfile.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
    return yamlfile, input_dir


@pytest.mark.parametrize('mip_era', ['CMIP6', 'CMIP6PLUS', 'CMIP7'])
def test_collect_stage_files_uses_mappings_bounds_and_ps(tmp_path, mip_era):
    """Only runnable mapped files and their same-date ps auxiliary are selected."""
    yamlfile, input_dir = _stage_case(tmp_path, mip_era=mip_era)

    result = collect_stage_files(str(yamlfile))

    assert result == sorted([
        str((input_dir / 'atmos.199501-199912.ps.nc').resolve()),
        str((input_dir / 'atmos.199501-199912.temp.nc').resolve()),
    ])


def test_collect_stage_files_cli_bounds_override_yaml(tmp_path):
    """Explicit bounds take precedence over bounds stored in YAML."""
    yamlfile, input_dir = _stage_case(tmp_path)

    result = collect_stage_files(str(yamlfile), start='2000', stop='2004')

    assert result == [str((input_dir / 'atmos.200001-200412.temp.nc').resolve())]


def test_stage_invokes_dmget_once_with_deduplicated_files(tmp_path):
    """Every selected path is passed to one subprocess invocation."""
    yamlfile, _ = _stage_case(tmp_path)

    with patch('fremor.cmor_stage.subprocess.run') as run_mock:
        result = cmor_stage_subtool(str(yamlfile), dmget_bin='/opt/bin/dmget')

    run_mock.assert_called_once_with(['/opt/bin/dmget', *result], check=True)


def test_stage_dry_run_does_not_invoke_dmget(tmp_path):
    """Dry-run discovery is side-effect free."""
    yamlfile, _ = _stage_case(tmp_path)

    with patch('fremor.cmor_stage.subprocess.run') as run_mock:
        result = cmor_stage_subtool(str(yamlfile), dry_run=True)

    assert len(result) == 2
    run_mock.assert_not_called()


def test_collect_stage_files_skips_disabled_tables(tmp_path):
    """Disabled table targets contribute no files and aren't validated."""
    yamlfile, input_dir = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    yaml_doc['cmor']['table_targets'].insert(0, {
        'table_name': 'MissingDisabledTable',
        'disabled': True,
        'target_components': [{
            'component_name': 'missing_component',
            'data_series_type': 'ts',
            'chunk': 'P5Y',
            'variable_list': str(tmp_path / 'missing-disabled-varlist.json'),
        }],
    })
    yamlfile.write_text(yaml.safe_dump(yaml_doc, sort_keys=False), encoding='utf-8')

    result = collect_stage_files(str(yamlfile))

    assert result == sorted([
        str((input_dir / 'atmos.199501-199912.ps.nc').resolve()),
        str((input_dir / 'atmos.199501-199912.temp.nc').resolve()),
    ])


def test_collect_stage_files_all_disabled_returns_empty_selection(tmp_path):
    """An all-disabled configuration performs no staging discovery."""
    yamlfile, _ = _stage_case(tmp_path)
    _rewrite_yaml(yamlfile, lambda cmor: cmor['table_targets'][0].update(disabled=True))

    assert collect_stage_files(str(yamlfile)) == []


def test_collect_stage_files_rejects_invalid_year(tmp_path):
    """Invalid year bounds fail before invoking dmget."""
    yamlfile, _ = _stage_case(tmp_path)

    with pytest.raises(ValueError, match='four-digit year'):
        collect_stage_files(str(yamlfile), start='95')


def test_stage_rejects_empty_selection(tmp_path):
    """An empty batch is reported rather than invoking dmget with no paths."""
    yamlfile, _ = _stage_case(tmp_path)

    with pytest.raises(ValueError, match='no mapped input files'):
        cmor_stage_subtool(str(yamlfile), start='2010', stop='2014', dry_run=True)


def _rewrite_yaml(yamlfile: Path, mutate) -> None:
    """Load a staging YAML, let ``mutate`` edit its ``cmor`` mapping in place, save it back."""
    config = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    mutate(config['cmor'])
    yamlfile.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')


def test_collect_stage_files_with_no_bounds_anywhere_keeps_every_mapped_file(tmp_path):
    """No start/stop in the YAML or the call means every mapped file is in range."""
    yamlfile, input_dir = _stage_case(tmp_path)
    _rewrite_yaml(yamlfile, lambda cmor: (cmor.pop('start'), cmor.pop('stop')))

    result = collect_stage_files(str(yamlfile))

    assert result == sorted([
        str((input_dir / 'atmos.199001-199412.temp.nc').resolve()),
        str((input_dir / 'atmos.199501-199912.temp.nc').resolve()),
        str((input_dir / 'atmos.199501-199912.ps.nc').resolve()),
        str((input_dir / 'atmos.200001-200412.temp.nc').resolve()),
    ])


def test_collect_stage_files_rejects_unparseable_date_range(tmp_path):
    """A mapped file whose name doesn't carry a '-' separated date range is reported."""
    yamlfile, input_dir = _stage_case(tmp_path)
    (input_dir / 'atmos.notadaterange.temp.nc').touch()

    with pytest.raises(ValueError, match='cannot read a start-end date range'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_missing_yamlfile(tmp_path):
    """A nonexistent YAML path is reported before anything else is read."""
    with pytest.raises(FileNotFoundError, match='YAML file does not exist'):
        collect_stage_files(str(tmp_path / 'missing.yaml'))


def test_collect_stage_files_rejects_yaml_without_cmor_section(tmp_path):
    """A YAML file without a top-level 'cmor' mapping is rejected."""
    yamlfile = tmp_path / 'bad.yaml'
    yamlfile.write_text(yaml.safe_dump({'not_cmor': {}}), encoding='utf-8')

    with pytest.raises(ValueError, match='expected a top-level cmor mapping'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_missing_directories_mapping(tmp_path):
    """A 'cmor' section without a 'directories' mapping is rejected."""
    yamlfile = tmp_path / 'bad.yaml'
    yamlfile.write_text(yaml.safe_dump({'cmor': {'mip_era': 'CMIP6'}}), encoding='utf-8')

    with pytest.raises(ValueError, match='missing directories mapping'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_missing_pp_dir(tmp_path):
    """A pp_dir that doesn't exist on disk is reported."""
    yamlfile, _ = _stage_case(tmp_path)
    _rewrite_yaml(yamlfile, lambda cmor: cmor['directories'].update(
        pp_dir=str(tmp_path / 'no_such_pp_dir')
    ))

    with pytest.raises(FileNotFoundError, match='pp_dir does not exist'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_missing_table_dir(tmp_path):
    """A table_dir that doesn't exist on disk is reported."""
    yamlfile, _ = _stage_case(tmp_path)
    _rewrite_yaml(yamlfile, lambda cmor: cmor['directories'].update(
        table_dir=str(tmp_path / 'no_such_table_dir')
    ))

    with pytest.raises(FileNotFoundError, match='MIP table directory does not exist'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_unsupported_mip_era(tmp_path):
    """A mip_era outside CMIP6/CMIP7 is rejected."""
    yamlfile, _ = _stage_case(tmp_path)
    _rewrite_yaml(yamlfile, lambda cmor: cmor.update(mip_era='CMIP5'))

    with pytest.raises(ValueError, match='unsupported mip_era'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_start_after_stop(tmp_path):
    """A start year later than the stop year is rejected."""
    yamlfile, _ = _stage_case(tmp_path)

    with pytest.raises(ValueError, match='is later than stop year'):
        collect_stage_files(str(yamlfile), start='2000', stop='1990')


def test_collect_stage_files_rejects_missing_table_json(tmp_path):
    """A MIP table json referenced by table_name that isn't on disk is reported."""
    yamlfile, _ = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    table_dir = Path(yaml_doc['cmor']['directories']['table_dir'])
    (table_dir / 'CMIP6_Amon.json').unlink()

    with pytest.raises(FileNotFoundError, match='MIP table does not exist'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_missing_variable_list(tmp_path):
    """A variable_list file referenced by a component that isn't on disk is reported."""
    yamlfile, _ = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    varlist_path = Path(
        yaml_doc['cmor']['table_targets'][0]['target_components'][0]['variable_list']
    )
    varlist_path.unlink()

    with pytest.raises(FileNotFoundError, match='variable list does not exist'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_table_without_variable_entry(tmp_path):
    """A MIP table json without a variable_entry mapping is rejected."""
    yamlfile, _ = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    table_dir = Path(yaml_doc['cmor']['directories']['table_dir'])
    (table_dir / 'CMIP6_Amon.json').write_text(json.dumps({'not_variable_entry': []}),
                                                encoding='utf-8')

    with pytest.raises(ValueError, match='MIP table has no variable_entry mapping'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_variable_list_not_a_mapping(tmp_path):
    """A variable_list file that isn't a JSON mapping is rejected."""
    yamlfile, _ = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    varlist_path = Path(
        yaml_doc['cmor']['table_targets'][0]['target_components'][0]['variable_list']
    )
    varlist_path.write_text(json.dumps(['tas']), encoding='utf-8')

    with pytest.raises(ValueError, match='variable list must contain a JSON mapping'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_requires_freq_for_cmip7_table_target(tmp_path):
    """A CMIP7 table_target without an explicit freq can't fall back to bronx lookup."""
    yamlfile, _ = _stage_case(tmp_path, mip_era='CMIP7')
    _rewrite_yaml(yamlfile, lambda cmor: cmor['table_targets'][0].pop('freq'))

    with pytest.raises(ValueError, match='freq is required for CMIP7 table target'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_derives_freq_from_table_for_cmip6(tmp_path):
    """A CMIP6 table_target without an explicit freq derives it from the MIP table."""
    yamlfile, input_dir = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    table_dir = Path(yaml_doc['cmor']['directories']['table_dir'])
    (table_dir / 'CMIP6_Amon.json').write_text(
        json.dumps({'variable_entry': {'tas': {'frequency': 'mon'}}}), encoding='utf-8'
    )
    _rewrite_yaml(yamlfile, lambda cmor: cmor['table_targets'][0].pop('freq'))

    result = collect_stage_files(str(yamlfile))

    assert result == sorted([
        str((input_dir / 'atmos.199501-199912.ps.nc').resolve()),
        str((input_dir / 'atmos.199501-199912.temp.nc').resolve()),
    ])


def test_collect_stage_files_rejects_undrivable_freq(tmp_path):
    """A MIP table frequency with no FRE-bronx equivalent can't be derived."""
    yamlfile, _ = _stage_case(tmp_path)
    yaml_doc = yaml.safe_load(yamlfile.read_text(encoding='utf-8'))
    table_dir = Path(yaml_doc['cmor']['directories']['table_dir'])
    (table_dir / 'CMIP6_Amon.json').write_text(
        json.dumps({'variable_entry': {'tas': {'frequency': '1hrCM'}}}), encoding='utf-8'
    )
    _rewrite_yaml(yamlfile, lambda cmor: cmor['table_targets'][0].pop('freq'))

    with pytest.raises(ValueError, match='could not derive freq'):
        collect_stage_files(str(yamlfile))


def test_collect_stage_files_rejects_missing_component_input_dir(tmp_path):
    """A component whose resolved <pp_dir>/.../<chunk> directory doesn't exist is reported."""
    yamlfile, _ = _stage_case(tmp_path)
    _rewrite_yaml(yamlfile, lambda cmor: cmor['table_targets'][0]['target_components'][0]
                  .update(chunk='P10Y'))

    with pytest.raises(FileNotFoundError, match='input directory does not exist'):
        collect_stage_files(str(yamlfile))


def test_stage_reports_missing_dmget_binary(tmp_path):
    """A dmget_bin that can't be executed is reported with its configured path."""
    yamlfile, _ = _stage_case(tmp_path)

    with pytest.raises(FileNotFoundError, match='dmget executable not found'):
        cmor_stage_subtool(str(yamlfile), dmget_bin='/definitely/not/a/real/dmget-binary')
