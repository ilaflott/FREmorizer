"""
``fremor``: pre-run configuration validation
============================================

Checks that run *before* CMOR is handed anything, so that a misconfigured experiment fails
immediately and legibly instead of part-way through a CMORization as a wall of per-variable
C tracebacks.

Functions
---------
- ``resolve_cv_path(json_exp_config, json_table_config)``
- ``check_exp_config_required_attributes(json_exp_config, json_table_config)``
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .cmor_constants import CMOR_PROVIDED_GLOBAL_ATTRIBUTES
from .cmor_helpers import get_json_file_data

fre_logger = logging.getLogger(__name__)


def resolve_cv_path( json_exp_config: str,
                     json_table_config: str) -> Optional[Path]:
    """
    Locate the controlled vocabulary file CMOR will load for a run.

    Mirrors ``cmor_load_table``: the CV named by the experiment config's
    ``_controlled_vocabulary_file`` is resolved against the directory of the MIP table being
    loaded. CMOR falls back to ``cmor_input_path`` when that misses, but cmor_mixer sets
    ``inpath`` to that same directory, so there is only the one place to look.

    :param json_exp_config: Path to the JSON experiment configuration.
    :type json_exp_config: str
    :param json_table_config: Path to the MIP table CMOR will load.
    :type json_table_config: str
    :return: Path to the CV file, or None if it is not where CMOR would look.
    :rtype: Path or None
    """
    exp_config_data = get_json_file_data(json_exp_config)

    # CMOR's own default, from cmor.h's TABLE_CONTROL_FILENAME, applies when the key is absent
    cv_name = exp_config_data.get('_controlled_vocabulary_file', 'CMIP6_CV.json')

    cv_path = Path(json_table_config).parent / cv_name
    if not cv_path.exists():
        return None
    return cv_path.resolve()


def check_exp_config_required_attributes( json_exp_config: str,
                                          json_table_config: str) -> None:
    """
    Verify, before CMOR runs, that the experiment config fills in every attribute the CV requires.

    CMOR only reports these one variable at a time, deep inside ``cmor_write``, and reports a
    blank value as a missing attribute (it discards empty values outright), so without this
    check a single unfilled template field surfaces as a wall of confusing per-variable errors
    after a good deal of work has already been done.

    Attributes CMOR supplies itself are exempt -- see ``CMOR_PROVIDED_GLOBAL_ATTRIBUTES``.

    :param json_exp_config: Path to the JSON experiment configuration.
    :type json_exp_config: str
    :param json_table_config: Path to the MIP table CMOR will load, used to locate the CV.
    :type json_table_config: str
    :raises ValueError: If any required attribute is missing or left blank.
    :return: None
    :rtype: None

    .. note:: Silently skipped when the CV cannot be found or read -- CMOR reports that itself,
              and far more clearly than a guess here would.
    """
    cv_path = resolve_cv_path(json_exp_config, json_table_config)
    if cv_path is None:
        fre_logger.warning(
            'could not find the controlled vocabulary file alongside %s, '
            'skipping the required-attribute check', json_table_config)
        return

    try:
        with open(cv_path, 'r', encoding='utf-8') as cv_file:
            required = json.load(cv_file)['CV']['required_global_attributes']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fre_logger.warning(
            'could not read required_global_attributes from %s (%s), '
            'skipping the required-attribute check', cv_path, exc)
        return

    exp_config_data = get_json_file_data(json_exp_config)

    missing, blank = [], []
    for attribute in required:
        if attribute in CMOR_PROVIDED_GLOBAL_ATTRIBUTES:
            continue
        if attribute not in exp_config_data:
            missing.append(attribute)
        elif not str(exp_config_data[attribute]).strip():
            blank.append(attribute)

    if not missing and not blank:
        fre_logger.info('all %s CV-required attributes are set in %s',
                        len(required), json_exp_config)
        return

    message = (
        'the experiment configuration does not satisfy the controlled vocabulary.\n'
        f'  experiment config: {json_exp_config}\n'
        f'  controlled vocabulary: {cv_path}\n')
    if blank:
        message += ('  left blank (CMOR discards empty values, so these never reach the output):\n'
                    '    ' + '\n    '.join(blank) + '\n')
    if missing:
        message += ('  absent entirely:\n'
                    '    ' + '\n    '.join(missing) + '\n')
    if any(attribute in ['grid', 'grid_label', 'nominal_resolution']
           for attribute in blank + missing):
        message += (
            '  note: grid, grid_label and nominal_resolution are overwritten from the gridding\n'
            '        block in the cmor yaml on every run, so fix them there rather than in the\n'
            '        config above (or drop the gridding block to leave the config alone).\n')
    raise ValueError(message)
