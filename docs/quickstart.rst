.. _quickstart:

============================
CMOR Quickstart (``fremor``)
============================

This guide adapts the ``README`` for ``fre.cmor`` in ``NOAA-GFDL/fre-cli`` for the
standalone ``fremor`` package. The ``fremor`` CLI rewrites climate model output
with CMIP-compliant metadata (\"CMORization\") and supports CMIP6, CMIP6Plus, and
CMIP7 workflows.

Comprehensive API and CLI reference material lives in :ref:`usage` and
:ref:`commands`. Use this page when you want a concise, end-to-end reminder of
what configuration is required and how to invoke each subcommand.

Documentation and References
----------------------------

* `fremor docs (latest) <https://fremor.readthedocs.io/en/latest/>`_
* `fre-cli CMOR docs <https://noaa-gfdl.readthedocs.io/projects/fre-cli/en/latest/usage.html#cmorize-postprocessed-output>`_
* `PCMDI cmor documentation <http://cmor.llnl.gov/>`_

Getting Started
---------------

Initialize CMOR resources
~~~~~~~~~~~~~~~~~~~~~~~~~

Before CMORizing data, use ``fremor init`` to set up required resources:

.. code-block:: bash

   # Generate a CMIP6 config template and fetch CMIP6 tables
   fremor init -m cmip6 -e exp_config.json -t cmip6-tables

   # Generate a CMIP6Plus config template and fetch CMIP6Plus tables (fast mode)
   fremor init -m cmip6plus -e exp_config.json -t mip-cmor-tables --fast

   # Generate a CMIP7 config template and fetch CMIP7 tables (fast mode)
   fremor init -m cmip7 -e exp_config.json -t cmip7-tables --fast

   # Only generate a config template (skip table fetching)
   fremor init -m cmip6 -e exp_config.json

   # Only fetch tables (skip config template)
   fremor init -m cmip6 -t cmip6-tables

   # Fetch a specific release tag
   fremor init -m cmip6 -t cmip6-tables --tag 6.9.33

The ``init`` command:

* Generates experiment configuration JSON templates with required CMIP metadata fields
* Fetches official MIP tables from trusted GitHub repositories:
   - CMIP6: `pcmdi/cmip6-cmor-tables <https://github.com/pcmdi/cmip6-cmor-tables>`_
   - CMIP6Plus: `PCMDI/mip-cmor-tables <https://github.com/PCMDI/mip-cmor-tables>`_
   - CMIP7: `WCRP-CMIP/cmip7-cmor-tables <https://github.com/WCRP-CMIP/cmip7-cmor-tables>`_
* Supports both git clone (default) and tarball download (``--fast``) methods

MIP Era Differences
~~~~~~~~~~~~~~~~~~~

``fremor`` supports three MIP eras, selected via the ``-m/--mip_era`` option:

* **CMIP6** — The original CMIP Phase 6 tables and controlled vocabularies from
  `pcmdi/cmip6-cmor-tables <https://github.com/pcmdi/cmip6-cmor-tables>`_. Use this
  for existing CMIP6 datasets.

* **CMIP6Plus** — A transitional era using the unified
  `PCMDI/mip-cmor-tables <https://github.com/PCMDI/mip-cmor-tables>`_ repository.
  CMIP6Plus shares the same experiment configuration structure as CMIP6 but uses
  updated table definitions. Choose this for new submissions that follow CMIP6-era
  conventions but target the newer consolidated table infrastructure.

  Two CMIP6Plus quirks are worth knowing, both handled for you by
  ``fremor init -m cmip6plus``:

  - Its variable tables are named ``MIP_<table>.json`` and live in ``Tables/``,
    while the coordinate, formula-terms and grids tables live one level up in
    ``Auxillary_files/``. CMOR resolves those relative to the table it is loading,
    so the generated config points at ``../Auxillary_files/MIP_coordinate.json``
    and friends.
  - The controlled vocabulary is *not* in the table repository. ``CMIP6Plus_CV.json``
    comes from `WCRP-CMIP/CMIP6Plus_CVs <https://github.com/WCRP-CMIP/CMIP6Plus_CVs>`_;
    ``fremor init`` downloads it into the fetched ``Tables/`` directory alongside
    the MIP tables.

  One rough edge remains upstream: ``Auxillary_files/MIP_grids.json`` ships without a
  ``Header``, so CMOR cannot select it as a table. Tripolar ocean components therefore
  need a grids table that declares one — copy ``CMIP6_grids.json`` from
  `pcmdi/cmip6-cmor-tables <https://github.com/pcmdi/cmip6-cmor-tables>`_ next to your
  MIP tables and ``fremor`` will pick it up. Rectilinear output is unaffected.

* **CMIP7** — The next-generation CMIP Phase 7 tables from
  `WCRP-CMIP/cmip7-cmor-tables <https://github.com/WCRP-CMIP/cmip7-cmor-tables>`_.
  CMIP7 uses a distinct experiment configuration template with fields tailored to the
  evolving CMIP7 data request.

External configuration
~~~~~~~~~~~~~~~~~~~~~~

``fremor`` relies on external CMIP metadata:

* MIP tables (for example the `cmip6-cmor-tables <https://github.com/pcmdi/cmip6-cmor-tables>`_)
* Controlled vocabularies (for example `CMIP6_CVs <https://github.com/WCRP-CMIP/CMIP6_CVs>`_)

Required user inputs
~~~~~~~~~~~~~~~~~~~~

* **Variable list** (JSON) that maps local variable names to MIP names. A small
  example lives in the repository at
  ``fremor/tests/test_files/CMORbite_var_list.json``.
* **Experiment configuration** (JSON) supplying metadata such as ``calendar``,
  ``grid``, and the desired output directory structure. The required fields are
  determined by the target MIP's controlled vocabularies — see
  `CMIP7 required global attributes <https://github.com/WCRP-CMIP/cmip7-cmor-tables/blob/main/tables-cvs/split-view/required_global_attributes.json>`_
  for an example of how a MIP specifies its required metadata. Reference
  example experiment configuration files for each MIP era:

  * **CMIP6**: `CMIP6_input_example.json <https://github.com/PCMDI/cmip6-cmor-tables/blob/main/Tables/CMIP6_input_example.json>`_
  * **CMIP6Plus**: `my_input.json <https://github.com/PCMDI/mip-cmor-tables/blob/main/src/exploration/old/my_input.json>`_
  * **CMIP7**: `cmor_test.py (lines 9–41) <https://github.com/WCRP-CMIP/cmip7-cmor-tables/blob/main/scripts/cmor_test.py#L9-L41>`_
    and `CMOR_input_example.json (PCMDI/cmor) <https://github.com/PCMDI/cmor/blob/9d82dfb7c091cd0e0366fffd8a50f4d17f85f4a6/Test/CMOR_input_example.json>`_

  Use ``fremor init -m <mip_era> -e exp_config.json`` to generate a template
  pre-populated with the correct fields for your target MIP era.
* **Optional CMOR YAML** if you want to batch multiple ``run`` calls via
  ``fremor yaml``. These YAML files are part of the larger FRE workflow and are
  not shipped here; point ``-y/--yamlfile`` at your project-specific YAMLs.

Subcommands and Examples
------------------------

The entry point to all subcommands is ``fremor``:

.. code-block:: bash

   fremor --help

``init``
~~~~~~~~

Initialize CMOR resources by generating experiment configuration templates and/or
fetching MIP tables from trusted sources.

.. code-block:: bash

   # Generate config template and fetch tables
   fremor init -m cmip6 -e exp_config.json -t cmip6-tables

   # CMIP6Plus: use the unified mip-cmor-tables repository
   fremor init -m cmip6plus -e exp_config.json -t mip-cmor-tables --fast

   # Use fast mode (tarball download instead of git clone)
   fremor init -m cmip7 -e exp_config.json -t cmip7-tables --fast

   # Fetch a specific release tag
   fremor init -m cmip6 -t cmip6-tables --tag 6.9.33

``run``
~~~~~~~

Rewrite NetCDF files in a directory using a specific MIP table and experiment
configuration.

.. code-block:: bash

   fremor run --run_one --grid_label gr --grid_desc FOO_BAR_PLACEHOLD --nom_res "10000 km" \
       -d /path/to/input/netcdf/dir \
       -l fremor/tests/test_files/CMORbite_var_list.json \
       -r fremor/tests/test_files/cmip6-cmor-tables/Tables/CMIP6_Omon.json \
       -p /path/to/CMOR_input_example.json \
       -o /tmp/cmorized_output

``yaml``
~~~~~~~~

Process multiple directories/tables using a self-contained CMOR YAML file.

.. code-block:: bash

   fremor -v yaml --run_one --dry_run \
       --yamlfile /path/to/cmor.yaml

``stage``
~~~~~~~~~

Recall all mapped input files from the GFDL archive before running CMORization.
Use ``--dry_run`` first to review the deduplicated file selection. YAML
``start`` and ``stop`` bounds are honored and can be overridden with
``--start YYYY`` and ``--stop YYYY``.

.. code-block:: bash

   fremor stage --yamlfile /path/to/cmor.yaml --dry_run
   fremor stage --yamlfile /path/to/cmor.yaml

``check``
~~~~~~~~~

Audit the mappings in a self-contained CMOR YAML before running. The report
identifies missing, duplicate, and unknown mappings. Add ``--staging`` to check
that mapped archive inputs are available, and ``--dims`` to compare a
representative input file's vertical dimension with its MIP-table definition.

.. code-block:: bash

   fremor check --yamlfile /path/to/cmor.yaml --staging --dims
   fremor check --yamlfile /path/to/cmor.yaml Amon --show_mapped

``map``
~~~~~~~

Use the interactive mapping UI to correct the issues reported by ``check``.
Select a MIP variable to inspect its table definition, select a post-processing
file to preview it, press ``m`` to stage a mapping or ``d`` to stage its
removal, then press ``s`` to save all staged edits. The variable-list files are
unchanged until you save.

.. code-block:: bash

   fremor map --yamlfile /path/to/cmor.yaml Amon

``resolve``
~~~~~~~~~~~

Resolve a FRE model YAML into the combined YAML document, expanding anchors
and merge keys from the model and grids files into the CMOR section.

.. code-block:: bash

   fremor resolve \
       --yamlfile /path/to/model.yaml \
       --experiment c96L65_am5f7b12r1_amip \
       --output resolved.yaml

``find``
~~~~~~~~

Search MIP tables for variable definitions.

.. code-block:: bash

   fremor -v find --table_config_dir fremor/tests/test_files/cmip6-cmor-tables/Tables/ \
       --opt_var_name sos

``varlist``
~~~~~~~~~~~

Generate a variable list from a directory of NetCDF files. Optionally filter
against a MIP table.

.. code-block:: bash

   fremor varlist --dir_targ /path/to/data_dir \
       --output_variable_list simple_varlist.json \
       --mip_table fremor/tests/test_files/cmip6-cmor-tables/Tables/CMIP6_Omon.json

``config``
~~~~~~~~~~

Scan a post-processing directory tree and emit the CMOR YAML plus per-component
variable lists that ``fremor yaml`` consumes.

.. code-block:: bash

   fremor config --pp_dir /path/to/pp \
       --mip_tables_dir /path/to/cmip7-cmor-tables/tables \
       --mip_era cmip7 \
       --exp_config /path/to/CMOR_CMIP7_input_example.json \
       --output_yaml cmor_config.yaml \
       --output_dir /path/to/cmor_output \
       --varlist_dir /path/to/varlists \
       --freq monthly --chunk 5yr --grid gn \
       --calendar noleap

In ``--dry_run`` mode, ``fremor yaml`` prints the generated ``fremor run`` calls
without executing them. Pass ``--no-print_cli_call`` to print the equivalent
Python ``cmor_run_subtool(...)`` invocation instead of the CLI command.
