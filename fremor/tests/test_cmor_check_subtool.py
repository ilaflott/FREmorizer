'''
tests for fremor.cmor_check.cmor_check_subtool
'''

import json
import tempfile
from pathlib import Path

import pytest
import yaml
from netCDF4 import Dataset

from fremor.cmor_check import cmor_check_subtool


@pytest.fixture
def temp_dir():
    ''' fixture yielding a temporary directory '''
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _write_table(tables_dir, table_name, var_names):
    variable_entry = {name: {'standard_name': name} for name in var_names}
    (Path(tables_dir) / f'CMIP6_{table_name}.json').write_text(
        json.dumps({'Header': {'table_id': f'Table {table_name}'}, 'variable_entry': variable_entry}),
        encoding='utf-8'
    )


def _write_amon_table(tables_dir, var_names):
    _write_table(tables_dir, 'Amon', var_names)


def _component_entry(component_name, variable_list_path, chunk='P5Y'):
    return {
        'component_name': component_name,
        'variable_list': str(variable_list_path),
        'data_series_type': 'ts',
        'chunk': chunk,
    }


def _table_target(table_name, components, freq='monthly'):
    return {
        'table_name': table_name,
        'freq': freq,
        'gridding': {'grid_label': 'gn', 'grid_desc': 'd', 'nom_res': '100 km'},
        'target_components': components,
    }


def _write_yaml(temp_dir, table_targets, mip_era='cmip6', pp_dir=None, table_dir=None): # pylint: disable=redefined-outer-name
    ''' write a minimal but structurally-real cmor yaml (as fremor config would produce) '''
    temp_root = Path(temp_dir)
    pp_dir = Path(pp_dir) if pp_dir else temp_root / 'pp'
    pp_dir.mkdir(parents=True, exist_ok=True)
    table_dir = Path(table_dir) if table_dir else temp_root / 'tables'
    table_dir.mkdir(parents=True, exist_ok=True)

    doc = {
        'cmor': {
            'start': None,
            'stop': None,
            'calendar_type': 'noleap',
            'mip_era': mip_era,
            'exp_json': str(temp_root / 'exp.json'),
            'directories': {
                'pp_dir': str(pp_dir),
                'table_dir': str(table_dir),
                'outdir': str(temp_root / 'out'),
            },
            'table_targets': table_targets,
        }
    }
    yamlfile = temp_root / 'cmor.yaml'
    yamlfile.write_text(yaml.safe_dump(doc), encoding='utf-8')
    return str(yamlfile)


def test_cmor_check_subtool_yamlfile_dne_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' yamlfile arg does not exist '''
    yamlfile = str(Path(temp_dir) / 'cmor_dne.yaml')
    with pytest.raises(FileNotFoundError, match=f'yamlfile does not exist: {yamlfile}'):
        cmor_check_subtool(yamlfile=yamlfile)


def test_cmor_check_subtool_noppdir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' pp_dir referenced by the yaml does not exist '''
    temp_root = Path(temp_dir)
    pp_dir = temp_root / 'pp_dne'
    table_dir = temp_root / 'tables'
    table_dir.mkdir()
    yamlfile = _write_yaml(temp_dir, [], pp_dir=pp_dir, table_dir=table_dir)
    # _write_yaml eagerly creates pp_dir/table_dir; remove pp_dir to simulate a stale yaml
    pp_dir.rmdir()
    with pytest.raises(FileNotFoundError, match=f'pp_dir from yamlfile does not exist: {pp_dir}'):
        cmor_check_subtool(yamlfile=yamlfile)


def test_cmor_check_subtool_notabledir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_dir referenced by the yaml does not exist '''
    temp_root = Path(temp_dir)
    table_dir = temp_root / 'tables_dne'
    yamlfile = _write_yaml(temp_dir, [], table_dir=table_dir)
    table_dir.rmdir()
    with pytest.raises(FileNotFoundError,
                       match=f'mip_tables_dir from yamlfile does not exist: {table_dir}'):
        cmor_check_subtool(yamlfile=yamlfile)


def test_cmor_check_subtool_no_table_targets_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' yaml has no table_targets at all '''
    yamlfile = _write_yaml(temp_dir, [])
    with pytest.raises(ValueError, match=f'no table_targets found in {yamlfile}'):
        cmor_check_subtool(yamlfile=yamlfile)


def test_cmor_check_subtool_missing_table_json_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' a table_target references a table with no corresponding MIP table JSON file '''
    temp_root = Path(temp_dir)
    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    varlist_path = varlist_dir / 'CMIP6_Amon_atmos.list'
    varlist_path.write_text(json.dumps({'t_ref': 'tas'}), encoding='utf-8')

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', varlist_path)])
    ])
    with pytest.raises(FileNotFoundError, match='MIP table for Amon not found'):
        cmor_check_subtool(yamlfile=yamlfile)


def test_cmor_check_subtool_cmip6plus_table_uses_mip_prefix(temp_dir): # pylint: disable=redefined-outer-name
    ''' cmip6plus's mip-cmor-tables repo names its table JSON files 'MIP_<table>.json',
    not '<ERA>_<table>.json' like cmip6/cmip7 -- fremor check (and map/stage, which share
    the same table-path resolution) must look for that prefix instead. '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    (tables_dir / 'MIP_ACmon.json').write_text(
        json.dumps({'Header': {'table_id': 'Table ACmon'},
                    'variable_entry': {'tas': {'standard_name': 'tas'}}}),
        encoding='utf-8'
    )

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'MIP_ACmon_atmos.list'
    atmos_list.write_text(json.dumps({'t_ref': 'tas'}), encoding='utf-8')

    yamlfile = _write_yaml(temp_dir, [
        _table_target('ACmon', [_component_entry('atmos', atmos_list)])
    ], mip_era='cmip6plus', table_dir=tables_dir)

    report = cmor_check_subtool(yamlfile=yamlfile)

    assert report['ACmon']['reference_var_count'] == 1
    assert report['ACmon']['unmapped'] == []


def test_cmor_check_subtool_reports_unmapped_and_multiply_mapped(temp_dir): # pylint: disable=redefined-outer-name
    ''' full report: unmapped, multiply-mapped, and unknown-mapped variables '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas', 'pr', 'ps'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(
        json.dumps({'t_ref': 'tas', 'precip': 'pr', 'sfc_pres': 'ps', 'weird': 'not_a_real_var'}),
        encoding='utf-8'
    )
    scalar_list = varlist_dir / 'CMIP6_Amon_atmos_scalar.list'
    scalar_list.write_text(
        json.dumps({'sfc_pres_scalar': 'ps'}),
        encoding='utf-8'
    )

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [
            _component_entry('atmos', atmos_list),
            _component_entry('atmos_scalar', scalar_list),
        ])
    ], table_dir=tables_dir)

    report = cmor_check_subtool(yamlfile=yamlfile)

    assert report['Amon']['reference_var_count'] == 3
    assert report['Amon']['unmapped'] == []
    assert set(report['Amon']['multiply_mapped']) == {'ps'}
    assert sorted(report['Amon']['multiply_mapped']['ps']) == [
        ('atmos', 'sfc_pres'), ('atmos_scalar', 'sfc_pres_scalar')
    ]
    assert report['Amon']['unknown_mapped'] == ['not_a_real_var']


def test_cmor_check_subtool_reports_fully_unmapped_table(temp_dir): # pylint: disable=redefined-outer-name
    ''' a component whose varlist file is missing should report every variable unmapped '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas', 'pr'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    missing_list = varlist_dir / 'CMIP6_Amon_atmos.list'  # never written

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', missing_list)])
    ], table_dir=tables_dir)

    report = cmor_check_subtool(yamlfile=yamlfile)

    assert report['Amon']['unmapped'] == ['pr', 'tas']
    assert report['Amon']['multiply_mapped'] == {}
    assert report['Amon']['unknown_mapped'] == []


def test_cmor_check_subtool_show_mapped(temp_dir): # pylint: disable=redefined-outer-name
    ''' show_mapped=True reports variables mapped from exactly one component/diagnostic '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas', 'pr', 'ps'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(
        json.dumps({'t_ref': 'tas', 'sfc_pres': 'ps'}),
        encoding='utf-8'
    )
    scalar_list = varlist_dir / 'CMIP6_Amon_atmos_scalar.list'
    scalar_list.write_text(
        json.dumps({'sfc_pres_scalar': 'ps'}),
        encoding='utf-8'
    )

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [
            _component_entry('atmos', atmos_list),
            _component_entry('atmos_scalar', scalar_list),
        ])
    ], table_dir=tables_dir)

    report_default = cmor_check_subtool(yamlfile=yamlfile)
    assert 'one_to_one_mapped' not in report_default['Amon']

    report = cmor_check_subtool(yamlfile=yamlfile, show_mapped=True)
    # 'ps' is multiply-mapped so it should not appear as one-to-one, only 'tas' should.
    assert report['Amon']['one_to_one_mapped'] == {'tas': ('atmos', 't_ref')}


def test_cmor_check_subtool_table_patterns_filters_tables(temp_dir): # pylint: disable=redefined-outer-name
    ''' positional table_patterns restrict which MIP tables are checked, wildcards included '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table(tables_dir, 'Amon', ['tas'])
    _write_table(tables_dir, 'Lmon', ['mrso'])
    _write_table(tables_dir, 'AERmon', ['o3'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    amon_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    amon_list.write_text('{}', encoding='utf-8')
    lmon_list = varlist_dir / 'CMIP6_Lmon_land.list'
    lmon_list.write_text('{}', encoding='utf-8')
    aermon_list = varlist_dir / 'CMIP6_AERmon_atmos.list'
    aermon_list.write_text('{}', encoding='utf-8')

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', amon_list)]),
        _table_target('Lmon', [_component_entry('land', lmon_list)]),
        _table_target('AERmon', [_component_entry('atmos', aermon_list)]),
    ], table_dir=tables_dir)

    report = cmor_check_subtool(yamlfile=yamlfile, table_patterns=['Lmon'])
    assert set(report) == {'Lmon'}

    report_wild = cmor_check_subtool(yamlfile=yamlfile, table_patterns=['AER*'])
    assert set(report_wild) == {'AERmon'}

    report_all = cmor_check_subtool(yamlfile=yamlfile)
    assert set(report_all) == {'Amon', 'Lmon', 'AERmon'}


def test_cmor_check_subtool_table_patterns_no_match_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_patterns matching nothing raises ValueError '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    amon_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    amon_list.write_text('{}', encoding='utf-8')

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', amon_list)])
    ], table_dir=tables_dir)

    with pytest.raises(ValueError, match='no table_targets .* matched table_patterns'):
        cmor_check_subtool(yamlfile=yamlfile, table_patterns=['Omon'])


# ---------------------------------------------------------------------------
# check_staging / check_dims: file-level checks against real pp_dir contents
# ---------------------------------------------------------------------------

def _write_table_with_dims(tables_dir, table_name, var_dims):
    ''' like _write_table, but each value is a MIP-style space-delimited dimensions string '''
    variable_entry = {
        name: {'standard_name': name, 'dimensions': dims} for name, dims in var_dims.items()
    }
    (Path(tables_dir) / f'CMIP6_{table_name}.json').write_text(
        json.dumps({'Header': {'table_id': f'Table {table_name}'}, 'variable_entry': variable_entry}),
        encoding='utf-8'
    )


def _write_cmip7_table_with_dims(tables_dir, table_name, var_dims):
    ''' like _write_table_with_dims, but for a CMIP7-style table: keys are brand-suffixed
    (``{var}_{brand}``) and 'dimensions' is a JSON list rather than a space-delimited
    string, matching the real mip-cmor-tables CMIP7 format. '''
    variable_entry = {
        name: {'standard_name': name.split('_')[0], 'dimensions': dims}
        for name, dims in var_dims.items()
    }
    (Path(tables_dir) / f'CMIP7_{table_name}.json').write_text(
        json.dumps({'Header': {'table_id': f'Table {table_name}'}, 'variable_entry': variable_entry}),
        encoding='utf-8'
    )


def _write_input_nc(nc_path, local_var, vertical_dim=None):
    ''' write a minimal real netCDF file with a time dim, optional vertical dim (axis='Z'),
    and a data variable named local_var over those dims '''
    with Dataset(str(nc_path), 'w') as ds:
        ds.createDimension('time', 2)
        ds.createVariable('time', 'f4', ('time',))
        dims = ['time']
        if vertical_dim is not None:
            ds.createDimension(vertical_dim, 3)
            vert_var = ds.createVariable(vertical_dim, 'f4', (vertical_dim,))
            vert_var.axis = 'Z'
            dims.append(vertical_dim)
        ds.createVariable(local_var, 'f4', tuple(dims))


def _input_dir(pp_dir, component_name, freq='monthly', chunk_bronx='5yr'):
    input_dir = Path(pp_dir) / component_name / 'ts' / freq / chunk_bronx
    input_dir.mkdir(parents=True, exist_ok=True)
    return input_dir


def test_cmor_check_subtool_staging_missing_files(temp_dir): # pylint: disable=redefined-outer-name
    ''' check_staging: one-to-one-mapped variable with no input files under pp_dir at all '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'tas': 'tas'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    report = cmor_check_subtool(yamlfile=yamlfile, check_staging=True)
    assert report['Amon']['files']['tas']['staging']['status'] == 'missing'


def test_cmor_check_subtool_staging_ok(temp_dir, capsys): # pylint: disable=redefined-outer-name
    ''' check_staging: normal staged results remain in structured data but are hidden from
    the human-readable report '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'tas': 'tas'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.tas.nc', 'tas')

    report = cmor_check_subtool(yamlfile=yamlfile, check_staging=True)
    staging = report['Amon']['files']['tas']['staging']
    assert staging['status'] == 'staged'
    assert staging['unstaged_files'] == []
    assert staging['gaps'] == []
    assert 'staging=staged' not in capsys.readouterr().out


def test_cmor_check_subtool_staging_gap_detection(temp_dir, capsys): # pylint: disable=redefined-outer-name
    ''' check_staging: a filename-only scan should catch a missing chunk between two others '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'tas': 'tas'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.tas.nc', 'tas')
    _write_input_nc(comp_dir / 'atmos.199001-199412.tas.nc', 'tas')  # gap: 1984-1989 missing

    report = cmor_check_subtool(yamlfile=yamlfile, check_staging=True)
    assert report['Amon']['files']['tas']['staging']['gaps'] == ['1983-1990']
    assert 'date-range gaps: 1983-1990' in capsys.readouterr().out


def test_cmor_check_subtool_staging_dmls_offline(temp_dir, monkeypatch, capsys): # pylint: disable=redefined-outer-name
    ''' check_staging: when a dmls binary is available, its (OFL) tag marks a file unstaged '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'tas': 'tas'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    nc_path = comp_dir / 'atmos.197901-198312.tas.nc'
    _write_input_nc(nc_path, 'tas')

    import subprocess as subprocess_mod # pylint: disable=import-outside-toplevel

    class _FakeResult:
        stdout = f'-rw-r--r-- 1 user group 123 Jan 1 12:00 (OFL) {nc_path}\n'

    def _fake_run(cmd, **kwargs): # pylint: disable=unused-argument
        assert cmd[0] == 'dmls'
        return _FakeResult()

    monkeypatch.setattr(subprocess_mod, 'run', _fake_run)

    report = cmor_check_subtool(yamlfile=yamlfile, check_staging=True, dmls_bin='dmls')
    staging = report['Amon']['files']['tas']['staging']
    assert staging['status'] == 'unstaged'
    assert staging['unstaged_files'] == [str(nc_path)]
    assert 'staging=unstaged' in capsys.readouterr().out


def test_cmor_check_subtool_dims_ok(temp_dir, capsys): # pylint: disable=redefined-outer-name
    ''' check_dims: a matching model-level dimension remains in structured data but is
    hidden from the human-readable report '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table_with_dims(tables_dir, 'Amon', {'ta': 'longitude latitude alevel time'})

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'ta': 'ta'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.ta.nc', 'ta', vertical_dim='lev')
    _write_input_nc(comp_dir / 'atmos.197901-198312.ps.nc', 'ps')

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    dims = report['Amon']['files']['ta']['dims']
    assert dims['status'] == 'ok'
    assert dims['input_vertical_dim'] == 'alevel'
    assert dims['mip_table_vertical_dims'] == ['alevel']
    assert 'dims=ok' not in capsys.readouterr().out


def test_cmor_check_subtool_dims_mismatch_plev_vs_alevel(temp_dir, capsys): # pylint: disable=redefined-outer-name
    ''' check_dims: table wants fixed pressure levels but input is only on native model levels '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table_with_dims(tables_dir, 'Amon', {'ta': 'longitude latitude plev19 time'})

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'ta': 'ta'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.ta.nc', 'ta', vertical_dim='lev')

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    dims = report['Amon']['files']['ta']['dims']
    assert dims['status'] == 'vertical_dim_mismatch'
    assert dims['input_vertical_dim'] == 'alevel'
    assert dims['mip_table_vertical_dims'] == ['plev19']
    assert 'dims=vertical_dim_mismatch' in capsys.readouterr().out


def test_cmor_check_subtool_dims_missing_vertical(temp_dir): # pylint: disable=redefined-outer-name
    ''' check_dims: table wants a vertical dim but the mapped input file is purely 2D '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table_with_dims(tables_dir, 'Amon', {'ta': 'longitude latitude alevel time'})

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'ta': 'ta'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.ta.nc', 'ta', vertical_dim=None)

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    dims = report['Amon']['files']['ta']['dims']
    assert dims['status'] == 'missing_vertical_dim'
    assert dims['input_vertical_dim'] is None


def test_cmor_check_subtool_dims_unexpected_vertical(temp_dir): # pylint: disable=redefined-outer-name
    ''' check_dims: table expects no vertical dim but the input file has one '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table_with_dims(tables_dir, 'Amon', {'tas': 'longitude latitude time'})

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'tas': 'tas'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.tas.nc', 'tas', vertical_dim='lev')

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    dims = report['Amon']['files']['tas']['dims']
    assert dims['status'] == 'unexpected_vertical_dim'


def test_cmor_check_subtool_dims_missing_ps_file(temp_dir, capsys): # pylint: disable=redefined-outer-name
    ''' check_dims: a hybrid-sigma ('alevel') variable is missing its companion .ps.nc file '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table_with_dims(tables_dir, 'Amon', {'ta': 'longitude latitude alevel time'})

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'ta': 'ta'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.ta.nc', 'ta', vertical_dim='lev')
    # no atmos.197901-198312.ps.nc written

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    dims = report['Amon']['files']['ta']['dims']
    assert dims['status'] == 'ok'
    assert dims['missing_ps_file'] == str(comp_dir / 'atmos.197901-198312.ps.nc')
    output = capsys.readouterr().out
    assert 'dims=ok' in output
    assert 'missing companion ps file' in output


def test_cmor_check_subtool_dims_ok_cmip7_list_dimensions(temp_dir): # pylint: disable=redefined-outer-name
    ''' check_dims: CMIP7 tables declare 'dimensions' as a JSON list (not a space-delimited
    string like CMIP6/CMIP6Plus) -- _mip_table_vertical_token must handle both. '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_cmip7_table_with_dims(tables_dir, 'Amon', {
        'ta_tavg-al': ['longitude', 'latitude', 'alevel', 'time'],
    })

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP7_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'ta': 'ta'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], mip_era='cmip7', table_dir=tables_dir, pp_dir=pp_dir)

    comp_dir = _input_dir(pp_dir, 'atmos')
    _write_input_nc(comp_dir / 'atmos.197901-198312.ta.nc', 'ta', vertical_dim='lev')

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    dims = report['Amon']['files']['ta']['dims']
    assert dims['status'] == 'ok'
    assert dims['input_vertical_dim'] == 'alevel'
    assert dims['mip_table_vertical_dims'] == ['alevel']


def test_cmor_check_subtool_dims_unknown_when_no_files(temp_dir): # pylint: disable=redefined-outer-name
    ''' check_dims: nothing to inspect when there are no input files for a mapped variable '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_table_with_dims(tables_dir, 'Amon', {'ta': 'longitude latitude alevel time'})

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'ta': 'ta'}), encoding='utf-8')

    pp_dir = temp_root / 'pp'
    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir, pp_dir=pp_dir)

    report = cmor_check_subtool(yamlfile=yamlfile, check_dims=True)
    assert report['Amon']['files']['ta']['dims']['status'] == 'unknown'


def test_cmor_check_subtool_no_files_key_by_default(temp_dir): # pylint: disable=redefined-outer-name
    ''' neither check_staging nor check_dims given: no 'files' key at all (backward compatible) '''
    temp_root = Path(temp_dir)
    tables_dir = temp_root / 'tables'
    tables_dir.mkdir()
    _write_amon_table(tables_dir, ['tas'])

    varlist_dir = temp_root / 'varlists'
    varlist_dir.mkdir()
    atmos_list = varlist_dir / 'CMIP6_Amon_atmos.list'
    atmos_list.write_text(json.dumps({'tas': 'tas'}), encoding='utf-8')

    yamlfile = _write_yaml(temp_dir, [
        _table_target('Amon', [_component_entry('atmos', atmos_list)])
    ], table_dir=tables_dir)

    report = cmor_check_subtool(yamlfile=yamlfile)
    assert 'files' not in report['Amon']
