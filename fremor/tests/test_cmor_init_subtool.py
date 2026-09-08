"""
Unit tests for cmor_init module

Tests the cmor_init_subtool and its helper functions including:
- _fetch_tables_git: git clone table fetching
- _fetch_tables_curl: curl tarball table fetching
- cmor_init_subtool: main initialization logic
- ValueError handling for invalid mip_era
"""

import json

import pytest

from fremor.cmor_init import (
    cmor_init_subtool,
    _fetch_tables_git,
    _fetch_tables_curl,
    MIP_TABLE_REPOS,
)


def test_cmor_init_invalid_mip_era():
    """
    Test that invalid mip_era raises ValueError
    """
    with pytest.raises(ValueError, match='mip_era must be cmip6, cmip6plus, or cmip7, got cmip5'):
        cmor_init_subtool(mip_era='cmip5')

    with pytest.raises(ValueError, match='mip_era must be cmip6, cmip6plus, or cmip7, got invalid'):
        cmor_init_subtool(mip_era='invalid')

    with pytest.raises(ValueError, match='mip_era must be cmip6, cmip6plus, or cmip7, got CMIP8'):
        cmor_init_subtool(mip_era='CMIP8')


def test_cmor_init_tables_dir_with_curl(tmp_path):
    """
    Test fetching tables with curl (fast mode) when tables_dir is provided.

    This test actually fetches the CMIP6 tables from GitHub using curl,
    without mocking, to ensure the full integration works.
    """
    tables_dir = tmp_path / 'cmip6_tables_curl'

    result = cmor_init_subtool(
        mip_era='cmip6',
        exp_config=None,
        tables_dir=str(tables_dir),
        tag=None,
        fast=True
    )

    # Should not create exp_config when tables_dir is provided without exp_config
    assert result['exp_config'] is None
    assert result['tables_dir'] == str(tables_dir)

    # Verify the tables directory was created and contains expected content
    assert tables_dir.exists()

    # The tarball extraction creates a subdirectory with the repo name
    # Check that some tables were downloaded
    extracted_dirs = list(tables_dir.iterdir())
    assert len(extracted_dirs) > 0, 'No content extracted from tarball'

    # Look for the actual tables directory inside the extracted content
    # The structure is typically: tables_dir/cmip6-cmor-tables-<ref>/Tables/
    found_tables = False
    for _ in tables_dir.rglob('CMIP6_*.json'):
        found_tables = True
        break

    assert found_tables, 'No CMIP6 table JSON files found in extracted content'


def test_cmor_init_tables_dir_with_git(tmp_path):
    """
    Test fetching tables with git clone when tables_dir is provided.

    This test actually clones the CMIP7 tables from GitHub using git,
    without mocking, to ensure the full integration works.
    """
    tables_dir = tmp_path / 'cmip7_tables_git'

    result = cmor_init_subtool(
        mip_era='cmip7',
        exp_config=None,
        tables_dir=str(tables_dir),
        tag=None,
        fast=False  # Use git clone
    )

    # Should not create exp_config when tables_dir is provided without exp_config
    assert result['exp_config'] is None
    assert result['tables_dir'] == str(tables_dir)

    # Verify the tables directory was created
    assert tables_dir.exists()

    # git clone creates the directory and populates it directly
    # Check for .git directory (shallow clone still has this)
    git_dir = tables_dir / '.git'
    assert git_dir.exists(), 'Git directory not found, git clone may have failed'

    # Look for CMIP7 table files
    found_tables = False
    for _ in tables_dir.rglob('CMIP7_*.json'):
        found_tables = True
        break

    assert found_tables, 'No CMIP7 table JSON files found in cloned repository'


def test_fetch_tables_git_directly(tmp_path):
    """
    Test _fetch_tables_git function directly.

    This test exercises the git clone functionality without mocking,
    using a small repository to keep the test fast.
    """
    tables_dir = tmp_path / 'direct_git_test'
    repo_url = MIP_TABLE_REPOS['cmip6']

    # Call the function directly
    _fetch_tables_git(repo_url, str(tables_dir), tag=None)

    # Verify directory exists and has content
    assert tables_dir.exists()
    assert (tables_dir / '.git').exists()

    # Check that some JSON files exist
    json_files = list(tables_dir.rglob('*.json'))
    assert len(json_files) > 0, 'No JSON files found in cloned repository'


def test_fetch_tables_curl_directly(tmp_path):
    """
    Test _fetch_tables_curl function directly.

    This test exercises the curl + tarball extraction functionality
    without mocking, using the actual CMIP7 repository.
    """
    tables_dir = tmp_path / 'direct_curl_test'
    repo_url = MIP_TABLE_REPOS['cmip7']

    # Call the function directly
    _fetch_tables_curl(repo_url, str(tables_dir), tag=None)

    # Verify directory exists and has content
    assert tables_dir.exists()

    # Check that some files were extracted
    all_files = list(tables_dir.rglob('*'))
    assert len(all_files) > 0, 'No files extracted from tarball'

    # Look for JSON files (tables)
    json_files = list(tables_dir.rglob('*.json'))
    assert len(json_files) > 0, 'No JSON table files found in extracted content'


def test_cmor_init_tables_dir_and_exp_config(tmp_path):
    """
    Test that both exp_config and tables_dir can be provided together.
    """
    tables_dir = tmp_path / 'tables'
    exp_config = tmp_path / 'experiment.json'

    result = cmor_init_subtool(
        mip_era='cmip6',
        exp_config=str(exp_config),
        tables_dir=str(tables_dir),
        tag=None,
        fast=True
    )

    # Both should be created
    assert result['exp_config'] == str(exp_config)
    assert result['tables_dir'] == str(tables_dir)

    # Verify exp_config was created
    assert exp_config.exists()
    with open(exp_config, encoding='utf-8') as f:
        config = json.load(f)
    assert config['mip_era'] == 'CMIP6'

    # Verify tables were fetched
    assert tables_dir.exists()


def test_cmor_init_cmip6plus_tables_dir_and_exp_config(tmp_path):
    """
    Test that both exp_config and tables_dir can be provided together.
    CMIP6Plus case
    """
    tables_dir = tmp_path / 'tables'
    exp_config = tmp_path / 'experiment.json'

    result = cmor_init_subtool(
        mip_era='cmip6plus',
        exp_config=str(exp_config),
        tables_dir=str(tables_dir),
        tag=None,
        fast=True
    )

    # Both should be created
    assert result['exp_config'] == str(exp_config)
    assert result['tables_dir'] == str(tables_dir)

    # Verify exp_config was created
    assert exp_config.exists()
    with open(exp_config, encoding='utf-8') as f:
        config = json.load(f)
    assert config['mip_era'] == 'CMIP6Plus'

    # Verify tables were fetched
    assert tables_dir.exists()


def test_cmor_init_cmip6plus_exp_config_points_at_cmip6plus_resources(tmp_path):
    """
    The CMIP6Plus template must point CMOR at the CMIP6Plus CV and at the auxiliary tables
    shipped by PCMDI/mip-cmor-tables (Auxillary_files/MIP_*.json, one level up from Tables/),
    not at the CMIP6 ones. CMOR resolves all three relative to the MIP table directory, so
    CMIP6 names here make cmor.load_table fail with 'Could not find file: .../CMIP6_*.json'.
    """
    exp_config = tmp_path / 'cmip6plus_experiment.json'

    cmor_init_subtool(mip_era='cmip6plus', exp_config=str(exp_config), tables_dir=None)

    with open(exp_config, encoding='utf-8') as f:
        config = json.load(f)

    assert config['mip_era'] == 'CMIP6Plus'
    assert config['_controlled_vocabulary_file'] == 'CMIP6Plus_CV.json'
    assert config['_AXIS_ENTRY_FILE'] == '../Auxillary_files/MIP_coordinate.json'
    assert config['_FORMULA_VAR_FILE'] == '../Auxillary_files/MIP_formula_terms.json'

    # CMOR keys off the presence of _cmip6_option, not its value, to enable the CV checks
    # (source_id/experiment/grids/parent+sub experiment ids) that CMIP6Plus still uses.
    assert '_cmip6_option' in config

    # the license is validated against a regex in CMIP6Plus_license.json, which differs from
    # the CMIP6 one: CMIP6Plus wording, and no 'Further information about this data' sentence.
    assert config['license'].startswith('CMIP6Plus model data produced by')
    assert 'pcmdi.llnl.gov/CMIP6Plus/TermsOfUse' in config['license']
    assert 'further_info_url' not in config['license']


def test_cmor_init_tables_dir_only_no_exp_config(tmp_path):
    """
    Test that when only tables_dir is provided, no exp_config is created.
    """
    tables_dir = tmp_path / 'tables_only'

    result = cmor_init_subtool(
        mip_era='cmip7',
        exp_config=None,
        tables_dir=str(tables_dir),
        tag=None,
        fast=True
    )

    # exp_config should be None
    assert result['exp_config'] is None
    assert result['tables_dir'] == str(tables_dir)

    # No default exp_config file should be created in tmp_path
    default_config = tmp_path / 'CMOR_cmip7_template.json'
    assert not default_config.exists()


def test_fetch_tables_curl_with_tag(tmp_path):
    """
    Test fetching tables with a specific git tag using curl.

    This verifies that the tag parameter works correctly.
    Uses a known tag from the CMIP6 repository (6.9.33).
    """
    tables_dir = tmp_path / 'tables_with_tag'
    repo_url = MIP_TABLE_REPOS['cmip6']

    # Use a known release tag from the repository
    # Tags follow pattern like '6.9.33', not 'v6.9.33'
    _fetch_tables_curl(repo_url, str(tables_dir), tag='6.9.33')

    # Verify content was extracted
    assert tables_dir.exists()
    json_files = list(tables_dir.rglob('*.json'))
    assert len(json_files) > 0


def test_fetch_tables_git_with_tag(tmp_path):
    """
    Test fetching tables with a specific git tag using git clone.

    This verifies that the --branch tag parameter works correctly.
    Uses a known tag from the CMIP6 repository (6.9.33).
    """
    tables_dir = tmp_path / 'tables_git_with_tag'
    repo_url = MIP_TABLE_REPOS['cmip6']

    # Use a known release tag from the repository
    # Tags follow pattern like '6.9.33', not 'v6.9.33'
    _fetch_tables_git(repo_url, str(tables_dir), tag='6.9.33')

    # Verify directory exists and has .git
    assert tables_dir.exists()
    assert (tables_dir / '.git').exists(), 'Git directory not found'

    # Verify content was cloned
    json_files = list(tables_dir.rglob('*.json'))
    assert len(json_files) > 0, 'No JSON files found in cloned repository'
