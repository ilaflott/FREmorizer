"""
``fremor init``: CMOR config initialization
===========================================

This module powers the ``fremor init`` command, providing two key capabilities:

1. **Experiment config template generation** – writes an empty (template)
   JSON experiment configuration file for either CMIP6 or CMIP7. The user
   fills in the placeholder values before running ``fremor run`` or
   ``fremor yaml``.

2. **MIP table retrieval** – fetches the official MIP tables from trusted
   GitHub repositories. By default tables are fetched via ``git clone``
   (shallow, depth 1); with ``--fast`` they are fetched as a tarball via
   ``curl`` and extracted in-place. CMIP6Plus additionally gets its controlled
   vocabulary, which lives in a different repository than its tables.

Trusted sources
---------------
- CMIP6:     https://github.com/PCMDI/cmip6-cmor-tables
- CMIP6Plus: https://github.com/PCMDI/mip-cmor-tables
- CMIP7:     https://github.com/WCRP-CMIP/cmip7-cmor-tables

CMIP6Plus controlled vocabulary (not shipped with its tables)
-------------------------------------------------------------
- https://github.com/WCRP-CMIP/CMIP6Plus_CVs

Functions
---------
- ``cmor_init_subtool(...)``
"""

import json
import logging
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .cmor_constants import MIP_ERA_RESOURCES, CMIP6PLUS_CV_URL

fre_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trusted sources for MIP tables
# ---------------------------------------------------------------------------
MIP_TABLE_REPOS = {
    'cmip6'     : 'https://github.com/PCMDI/cmip6-cmor-tables',
    'cmip6plus' : 'https://github.com/PCMDI/mip-cmor-tables',
    'cmip7'     : 'https://github.com/WCRP-CMIP/cmip7-cmor-tables',
}


# ---------------------------------------------------------------------------
# Empty / template experiment configuration dictionaries
# ---------------------------------------------------------------------------

def _cmip6_exp_config_template():
    """Return an ordered dict-like structure for an empty CMIP6 experiment config."""
    return {
        '#note': ' **** CMIP6 experiment configuration template – fill in values below ****',
        'source_type': '',
        'experiment_id': '',
        'activity_id': '',
        'sub_experiment_id': 'none',
        'realization_index': '1',
        'initialization_index': '1',
        'physics_index': '1',
        'forcing_index': '1',
        'run_variant': '',
        'parent_experiment_id': 'no parent',
        'parent_activity_id': 'no parent',
        'parent_source_id': 'no parent',
        'parent_variant_label': 'no parent',
        'parent_time_units': 'no parent',
        'branch_method': 'no parent',
        'branch_time_in_child': 0.0,
        'branch_time_in_parent': 0.0,
        'institution_id': 'NOAA-GFDL',
        'source_id': '',
        'calendar': '',
        'grid': '',
        'grid_label': '',
        'nominal_resolution': '',
        'license': 'CMIP6 model data produced by Lawrence Livermore NOAA-GFDL is licensed under a Creative Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/). Consult https://pcmdi.llnl.gov/CMIP6/TermsOfUse for terms of use governing CMIP6 output, including citation requirements and proper acknowledgment. Further information about this data, including some limitations, can be found via the further_info_url (recorded as a global attribute in this file) and at https:///pcmdi.llnl.gov/. The data producers and data providers make no warranty, either express or implied, including, but not limited to, warranties of merchantability and fitness for a particular purpose. All liabilities arising from the supply of the information (including any liability arising in negligence) are excluded to the fullest extent permitted by law.', # pylint: disable=line-too-long
        'outpath': '',
        'contact': '',
        'history': '',
        'comment': '',
        'references': '',
        'sub_experiment': 'none',
        'institution': 'NOAA-GFDL',
        'source': '',
        '_controlled_vocabulary_file': MIP_ERA_RESOURCES['CMIP6']['cv'],
        '_AXIS_ENTRY_FILE': MIP_ERA_RESOURCES['CMIP6']['coordinate'],
        '_FORMULA_VAR_FILE': MIP_ERA_RESOURCES['CMIP6']['formula_terms'],
        '_cmip6_option': 'CMIP6',
        'mip_era': 'CMIP6',
        'parent_mip_era': 'no parent',
        'tracking_prefix': 'hdl:21.14100',
        '_history_template': (
            '%s ;rewrote data to be consistent with '
            '<activity_id> for variable <variable_id> found in table <table_id>.'
        ),
        'output_path_template': (
            '<mip_era><activity_id><institution_id><source_id>'
            '<experiment_id><_member_id><table><variable_id><grid_label><version>'
        ),
        'output_file_template': (
            '<variable_id><table><source_id><experiment_id><_member_id><grid_label>'
        ),
    }

def _cmip6plus_exp_config_template():
    """
    return a template for CMIP6Plus.

    the ``_``-prefixed keys point CMOR at the CMIP6Plus controlled vocabulary and auxiliary
    tables. CMOR resolves all three relative to the directory holding the MIP table being
    loaded (cmor_load_table builds ``dirname(<table>)/<name>``), and PCMDI/mip-cmor-tables
    keeps its auxiliary tables in ``Auxillary_files/`` alongside ``Tables/`` -- hence the
    ``../`` prefixes. the CMIP6Plus CV is not shipped with the MIP tables; it lives in
    WCRP-CMIP/CMIP6Plus_CVs and must be placed next to the tables by the user.
    """
    return {
        '#note': ' **** CMIP6Plus experiment configuration template – fill in values below ****',
        'source_type': '',
        'experiment_id': '',
        'activity_id': '',
        'sub_experiment_id': 'none',
        'realization_index': '1',
        'initialization_index': '1',
        'physics_index': '1',
        'forcing_index': '1',
        'run_variant': '',
        'parent_experiment_id': 'no parent',
        'parent_activity_id': 'no parent',
        'parent_source_id': 'no parent',
        'parent_variant_label': 'no parent',
        'parent_time_units': 'no parent',
        'branch_method': 'no parent',
        'branch_time_in_child': 0.0,
        'branch_time_in_parent': 0.0,
        'institution_id': 'NOAA-GFDL',
        'source_id': '',
        'calendar': '',
        'grid': '',
        'grid_label': '',
        'nominal_resolution': '',
        'license': 'CMIP6Plus model data produced by NOAA-GFDL is licensed under a Creative Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/). Consult https://pcmdi.llnl.gov/CMIP6Plus/TermsOfUse for terms of use governing CMIP6Plus output, including citation requirements and proper acknowledgment. The data producers and data providers make no warranty, either express or implied, including, but not limited to, warranties of merchantability and fitness for a particular purpose. All liabilities arising from the supply of the information (including any liability arising in negligence) are excluded to the fullest extent permitted by law.', # pylint: disable=line-too-long
        'outpath': '',
        'contact': '',
        'history': '',
        'comment': '',
        'references': '',
        'sub_experiment': 'none',
        'institution': 'NOAA-GFDL',
        'source': '',
        # CMOR looks for these three relative to the MIP table directory. supply
        # CMIP6Plus_CV.json from WCRP-CMIP/CMIP6Plus_CVs; the coordinate/formula tables ship
        # with PCMDI/mip-cmor-tables under Auxillary_files/, one level up from Tables/.
        '_controlled_vocabulary_file': MIP_ERA_RESOURCES['CMIP6PLUS']['cv'],
        '_AXIS_ENTRY_FILE': MIP_ERA_RESOURCES['CMIP6PLUS']['coordinate'],
        '_FORMULA_VAR_FILE': MIP_ERA_RESOURCES['CMIP6PLUS']['formula_terms'],
        # CMOR only tests whether _cmip6_option is present, never its value; presence enables
        # the CMIP6-style CV checks (source_id, experiment, grids, parent/sub experiment ids),
        # which CMIP6Plus still uses. keep it.
        '_cmip6_option': 'CMIP6',
        'mip_era': 'CMIP6Plus',
        'parent_mip_era': 'no parent',
        'tracking_prefix': 'hdl:21.14100',
        '_history_template': (
            '%s ;rewrote data to be consistent with '
            '<activity_id> for variable <variable_id> found in table <table_id>.'
        ),
        'output_path_template': (
            '<mip_era><activity_id><institution_id><source_id>'
            '<experiment_id><_member_id><table><variable_id><grid_label><version>'
        ),
        'output_file_template': (
            '<variable_id><table><source_id><experiment_id><_member_id><grid_label>'
        ),
    }

def _cmip7_exp_config_template():
    """Return an ordered dict-like structure for an empty CMIP7 experiment config."""
    return {
        '#note': ' **** CMIP7 experiment configuration template – fill in values below ****',
        'contact': 'MIP participant mipmember@foobar.c.om',
        'comment': 'additional important information not fitting into other fields can be placed here',
        'license': 'CC-BY-4.0; CMIP7 data produced by NOAA-GFDL is licensed under a Creative Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0). Consult https://wcrp-cmip.github.io/cmip7-guidance/docs/CMIP7/Guidance_for_users/#2-terms-of-use-and-citations-requirements for terms of use governing CMIP7 output, including citation requirements and proper acknowledgment. The data producers and data providers make no warranty, either express or implied, including, but not limited to, warranties of merchantability and fitness for a particular purpose. All liabilities arising from the supply of the information (including any liability arising in negligence) are excluded to the fullest extent permitted by law.', # pylint: disable=line-too-long
        'references': '',
        'drs_specs': 'MIP-DRS7',
        'archive_id': 'WCRP',
        'license_id': 'CC-BY-4.0',
        'tracking_prefix': 'hdl:21.14107',
        '_cmip7_option': 1,
        'mip_era': 'CMIP7',
        'activity_id': 'CMIP',
        'parent_mip_era': 'CMIP7',
        'parent_activity_id': 'CMIP',
        'institution_id': 'NOAA-GFDL',
        'source': 'GFDL-ESM4p5: aerosol: gfdl-am4p5-aerosol; atmosphere: gfdl-am4p5; land-surface: gfdl-lm4p5; ocean-biogeochemistry: cobaltv3p1; ocean: gfdl-om4p5; sea-ice: sis2', # pylint disable=line-too-long
        'source_id': 'GFDL-ESM4p5',
        'source_type': '',
        'experiment_id': '',
        'parent_experiment_id': '',
        'parent_variant_label': '',
        'parent_source_id': '',
        'sub_experiment': 'none',
        'sub_experiment_id': 'none',
        'realization_index': 'r1',
        'initialization_index': 'i1',
        'physics_index': 'p1',
        'forcing_index': 'f1',
        'run_variant': '',
        'branch_method': 'no parent',
        'branch_time_in_child': 0.0,
        'branch_time_in_parent': 0.0,
        'parent_time_units': '',
        'calendar': '',
        'grid': 'PLACEHOLD',
        'grid_label': 'g999',
        'frequency': '',
        'region': '',
        'nominal_resolution': '',
        'history': '',
        '_history_template': (
            '%s ;rewrote data to be consistent with '
            '<activity_id> for variable <variable_id> found in table <table_id>.'
        ),
        'outpath': '.',
        'output_path_template': (
            '<activity_id><source_id><experiment_id><member_id>'
            '<variable_id><branding_suffix><grid_label>'
        ),
        'output_file_template': (
            '<variable_id><branding_suffix><frequency><region>'
            '<grid_label><source_id><experiment_id><variant_label>'
        ),
        '_controlled_vocabulary_file': MIP_ERA_RESOURCES['CMIP7']['cv'],
        '_AXIS_ENTRY_FILE': MIP_ERA_RESOURCES['CMIP7']['coordinate'],
        '_FORMULA_VAR_FILE': MIP_ERA_RESOURCES['CMIP7']['formula_terms'],
    }


# ---------------------------------------------------------------------------
# Table-fetching helpers
# ---------------------------------------------------------------------------

def _fetch_tables_git(repo_url, tables_dir, tag=None):
    """
    Clone MIP tables via ``git clone --depth 1``.

    Parameters
    ----------
    repo_url : str
        HTTPS URL of the MIP table repository.
    tables_dir : str
        Local directory to clone into.
    tag : str or None
        Optional git tag / branch to check out.
    """
    cmd = ['git', 'clone', '--depth', '1']
    if tag:
        cmd += ['--branch', tag]
    cmd += [repo_url, tables_dir]

    fre_logger.info('fetching MIP tables via git: %s', ' '.join(cmd))
    subprocess.run(cmd, check=True)
    fre_logger.info('MIP tables cloned to %s', tables_dir)


def _fetch_tables_curl(repo_url, tables_dir, tag=None):
    """
    Fetch MIP tables as a tarball via ``curl`` and extract.

    Parameters
    ----------
    repo_url : str
        HTTPS URL of the MIP table repository.
    tables_dir : str
        Local directory to extract into.
    tag : str or None
        Optional git tag / branch. Defaults to ``main`` if *None*.
    """
    ref = tag if tag else 'main'
    if tag:
        tarball_url = f'{repo_url}/archive/refs/tags/{ref}.tar.gz'
    else:
        tarball_url = f'{repo_url}/archive/refs/heads/{ref}.tar.gz'

    tables_path = Path(tables_dir)
    tables_path.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        curl_cmd = ['curl', '-L', '-o', tmp_path, tarball_url]
        fre_logger.info('fetching MIP tables via curl: %s', ' '.join(curl_cmd))
        subprocess.run(curl_cmd, check=True)

        fre_logger.info('extracting tarball to %s', tables_dir)
        with tarfile.open(tmp_path, 'r:gz') as tar:
            tar.extractall(path=tables_dir)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    fre_logger.info('MIP tables extracted to %s', tables_dir)


def _fetch_cmip6plus_cv(tables_dir):
    """
    Fetch ``CMIP6Plus_CV.json`` and drop it next to the CMIP6Plus MIP tables.

    PCMDI/mip-cmor-tables ships no controlled vocabulary; the CMIP6Plus CV is maintained
    separately in WCRP-CMIP/CMIP6Plus_CVs. CMOR looks for the CV named in the experiment
    config's ``_controlled_vocabulary_file`` in the directory of the MIP table it loads, so
    the CV is written into the ``Tables`` directory of the freshly fetched table set.

    Parameters
    ----------
    tables_dir : str
        Directory the MIP tables were fetched into.

    Returns
    -------
    str or None
        Path to the CV that was written, or *None* if it could not be fetched.
    """
    tables_path = Path(tables_dir)

    # `git clone` puts Tables/ at the top; the --fast tarball nests it one level down
    candidate_dirs = [tables_path / 'Tables'] + sorted(tables_path.glob('*/Tables'))
    target_dirs = [candidate for candidate in candidate_dirs if candidate.is_dir()]
    if not target_dirs:
        fre_logger.warning(
            'no Tables directory found under %s, skipping the CMIP6Plus CV fetch. '
            'download %s by hand and place it alongside your MIP tables.',
            tables_dir, CMIP6PLUS_CV_URL)
        return None

    target = target_dirs[0] / 'CMIP6Plus_CV.json'
    curl_cmd = ['curl', '-L', '--fail', '-o', str(target), CMIP6PLUS_CV_URL]
    fre_logger.info('fetching the CMIP6Plus controlled vocabulary: %s', ' '.join(curl_cmd))
    try:
        subprocess.run(curl_cmd, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        target.unlink(missing_ok=True)
        fre_logger.warning(
            'could not fetch the CMIP6Plus CV (%s). download %s by hand and place it in %s, '
            'or point _controlled_vocabulary_file at your own copy.',
            exc, CMIP6PLUS_CV_URL, target_dirs[0])
        return None

    fre_logger.info('CMIP6Plus controlled vocabulary written to %s', target)
    return str(target)

# ---------------------------------------------------------------------------
# Main subtool entry-point
# ---------------------------------------------------------------------------

def cmor_init_subtool(
        mip_era,
        exp_config=None,
        tables_dir=None,
        tag=None,
        fast=False
):
    """
    Initialise CMOR resources for the user.

    Depending on the arguments supplied this function will:

    * Write an empty experiment-configuration JSON file for the requested MIP
      era (``cmip6`` or ``cmip7``) when *exp_config* is given (or when neither
      *exp_config* nor *tables_dir* is provided — in which case a default
      filename is used).
    * Clone / download the official MIP tables into *tables_dir* when that
      argument is provided. For CMIP6Plus the controlled vocabulary, which the
      table repo does not ship, is fetched alongside them.

    Parameters
    ----------
    mip_era : str
        ``'cmip6'``, ``'cmip6plus'```, or ``'cmip7'``.
    exp_config : str or None
        Output path for the template experiment-config JSON file.
        When *None* and *tables_dir* is also *None*, a default path
        ``CMOR_<MIP_ERA>_template.json`` in the current directory is used.
    tables_dir : str or None
        Directory into which MIP tables will be fetched.
    tag : str or None
        Optional git tag / release for the MIP tables repository.
    fast : bool
        When *True*, use ``curl`` to download a tarball instead of ``git clone``.

    Returns
    -------
    dict
        A dictionary with keys ``'exp_config'`` (path written or *None*),
        ``'tables_dir'`` (path written or *None*) and ``'cv_file'`` (path of the
        CMIP6Plus CV fetched alongside the tables, or *None*).
    """
    mip_era_lower = mip_era.lower()
    if mip_era_lower not in ('cmip6', 'cmip6plus', 'cmip7'):
        raise ValueError(f'mip_era must be cmip6, cmip6plus, or cmip7, got {mip_era}')

    result = {'exp_config': None, 'tables_dir': None, 'cv_file': None}

    if exp_config is None and tables_dir is None: # create a default user exp json
        exp_config = f'CMOR_{mip_era_lower}_template.json'

    # -- MIP tables --
    if tables_dir is not None:
        repo_url = MIP_TABLE_REPOS[mip_era_lower]
        if fast:
            _fetch_tables_curl(repo_url, tables_dir, tag=tag)
        else:
            _fetch_tables_git(repo_url, tables_dir, tag=tag)
        result['tables_dir'] = tables_dir

        # the CMIP6Plus CV is not part of the CMIP6Plus table repo; fetch it so the
        # _controlled_vocabulary_file written into the config template resolves.
        if mip_era_lower == 'cmip6plus':
            result['cv_file'] = _fetch_cmip6plus_cv(tables_dir)

    # -- experiment config --
    # Write config when explicitly requested OR when tables_dir is not given
    # (i.e. the user invoked `fremor init` without --tables_dir).
    if exp_config is not None:

        template_func = {
            'cmip6'     : _cmip6_exp_config_template,
            'cmip6plus' : _cmip6plus_exp_config_template,
            'cmip7'     : _cmip7_exp_config_template,
        }[mip_era_lower]

        config_data = template_func()

        out_path = Path(exp_config)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump(config_data, fh, indent=4)
            fh.write('\n')

        fre_logger.info('wrote %s experiment config template to %s',
                        mip_era_lower.upper(), out_path)
        click_echo = f'Wrote {mip_era_lower.upper()} experiment config template to {out_path}'
        print(click_echo)
        result['exp_config'] = str(out_path)

    return result
