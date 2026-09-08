"""
tests for fremor pre-run configuration validation in cmor_validate
"""

import json
from pathlib import Path

import pytest

from fremor.cmor_validate import resolve_cv_path, check_exp_config_required_attributes

TEST_FILES = Path(__file__).parent / 'test_files'

CMIP6_TABLE = TEST_FILES / 'cmip6-cmor-tables' / 'Tables' / 'CMIP6_Omon.json'
CMIP6_EXP_CONFIG = TEST_FILES / 'CMOR_input_example.json'


def _exp_config_with(tmp_path, **overrides):
    """ copy the CMIP6 example exp config, applying overrides (a None value deletes the key) """
    data = json.loads(CMIP6_EXP_CONFIG.read_text())
    for key, value in overrides.items():
        if value is None:
            del data[key]
        else:
            data[key] = value
    target = tmp_path / 'exp_config.json'
    target.write_text(json.dumps(data))
    return str(target)


def test_check_exp_config_required_attributes_passes():
    """ the shipped example configs satisfy their CVs, so the check must stay out of the way """
    check_exp_config_required_attributes(str(CMIP6_EXP_CONFIG), str(CMIP6_TABLE))

    cmip7_table = TEST_FILES / 'cmip7-cmor-tables' / 'tables' / 'CMIP7_ocean.json'
    cmip7_config = TEST_FILES / 'CMOR_CMIP7_input_example.json'
    check_exp_config_required_attributes(str(cmip7_config), str(cmip7_table))


def test_check_exp_config_required_attributes_blank_raises(tmp_path):
    """ a blank required attribute is as fatal as a missing one -- CMOR discards empty values,
        so nothing distinguishes the two by the time cmor_write complains """
    exp_config = _exp_config_with(tmp_path, grid='', nominal_resolution='   ')

    with pytest.raises(ValueError, match='does not satisfy the controlled vocabulary') as excinfo:
        check_exp_config_required_attributes(exp_config, str(CMIP6_TABLE))

    message = str(excinfo.value)
    assert 'left blank' in message
    assert 'grid' in message
    assert 'nominal_resolution' in message
    # blanked grid fields come from the cmor yaml, so the message has to send the user there
    assert 'gridding block' in message


def test_check_exp_config_required_attributes_missing_raises(tmp_path):
    """ an attribute absent from the config entirely is reported separately from a blank one """
    exp_config = _exp_config_with(tmp_path, source=None)

    with pytest.raises(ValueError, match='absent entirely') as excinfo:
        check_exp_config_required_attributes(exp_config, str(CMIP6_TABLE))

    message = str(excinfo.value)
    assert 'source' in message
    # not a gridding problem, so no gridding advice
    assert 'gridding block' not in message


def test_check_exp_config_required_attributes_ignores_cmor_provided(tmp_path):
    """ CMOR fills in creation_date/tracking_id/variable_id and friends itself, so the check must
        not demand them of the experiment config """
    cv_path = tmp_path / 'CMIP6_CV.json'
    cv_path.write_text(json.dumps(
        {'CV': {'required_global_attributes': ['creation_date', 'tracking_id', 'variable_id',
                                               'variant_label', 'experiment', 'institution']}}))
    (tmp_path / 'CMIP6_Omon.json').write_text('{}')
    exp_config = tmp_path / 'exp_config.json'
    exp_config.write_text(json.dumps({'mip_era': 'CMIP6'}))

    check_exp_config_required_attributes(str(exp_config), str(tmp_path / 'CMIP6_Omon.json'))


def test_check_exp_config_required_attributes_skips_without_cv(tmp_path, caplog):
    """ no CV where CMOR would look -> warn and defer to CMOR, which reports that itself """
    (tmp_path / 'MIP_OPmon.json').write_text('{}')
    exp_config = tmp_path / 'exp_config.json'
    exp_config.write_text(json.dumps({'_controlled_vocabulary_file': 'CMIP6Plus_CV.json'}))

    check_exp_config_required_attributes(str(exp_config), str(tmp_path / 'MIP_OPmon.json'))
    assert 'skipping the required-attribute check' in caplog.text


def test_check_exp_config_required_attributes_skips_unreadable_cv(tmp_path, caplog):
    """ a CV that is present but unparseable must not take the run down on its own """
    (tmp_path / 'CMIP6_Omon.json').write_text('{}')
    (tmp_path / 'CMIP6_CV.json').write_text('not json at all')
    exp_config = tmp_path / 'exp_config.json'
    exp_config.write_text(json.dumps({'mip_era': 'CMIP6'}))

    check_exp_config_required_attributes(str(exp_config), str(tmp_path / 'CMIP6_Omon.json'))
    assert 'skipping the required-attribute check' in caplog.text


def test_resolve_cv_path_defaults_to_cmor_default(tmp_path):
    """ with no _controlled_vocabulary_file key, CMOR falls back to CMIP6_CV.json (cmor.h) """
    (tmp_path / 'CMIP6_Omon.json').write_text('{}')
    (tmp_path / 'CMIP6_CV.json').write_text('{}')
    exp_config = tmp_path / 'exp_config.json'
    exp_config.write_text(json.dumps({'mip_era': 'CMIP6'}))

    result = resolve_cv_path(str(exp_config), str(tmp_path / 'CMIP6_Omon.json'))
    assert result == (tmp_path / 'CMIP6_CV.json').resolve()


def test_resolve_cv_path_relative_to_table_dir(tmp_path):
    """ CMIP7 points at ../tables-cvs/, resolved against the MIP table's directory """
    (tmp_path / 'tables').mkdir()
    (tmp_path / 'tables-cvs').mkdir()
    (tmp_path / 'tables' / 'CMIP7_ocean.json').write_text('{}')
    (tmp_path / 'tables-cvs' / 'cmor-cvs.json').write_text('{}')
    exp_config = tmp_path / 'exp_config.json'
    exp_config.write_text(json.dumps({'_controlled_vocabulary_file': '../tables-cvs/cmor-cvs.json'}))

    result = resolve_cv_path(str(exp_config), str(tmp_path / 'tables' / 'CMIP7_ocean.json'))
    assert result == (tmp_path / 'tables-cvs' / 'cmor-cvs.json').resolve()
