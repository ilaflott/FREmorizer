'''
tests for fremor.cmor_map
'''

import json
import stat
import tempfile
from pathlib import Path

import pytest
import yaml
from netCDF4 import Dataset
from textual.worker import WorkerCancelled

from fremor.cmor_map import (
    MapApp,
    MapSession,
    _discover_chunks,
    _discover_freqs,
    _discover_nc_files,
    _discover_pp_components,
    _find_ncinfo_bin,
    _format_variable_detail,
    _inspect_nc_variable,
    _local_var_name_from_nc_path,
    _ncinfo_preview,
    _preview_nc_file,
)


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


def _write_nc_file(nc_path, var_name, units='degC', long_name='a variable'):
    with Dataset(nc_path, 'w') as ds:
        ds.createDimension('time', 2)
        var = ds.createVariable(var_name, 'f4', ('time',))
        var.units = units
        var.long_name = long_name


def _write_fake_ncinfo(script_path, variables_json):
    script_path = Path(script_path)
    script_path.write_text(
        '#!/usr/bin/env python3\n'
        'import sys\n'
        f'print(\'{{"variables": {variables_json}}}\')\n',
        encoding='utf-8'
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script_path)


def _write_broken_ncinfo(script_path):
    script_path = Path(script_path)
    script_path.write_text('#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n', encoding='utf-8')
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script_path)


# ── pp-directory discovery helpers ──────────────────────────────────────────

def test_discover_pp_components(temp_dir): # pylint: disable=redefined-outer-name
    ''' component discovery ignores non-directory entries and respects the glob '''
    pp_dir = Path(temp_dir) / 'pp'
    (pp_dir / 'ocean_monthly').mkdir(parents=True)
    (pp_dir / 'atmos').mkdir(parents=True)
    (pp_dir / 'foo.json').parent.mkdir(parents=True, exist_ok=True)
    (pp_dir / 'foo.json').touch()

    components = _discover_pp_components(str(pp_dir))
    assert sorted(Path(c).name for c in components) == ['atmos', 'ocean_monthly']

    filtered = _discover_pp_components(str(pp_dir), 'ocean*')
    assert [Path(c).name for c in filtered] == ['ocean_monthly']


def test_discover_freqs_and_chunks_and_nc_files(temp_dir): # pylint: disable=redefined-outer-name
    ''' freq/chunk/file discovery walks the FRE ts/<freq>/<chunk> convention, ignoring av/ '''
    component_dir = Path(temp_dir) / 'ocean_monthly'
    ts_dir = component_dir / 'ts' / 'monthly' / '5yr'
    ts_dir.mkdir(parents=True)
    (component_dir / 'av').mkdir(parents=True)  # must be ignored -- not a freq

    nc_path = ts_dir / 'ocean_monthly.000101-000512.sos.nc'
    _write_nc_file(nc_path, 'sos')

    assert _discover_freqs(str(component_dir)) == ['monthly']
    assert _discover_chunks(str(component_dir), 'monthly') == ['5yr']
    files = _discover_nc_files(str(component_dir), 'monthly', '5yr')
    assert files == [str(nc_path)]


def test_discover_freqs_no_ts_dir(temp_dir): # pylint: disable=redefined-outer-name
    ''' a component with no ts/ dir yields no freqs '''
    component_dir = Path(temp_dir) / 'land'
    (component_dir / 'av').mkdir(parents=True)
    assert _discover_freqs(str(component_dir)) == []


def test_local_var_name_from_nc_path():
    ''' local var name is the second-to-last dot-delimited field '''
    assert _local_var_name_from_nc_path('/a/b/ocean_monthly.000101-000512.sos.nc') == 'sos'
    assert _local_var_name_from_nc_path('component.199301-199302.sea_sfc_salinity.nc') == \
        'sea_sfc_salinity'


# ── netCDF4-based preview fallback ──────────────────────────────────────────

def test_inspect_nc_variable_happy_path(temp_dir): # pylint: disable=redefined-outer-name
    ''' happy path returns attrs plus dimensions/shape '''
    nc_path = Path(temp_dir) / 'test.nc'
    _write_nc_file(nc_path, 'sos', units='psu', long_name='sea surface salinity')

    result = _inspect_nc_variable(str(nc_path), 'sos')
    assert result['units'] == 'psu'
    assert result['long_name'] == 'sea surface salinity'
    assert result['dimensions'] == ['time']
    assert result['shape'] == [2]


def test_inspect_nc_variable_missing_var(temp_dir): # pylint: disable=redefined-outer-name
    ''' missing variable name returns None, not a raise '''
    nc_path = Path(temp_dir) / 'test.nc'
    _write_nc_file(nc_path, 'sos')
    assert _inspect_nc_variable(str(nc_path), 'not_a_var') is None


def test_inspect_nc_variable_missing_file(temp_dir): # pylint: disable=redefined-outer-name
    ''' missing/unreadable file returns None, not a raise '''
    assert _inspect_nc_variable(str(Path(temp_dir) / 'does_not_exist.nc'), 'sos') is None


# ── ncinfo external-tool preview ────────────────────────────────────────────

def test_find_ncinfo_bin_explicit_path_wins(temp_dir): # pylint: disable=redefined-outer-name
    ''' an explicit ncinfo_bin is used as-is, without consulting PATH '''
    fake_bin = str(Path(temp_dir) / 'ncinfo')
    assert _find_ncinfo_bin(fake_bin) == fake_bin


def test_find_ncinfo_bin_not_found(monkeypatch):
    ''' with no explicit path and nothing on PATH, resolution returns None '''
    monkeypatch.setattr('shutil.which', lambda _: None)
    assert _find_ncinfo_bin() is None


def test_ncinfo_preview_happy_path(temp_dir): # pylint: disable=redefined-outer-name
    ''' a working ncinfo stub is parsed into the matching variable entry '''
    script = _write_fake_ncinfo(
        Path(temp_dir) / 'ncinfo',
        '[{"name": "sos", "type": "float", "dims": ["time"], '
        '"attributes": [{"name": "units", "type": "string", "value": "psu"}]}]'
    )
    result = _ncinfo_preview(str(Path(temp_dir) / 'whatever.nc'), 'sos', ncinfo_bin=script)
    assert result['name'] == 'sos'
    assert result['attributes'][0]['value'] == 'psu'


def test_ncinfo_preview_var_not_present(temp_dir): # pylint: disable=redefined-outer-name
    ''' a non-matching var_name returns None '''
    script = _write_fake_ncinfo(
        Path(temp_dir) / 'ncinfo',
        '[{"name": "sos", "type": "float", "dims": [], "attributes": []}]'
    )
    result = _ncinfo_preview(str(Path(temp_dir) / 'whatever.nc'), 'tos', ncinfo_bin=script)
    assert result is None


def test_ncinfo_preview_nonzero_exit_returns_none(temp_dir): # pylint: disable=redefined-outer-name
    ''' a failing ncinfo binary never raises -- returns None '''
    script = _write_broken_ncinfo(Path(temp_dir) / 'ncinfo')
    result = _ncinfo_preview(str(Path(temp_dir) / 'whatever.nc'), 'sos', ncinfo_bin=script)
    assert result is None


def test_ncinfo_preview_missing_binary_returns_none(temp_dir): # pylint: disable=redefined-outer-name
    ''' a nonexistent binary path never raises -- returns None '''
    result = _ncinfo_preview(str(Path(temp_dir) / 'whatever.nc'), 'sos',
                             ncinfo_bin=str(Path(temp_dir) / 'does_not_exist_bin'))
    assert result is None


def test_preview_nc_file_prefers_ncinfo(temp_dir): # pylint: disable=redefined-outer-name
    ''' when ncinfo succeeds, its output wins over the netCDF4 fallback '''
    nc_path = Path(temp_dir) / 'test.nc'
    _write_nc_file(nc_path, 'sos')
    script = _write_fake_ncinfo(
        Path(temp_dir) / 'ncinfo',
        '[{"name": "sos", "type": "float", "dims": ["time"], "attributes": []}]'
    )
    source, data = _preview_nc_file(str(nc_path), 'sos', ncinfo_bin=script)
    assert source == 'ncinfo'
    assert data['name'] == 'sos'


def test_preview_nc_file_falls_back_to_netcdf4(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' with ncinfo unavailable, the netCDF4 fallback is used '''
    monkeypatch.setattr('shutil.which', lambda _: None)
    nc_path = Path(temp_dir) / 'test.nc'
    _write_nc_file(nc_path, 'sos', units='psu')

    source, data = _preview_nc_file(str(nc_path), 'sos', ncinfo_bin=None)
    assert source == 'netcdf4'
    assert data['units'] == 'psu'


def test_preview_nc_file_none_available(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' with neither ncinfo nor a valid file, source is 'none' '''
    monkeypatch.setattr('shutil.which', lambda _: None)
    source, data = _preview_nc_file(str(Path(temp_dir) / 'does_not_exist.nc'), 'sos', ncinfo_bin=None)
    assert source == 'none'
    assert data == {}


# ── MapSession ───────────────────────────────────────────────────────────────

def _make_session_fixture(root_dir):
    ''' build a pp_dir / varlist_dir / mip_tables_dir trio and return their paths '''
    pp_dir = Path(root_dir) / 'pp'
    varlist_dir = Path(root_dir) / 'varlists'
    tables_dir = Path(root_dir) / 'tables'
    for one_dir in (pp_dir, varlist_dir, tables_dir):
        one_dir.mkdir()
    _write_table(tables_dir, 'Amon', ['tas', 'pr', 'ps'])
    return str(pp_dir), str(varlist_dir), str(tables_dir)


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


def _write_map_yaml(root_dir, pp_dir, tables_dir, table_targets, mip_era='cmip6'):
    ''' write a minimal but structurally-real cmor yaml (as fremor config would produce) '''
    doc = {
        'cmor': {
            'start': None,
            'stop': None,
            'calendar_type': 'noleap',
            'mip_era': mip_era,
            'exp_json': str(Path(root_dir) / 'exp.json'),
            'directories': {
                'pp_dir': str(pp_dir),
                'table_dir': str(tables_dir),
                'outdir': str(Path(root_dir) / 'out'),
            },
            'table_targets': table_targets,
        }
    }
    yamlfile = Path(root_dir) / 'cmor.yaml'
    yamlfile.write_text(yaml.safe_dump(doc), encoding='utf-8')
    return str(yamlfile)


def _amon_yaml(root_dir, pp_dir, varlist_dir, tables_dir, component_names=('atmos',)):
    ''' cmor yaml with one Amon table_target, one entry per given component name, pointing at
    {varlist_dir}/CMIP6_Amon_{component}.list (whether or not that file exists yet) '''
    components = [
        _component_entry(name, Path(varlist_dir) / f'CMIP6_Amon_{name}.list')
        for name in component_names
    ]
    return _write_map_yaml(root_dir, pp_dir, tables_dir, [_table_target('Amon', components)])


def test_mapsession_missing_pp_dir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' pp_dir referenced by the yaml does not exist '''
    _, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    pp_dir_dne = str(Path(temp_dir) / 'pp_dne')
    yamlfile = _amon_yaml(temp_dir, pp_dir_dne, varlist_dir, tables_dir)
    with pytest.raises(FileNotFoundError,
                       match=f'pp_dir from yamlfile does not exist: {pp_dir_dne}'):
        MapSession(yamlfile)


def test_mapsession_missing_tables_dir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_dir referenced by the yaml does not exist '''
    pp_dir, varlist_dir, _ = _make_session_fixture(temp_dir)
    tables_dir_dne = str(Path(temp_dir) / 'tables_dne')
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir_dne)
    with pytest.raises(FileNotFoundError,
                       match=f'mip_tables_dir from yamlfile does not exist: {tables_dir_dne}'):
        MapSession(yamlfile)


def test_mapsession_no_table_targets_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' yaml has no table_targets at all '''
    pp_dir, _, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _write_map_yaml(temp_dir, pp_dir, tables_dir, [])
    with pytest.raises(ValueError, match=f'no table_targets found in {yamlfile}'):
        MapSession(yamlfile)


def test_mapsession_table_patterns_no_match_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_patterns matching nothing raises ValueError '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    with pytest.raises(ValueError, match='no table_targets .* matched table_patterns'):
        MapSession(yamlfile, table_patterns=['Omon'])


def test_mapsession_table_report_shape(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_report reports unmapped/multiply-mapped/unknown, same shape as fremor check '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas', 'sfc_pres': 'ps', 'weird': 'not_a_real_var'}),
        encoding='utf-8'
    )
    (Path(varlist_dir) / 'CMIP6_Amon_atmos_scalar.list').write_text(
        json.dumps({'sfc_pres_scalar': 'ps'}),
        encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir,
                          component_names=['atmos', 'atmos_scalar'])

    session = MapSession(yamlfile)
    report = session.table_report('Amon')

    assert report['reference_var_count'] == 3
    assert report['unmapped'] == ['pr']
    assert set(report['multiply_mapped']) == {'ps'}
    assert report['one_to_one_mapped'] == {'tas': ('atmos', 't_ref')}
    assert report['unknown_mapped'] == ['not_a_real_var']
    assert report['unknown_sources'] == {'not_a_real_var': [('atmos', 'weird')]}


def test_mapsession_table_report_missing_varlist_file(temp_dir): # pylint: disable=redefined-outer-name
    ''' a component declared in the yaml whose variable_list file is missing on disk is
    treated as an empty mapping, not an error '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])

    session = MapSession(yamlfile)
    report = session.table_report('Amon')

    assert sorted(report['unmapped']) == ['pr', 'ps', 'tas']


def test_mapsession_set_mapping_stages_without_writing(temp_dir): # pylint: disable=redefined-outer-name
    ''' set_mapping stages the edit in memory (report reflects it immediately, and it's
    tracked as dirty) but does not touch disk until save_pending() is called '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    assert session.has_pending_changes is False

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')

    # in-memory cache reflects the new mapping without needing a re-read
    report = session.table_report('Amon')
    assert 'pr' not in report['unmapped']

    # but nothing has been written to disk yet
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'
    assert json.loads(out_path.read_text(encoding='utf-8')) == {'t_ref': 'tas'}
    assert session.has_pending_changes is True
    assert ('Amon', 'atmos', 'precip') in session.dirty_keys


def test_mapsession_save_pending_creates_new_varlist(temp_dir): # pylint: disable=redefined-outer-name
    ''' save_pending flushes a staged mapping for a component not yet declared in the yaml
    to a brand-new varlist file, inferring the varlist directory from an existing
    table_target entry, then clears dirty tracking '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_landuse.list').write_text(
        json.dumps({'x': 'ps'}), encoding='utf-8')
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['landuse'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    saved_count = session.save_pending()

    assert saved_count == 1
    assert session.has_pending_changes is False
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'
    assert out_path.exists()
    assert json.loads(out_path.read_text(encoding='utf-8')) == {'precip': 'pr'}


def test_mapsession_save_pending_noop_when_clean(temp_dir): # pylint: disable=redefined-outer-name
    ''' save_pending with nothing staged is a no-op that touches no files '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    assert session.save_pending() == 0
    assert not (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').exists()


def test_mapsession_set_mapping_no_varlist_dir_err(temp_dir): # pylint: disable=redefined-outer-name
    ''' with no existing table_target entries anywhere to infer a varlist directory from,
    mapping a brand-new component raises a clear error instead of guessing a path '''
    pp_dir, _, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _write_map_yaml(temp_dir, pp_dir, tables_dir, [_table_target('Amon', [])])
    session = MapSession(yamlfile)

    with pytest.raises(ValueError, match='cannot create a new varlist'):
        session.set_mapping('Amon', 'atmos', 'precip', 'pr')


def test_mapsession_set_mapping_updates_existing_varlist(temp_dir): # pylint: disable=redefined-outer-name
    ''' after save_pending, a staged edit updates an existing varlist file on disk without
    clobbering sibling keys '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    session.save_pending()

    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'
    assert json.loads(out_path.read_text(encoding='utf-8')) == {'t_ref': 'tas', 'precip': 'pr'}


def test_mapsession_clear_mapping(temp_dir): # pylint: disable=redefined-outer-name
    ''' after save_pending, a staged clear sets the target key back to '' on disk without
    touching sibling keys '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas', 'sfc_pres': 'ps'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.clear_mapping('Amon', 'atmos', 'sfc_pres')
    session.save_pending()

    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'
    assert json.loads(out_path.read_text(encoding='utf-8')) == {'t_ref': 'tas', 'sfc_pres': ''}


def test_mapsession_undo_new_key(temp_dir): # pylint: disable=redefined-outer-name
    ''' undoing a staged mapping for a key that didn't exist before removes it entirely
    (rather than leaving it as '') and drops it from dirty_keys '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    edit = session.undo()

    assert edit.local_key == 'precip'
    assert ('Amon', 'atmos', 'precip') not in session.dirty_keys
    report = session.table_report('Amon')
    assert 'pr' not in dict(report['one_to_one_mapped'])
    for _component, _path, data in session.varlists_by_table['Amon']:
        assert 'precip' not in data


def test_mapsession_undo_overwritten_key(temp_dir): # pylint: disable=redefined-outer-name
    ''' undoing a staged edit that overwrote an existing mapping restores the prior value
    rather than deleting the key '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 't_ref', 'ps')  # re-point an existing key
    session.undo()

    assert ('Amon', 'atmos', 't_ref') not in session.dirty_keys
    report = session.table_report('Amon')
    assert report['one_to_one_mapped']['tas'] == ('atmos', 't_ref')


def test_mapsession_undo_leaves_key_dirty_if_earlier_edit_pending(temp_dir): # pylint: disable=redefined-outer-name
    ''' undoing one of two staged edits to the same key steps back only one edit -- the key
    stays dirty since it still differs from the last-saved value '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    session.set_mapping('Amon', 'atmos', 'precip', 'ps')
    session.undo()

    assert ('Amon', 'atmos', 'precip') in session.dirty_keys
    report = session.table_report('Amon')
    assert report['one_to_one_mapped']['pr'] == ('atmos', 'precip')


def test_mapsession_undo_empty_history(temp_dir): # pylint: disable=redefined-outer-name
    ''' undo with nothing staged returns None and is a no-op '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    assert session.undo() is None


def test_mapsession_restore_pending(temp_dir): # pylint: disable=redefined-outer-name
    ''' restore_pending discards every staged edit at once, back to the last save_pending()
    (or, if nothing was ever saved, back to the initial load) -- and clears undo history '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 't_ref', 'ps')  # overwrite an existing mapping
    session.set_mapping('Amon', 'atmos', 'precip', 'pr')  # stage a brand-new key
    discarded = session.restore_pending()

    assert discarded == 2
    assert session.has_pending_changes is False
    assert session.undo() is None  # undo history was cleared too
    report = session.table_report('Amon')
    assert report['one_to_one_mapped']['tas'] == ('atmos', 't_ref')
    assert 'pr' not in dict(report['one_to_one_mapped'])

    # disk is untouched either way -- restore_pending never writes
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'
    assert json.loads(out_path.read_text(encoding='utf-8')) == {'t_ref': 'tas'}


def test_mapsession_restore_pending_after_save_keeps_saved_edits(temp_dir): # pylint: disable=redefined-outer-name
    ''' restore_pending only rewinds edits staged since the last save -- a save_pending()
    re-baselines, so restoring afterward keeps what was already saved '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 't_ref', 'tas')
    session.save_pending()
    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    session.restore_pending()

    report = session.table_report('Amon')
    assert report['one_to_one_mapped']['tas'] == ('atmos', 't_ref')
    assert 'pr' not in dict(report['one_to_one_mapped'])


def test_mapsession_usage_count(temp_dir): # pylint: disable=redefined-outer-name
    ''' usage_count reflects how many loaded tables' varlists map a given (component,
    local_key) to a non-empty CMIP variable, ignoring other components/keys and empty
    (cleared) values '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas', 'sfc_pres': ''}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    assert session.usage_count('atmos', 't_ref') == 1
    assert session.usage_count('atmos', 'sfc_pres') == 0  # cleared -- empty value
    assert session.usage_count('atmos', 'not_a_key') == 0
    assert session.usage_count('other_component', 't_ref') == 0


def test_mapsession_usage_count_reflects_staged_edits(temp_dir): # pylint: disable=redefined-outer-name
    ''' usage_count picks up staged-but-unsaved set_mapping/clear_mapping calls immediately,
    since they mutate varlists_by_table in memory '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    assert session.usage_count('atmos', 'precip') == 0

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    assert session.usage_count('atmos', 'precip') == 1

    session.clear_mapping('Amon', 'atmos', 'precip')
    assert session.usage_count('atmos', 'precip') == 0


# ── selected-CMIP-variable box formatting ───────────────────────────────────

def test_format_selected_cmip_none(temp_dir): # pylint: disable=redefined-outer-name
    ''' with nothing selected, the box shows the placeholder text '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))
    assert app._format_selected_cmip(None) == MapApp.NO_CMIP_SELECTION # pylint: disable=protected-access


def test_format_selected_cmip_var(temp_dir): # pylint: disable=redefined-outer-name
    ''' an unmapped/group 'var' node shows just table/var, no source line '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))
    text = app._format_selected_cmip( # pylint: disable=protected-access
        {'kind': 'var', 'table': 'Amon', 'var': 'pr'})
    assert 'Amon' in text and 'pr' in text
    assert 'current source' not in text


def test_format_selected_cmip_source(temp_dir): # pylint: disable=redefined-outer-name
    ''' a 'source' node (existing mapping) also shows its current component:local_key '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))
    text = app._format_selected_cmip( # pylint: disable=protected-access
        {'kind': 'source', 'table': 'Amon', 'var': 'tas', 'component': 'atmos', 'local_key': 't_ref'})
    assert 'Amon' in text and 'tas' in text
    assert 'atmos:t_ref' in text


# ── TUI integration (Pilot-driven) ──────────────────────────────────────────
#
# Rather than guessing at cursor-navigation key counts or the exact Tree
# selection/message-posting API, these tests drive the app's own message
# handlers directly with lightweight fake events wrapping real TreeNode
# objects pulled from the actually-populated tree. This keeps the tests
# deterministic while still exercising real app mounting/population and
# real keybinding dispatch via pilot.press(), which is the part these tests
# exist to verify.

class _FakeControl: # pylint: disable=too-few-public-methods
    ''' stand-in for Tree.NodeSelected/.NodeExpanded's `.control` property '''
    def __init__(self, control_id):
        self.id = control_id


class _FakeTreeEvent: # pylint: disable=too-few-public-methods
    ''' stand-in for a Tree.NodeSelected/.NodeExpanded message '''
    def __init__(self, node, control_id):
        self.node = node
        self.control = _FakeControl(control_id)


@pytest.mark.asyncio
async def test_map_app_assign_mapping(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 'm' only stages the mapping in memory (dirty-marks the node in place,
    doesn't touch disk or rebuild the tree); pressing 's' afterward saves it '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    nc_path = comp_ts_dir / 'atmos.000101-000512.pr.nc'
    _write_nc_file(nc_path, 'pr', units='kg m-2 s-1')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    app = MapApp(session)
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        # tree layout: root -> Amon (table) -> Unmapped -> pr
        table_node = cmip_tree.root.children[0]
        unmapped_node = table_node.children[0]
        pr_node = next(n for n in unmapped_node.children if n.data['var'] == 'pr')
        app.on_tree_node_selected(_FakeTreeEvent(pr_node, 'cmip_tree'))
        assert app.selected_cmip['var'] == 'pr'
        selected_box = app.query_one('#selected_cmip')
        assert 'Amon' in selected_box.content and 'pr' in selected_box.content

        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]
        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        assert app.selected_pp['local_key'] == 'pr'
        await pilot.pause()

        await pilot.press('m')
        await pilot.pause()

        # staged only -- nothing written to disk yet, node/table marked dirty in place
        assert not out_path.exists()
        assert session.has_pending_changes
        assert str(pr_node.label).endswith('<- atmos:pr')
        assert 'unsaved' in str(table_node.label)
        # tree wasn't rebuilt -- the very same node objects are still in the tree
        assert cmip_tree.root.children[0] is table_node
        assert unmapped_node.children[0] is pr_node

        await pilot.press('s')
        await pilot.pause()

    assert out_path.exists()
    assert json.loads(out_path.read_text(encoding='utf-8')) == {'pr': 'pr'}
    assert not session.has_pending_changes


@pytest.mark.asyncio
async def test_pp_tree_file_label_shows_usage_count(temp_dir): # pylint: disable=redefined-outer-name
    ''' pp-file leaf labels show how many times their (component, local_key) is currently
    mapped -- 0 for a pp file with no matching mapping, 1 once one exists in the varlist '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.t_ref.nc', 't_ref')
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.precip.nc', 'precip')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test():
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))

        t_ref_node = next(n for n in chunk_node.children if n.data['local_key'] == 't_ref')
        precip_node = next(n for n in chunk_node.children if n.data['local_key'] == 'precip')
        assert '(used 1x)' in str(t_ref_node.label)
        assert '(used 0x)' in str(precip_node.label)


@pytest.mark.asyncio
async def test_pp_preview_runs_in_background_with_loading_message(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' selecting a pp file immediately shows a loading message (before the background
    worker has had a chance to run), then the real preview once it finishes -- proves the
    preview doesn't run synchronously on the UI thread '''
    monkeypatch.setattr('shutil.which', lambda _: None)  # force the netCDF4 fallback path
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.pr.nc', 'pr', units='kg m-2 s-1')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]

        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        preview_box = app.query_one('#preview')
        # loading message is shown synchronously, before the background worker completes
        assert 'loading' in preview_box.content

        await app.workers.wait_for_complete()
        await pilot.pause()

        assert 'loading' not in preview_box.content
        assert '[netcdf4] pr' in preview_box.content
        assert 'kg m-2 s-1' in preview_box.content


def _fake_dmls(monkeypatch, dmls_bin, state_by_path):
    ''' patch shutil.which('dmls') to resolve to dmls_bin, and subprocess.run to return a
    dmls -l-style listing line (with the given state) for whichever path is queried '''
    import subprocess as subprocess_mod # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        'shutil.which', lambda name: dmls_bin if name == 'dmls' else None)

    class _FakeResult: # pylint: disable=too-few-public-methods
        def __init__(self, stdout):
            self.stdout = stdout

    def _fake_run(cmd, **kwargs): # pylint: disable=unused-argument
        assert cmd[0] == dmls_bin
        path = cmd[-1]
        state = state_by_path[path]
        return _FakeResult(f'-rw-r--r-- 1 user group 123 Jan 1 12:00 ({state}) {path}\n')

    monkeypatch.setattr(subprocess_mod, 'run', _fake_run)


@pytest.mark.asyncio
async def test_pp_preview_shows_tape_message_when_offline(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' a pp file whose dmls status is anything other than REG/DUL (still on tape) shows a
    "still on tape" message instead of a preview -- ncinfo/netCDF4 are never even attempted '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    nc_path = comp_ts_dir / 'atmos.000101-000512.pr.nc'
    _write_nc_file(nc_path, 'pr', units='kg m-2 s-1')
    _fake_dmls(monkeypatch, '/usr/bin/dmls', {str(nc_path): 'OFL'})

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]

        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        await app.workers.wait_for_complete()
        await pilot.pause()

        preview_box = app.query_one('#preview')
        assert 'still on tape' in preview_box.content
        assert 'OFL' in preview_box.content


@pytest.mark.asyncio
async def test_pp_preview_shown_when_dmls_reports_disk_resident(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' a pp file whose dmls status is REG or DUL is previewed normally '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    nc_path = comp_ts_dir / 'atmos.000101-000512.pr.nc'
    _write_nc_file(nc_path, 'pr', units='kg m-2 s-1')
    _fake_dmls(monkeypatch, '/usr/bin/dmls', {str(nc_path): 'DUL'})

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]

        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        await app.workers.wait_for_complete()
        await pilot.pause()

        preview_box = app.query_one('#preview')
        assert 'still on tape' not in preview_box.content
        assert '[netcdf4] pr' in preview_box.content
        assert 'kg m-2 s-1' in preview_box.content


@pytest.mark.asyncio
async def test_pp_preview_falls_back_to_stat_heuristic_without_dmls(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' with no dmls binary available at all, a normal on-disk file still previews (via the
    stat-based residency heuristic) instead of being reported as on tape '''
    monkeypatch.setattr('shutil.which', lambda _: None)
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.pr.nc', 'pr', units='kg m-2 s-1')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]

        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        await app.workers.wait_for_complete()
        await pilot.pause()

        preview_box = app.query_one('#preview')
        assert '[netcdf4] pr' in preview_box.content


@pytest.mark.asyncio
async def test_pp_preview_shows_latest_selection_after_rapid_switch(temp_dir, monkeypatch): # pylint: disable=redefined-outer-name
    ''' selecting a second pp file before the first one's preview has finished loading still
    ends up showing the second file's preview, never the first's -- the in-flight preview
    for the first selection is effectively cancelled from the user's point of view '''
    monkeypatch.setattr('shutil.which', lambda _: None)
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.tas.nc', 'tas', units='K')
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.pr.nc', 'pr', units='kg m-2 s-1')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        tas_node = next(n for n in chunk_node.children if n.data['local_key'] == 'tas')
        pr_node = next(n for n in chunk_node.children if n.data['local_key'] == 'pr')

        app.on_tree_node_selected(_FakeTreeEvent(tas_node, 'pp_tree'))
        app.on_tree_node_selected(_FakeTreeEvent(pr_node, 'pp_tree'))

        try:
            # the superseded 'tas' worker is cancelled outright (exclusive=True) before it
            # ever gets to run -- wait_for_complete() re-raises that as WorkerCancelled, but
            # we only care that everything has settled before checking the final UI state
            await app.workers.wait_for_complete()
        except WorkerCancelled:
            pass
        await pilot.pause()

        preview_box = app.query_one('#preview')
        assert '[netcdf4] pr' in preview_box.content


@pytest.mark.asyncio
async def test_pp_preview_stale_result_is_discarded(temp_dir): # pylint: disable=redefined-outer-name
    ''' a preview result carrying an older generation than the current selection is dropped
    -- this is the mechanism behind "cancelling" an in-flight (unkillable) preview thread '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))

    async with app.run_test():
        preview_box = app.query_one('#preview')
        app._preview_generation = 2 # pylint: disable=protected-access

        app._apply_preview_result(1, 'stale result from a superseded selection') # pylint: disable=protected-access
        assert preview_box.content != 'stale result from a superseded selection'

        app._apply_preview_result(2, 'current result') # pylint: disable=protected-access
        assert preview_box.content.endswith('current result')


@pytest.mark.asyncio
async def test_map_app_clear_mapping(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 'd' only stages clearing an existing multiply-mapped source node; pressing
    's' afterward saves it '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'sfc_pres': 'ps'}), encoding='utf-8'
    )
    (Path(varlist_dir) / 'CMIP6_Amon_atmos_scalar.list').write_text(
        json.dumps({'sfc_pres_scalar': 'ps'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir,
                          component_names=['atmos', 'atmos_scalar'])
    session = MapSession(yamlfile)
    app = MapApp(session)
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        # branches: Unmapped, Mapped, Multiply-mapped, Unknown
        multi_node = table_node.children[2]
        ps_node = multi_node.children[0]
        source_node = next(n for n in ps_node.children if n.data['component'] == 'atmos')
        app.on_tree_node_selected(_FakeTreeEvent(source_node, 'cmip_tree'))
        assert app.selected_cmip['kind'] == 'source'
        selected_box = app.query_one('#selected_cmip')
        assert 'atmos:sfc_pres' in selected_box.content
        await pilot.pause()

        await pilot.press('d')
        await pilot.pause()

        # staged only -- original file untouched, source node marked dirty in place
        assert json.loads(out_path.read_text(encoding='utf-8')) == {'sfc_pres': 'ps'}
        assert session.has_pending_changes
        assert str(source_node.label).endswith('(deleted)')
        assert any(span.style == 'strike' for span in source_node.label.spans)
        # clearing a mapping also deselects it, resetting the box
        assert selected_box.content == MapApp.NO_CMIP_SELECTION

        await pilot.press('s')
        await pilot.pause()

    assert json.loads(out_path.read_text(encoding='utf-8')) == {'sfc_pres': ''}
    assert not session.has_pending_changes


@pytest.mark.asyncio
async def test_map_app_undo_key(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 'u' after 'm' undoes the just-staged mapping, rebuilding the tree so the
    variable shows back up under Unmapped and the table's pending count drops '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.pr.nc', 'pr', units='kg m-2 s-1')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    app = MapApp(session)
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        unmapped_node = table_node.children[0]
        pr_node = next(n for n in unmapped_node.children if n.data['var'] == 'pr')
        app.on_tree_node_selected(_FakeTreeEvent(pr_node, 'cmip_tree'))

        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]
        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        await pilot.pause()

        await pilot.press('m')
        await pilot.pause()
        assert session.has_pending_changes

        await pilot.press('u')
        await pilot.pause()

        assert not session.has_pending_changes
        assert not out_path.exists()
        # tree was rebuilt -- re-fetch nodes rather than reusing the old (now-stale) ones
        table_node = cmip_tree.root.children[0]
        assert 'unsaved' not in str(table_node.label)
        unmapped_node = table_node.children[0]
        assert any(n.data['var'] == 'pr' for n in unmapped_node.children)

        # a second undo has nothing left to do
        await pilot.press('u')
        await pilot.pause()
        assert not session.has_pending_changes


@pytest.mark.asyncio
async def test_map_app_restore_pending_key(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 'R' discards every staged-but-unsaved edit at once, rebuilding the tree back
    to the last-saved state without touching disk '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    session.set_mapping('Amon', 'atmos', 'sfc_pres', 'ps')
    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    app = MapApp(session)
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'

    async with app.run_test() as pilot:
        assert session.has_pending_changes

        await pilot.press('R')
        await pilot.pause()

        assert not session.has_pending_changes
        # nothing was ever written -- restore_pending only affects in-memory state
        assert json.loads(out_path.read_text(encoding='utf-8')) == {'t_ref': 'tas'}

        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        assert 'unsaved' not in str(table_node.label)
        unmapped_node = table_node.children[0]
        assert {n.data['var'] for n in unmapped_node.children} == {'pr', 'ps'}

        # nothing left to restore -- pressing it again is a no-op
        await pilot.press('R')
        await pilot.pause()
        assert not session.has_pending_changes


@pytest.mark.asyncio
async def test_map_app_quit_no_pending_changes(temp_dir): # pylint: disable=redefined-outer-name
    ''' with nothing staged, 'q' quits immediately -- no confirmation needed '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        assert app.is_running
        await pilot.press('q')
        await pilot.pause()

    assert not app.is_running


@pytest.mark.asyncio
async def test_map_app_quit_requires_confirmation_with_pending_changes(temp_dir): # pylint: disable=redefined-outer-name
    ''' with staged-but-unsaved changes, the first 'q' only warns; a second 'q' confirms
    quitting (and discards the staged changes without writing them) '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    app = MapApp(session)

    async with app.run_test() as pilot:
        await pilot.press('q')
        await pilot.pause()
        assert app.is_running  # first press only warns
        assert session.has_pending_changes

        await pilot.press('q')
        await pilot.pause()

    assert not app.is_running
    # quitting without saving discards the staged change -- nothing was written to disk
    assert not (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').exists()


# ── cmip-tree detail box: MIP table variable_entry definitions ─────────────

def test_format_variable_detail_single_entry():
    ''' a plain CMIP6-style single entry is rendered attr: value, one per line, in the
    fixed display order, skipping absent/empty attrs and any attr not in that allowlist '''
    text = _format_variable_detail('tas', {'tas': {
        'long_name': 'Near-Surface Air Temperature',
        'units': 'K',
        'standard_name': 'air_temperature',
        'comment': '',
        'some_other_field': 'ignored',
    }})
    assert text == (
        'long_name: Near-Surface Air Temperature\n'
        'standard_name: air_temperature\n'
        'units: K'
    )


def test_format_variable_detail_no_definitions():
    ''' an empty definitions dict (var not found in the table) shows a clear message
    instead of a blank box '''
    assert _format_variable_detail('nope', {}) == 'no MIP table definition found for "nope"'


def test_format_variable_detail_multiple_brands():
    ''' a CMIP7 variable with more than one matching variable_entry key (brand) gets each
    brand rendered as its own labeled section '''
    text = _format_variable_detail('pr', {
        'pr_tavg-h2m-hxy-u': {'long_name': 'Precipitation', 'units': 'kg m-2 s-1'},
        'pr_tpt': {'long_name': 'Precipitation (instantaneous)', 'units': 'kg m-2 s-1'},
    })
    assert text == (
        '[pr_tavg-h2m-hxy-u]\n'
        'long_name: Precipitation\n'
        'units: kg m-2 s-1\n'
        '\n'
        '[pr_tpt]\n'
        'long_name: Precipitation (instantaneous)\n'
        'units: kg m-2 s-1'
    )


def test_mapsession_variable_definitions_cmip6(temp_dir): # pylint: disable=redefined-outer-name
    ''' variable_definitions reads straight from the MIP table JSON, independent of mapping
    status -- works for an unmapped variable too '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(tables_dir) / 'CMIP6_Amon.json').write_text(
        json.dumps({'variable_entry': {
            'tas': {'long_name': 'Near-Surface Air Temperature', 'units': 'K'},
            'pr': {'long_name': 'Precipitation', 'units': 'kg m-2 s-1'},
        }}),
        encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)

    assert session.variable_definitions('Amon', 'tas') == {
        'tas': {'long_name': 'Near-Surface Air Temperature', 'units': 'K'}
    }
    assert session.variable_definitions('Amon', 'not_a_real_var') == {}


def test_mapsession_variable_definitions_cmip7_multi_brand(temp_dir): # pylint: disable=redefined-outer-name
    ''' for a CMIP7 table, every branded variable_entry key matching the bare var name comes
    back, not just the first '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(tables_dir) / 'CMIP7_Amon.json').write_text(
        json.dumps({'variable_entry': {
            'pr_tavg-h2m-hxy-u': {'long_name': 'Precipitation'},
            'pr_tpt': {'long_name': 'Precipitation (instantaneous)'},
            'tas_tavg-h2m-hxy-u': {'long_name': 'Near-Surface Air Temperature'},
        }}),
        encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    doc = yaml.safe_load(Path(yamlfile).read_text(encoding='utf-8'))
    doc['cmor']['mip_era'] = 'cmip7'
    Path(yamlfile).write_text(yaml.safe_dump(doc), encoding='utf-8')

    session = MapSession(yamlfile)
    definitions = session.variable_definitions('Amon', 'pr')
    assert set(definitions) == {'pr_tavg-h2m-hxy-u', 'pr_tpt'}
    assert definitions['pr_tpt'] == {'long_name': 'Precipitation (instantaneous)'}


@pytest.mark.asyncio
async def test_map_app_cmip_tree_selection_shows_detail_box(temp_dir): # pylint: disable=redefined-outer-name
    ''' selecting a CMIP variable node populates the detail box with its MIP table
    definition, independent of whether it's mapped '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(tables_dir) / 'CMIP6_Amon.json').write_text(
        json.dumps({'variable_entry': {
            'tas': {'long_name': 'Near-Surface Air Temperature', 'units': 'K'},
            'pr': {'long_name': 'Precipitation', 'units': 'kg m-2 s-1'},
            'ps': {'long_name': 'Surface Air Pressure', 'units': 'Pa'},
        }}),
        encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))

    async with app.run_test():
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        unmapped_node = table_node.children[0]
        pr_node = next(n for n in unmapped_node.children if n.data['var'] == 'pr')

        app.on_tree_node_selected(_FakeTreeEvent(pr_node, 'cmip_tree'))
        detail_box = app.query_one('#cmip_detail')
        assert 'Precipitation' in detail_box.content
        assert 'kg m-2 s-1' in detail_box.content


@pytest.mark.asyncio
async def test_map_app_clear_mapping_resets_detail_box(temp_dir): # pylint: disable=redefined-outer-name
    ''' clearing a mapping drops the cmip-tree selection, so the detail box goes back to its
    placeholder text rather than showing a stale definition '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        mapped_node = table_node.children[1]
        source_node = mapped_node.children[0]

        app.on_tree_node_selected(_FakeTreeEvent(source_node, 'cmip_tree'))
        detail_box = app.query_one('#cmip_detail')
        assert detail_box.content != app.NO_CMIP_DETAIL

        await pilot.press('d')
        await pilot.pause()

        assert detail_box.content == app.NO_CMIP_DETAIL


# ── cmip-tree selection -> pp-tree navigation, pp-file detail's mapped-variables list ──────

@pytest.mark.asyncio
async def test_map_app_selecting_one_to_one_mapped_var_navigates_pp_tree(temp_dir): # pylint: disable=redefined-outer-name
    ''' selecting a one-to-one mapped CMIP variable in the cmip tree automatically expands
    and selects its source pp file in the pp-directory browser, same as browsing there '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.t_ref.nc', 't_ref')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        mapped_node = table_node.children[1]
        source_node = next(n for n in mapped_node.children if n.data['var'] == 'tas')

        # pp_tree hasn't been touched yet -- nothing populated below the component node
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        assert not component_node.children

        app.on_tree_node_selected(_FakeTreeEvent(source_node, 'cmip_tree'))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.selected_pp is not None
        assert app.selected_pp['local_key'] == 't_ref'
        assert app.selected_pp['component'] == 'atmos'
        assert pp_tree.cursor_node is not None
        assert pp_tree.cursor_node.data['local_key'] == 't_ref'
        assert component_node.is_expanded
        preview_box = app.query_one('#preview')
        assert 'Amon / tas' in preview_box.content


@pytest.mark.asyncio
async def test_map_app_selecting_multiply_mapped_source_navigates_pp_tree(temp_dir): # pylint: disable=redefined-outer-name
    ''' selecting one particular component:local_key source under a multiply-mapped CMIP
    variable navigates to that source's own pp file, not some other source's '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'sfc_pres': 'ps'}), encoding='utf-8'
    )
    (Path(varlist_dir) / 'CMIP6_Amon_atmos_scalar.list').write_text(
        json.dumps({'sfc_pres_scalar': 'ps'}), encoding='utf-8'
    )
    for component, local_key in (('atmos', 'sfc_pres'), ('atmos_scalar', 'sfc_pres_scalar')):
        comp_ts_dir = Path(pp_dir) / component / 'ts' / 'monthly' / '5yr'
        comp_ts_dir.mkdir(parents=True)
        _write_nc_file(comp_ts_dir / f'{component}.000101-000512.{local_key}.nc', local_key)

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir,
                          component_names=['atmos', 'atmos_scalar'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        multi_node = table_node.children[2]
        ps_node = next(n for n in multi_node.children if n.data['var'] == 'ps')
        scalar_source = next(
            n for n in ps_node.children if n.data['component'] == 'atmos_scalar')

        app.on_tree_node_selected(_FakeTreeEvent(scalar_source, 'cmip_tree'))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.selected_pp['component'] == 'atmos_scalar'
        assert app.selected_pp['local_key'] == 'sfc_pres_scalar'


@pytest.mark.asyncio
async def test_map_app_selecting_source_with_no_pp_file_notifies(temp_dir): # pylint: disable=redefined-outer-name
    ''' selecting a mapped CMIP variable whose pp file isn't discoverable on disk (e.g. the
    component directory is missing) doesn't crash, and warns instead of silently no-opping '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test():
        notifications = []
        app.notify = lambda message, **kwargs: notifications.append(message) # pylint: disable=protected-access

        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        mapped_node = table_node.children[1]
        source_node = next(n for n in mapped_node.children if n.data['var'] == 'tas')

        app.on_tree_node_selected(_FakeTreeEvent(source_node, 'cmip_tree'))

        assert app.selected_pp is None
        assert any('not found' in message for message in notifications)


@pytest.mark.asyncio
async def test_pp_file_detail_box_lists_mapped_variables(temp_dir): # pylint: disable=redefined-outer-name
    ''' the pp-file detail box lists every currently-loaded MIP table variable mapped to the
    selected pp file, alongside its preview, and says so plainly when there are none '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.t_ref.nc', 't_ref')
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.precip.nc', 'precip')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        t_ref_node = next(n for n in chunk_node.children if n.data['local_key'] == 't_ref')
        precip_node = next(n for n in chunk_node.children if n.data['local_key'] == 'precip')

        app.on_tree_node_selected(_FakeTreeEvent(t_ref_node, 'pp_tree'))
        await app.workers.wait_for_complete()
        await pilot.pause()
        preview_box = app.query_one('#preview')
        assert 'mapped MIP variables:' in preview_box.content
        assert 'Amon / tas' in preview_box.content

        app.on_tree_node_selected(_FakeTreeEvent(precip_node, 'pp_tree'))
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert 'not mapped to any MIP variable' in preview_box.content


# ── disabled-flag toggling ──────────────────────────────────────────────────

def test_mapsession_is_disabled_default_false(temp_dir): # pylint: disable=redefined-outer-name
    ''' a table_target with no 'disabled' key at all is treated as enabled '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)
    assert session.is_disabled('Amon') is False


def test_mapsession_toggle_disabled_stages_without_writing(temp_dir): # pylint: disable=redefined-outer-name
    ''' toggle_disabled flips the in-memory flag immediately (is_disabled reflects it right
    away) and stages it as dirty, but doesn't touch the yaml file on disk until save_pending '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)
    assert session.has_pending_changes is False

    new_state = session.toggle_disabled('Amon')

    assert new_state is True
    assert session.is_disabled('Amon') is True
    assert 'Amon' in session.disabled_dirty
    assert session.has_pending_changes is True
    on_disk = yaml.safe_load(Path(yamlfile).read_text(encoding='utf-8'))
    assert not on_disk['cmor']['table_targets'][0].get('disabled')


def test_mapsession_toggle_disabled_back_to_baseline_clears_dirty(temp_dir): # pylint: disable=redefined-outer-name
    ''' toggling a flag twice (back to its last-saved value) drops it from disabled_dirty
    again, same as a mapping edit undone back to its prior value '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)

    session.toggle_disabled('Amon')
    session.toggle_disabled('Amon')

    assert session.is_disabled('Amon') is False
    assert 'Amon' not in session.disabled_dirty
    assert session.has_pending_changes is False


def test_mapsession_save_pending_writes_disabled_flag_to_yaml(temp_dir): # pylint: disable=redefined-outer-name
    ''' save_pending persists a staged disabled toggle back into yamlfile itself (not a
    varlist file), then clears disabled_dirty and re-baselines it '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)

    session.toggle_disabled('Amon')
    saved_count = session.save_pending()

    assert saved_count == 1
    assert session.has_pending_changes is False
    assert not session.disabled_dirty
    on_disk = yaml.safe_load(Path(yamlfile).read_text(encoding='utf-8'))
    assert on_disk['cmor']['table_targets'][0]['disabled'] is True
    # a second toggle now stages against the freshly-saved baseline
    session.toggle_disabled('Amon')
    assert session.is_disabled('Amon') is False
    assert 'Amon' in session.disabled_dirty


def test_mapsession_save_pending_combines_mapping_and_disabled_counts(temp_dir): # pylint: disable=redefined-outer-name
    ''' save_pending's returned count includes both staged mapping edits and staged
    disabled-flag toggles '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    session.toggle_disabled('Amon')

    assert session.save_pending() == 2


def test_mapsession_restore_pending_discards_disabled_toggle(temp_dir): # pylint: disable=redefined-outer-name
    ''' restore_pending rewinds a staged disabled toggle back to the last-saved state too,
    alongside mapping edits, without ever touching the yaml file on disk '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)

    session.set_mapping('Amon', 'atmos', 'precip', 'pr')
    session.toggle_disabled('Amon')
    discarded = session.restore_pending()

    assert discarded == 2
    assert session.is_disabled('Amon') is False
    assert not session.disabled_dirty
    assert session.has_pending_changes is False
    on_disk = yaml.safe_load(Path(yamlfile).read_text(encoding='utf-8'))
    assert not on_disk['cmor']['table_targets'][0].get('disabled')


@pytest.mark.asyncio
async def test_map_app_toggle_disabled_key(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 't' with a MIP table node selected stages toggling its disabled flag (marks
    the node label in place, doesn't touch the yaml file yet); pressing 's' afterward saves it '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)
    app = MapApp(session)

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        app.on_tree_node_selected(_FakeTreeEvent(table_node, 'cmip_tree'))
        await pilot.pause()

        await pilot.press('t')
        await pilot.pause()

        assert session.is_disabled('Amon') is True
        assert session.has_pending_changes
        assert '(disabled)' in str(table_node.label)
        assert 'unsaved' in str(table_node.label)
        on_disk = yaml.safe_load(Path(yamlfile).read_text(encoding='utf-8'))
        assert not on_disk['cmor']['table_targets'][0].get('disabled')

        await pilot.press('s')
        await pilot.pause()

    assert not session.has_pending_changes
    on_disk = yaml.safe_load(Path(yamlfile).read_text(encoding='utf-8'))
    assert on_disk['cmor']['table_targets'][0]['disabled'] is True


@pytest.mark.asyncio
async def test_map_app_toggle_disabled_from_variable_selection(temp_dir): # pylint: disable=redefined-outer-name
    ''' 't' also works with a variable/source node selected (not just the table node itself)
    -- it toggles whichever table that variable belongs to '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)
    app = MapApp(session)

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        unmapped_node = table_node.children[0]
        pr_node = next(n for n in unmapped_node.children if n.data['var'] == 'pr')
        app.on_tree_node_selected(_FakeTreeEvent(pr_node, 'cmip_tree'))
        await pilot.pause()

        await pilot.press('t')
        await pilot.pause()

        assert session.is_disabled('Amon') is True


@pytest.mark.asyncio
async def test_map_app_toggle_disabled_no_selection_warns(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 't' with nothing selected yet warns instead of raising '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        notifications = []
        app.notify = lambda message, **kwargs: notifications.append(message) # pylint: disable=protected-access

        await pilot.press('t')
        await pilot.pause()

        assert any('select' in message for message in notifications)


# ── freq honoring ────────────────────────────────────────────────────────

def test_mapsession_table_freq(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_freq reads the freq configured for a table_target straight out of the yaml '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    session = MapSession(yamlfile)
    assert session.table_freq('Amon') == 'monthly'


def test_mapsession_table_freq_none_when_unset(temp_dir): # pylint: disable=redefined-outer-name
    ''' table_freq is None when the table_target's freq is unset in the yaml -- fremor yaml
    then derives one from the MIP table itself (only for CMIP6-family eras), so freq-based
    validation in the map UI is skipped rather than guessing '''
    pp_dir, _, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _write_map_yaml(temp_dir, pp_dir, tables_dir, [_table_target('Amon', [], freq=None)])
    session = MapSession(yamlfile)
    assert session.table_freq('Amon') is None


@pytest.mark.asyncio
async def test_map_app_table_label_shows_freq(temp_dir): # pylint: disable=redefined-outer-name
    ''' the MIP-table node's label surfaces its configured freq, since staging a mapping now
    depends on it matching the selected pp file's freq '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir)
    app = MapApp(MapSession(yamlfile))

    async with app.run_test():
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        assert 'freq=monthly' in str(table_node.label)


@pytest.mark.asyncio
async def test_map_app_assign_mapping_blocked_on_freq_mismatch(temp_dir): # pylint: disable=redefined-outer-name
    ''' pressing 'm' refuses to stage a mapping when the selected pp file lives under a freq
    directory that doesn't match the table's configured freq -- fremor yaml would never
    actually find the file there at CMORization time, so nothing is staged '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)

    # Amon's table_target freq defaults to 'monthly', but this pp file lives under 'daily'
    comp_ts_dir = Path(pp_dir) / 'atmos' / 'ts' / 'daily' / '5yr'
    comp_ts_dir.mkdir(parents=True)
    _write_nc_file(comp_ts_dir / 'atmos.000101-000512.pr.nc', 'pr', units='kg m-2 s-1')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    session = MapSession(yamlfile)
    app = MapApp(session)
    out_path = Path(varlist_dir) / 'CMIP6_Amon_atmos.list'

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        unmapped_node = table_node.children[0]
        pr_node = next(n for n in unmapped_node.children if n.data['var'] == 'pr')
        app.on_tree_node_selected(_FakeTreeEvent(pr_node, 'cmip_tree'))

        pp_tree = app.query_one('#pp_tree')
        component_node = pp_tree.root.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(component_node, 'pp_tree'))
        freq_node = component_node.children[0]
        assert freq_node.data['freq'] == 'daily'
        app.on_tree_node_expanded(_FakeTreeEvent(freq_node, 'pp_tree'))
        chunk_node = freq_node.children[0]
        app.on_tree_node_expanded(_FakeTreeEvent(chunk_node, 'pp_tree'))
        file_node = chunk_node.children[0]
        app.on_tree_node_selected(_FakeTreeEvent(file_node, 'pp_tree'))
        await pilot.pause()

        notifications = []
        app.notify = lambda message, **kwargs: notifications.append((message, kwargs.get('severity'))) # pylint: disable=protected-access

        await pilot.press('m')
        await pilot.pause()

        assert not session.has_pending_changes
        assert not out_path.exists()
        assert any(sev == 'error' for _msg, sev in notifications)


@pytest.mark.asyncio
async def test_map_app_navigate_prefers_matching_freq(temp_dir): # pylint: disable=redefined-outer-name
    ''' auto-navigating to a mapped variable's source, when the same local_key exists under
    more than one freq, lands on the file under the table's configured freq -- not just
    whichever one happens to be discovered first '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    monthly_dir = Path(pp_dir) / 'atmos' / 'ts' / 'monthly' / '5yr'
    monthly_dir.mkdir(parents=True)
    _write_nc_file(monthly_dir / 'atmos.000101-000512.t_ref.nc', 't_ref', units='K')
    daily_dir = Path(pp_dir) / 'atmos' / 'ts' / 'daily' / '5yr'
    daily_dir.mkdir(parents=True)
    _write_nc_file(daily_dir / 'atmos.00010101-00051231.t_ref.nc', 't_ref', units='K')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        mapped_node = table_node.children[1]
        source_node = next(n for n in mapped_node.children if n.data['var'] == 'tas')

        notifications = []
        app.notify = lambda message, **kwargs: notifications.append((message, kwargs.get('severity'))) # pylint: disable=protected-access

        app.on_tree_node_selected(_FakeTreeEvent(source_node, 'cmip_tree'))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.selected_pp['freq'] == 'monthly'
        pp_tree = app.query_one('#pp_tree')
        assert pp_tree.cursor_node.data['freq'] == 'monthly'
        assert not notifications


@pytest.mark.asyncio
async def test_map_app_navigate_falls_back_to_mismatched_freq_with_warning(temp_dir): # pylint: disable=redefined-outer-name
    ''' if the mapped variable's local_key doesn't exist anywhere under the table's
    configured freq (a stale/hand-edited mapping), auto-navigation still finds the file under
    a different freq for visibility, but warns clearly that fremor yaml won't actually use it '''
    pp_dir, varlist_dir, tables_dir = _make_session_fixture(temp_dir)
    (Path(varlist_dir) / 'CMIP6_Amon_atmos.list').write_text(
        json.dumps({'t_ref': 'tas'}), encoding='utf-8'
    )
    daily_dir = Path(pp_dir) / 'atmos' / 'ts' / 'daily' / '5yr'
    daily_dir.mkdir(parents=True)
    _write_nc_file(daily_dir / 'atmos.00010101-00051231.t_ref.nc', 't_ref', units='K')

    yamlfile = _amon_yaml(temp_dir, pp_dir, varlist_dir, tables_dir, component_names=['atmos'])
    app = MapApp(MapSession(yamlfile))

    async with app.run_test() as pilot:
        cmip_tree = app.query_one('#cmip_tree')
        table_node = cmip_tree.root.children[0]
        mapped_node = table_node.children[1]
        source_node = next(n for n in mapped_node.children if n.data['var'] == 'tas')

        notifications = []
        app.notify = lambda message, **kwargs: notifications.append((message, kwargs.get('severity'))) # pylint: disable=protected-access

        app.on_tree_node_selected(_FakeTreeEvent(source_node, 'cmip_tree'))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.selected_pp['freq'] == 'daily'
        pp_tree = app.query_one('#pp_tree')
        assert pp_tree.cursor_node.data['freq'] == 'daily'
        assert any(sev == 'warning' and 'freq' in msg for msg, sev in notifications)
