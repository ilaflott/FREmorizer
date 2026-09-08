.. _cookbook:

========
Cookbook
========

This cookbook provides practical examples and procedures for using ``fremor`` to CMORize climate model output.
It demonstrates the relationship between the different subcommands and provides guidance on debugging CMORization processes.

.. contents:: Contents
   :local:
   :depth: 2

Overview
--------

The ``fremor`` process typically follows this pattern:

1. **Initialize Resources** — Use ``fremor init`` to generate config templates and fetch MIP tables
2. **Setup and Configuration** — Customize your experiment configuration, create variable lists, and prepare metadata
3. **CMORization** — Use ``fremor run`` to process individual directories or ``fremor yaml`` for bulk processing
4. **Troubleshooting** — Diagnose issues as needed

Initialize Resources
--------------------

Before beginning CMORization, initialize the required resources:

.. code-block:: bash

   # Generate CMIP6 experiment config template and fetch tables
   fremor init -m cmip6 -e CMOR_cmip6_config.json -t cmip6-tables

   # Generate CMIP6Plus experiment config template and fetch tables (fast mode)
   fremor init -m cmip6plus -e CMOR_cmip6plus_config.json -t mip-cmor-tables --fast

   # Generate CMIP7 experiment config template and fetch tables (fast mode)
   fremor init -m cmip7 -e CMOR_cmip7_config.json -t cmip7-tables --fast

The ``init`` command:

* Creates an experiment configuration JSON template with all required CMIP metadata fields
* Fetches official MIP tables from trusted GitHub repositories
* Supports git clone (default) or tarball download (``--fast``) for table retrieval
* Can fetch specific release tags using ``--tag``

After running ``init``, you'll have:

* An experiment configuration JSON file to customize with your experiment metadata
* A directory of MIP tables ready to use for CMORization

Setup and Configuration
-----------------------

Before beginning CMORization, gather the following information:

* **Experiment name** (``-e``) — The name of your experiment as defined in the model YAML
* **Platform** (``-p``) — The platform configuration (e.g., ``gfdl.ncrc6-intel23``, ``ncrc5.intel``)
* **Target** (``-t``) — The compilation target (e.g., ``prod-openmp``, ``debug``)
* **Post-processing directory** — Location of your model's post-processed output (e.g., ``/archive/user/experiment/pp/``)
* **Output directory** — Where CMORized output should be written

Identifying Parameters from FRE Output
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have existing FRE output, you can extract the required parameters from the directory structure. The post-processing directory is typically located at::

    /archive/username/experiment/platform-target/pp/

From this path, you can identify:

* ``experiment`` = experiment (the experiment name)
* ``platform-target`` = the combined platform and target string (e.g., ``ncrc5.intel-prod-openmp``)

You will need to split the platform-target string appropriately to extract the individual ``platform`` and ``target`` values for use with ``fremor`` commands.

Creating Variable Lists
~~~~~~~~~~~~~~~~~~~~~~~

Variable lists map your modeler variable names to MIP table variable names. Generate a variable list from a directory of netCDF files:

.. code-block:: bash

   fremor varlist \
       -d /path/to/component/output \
       -o generated_varlist.json

This tool examines filenames to extract variable names. It assumes FRE-style naming conventions
(e.g., ``component.YYYYMMDD.variable.nc``). The same variable name may appear in multiple files
at different datetimes; ``fremor varlist`` deduplicates automatically so each variable appears
only once in the output.

**Using a MIP table to filter variables**

Pass ``-t`` to cross-reference found variables against a specific MIP table:

.. code-block:: bash

   fremor varlist \
       -d /path/to/ocean/ts/monthly/5yr \
       -t /path/to/cmip6-cmor-tables/Tables/CMIP6_Omon.json \
       -o ocean_varlist.json

When a MIP table is provided:

* Variables whose names match a MIP entry are **self-mapped** (key and value are identical, e.g., ``"sos": "sos"``). These are ready to use immediately.
* Variables whose names do **not** match any MIP entry receive an **empty-string value** (e.g., ``"sea_sfc_salinity": ""``). This signals that manual mapping is required.
* Matching is **case-insensitive** — ``LWP`` in a filename will match ``lwp`` in the MIP table.

Review the generated file and fill in any empty-string values to map local variable names to target MIP variable names. For example:

.. code-block:: json

   {
       "sos": "sos",
       "sea_sfc_salinity": "sos"
   }

The key (e.g., ``sea_sfc_salinity``) is the modeler's variable name — it must match both the filename
and the variable name inside the netCDF file. The value (e.g., ``sos``) is the MIP table variable name
used for metadata lookups.

**Strict mode**

If you provide a MIP table and want to skip components where none of the found variables match any MIP
entry (rather than writing a file full of empty-value entries), use ``--strict_mode``:

.. code-block:: bash

   fremor varlist \
       -d /path/to/land/ts/monthly/5yr \
       -t /path/to/cmip6-cmor-tables/Tables/CMIP6_Amon.json \
       --strict_mode \
       -o land_atmos_varlist.json

If no variables match, nothing is written and the tool exits without error. This is useful for
batch workflows where many components are checked against many tables and most pairs have no overlap.

To verify variables exist in MIP tables, search for variable definitions:

.. code-block:: bash

   fremor -v find \
       -r /path/to/cmip6-cmor-tables/Tables/ \
       -v variable_name

Or search for all variables in a varlist:

.. code-block:: bash

   fremor -v find \
       -r /path/to/cmip6-cmor-tables/Tables/ \
       -l /path/to/varlist.json

This displays which MIP table contains the variable and its metadata requirements.

Preparing Experiment Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The experiment configuration JSON file contains required metadata for CMORization.
The required fields are determined by the target MIP's controlled vocabularies — see
`CMIP7 required global attributes <https://github.com/WCRP-CMIP/cmip7-cmor-tables/blob/main/tables-cvs/split-view/required_global_attributes.json>`_
for an example of how a MIP specifies its required metadata. Reference example
experiment configuration files for each MIP era:

* **CMIP6**: `CMIP6_input_example.json <https://github.com/PCMDI/cmip6-cmor-tables/blob/main/Tables/CMIP6_input_example.json>`_
* **CMIP6Plus**: `CMOR_input_example.json <https://github.com/PCMDI/mip-cmor-tables/blob/main/src/exploration/old/CMOR_input_example.json>`_
* **CMIP7**: `cmor_test.py (lines 9–41) <https://github.com/WCRP-CMIP/cmip7-cmor-tables/blob/main/scripts/cmor_test.py#L9-L41>`_
  and `CMOR_input_example.json (PCMDI/cmor) <https://github.com/PCMDI/cmor/blob/9d82dfb7c091cd0e0366fffd8a50f4d17f85f4a6/Test/CMOR_input_example.json>`_

Use ``fremor init -m <mip_era> -e exp_config.json`` to generate a template
pre-populated with the correct fields for your target MIP era.
This file should include:

* Experiment metadata (``experiment_id``, ``activity_id``, ``source_id``, etc.)
* Institution and contact information
* Grid information (``grid_label``, ``nominal_resolution``)
* Variant labels (``realization_index``, ``initialization_index``, etc.)
* Parent experiment information (if applicable)
* Calendar type

Refer to CMIP6 controlled vocabularies and your project's requirements when filling in these fields.

Running Your CMORization
------------------------

CMORizing One Table/Variable List in a Directory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``fremor run`` command is the fundamental building block for CMORization. It processes netCDF files from a single input directory according to a specified MIP table and variable list.

For processing individual directories or debugging specific issues, use ``fremor run`` directly:

.. code-block:: bash

   fremor -vv run \
       --indir /path/to/component/output \
       --varlist /path/to/varlist.json \
       --table_config /path/to/CMIP6_Table.json \
       --exp_config /path/to/experiment_config.json \
       --outdir /path/to/cmor/output \
       --grid_label gn \
       --grid_desc "native grid description" \
       --nom_res "100 km" \
       --run_one

Required arguments:

* ``--indir``: Directory containing netCDF files to CMORize
* ``--varlist``: JSON file mapping modeler variable names to MIP table variable names
* ``--table_config``: MIP table JSON file (e.g., ``CMIP6_Omon.json``)
* ``--exp_config``: Experiment configuration JSON with metadata
* ``--outdir``: Output directory root for CMORized files

Optional but recommended:

* ``--grid_label``: Grid label (``gn`` for native, ``gr`` for regridded)
* ``--grid_desc``: Description of the grid
* ``--nom_res``: Nominal resolution (must match controlled vocabulary)
* ``--opt_var_name``: Process only files matching this variable name
* ``--run_one``: Process only one file (for testing)
* ``--start``: Start year (``YYYY`` format)
* ``--stop``: Stop year (``YYYY`` format)
* ``--calendar``: Calendar type (e.g., ``julian``, ``noleap``, ``360_day``)

Bulk CMORization Over Many Tables and Directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``fremor yaml`` command provides a higher-level interface for CMORizing multiple components and MIP tables.
It parses YAML configuration, then generates and executes a set of ``fremor run`` commands based on that
configuration.

This is the recommended approach for CMORizing multiple components and MIP tables in a systematic way.

**Step 1: Test with Dry Run**

Test the process without actually CMORizing files:

.. code-block:: bash

   fremor -vv yaml \
       -y /path/to/model.yaml \
       -e EXPERIMENT_NAME \
       -p PLATFORM \
       -t TARGET \
       --dry_run \
       --run_one

This prints the ``fremor run`` commands that would be executed, allowing you to verify:

* Input directories are correct
* Output paths are as expected
* Variable lists are found
* MIP tables are accessible

**Step 2: Process One File for Testing**

Process only one file to verify the process:

.. code-block:: bash

   fremor -vv yaml \
       -y /path/to/model.yaml \
       -e EXPERIMENT_NAME \
       -p PLATFORM \
       -t TARGET \
       --run_one

**Step 3: Full CMORization**

Once validated, remove ``--run_one`` for full processing:

.. code-block:: bash

   fremor -v yaml \
       -y /path/to/model.yaml \
       -e EXPERIMENT_NAME \
       -p PLATFORM \
       -t TARGET

Auto-generating CMOR YAML from Post-processing Output
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have a post-processing directory tree and MIP tables, ``fremor config`` can auto-generate the CMOR
YAML configuration for you:

.. code-block:: bash

   fremor config \
       -p /path/to/pp \
       -t /path/to/mip-tables \
       -m cmip7 \
       -e exp_config.json \
       -o cmor.yaml \
       -d /path/to/output \
       -l /path/to/varlists

This scans the ``pp_dir`` for post-processing components, cross-references found variables against MIP
tables, writes per-component variable list files, and emits a structured YAML that ``fremor yaml`` can
later consume.

To limit which pp component directories are scanned, use ``-g``/``--pp_comp_glob``:

.. code-block:: bash

   fremor config \
       -p /path/to/pp \
       -t /path/to/mip-tables \
       -m cmip7 \
       -e exp_config.json \
       -o cmor.yaml \
       -d /path/to/output \
       -l /path/to/varlists \
       -g 'ocean*'

To skip components where none of the found variables match any MIP entry (i.e., apply
``--strict_mode`` to every internal ``fremor varlist`` call), add ``--strict_varlist``:

.. code-block:: bash

   fremor config \
       -p /path/to/pp \
       -t /path/to/mip-tables \
       -m cmip7 \
       -e exp_config.json \
       -o cmor.yaml \
       -d /path/to/output \
       -l /path/to/varlists \
       --strict_varlist

Components that produce no MIP-matching variables are silently omitted from the generated YAML,
keeping the output focused on components that actually have data that can be cmorized.

Common Issues and Solutions
---------------------------

``fremor run`` Stops on ``does not satisfy the controlled vocabulary``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Before any CMORization work begins, ``fremor`` checks the experiment configuration against the
``required_global_attributes`` list in the controlled vocabulary CMOR will load, and stops if any
of them is missing or left blank. Attributes CMOR supplies itself (``creation_date``,
``tracking_id``, ``variable_id``, the CMIP7 brand components, and so on) are exempt.

A blank value is treated exactly like a missing one, because CMOR discards empty attributes
outright — so ``"grid": ""`` never reaches the output file, and without this check it surfaces
much later as a per-variable ``Please set attribute: "grid" in your input file`` from
``cmor_write``.

The fix is to fill the listed fields in the experiment config. The exception is ``grid``,
``grid_label`` and ``nominal_resolution``: those are rewritten from the ``gridding:`` block of the
cmor YAML on every run, so set them there instead — or remove the ``gridding:`` block to leave the
experiment config untouched.

The check is skipped, with a warning, when the CV cannot be found or parsed; CMOR reports that
case itself.

``fremor resolve`` Fails at YAML Combination Step
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. note::

   This section and general YAML behavior is under-review and being refactored

``fremor resolve`` fails with key errors or anchor errors during model/CMOR YAML resolution.

.. note::

   ``fremor resolve`` now uses ``fremor``'s own lightweight YAML loader. It reads the
   model yaml, finds the referenced CMOR yaml and optional grids yaml, and resolves
   only the YAML needed for CMOR debugging.

To debug this issue:

* Verify all referenced YAML files exist and are readable
* Verify anchors referenced in the CMOR YAML are defined in the model YAML or grids YAML
* Verify that the ``cmor:`` section exists in the resolved output
* Verify the CMOR YAML path is relative to the model YAML location

No Files Found in Input Directory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``fremor run`` reports no files matching the variable list.

Solutions:

* Verify ``--indir`` points to the correct directory
* Check that files follow expected naming conventions
* Use ``fremor varlist`` to generate a list from actual filenames
* Use ``--opt_var_name`` to target a specific variable for testing

Grid Metadata Issues
~~~~~~~~~~~~~~~~~~~~

Errors about missing or invalid grid labels or nominal resolution.

Solutions:

* Ensure ``--grid_label`` matches controlled vocabulary (typically ``gn`` or ``gr``)
* Verify ``--nom_res`` is in the controlled vocabulary for your MIP
* Check that grid descriptions are provided if overriding experiment config
* Review the experiment configuration JSON for grid-related fields

Calendar or Date Range Issues
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Files are skipped or errors related to calendar types.

Solutions:

* Specify ``--calendar`` if the automatic detection fails
* Use ``--start`` and ``--stop`` to limit the date range processed
* Verify that datetime strings in filenames match expected ISO8601 format
* Check that the calendar type in your data matches the MIP requirements

Example: Ocean Monthly Data CMORization
---------------------------------------

This example demonstrates CMORizing ocean monthly output for multiple components.

Prepare the model YAML (excerpt from ``experiments`` section):

.. code-block:: yaml

   experiments:
     - name: "my_ocean_experiment"
       pp:
         - "pp_yamls/settings.yaml"
         - "pp_yamls/ocean_monthly.yaml"
       cmor:
         - "cmor_yamls/ocean_cmor.yaml"
       grid_yaml:
         - "grid_yamls/ocean_grids.yaml"

Prepare the CMOR YAML (``cmor_yamls/ocean_cmor.yaml``):

.. code-block:: yaml

   cmor:
     start: "1950"
     stop: "2000"
     mip_era: "CMIP6"
     exp_json: "/path/to/experiment_config.json"

     directories:
       pp_dir: "/path/to/pp"
       table_dir: "/path/to/cmip6-cmor-tables/Tables"
       outdir: "/path/to/cmor/output"

     table_targets:
       - table_name: "Omon"
         # disabled: true   # optional -- set to skip this table_target in `fremor yaml` entirely
         freq: "monthly"
         gridding:
           grid_label: "gn"
           grid_desc: "native tripolar ocean grid"
           nom_res: "100 km"

         target_components:
           - component_name: "ocean_monthly"
             variable_list: "/path/to/ocean_varlist.json"
             data_series_type: "ts"
             chunk: "P1Y"

Test with dry run:

.. code-block:: bash

   fremor -vv yaml \
       -y model.yaml \
       -e my_ocean_experiment \
       -p ncrc5.intel \
       -t prod-openmp \
       --dry_run

Process one file:

.. code-block:: bash

   fremor -vv yaml \
       -y model.yaml \
       -e my_ocean_experiment \
       -p ncrc5.intel \
       -t prod-openmp \
       --run_one

Full processing:

.. code-block:: bash

   fremor yaml \
       -y model.yaml \
       -e my_ocean_experiment \
       -p ncrc5.intel \
       -t prod-openmp

Tips
----

* Use ``--dry_run`` with ``fremor yaml`` to preview the equivalent ``fremor run`` calls before execution
* Use ``--no-print_cli_call`` with ``--dry_run`` to see the Python ``cmor_run_subtool(...)`` call instead of the CLI invocation — useful for debugging
* Use ``--run_one`` with ``fremor run`` for testing to only process a single file and catch issues early
* Use ``--run_one`` with ``fremor yaml`` to process a single file per ``fremor run`` call for quicker debugging
* Use ``--run_strict`` with ``fremor yaml`` to stop immediately when any ``fremor run`` call raises an exception — without it, failures are logged as warnings and processing continues to the next component
* Use ``fremor config`` to auto-generate a CMOR YAML configuration from a post-processing directory tree — it scans components, cross-references against MIP tables, and writes both variable lists and the YAML that ``fremor yaml`` expects
* Use ``-t`` with ``fremor varlist`` to cross-reference found variables against a MIP table: matched variables are self-mapped, unmatched variables receive an empty-string value indicating they need manual mapping
* Use ``--strict_mode`` with ``fremor varlist`` (or ``--strict_varlist`` with ``fremor config``) to suppress output for components where no found variables match the MIP table
* Increase verbosity when debugging — use ``-v`` to see ``INFO`` logging, and ``-vv`` (or ``-v -v``) for ``DEBUG`` logging
* Version control your YAML files — track changes to your CMORization configuration and commit them to git!
* Check controlled vocabulary — verify grid labels and nominal resolutions are CV-compliant
* Review experiment config — ensure all required metadata fields are populated; ``fremor run`` verifies this against the CV before starting and names every unfilled field at once
