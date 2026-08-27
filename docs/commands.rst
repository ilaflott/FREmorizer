.. _commands:

=====================
Subcommands Reference
=====================

``fremor`` rewrites climate model output files with CMIP-compliant metadata. CMIP6, CMIP6Plus, and CMIP7
workflows are supported. Available subcommands:

* ``fremor init`` — Initialize CMOR resources: generate config templates and fetch MIP tables
* ``fremor run`` — Rewrite individual directories of netCDF files
* ``fremor yaml`` — Process multiple directories/tables using YAML configuration
* ``fremor stage`` — Recall all mapped YAML-run inputs with one batched ``dmget`` call
* ``fremor resolve`` — Combine model + grids + cmor YAMLs into one resolved document for inspection
* ``fremor find`` — Search MIP tables for variable definitions
* ``fremor varlist`` — Generate variable lists from netCDF files
* ``fremor config`` — Generate a CMOR YAML configuration from a post-processing directory tree
* ``fremor check`` — Check variable-mapping coverage of varlist files against MIP tables
* ``fremor map`` — Interactive TUI to review and edit variable-mapping varlist files

``init``
--------

* Initializes CMOR resources by generating experiment configuration templates and/or fetching MIP tables
* Fetches tables from trusted GitHub repositories (CMIP6: ``PCMDI/cmip6-cmor-tables``, CMIP6Plus: ``PCMDI/mip-cmor-tables``, CMIP7: ``WCRP-CMIP/cmip7-cmor-tables``)
* Minimal Syntax: ``fremor init -m [mip_era] [options]``
* Required Options:
   - ``-m, --mip_era TEXT`` — MIP era: ``cmip6``, ``cmip6plus``, or ``cmip7``
* Optional:
   - ``-e, --exp_config TEXT`` — Output path for experiment config JSON template
   - ``-t, --tables_dir TEXT`` — Directory to fetch MIP tables into
   - ``--tag TEXT`` — Specific git tag or release for MIP tables (e.g., ``6.9.33``)
   - ``--fast`` — Use curl to download tarball instead of git clone (faster)
* Examples:
   - ``fremor init -m cmip6 -e exp_config.json -t cmip6-tables``
   - ``fremor init -m cmip6plus -e exp_config.json -t mip-cmor-tables --fast``
   - ``fremor init -m cmip7 -e exp_config.json -t cmip7-tables --fast``
   - ``fremor init -m cmip6 -t cmip6-tables --tag 6.9.33``

``run``
-------

* Rewrites netCDF files in a directory to be CMIP-compliant
* Requires MIP tables and controlled vocabulary configuration
* Minimal Syntax: ``fremor run -d [indir] -l [varlist] -r [table_config] -p [exp_config] -o [outdir] [options]``
* Required Options:
   - ``-d, --indir TEXT`` — Input directory with netCDF files
   - ``-l, --varlist TEXT`` — Variable list dictionary mapping modeler variable names to MIP table variable names
   - ``-r, --table_config TEXT`` — MIP table JSON configuration
   - ``-p, --exp_config TEXT`` — Experiment/model metadata JSON
   - ``-o, --outdir TEXT`` — Output directory prefix
* Optional:
   - ``-v, --opt_var_name TEXT`` — Target specific variable
   - ``--run_one`` — Process one file for testing
   - ``-g, --grid_label TEXT`` — Grid type (e.g. ``gn``, ``gr``)
   - ``--grid_desc TEXT`` — Grid description
   - ``--nom_res TEXT`` — Nominal resolution
   - ``--start TEXT`` — Minimum year (``YYYY``)
   - ``--stop TEXT`` — Maximum year (``YYYY``)
   - ``--calendar TEXT`` — Calendar type
* Example: ``fremor run --run_one -g gr --nom_res "10000 km" -d input/ -l varlist.json -r CMIP6_Omon.json -p exp_config.json -o output/``

``yaml``
--------

* Processes YAML configuration to CMORize multiple directories/tables
* Expects a self-contained CMOR YAML file
* A table_target with ``disabled: true`` is skipped entirely (logged, not processed) — toggle it from ``fremor map``'s MIP-table tree, or hand-edit the yaml
* Minimal Syntax: ``fremor yaml -y [yamlfile] [options]``
* Required Options:
   - ``-y, --yamlfile TEXT`` — YAML file to parse
* Optional:
   - ``--run_strict`` — Exit immediately when a ``fremor run`` call raises an exception, rather than logging a warning and continuing to the next component
   - ``--run_one`` — Process one file for testing
   - ``--dry_run`` — Print planned calls without executing
   - ``--print_cli_call/--no-print_cli_call`` — In dry-run mode, print the equivalent CLI invocation (default) or the Python ``cmor_run_subtool()`` call
   - ``--start TEXT`` — Minimum year (YYYY)
   - ``--stop TEXT`` — Maximum year (YYYY)
* Example: ``fremor yaml -y cmor.yaml --dry_run``

``stage``
---------

* Discovers the mapped NetCDF inputs selected by a self-contained CMOR YAML file and submits them in one ``dmget`` invocation
* Deduplicates files referenced by multiple table targets and includes existing same-date ``ps`` auxiliary files
* Uses the YAML ``start``/``stop`` bounds unless they are overridden on the command line
* Minimal Syntax: ``fremor stage -y [yamlfile] [options]``
* Required Options:
   - ``-y, --yamlfile TEXT`` — Self-contained CMOR YAML file
* Optional:
   - ``--start TEXT`` — Override the minimum year (YYYY)
   - ``--stop TEXT`` — Override the maximum year (YYYY)
   - ``--dmget_bin TEXT`` — Executable name or path (default: ``dmget``)
   - ``--dry_run`` — Print selected paths without invoking ``dmget``
* Examples:
   - ``fremor stage -y cmor.yaml``
   - ``fremor stage -y cmor.yaml --start 2000 --stop 2014 --dry_run``

``resolve``
-----------

* Resolves a FRE model YAML plus referenced CMOR/grids YAML files into one combined YAML document
* Useful for inspecting how anchors and merge keys from the model and grids files expand into the CMOR section
* Minimal Syntax: ``fremor resolve -y [model_yaml] -e [experiment] [options]``
* Required Options:
   - ``-y, --yamlfile TEXT`` — Model YAML file to resolve
   - ``-e, --experiment TEXT`` — Experiment name
* Optional:
   - ``-o, --output TEXT`` — Write the resolved YAML to a file instead of stdout
* Example: ``fremor resolve -y am5.yaml -e c96L65_am5f7b12r1_amip --output resolved.yaml``

``find``
--------

* Searches MIP tables for variable definitions
* Minimal Syntax: ``fremor find -r [table_config_dir] [options]``
* Required Options:
   - ``-r, --table_config_dir TEXT`` — Directory with MIP tables
* Optional:
   - ``-l, --varlist TEXT`` — Variable list file
   - ``-v, --opt_var_name TEXT`` — Specific variable to search
* Example: ``fremor find -r cmip6-cmor-tables/Tables/ -v sos``

``varlist``
-----------

* Generates variable list from netCDF files in a directory
* Scans filenames (``component.YYYYMMDD.variable.nc``) and extracts variable names; deduplicates across datetimes
* When a MIP table is provided, variables that match a MIP entry are self-mapped (key == value); variables not found in the table receive an empty-string value signalling that manual mapping is required
* Variable name matching is case-insensitive (e.g., ``LWP`` matches ``lwp`` in the MIP table)
* Minimal Syntax: ``fremor varlist -d [dir_targ] -o [output_file]``
* Required Options:
   - ``-d, --dir_targ TEXT`` — Target directory
   - ``-o, --output_variable_list TEXT`` — Output file path
* Optional:
   - ``-t, --mip_table TEXT`` — MIP table JSON file to cross-reference variables against
   - ``--strict_mode`` — If a MIP table is provided and none of the found variable names match any MIP entry, do not write the output file (return nothing instead of a file full of empty-value entries)
* Examples:
   - ``fremor varlist -d ocean_data/ -o varlist.json``
   - ``fremor varlist -d ocean_data/ -t CMIP6_Omon.json -o varlist.json``
   - ``fremor varlist -d ocean_data/ -t CMIP6_Omon.json --strict_mode -o varlist.json``

``config``
----------

* Generates a CMOR YAML configuration file by scanning a post-processing directory tree and cross-referencing against MIP tables
* Creates per-component variable list JSON files and the structured YAML that ``fremor yaml`` consumes
* Minimal Syntax: ``fremor config -p [pp_dir] -t [mip_tables_dir] -m [mip_era] -e [exp_config] -o [output_yaml] -d [output_dir] -l [varlist_dir]``
* Required Options:
   - ``-p, --pp_dir TEXT`` — Root post-processing directory
   - ``-t, --mip_tables_dir TEXT`` — Directory containing MIP table JSON files
   - ``-m, --mip_era TEXT`` — MIP era identifier (e.g. ``cmip6``, ``cmip7``)
   - ``-e, --exp_config TEXT`` — Path to experiment configuration JSON
   - ``-o, --output_yaml TEXT`` — Path for the output CMOR YAML file
   - ``-d, --output_dir TEXT`` — Root output directory for CMORized data
   - ``-l, --varlist_dir TEXT`` — Directory for per-component variable list files
* Optional:
   - ``-g, --pp_comp_glob TEXT`` — Glob pattern for selecting pp component directory names (default: ``*``)
   - ``--strict_varlist`` — When generating per-component variable lists, apply ``--strict_mode``: if none of a component's variables match any MIP entry, skip that component entirely (no varlist file, no entry in the generated YAML)
   - ``--freq TEXT`` — Temporal frequency (default: ``monthly``)
   - ``--chunk TEXT`` — Time chunk string (default: ``5yr``)
   - ``--grid TEXT`` — Grid label anchor name (default: ``g999``; the previous documentation incorrectly listed ``g99``)
   - ``--overwrite`` — Overwrite existing variable list files
   - ``--calendar TEXT`` — Calendar type (default: ``noleap``)
* Example: ``fremor config -p /path/to/pp -t /path/to/tables -m cmip7 -e exp_config.json -o cmor.yaml -d /path/to/output -l /path/to/varlists``

``check``
---------

* Cross-references per-component varlist files against MIP table JSON files and reports, per MIP table: variables required by the table but not mapped from any component (unmapped), variables mapped from more than one component/diagnostic (multiply-mapped), and mapped values that don't correspond to any variable actually defined in that table (unknown / likely typos)
* pp_dir, the MIP tables directory, the MIP era, and each component's variable list path are all derived from ``yamlfile``, the self-contained CMOR YAML written by ``fremor config`` — no separate directory/era flags are needed
* Minimal Syntax: ``fremor check -y [yamlfile] [TABLES...]``
* Required Options:
   - ``-y, --yamlfile TEXT`` — Self-contained CMOR YAML file, as written by ``fremor config``
* Optional:
   - ``TABLES`` — MIP table names to check, e.g. ``Amon``; shell-style wildcards supported (e.g. ``AER*``); defaults to every table in yamlfile's table_targets
   - ``--show_mapped`` — Also report variables mapped from exactly one component/diagnostic (one-to-one)
   - ``--staging`` — For each one-to-one mapping, check whether its selected input files exist, appear disk-resident, and have gaps between date chunks; uses ``dmls`` when available and otherwise a stat-only heuristic
   - ``--dims`` — For each one-to-one mapping, compare a representative input file's vertical dimension with the MIP-table definition and check for required hybrid-sigma ``ps`` companion files
   - ``--dmls_bin TEXT`` — Path to the ``dmls`` executable used by ``--staging``; defaults to searching ``PATH``
   - ``--json`` — Print the report as JSON instead of a text summary
   - ``-o, --output_report TEXT`` — Optional path to also write the JSON report to
* Examples:
   - ``fremor check -y cmor.yaml --show_mapped``
   - ``fremor check -y cmor.yaml Amon --staging --dims``

``map``
-------

* Opens an interactive terminal UI to review and edit variable-mapping varlist files
* Shows each selected MIP table as a tree of variables alongside their mapping status (unmapped / mapped / multiply-mapped / unknown), and lets you browse time-series files under ``pp_dir`` to assign or fix a mapping
* Selecting a MIP variable shows its own MIP-table definition (such as long name, units, dimensions, and cell methods); CMIP7 variables with multiple brands show each matching definition
* A box above the pp browser always shows the currently-selected CMIP variable (and its current source, if reassigning an existing mapping)
* Press ``m`` to stage mapping the selected pp file to the selected CMIP variable, ``d`` to stage clearing a selected existing mapping, ``t`` to stage toggling the ``disabled`` flag of the MIP table currently in context (select the table node itself, or any of its variables), ``s`` to save all staged changes to disk, ``r`` to refresh the tree (re-categorizing it from current, possibly-unsaved, state), ``q`` to quit
* Staged-but-unsaved edits are marked in place instead of triggering a full tree rebuild, so expanded branches stay expanded while you batch edits across many variables: a newly (re)mapped variable shows ``<- component:local_key`` pointing at its new source, a cleared mapping is struck through and labeled ``(deleted)``, and a disabled table's node is labeled ``(disabled)``; nothing is written to disk until you press ``s`` — for a disabled toggle, that rewrites the whole yamlfile (so hand-added comments/formatting there won't survive a save that includes one)
* If there are unsaved staged changes, ``q`` warns first instead of quitting immediately; press ``q`` again to quit anyway and discard them, or ``s`` to save first
* File previews use the ``ncinfo`` tool if it's found on PATH (or via ``--ncinfo_bin``), falling back to a plain netCDF4-based preview otherwise; previews load in a background thread (showing a loading message while they do) so the UI stays responsive, and switching to another file before a preview finishes discards the outdated result once it arrives
* pp_dir, the MIP tables directory, the MIP era, and each component's variable list path are all derived from ``yamlfile``, the self-contained CMOR YAML written by ``fremor config`` — mapping edits are saved straight back into the variable list files referenced there
* Minimal Syntax: ``fremor map -y [yamlfile] [TABLES...]``
* Required Options:
   - ``-y, --yamlfile TEXT`` — Self-contained CMOR YAML file, as written by ``fremor config``
* Optional:
   - ``TABLES`` — MIP table names to load, e.g. ``Amon``; shell-style wildcards supported (e.g. ``AER*``); defaults to every table in yamlfile's table_targets
   - ``--ncinfo_bin TEXT`` — Path to the ``ncinfo`` binary for richer previews
* Example: ``fremor map -y cmor.yaml Amon``
