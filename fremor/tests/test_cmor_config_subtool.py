'''
largely tests for fremor.cmor_config.cmor_config_subtool error conditions / messages
'''

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

import pytest

from fremor.cmor_config import cmor_config_subtool, _bronx_to_iso_chunk, _load_config_yaml

@pytest.fixture
def temp_dir():
    ''' fixture yielding a temporary directory '''
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_cmor_config_subtool_noppdir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' pp_dir arg does not exist '''
    pp_dir_targ= Path(temp_dir) / 'foobar'
    mip_tables_targ=''
    mip_era_targ=''
    exp_config_targ=''
    with pytest.raises(FileNotFoundError,
                       match=f'pp_dir does not exist: {pp_dir_targ}'):
        cmor_config_subtool(pp_dir=pp_dir_targ,
                            mip_tables_dir=mip_tables_targ,
                            mip_era=mip_era_targ,
                            exp_config=exp_config_targ,
                            output_yaml='',
                            output_dir='',
                            varlist_dir='',
        )


def test_cmor_config_subtool_notabledir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' mip_tables_dir arg does not exist '''
    pp_dir_targ=Path(temp_dir) / 'foobar'
    mip_tables_targ='fremor/tests/test_files/cmip7-cmor-tables/tablesDNE'
    mip_era_targ=''
    exp_config_targ=''
    pp_dir_targ.mkdir(exist_ok=True,parents=True)
    with pytest.raises(FileNotFoundError,
                       match=f'mip_tables_dir does not exist: {mip_tables_targ}'):
        cmor_config_subtool(pp_dir=pp_dir_targ,
                            mip_tables_dir=mip_tables_targ,
                            mip_era=mip_era_targ,
                            exp_config=exp_config_targ,
                            output_yaml='',
                            output_dir='',
                            varlist_dir='',
        )


def test_cmor_config_subtool_noexpcfg_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' exp_config arg does not exist '''
    pp_dir_targ=Path(temp_dir) / 'foobar'
    mip_tables_targ=Path(temp_dir) / 'tables'
    mip_era_targ=''
    exp_config_targ='fremor/tests/test_files/DNE_CMOR_CMIP7_input_example.json'
    pp_dir_targ.mkdir(exist_ok=True,parents=True)
    mip_tables_targ.mkdir(exist_ok=True, parents=True)
    with pytest.raises(FileNotFoundError,
                       match=f'exp_config does not exist: {exp_config_targ}'):
        cmor_config_subtool(pp_dir=pp_dir_targ,
                            mip_tables_dir=mip_tables_targ,
                            mip_era=mip_era_targ,
                            exp_config=exp_config_targ,
                            output_yaml='',
                            output_dir='',
                            varlist_dir='',
        )


def test_cmor_config_subtool_nomip6_tables_in_mip7_tables_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' trying to target mip7 tables for mip6 '''
    pp_dir_targ= Path(temp_dir) / 'foobar'
    mip_tables_targ=Path(temp_dir) / 'tables'
    mip_era_targ='cmip6'
    exp_config_targ=Path(temp_dir) / 'exp.json'
    pp_dir_targ.mkdir(exist_ok=True,parents=True)
    mip_tables_targ.mkdir(exist_ok=True, parents=True)
    (mip_tables_targ / 'CMIP7_ocean.json').write_text('{}', encoding='utf-8')
    exp_config_targ.write_text(json.dumps({
        'grid': 'native grid from exp config',
        'nominal_resolution': '100 km',
    }), encoding='utf-8')
    with pytest.raises(ValueError,
                       match=f'no MIP tables found in {mip_tables_targ} for era {mip_era_targ} after filtering'):
        cmor_config_subtool(pp_dir=pp_dir_targ,
                            mip_tables_dir=mip_tables_targ,
                            mip_era=mip_era_targ,
                            exp_config=exp_config_targ,
                            output_yaml='',
                            output_dir='',
                            varlist_dir='',
        )


def test_cmor_config_subtool_nomip7_tables_in_mip6_tables_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' trying to target mip6 tables for mip7 '''
    pp_dir_targ= Path(temp_dir) / 'foobar'
    mip_tables_targ=Path(temp_dir) / 'tables'
    mip_era_targ='cmip7'
    exp_config_targ=Path(temp_dir) / 'exp.json'
    pp_dir_targ.mkdir(exist_ok=True,parents=True)
    mip_tables_targ.mkdir(exist_ok=True, parents=True)
    (mip_tables_targ / 'CMIP6_Omon.json').write_text('{}', encoding='utf-8')
    exp_config_targ.write_text(
        json.dumps(
            {'grid': 'native grid from exp config',
             'nominal_resolution': '100 km',}),
        encoding='utf-8')
    with pytest.raises(ValueError,
                       match=f'no MIP tables found in {mip_tables_targ} for era {mip_era_targ} after filtering'):
        cmor_config_subtool(pp_dir=pp_dir_targ,
                            mip_tables_dir=mip_tables_targ,
                            mip_era=mip_era_targ,
                            exp_config=exp_config_targ,
                            output_yaml='',
                            output_dir='',
                            varlist_dir='',
        )


def test_cmor_config_subtool_writes_self_contained_yaml(temp_dir): # pylint: disable=redefined-outer-name
    ''' generated config yaml should be directly loadable without unresolved aliases '''
    temp_root = Path(temp_dir)
    pp_dir = temp_root / 'pp'
    target_dir = pp_dir / 'ocean' / 'ts' / 'monthly' / '5yr'
    target_dir.mkdir(parents=True)
    (target_dir / 'dummy.nc').touch()

    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    (tables_dir / 'CMIP6_Omon.json').write_text(json.dumps({
        'variable_entry': {'sos': {'frequency': 'mon'}}
    }))

    exp_config = temp_root / 'exp.json'
    exp_config.write_text(json.dumps({
        'grid': 'native grid from exp config',
        'nominal_resolution': '100 km',
    }))

    output_yaml = temp_root / 'cmor.yaml'
    output_dir = temp_root / 'cmor_out'
    varlist_dir = temp_root / 'varlists'

    def _fake_make_simple_varlist(
            dir_targ,
            output_variable_list,
            json_mip_table,
            return_none_if_no_mip_vars, # pylint: disable=unused-argument
    ):
        del dir_targ, json_mip_table
        Path(output_variable_list).write_text('{}', encoding='utf-8')

    Path(varlist_dir).mkdir(parents=True)
    Path(varlist_dir / 'CMIP6_Omon_ocean.list').touch() # for variable list recreation coverage
    with patch('fremor.cmor_config.make_simple_varlist', side_effect=_fake_make_simple_varlist):
        cmor_config_subtool(
            pp_dir=str(pp_dir),
            mip_tables_dir=str(tables_dir),
            mip_era='cmip6',
            exp_config=str(exp_config),
            output_yaml=str(output_yaml),
            output_dir=str(output_dir),
            varlist_dir=str(varlist_dir),
            freq='monthly',
            chunk='5yr',
            grid='gn',
            overwrite=True
        )

    loaded_yaml = yaml.safe_load(output_yaml.read_text(encoding='utf-8'))
    target_component = loaded_yaml['cmor']['table_targets'][0]['target_components'][0]
    assert loaded_yaml['cmor']['start'] is None
    assert loaded_yaml['cmor']['stop'] is None
    assert loaded_yaml['cmor']['table_targets'][0]['gridding']['grid_label'] == 'gn'
    assert loaded_yaml['cmor']['table_targets'][0]['gridding']['grid_desc'] == 'native grid from exp config'
    assert target_component['chunk'] == 'P5Y'

def test_cmor_config_subtool_err_no_ppcompdirs(temp_dir): # pylint: disable=redefined-outer-name
    ''' generated config yaml should be directly loadable without unresolved aliases '''
    temp_root = Path(temp_dir)
    pp_dir = temp_root / 'pp'
    target_dir = pp_dir # / 'ocean' / 'ts' / 'monthly' / '5yr'
    target_dir.mkdir(parents=True)
    #(target_dir / 'dummy.nc').touch()

    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    (tables_dir / 'CMIP6_Omon.json').write_text(json.dumps({
        'variable_entry': {'sos': {'frequency': 'mon'}}
    }))

    exp_config = temp_root / 'exp.json'
    exp_config.write_text(json.dumps({
        'grid': 'native grid from exp config',
        'nominal_resolution': '100 km',
    }))

    output_yaml = temp_root / 'cmor.yaml'
    output_dir = temp_root / 'cmor_out'
    varlist_dir = temp_root / 'varlists'

    with pytest.raises(FileNotFoundError):
        cmor_config_subtool(
            pp_dir=str(pp_dir),
            mip_tables_dir=str(tables_dir),
            mip_era='cmip6',
            exp_config=str(exp_config),
            output_yaml=str(output_yaml),
            output_dir=str(output_dir),
            varlist_dir=str(varlist_dir),
            freq='monthly',
            chunk='5yr',
            grid='gn',
        )

def test_bronx_to_iso_chunk_cases():
    ''' test cases of conversion to ISO from bronx or ISO '''
    assert _bronx_to_iso_chunk('5yr') == 'P5Y'
    assert _bronx_to_iso_chunk('P5Y') == 'P5Y'
    with pytest.raises(ValueError, match='chunk must be ISO8601 like P5Y or bronx-style like 5yr, got 999999'):
        _bronx_to_iso_chunk('999999')


def test_cmor_config_subtool_nocmip6plus_tables_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' cmip6plus filters on the MIP_*.json glob, distinct from the CMIP6/CMIP7 <era>_*.json one '''
    pp_dir_targ = Path(temp_dir) / 'foobar'
    mip_tables_targ = Path(temp_dir) / 'tables'
    mip_era_targ = 'cmip6plus'
    exp_config_targ = Path(temp_dir) / 'exp.json'
    pp_dir_targ.mkdir(exist_ok=True, parents=True)
    mip_tables_targ.mkdir(exist_ok=True, parents=True)
    # a CMIP6-style name doesn't match the cmip6plus 'MIP_*.json' glob
    (mip_tables_targ / 'CMIP6_Omon.json').write_text('{}', encoding='utf-8')
    exp_config_targ.write_text(json.dumps({
        'grid': 'native grid from exp config',
        'nominal_resolution': '100 km',
    }), encoding='utf-8')
    with pytest.raises(ValueError,
                       match=f'no MIP tables found in {mip_tables_targ} for era {mip_era_targ} after filtering'):
        cmor_config_subtool(pp_dir=pp_dir_targ,
                            mip_tables_dir=mip_tables_targ,
                            mip_era=mip_era_targ,
                            exp_config=exp_config_targ,
                            output_yaml='',
                            output_dir='',
                            varlist_dir='',
        )


def test_load_config_yaml_rejects_yaml_without_cmor_section(temp_dir): # pylint: disable=redefined-outer-name
    ''' _load_config_yaml is what fremor check/map use to resolve a self-contained yaml '''
    bad_yaml = Path(temp_dir) / 'bad.yaml'
    bad_yaml.write_text(yaml.safe_dump({'not_cmor': {}}), encoding='utf-8')

    with pytest.raises(ValueError, match="expected a top-level mapping"):
        _load_config_yaml(str(bad_yaml))
