"""
tests for fremor.cmor_yamler.cmor_yaml_subtool

Covers direct loading of self-contained CMOR YAML files.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import fremor
from fremor.cmor_yamler import cmor_yaml_subtool


ROOTDIR = str(Path(fremor.__file__).parent) + '/tests/test_files'
VARLIST = f'{ROOTDIR}/varlist'
EXP_CONFIG = f'{ROOTDIR}/CMOR_input_example.json'
CDL_SOURCE = f'{ROOTDIR}/reduced_ascii_files/reduced_ocean_monthly_1x1deg.199301-199302.sos.cdl'
NC_FILENAME = 'reduced_ocean_monthly_1x1deg.199301-199302.sos.nc'

GRID = 'regridded to FOO grid from native'
GRID_LABEL = 'gr'
NOM_RES = '10000 km'


def _build_cmor_dict(*, pp_dir, table_dir, outdir, exp_config,
                     varlist, mip_era='CMIP6', table_name='Omon',
                     freq='monthly', component='ocean_monthly_1x1deg',
                     chunk='P5Y', data_series_type='ts',
                     gridding=None, start='1993', stop='1993',
                     calendar_type='julian'):
    """Build the CMOR YAML mapping consumed by cmor_yaml_subtool."""
    if gridding is None:
        gridding = {
            'grid_label': GRID_LABEL,
            'grid_desc': GRID,
            'nom_res': NOM_RES,
        }
    return {
        'mip_era': mip_era,
        'directories': {
            'pp_dir': pp_dir,
            'table_dir': table_dir,
            'outdir': outdir,
        },
        'exp_json': exp_config,
        'start': start,
        'stop': stop,
        'calendar_type': calendar_type,
        'table_targets': [
            {
                'table_name': table_name,
                'freq': freq,
                'gridding': gridding,
                'target_components': [
                    {
                        'component_name': component,
                        'chunk': chunk,
                        'data_series_type': data_series_type,
                        'variable_list': varlist,
                    }
                ],
            }
        ],
    }


def _write_cmor_yaml(tmp_path, cmor_dict, filename='cmor.yaml'):
    """Write a self-contained CMOR YAML file and return its path."""
    yamlfile = tmp_path / filename
    yamlfile.write_text(yaml.safe_dump({'cmor': cmor_dict}, sort_keys=False), encoding='utf-8')
    return str(yamlfile)


def _write_table_json(table_dir, era, table_name, frequency='mon'):
    """Write a minimal MIP table JSON file and return the containing directory path."""
    table_dir = Path(table_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    (table_dir / f'{era}_{table_name}.json').write_text(
        json.dumps({'variable_entry': {'sos': {'frequency': frequency}}}),
        encoding='utf-8',
    )
    return str(table_dir)


@pytest.fixture
def yamler_env(tmp_path):
    """Set up a temporary pp directory tree and matching CMOR YAML."""
    component = 'ocean_monthly_1x1deg'
    freq = 'monthly'
    chunk_bronx = '5yr'

    indir = tmp_path / 'pp' / component / 'ts' / freq / chunk_bronx
    indir.mkdir(parents=True)

    nc_target = indir / NC_FILENAME
    subprocess.run(
        ['ncgen3', '-k', 'netCDF-4', '-o', str(nc_target), CDL_SOURCE],
        check=True,
    )
    assert nc_target.exists()

    outdir = tmp_path / 'cmor_output'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')

    local_exp_config = tmp_path / 'exp_config.json'
    shutil.copy(EXP_CONFIG, local_exp_config)

    cmor_dict = _build_cmor_dict(
        pp_dir=str(tmp_path / 'pp'),
        table_dir=table_dir,
        outdir=str(outdir),
        exp_config=str(local_exp_config),
        varlist=str(Path(VARLIST).resolve()),
    )

    return {
        'pp_dir': str(tmp_path / 'pp'),
        'outdir': str(outdir),
        'exp_config': str(local_exp_config),
        'table_dir': table_dir,
        'varlist': str(Path(VARLIST).resolve()),
        'yamlfile': _write_cmor_yaml(tmp_path, cmor_dict),
        'component': component,
        'freq': freq,
    }


def test_yamlfile_does_not_exist():
    """FileNotFoundError when yamlfile path does not exist."""
    with pytest.raises(FileNotFoundError):
        cmor_yaml_subtool(
            yamlfile='DOES_NOT_EXIST.yaml',
            dry_run_mode=True,
        )


def test_cmor_yaml_subtool_dry_run_false(yamler_env):  # pylint: disable=redefined-outer-name
    """Full end-to-end run should produce at least one CMORized file."""
    cmor_yaml_subtool(
        yamlfile=yamler_env['yamlfile'],
        dry_run_mode=False,
        run_one_mode=True,
    )

    output_nc_files = list(Path(yamler_env['outdir']).rglob('*.nc'))
    assert output_nc_files, 'cmor_yaml_subtool with dry_run=False produced no output'


def test_pp_dir_does_not_exist(tmp_path):
    """FileNotFoundError when pp_dir does not exist."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir='/no/such/pp_dir',
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
        ),
    )

    with pytest.raises(FileNotFoundError, match='does not exist'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_table_dir_does_not_exist(tmp_path):
    """FileNotFoundError when cmip_cmor_table_dir does not exist."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir='/no/such/table_dir',
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
        ),
    )

    with pytest.raises(FileNotFoundError, match='does not exist'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_exp_json_does_not_exist(tmp_path):
    """FileNotFoundError when exp_json path does not exist."""
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config='/no/such/exp.json',
            varlist=VARLIST,
        ),
    )

    with pytest.raises(FileNotFoundError, match='does not exist'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_mip_table_file_does_not_exist(tmp_path):
    """FileNotFoundError when the derived json_mip_table_config does not exist."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=str(tmp_path / 'tables'),
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
            table_name='NoSuchTable',
        ),
    )

    with pytest.raises(FileNotFoundError, match='does not exist'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_cmip7_freq_none_raises(tmp_path):
    """ValueError when mip_era=CMIP7 and freq is None."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    cmip7_table_dir = _write_table_json(tmp_path / 'tables', 'CMIP7', 'ocean')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=cmip7_table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
            mip_era='CMIP7',
            table_name='ocean',
            freq=None,
        ),
    )

    with pytest.raises(ValueError, match='freq is required for CMIP7'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_cmip6_freq_none_no_derivation_raises(tmp_path):
    """ValueError when CMIP6 freq is missing and cannot be derived."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    fake_table_dir = tmp_path / 'tables'
    fake_table_dir.mkdir()
    (fake_table_dir / 'CMIP6_FakeFx.json').write_text(json.dumps({
        'variable_entry': {
            'areacella': {'frequency': 'fx'}
        }
    }), encoding='utf-8')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=str(fake_table_dir),
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
            mip_era='CMIP6',
            table_name='FakeFx',
            freq=None,
        ),
    )

    with pytest.raises(ValueError, match='not enough frequency information'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_cmip6_freq_none_derivation_exception_caught(tmp_path):
    """KeyError in MIP-table frequency lookup should become ValueError."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    fake_table_dir = tmp_path / 'tables'
    fake_table_dir.mkdir()
    (fake_table_dir / 'CMIP6_FakeBad.json').write_text(json.dumps({
        'Header': {'table_id': 'Table FakeBad'}
    }), encoding='utf-8')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=str(fake_table_dir),
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
            mip_era='CMIP6',
            table_name='FakeBad',
            freq=None,
        ),
    )

    with pytest.raises(ValueError, match='not enough frequency information'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_gridding_dict_has_none_value_raises(tmp_path):
    """ValueError when a gridding field is None."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
            gridding={
                'grid_label': GRID_LABEL,
                'grid_desc': None,
                'nom_res': NOM_RES,
            },
        ),
    )

    with pytest.raises(ValueError, match='must have all three fields'):
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_outdir_creation_when_missing(tmp_path):
    """Missing cmorized_outdir should be created in dry-run mode."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'brand_new_outdir'
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
        ),
    )

    cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)
    assert outdir.is_dir()


def test_outdir_creation_failure_raises_oserror(tmp_path):
    """OSError when cmorized_outdir creation fails."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'impossible_outdir'
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
        ),
    )

    with patch.object(Path, 'mkdir', side_effect=PermissionError('no permission')):
        with pytest.raises(OSError, match='could not create cmorized_outdir'):
            cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_start_stop_calendar_missing_from_yaml(tmp_path):
    """Missing start/stop/calendar_type keys should log warnings and continue."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    cmor_dict = _build_cmor_dict(
        pp_dir=str(pp_dir),
        table_dir=table_dir,
        outdir=str(outdir),
        exp_config=str(local_exp),
        varlist=VARLIST,
    )
    del cmor_dict['start']
    del cmor_dict['stop']
    del cmor_dict['calendar_type']
    yamlfile = _write_cmor_yaml(tmp_path, cmor_dict)

    cmor_yaml_subtool(
        yamlfile=yamlfile,
        dry_run_mode=True,
        start=None,
        stop=None,
        calendar_type=None,
    )


def test_cmip6_freq_none_derivation_succeeds(tmp_path):
    """CMIP6 frequency should be derived from the MIP table when omitted."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
            mip_era='CMIP6',
            table_name='Omon',
            freq=None,
        ),
    )

    cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=True)


def test_dry_run_prints_cli_call(tmp_path):
    """dry_run_mode=True with print_cli_call=True should not create outputs."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
        ),
    )

    cmor_yaml_subtool(
        yamlfile=yamlfile,
        dry_run_mode=True,
        print_cli_call=True,
    )

    assert not list(outdir.rglob('*.nc'))


def test_dry_run_prints_python_call(tmp_path):
    """dry_run_mode=True with print_cli_call=False should not create outputs."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')
    yamlfile = _write_cmor_yaml(
        tmp_path,
        _build_cmor_dict(
            pp_dir=str(pp_dir),
            table_dir=table_dir,
            outdir=str(outdir),
            exp_config=str(local_exp),
            varlist=VARLIST,
        ),
    )

    cmor_yaml_subtool(
        yamlfile=yamlfile,
        dry_run_mode=True,
        print_cli_call=False,
    )


def test_disabled_table_target_is_skipped(tmp_path, caplog):
    """A table_target flagged disabled: true is skipped entirely -- never checked against a
    MIP table JSON (none is even written here) and never handed to cmor_run_subtool."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = tmp_path / 'tables'
    table_dir.mkdir()

    cmor_dict = _build_cmor_dict(
        pp_dir=str(pp_dir),
        table_dir=str(table_dir),
        outdir=str(outdir),
        exp_config=str(local_exp),
        varlist=VARLIST,
    )
    cmor_dict['table_targets'][0]['disabled'] = True
    yamlfile = _write_cmor_yaml(tmp_path, cmor_dict)

    with patch('fremor.cmor_yamler.cmor_run_subtool') as mock_run:
        with caplog.at_level(logging.INFO):
            cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=False)

    mock_run.assert_not_called()
    assert 'table_target Omon is disabled, skipping' in caplog.text


def test_disabled_false_table_target_is_still_processed(tmp_path):
    """disabled: false (an explicit, non-default value) behaves the same as the flag being
    absent -- the table_target is still processed normally."""
    local_exp = tmp_path / 'exp.json'
    shutil.copy(EXP_CONFIG, local_exp)
    pp_dir = tmp_path / 'pp'
    pp_dir.mkdir()
    outdir = tmp_path / 'out'
    outdir.mkdir()
    table_dir = _write_table_json(tmp_path / 'tables', 'CMIP6', 'Omon')

    cmor_dict = _build_cmor_dict(
        pp_dir=str(pp_dir),
        table_dir=table_dir,
        outdir=str(outdir),
        exp_config=str(local_exp),
        varlist=VARLIST,
    )
    cmor_dict['table_targets'][0]['disabled'] = False
    yamlfile = _write_cmor_yaml(tmp_path, cmor_dict)

    with patch('fremor.cmor_yamler.cmor_run_subtool') as mock_run:
        cmor_yaml_subtool(yamlfile=yamlfile, dry_run_mode=False)

    mock_run.assert_called_once()

    assert not list(outdir.rglob('*.nc'))
