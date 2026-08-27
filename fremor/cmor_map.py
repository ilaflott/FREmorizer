"""
``fremor map``: Interactive Varlist Mapping Editor
===================================================

This module powers the ``fremor map`` command: an interactive terminal UI (built with
Textual) for reviewing and editing the per-component variable list files (as written by
``fremor config`` / ``fremor varlist``) against the authoritative MIP table JSON files
fetched by ``fremor init``.

Like ``fremor check``, it reads a self-contained CMOR YAML file (as written by ``fremor
config``) to derive pp_dir, the MIP tables directory, the MIP era, and each component's
variable list path -- no separate pp_dir/varlist_dir/mip_tables_dir/mip_era flags are
needed. It reuses the coverage analysis from ``fremor check`` (``cmor_check.py``) to show
each selected MIP table as a tree of variables grouped by mapping status (unmapped / mapped
/ multiply-mapped / unknown), lets the user select a variable, browse time-series NetCDF
files under pp_dir to find the right one, and stage a mapping edit. Selecting a variable also
fills a detail box under the tree with that variable's own MIP table definition (long_name,
units, cell_methods, etc.), straight from the table JSON and independent of its mapping
status.

Selecting a mapped CMIP variable in the tree -- whether a one-to-one mapping or one
particular source of a multiply-mapped variable, both of which point at a single
component/local_key pp file -- automatically navigates the pp-directory browser to that
file, same as if the user had browsed there by hand. The pp-file detail box also lists every
currently-loaded MIP table variable mapped to the selected pp file, alongside its preview.

Mapping/clearing a variable only stages the change in memory -- nothing is written to disk
until the user explicitly saves, so the tree doesn't collapse/re-categorize after every
single edit while batching changes across many variables. The affected node is marked in
place to show what's staged: a newly (re)mapped variable shows ``<- component:local_key``
pointing at its new source, and a cleared mapping is struck through and labeled
``(deleted)``. Only the explicit save writes the affected varlist JSON files to disk.

Staged edits can also be walked back: 'u' undoes the single most recent staged edit, and
'R' discards every staged edit at once, restoring the whole session back to its state as of
the last save (or the initial load, if nothing has been saved yet).

Before previewing a selected pp file, its ``dmls -l`` status (or via ``--dmls_bin``) is
checked to confirm it has actually been retrieved from tape ('REG' disk-resident, or 'DUL'
disk-resident and also copied to tape) -- any other status shows a "still on tape" message
instead of a preview, since opening/inspecting an offline file would otherwise silently
trigger (or block on) a tape retrieval. If dmls isn't available, falls back to a stat-only
residency heuristic (same one ``fremor check --staging`` uses).

File previews in the pp-directory browser prefer the user's ``ncinfo`` tool (an external,
non-Python CLI) when available on PATH or via ``--ncinfo_bin``, falling back to a plain
``netCDF4``-based attribute dump otherwise. Since either can be slow (a subprocess call, or
reading a file over a network filesystem), previews run in a background thread -- selecting
a file shows a loading message immediately and the UI stays responsive while it loads.
Selecting another file before a preview finishes discards that in-flight result once it
eventually arrives, so only the most recently selected file's preview is ever shown.

Functions
---------
- ``cmor_map_subtool(...)``
"""

import glob
import json
import logging
import os
import shutil
import subprocess
from collections import defaultdict, namedtuple
from pathlib import Path
from typing import Optional, Sequence

import yaml
from netCDF4 import Dataset
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static, Tree

from .cmor_check import _build_table_report, _dmls_state_for_file, _find_dmls_bin, \
    _is_file_staged, _matching_variable_keys, _mip_table_paths, _select_table_names, \
    _varlists_by_table_from_yaml
from .cmor_config import _load_config_yaml
from .cmor_helpers import get_json_file_data

fre_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# pp-directory discovery helpers
# ---------------------------------------------------------------------------

def _discover_pp_components(pp_dir: str, pp_comp_glob: str = '*') -> list:
    """Top-level pp component directories, mirroring cmor_config_subtool's ppcompdirs glob."""
    return sorted(p for p in glob.glob(f'{pp_dir}/{pp_comp_glob}') if Path(p).is_dir())


def _discover_freqs(component_dir: str) -> list:
    """Subdirectories of <component_dir>/ts/ (empty list if there's no ts/ dir)."""
    ts_dir = Path(component_dir) / 'ts'
    if not ts_dir.is_dir():
        return []
    return sorted(p.name for p in ts_dir.iterdir() if p.is_dir())


def _discover_chunks(component_dir: str, freq: str) -> list:
    """Subdirectories of <component_dir>/ts/<freq>/."""
    freq_dir = Path(component_dir) / 'ts' / freq
    if not freq_dir.is_dir():
        return []
    return sorted(p.name for p in freq_dir.iterdir() if p.is_dir())


def _discover_nc_files(component_dir: str, freq: str, chunk: str) -> list:
    """NetCDF files under <component_dir>/ts/<freq>/<chunk>/."""
    return sorted(glob.glob(f'{component_dir}/ts/{freq}/{chunk}/*.*.nc'))


def _local_var_name_from_nc_path(nc_path: str) -> str:
    """Extract the local variable name from a filename, same convention as make_simple_varlist:
    <something>.<datetime>.<variable>.nc -- variable is the second-to-last dot-delimited field."""
    return Path(nc_path).name.split('.')[-2]


# ---------------------------------------------------------------------------
# NetCDF preview helpers: ncinfo (external tool, best-effort) + netCDF4 fallback
# ---------------------------------------------------------------------------

def _inspect_nc_variable(nc_path: str, var_name: str) -> Optional[dict]:
    """Best-effort netCDF4-based preview of a single variable's attributes/dims/shape.
    Never raises -- returns None on any failure, since this is purely a UI nicety."""
    try:
        with Dataset(nc_path, 'r') as dataset:
            if var_name not in dataset.variables:
                return None
            variable = dataset.variables[var_name]
            attrs = {attr: variable.getncattr(attr) for attr in variable.ncattrs()}
            attrs['dimensions'] = list(variable.dimensions)
            attrs['shape'] = list(variable.shape)
            return attrs
    except Exception:  # pylint: disable=broad-except
        fre_logger.debug('could not inspect %s in %s', var_name, nc_path, exc_info=True)
        return None


def _find_ncinfo_bin(ncinfo_bin: Optional[str] = None) -> Optional[str]:
    """Resolve the ncinfo binary: explicit path if given, else look it up on PATH."""
    if ncinfo_bin:
        return ncinfo_bin
    return shutil.which('ncinfo')


def _ncinfo_preview(nc_path: str, var_name: str, ncinfo_bin: Optional[str] = None) -> Optional[dict]:
    """Best-effort richer preview via the user's ncinfo tool (a separate Go CLI, not a Python
    package -- invoked as an external binary if present, never a hard dependency). Returns the
    matching entry from ncinfo's ``variables`` list, or None if the binary isn't available, the
    call fails, or the variable isn't present in ncinfo's output."""
    binary = _find_ncinfo_bin(ncinfo_bin)
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, '--json', nc_path],
            capture_output=True, text=True, timeout=10, check=True
        )
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        fre_logger.debug('ncinfo preview failed for %s: %s', nc_path, exc)
        return None

    for variable in data.get('variables', []) or []:
        if variable.get('name') == var_name:
            return variable
    return None


_DMLS_DISK_RESIDENT_STATES = {'REG', 'DUL'}


def _format_tape_message(local_var_name: str, nc_path: str, dmls_state: Optional[str]) -> str:
    """Message shown in place of a preview when a pp file hasn't been retrieved from tape."""
    status = f' (dmls status: {dmls_state})' if dmls_state else ''
    return (f'"{local_var_name}" in {Path(nc_path).name} is still on tape -- '
            f'not yet retrieved to disk{status}')


def _preview_nc_file(nc_path: str, var_name: str, ncinfo_bin: Optional[str] = None):
    """Try ncinfo first, then fall back to netCDF4. Returns (source, data) where source is
    'ncinfo', 'netcdf4', or 'none'."""
    ncinfo_data = _ncinfo_preview(nc_path, var_name, ncinfo_bin)
    if ncinfo_data is not None:
        return 'ncinfo', ncinfo_data
    netcdf4_data = _inspect_nc_variable(nc_path, var_name)
    if netcdf4_data is not None:
        return 'netcdf4', netcdf4_data
    return 'none', {}


def _format_preview_text(local_var_name: str, source: str, data: dict) -> str:
    """Render a (source, data) preview pair from _preview_nc_file into display text."""
    if source == 'none':
        return f'no preview available for variable "{local_var_name}"'

    lines = [f'[{source}] {local_var_name}']
    if source == 'ncinfo':
        dims = ','.join(data.get('dims') or [])
        lines.append(f'type={data.get("type")} dims=({dims})')
        for attr in data.get('attributes') or []:
            lines.append(f'  {attr.get("name")}: {attr.get("value")}')
    else:
        dims = data.get('dimensions')
        shape = data.get('shape')
        if dims is not None:
            lines.append(f'dims={",".join(dims)} shape={shape}')
        for key, val in data.items():
            if key in ('dimensions', 'shape'):
                continue
            lines.append(f'  {key}: {val}')
    return '\n'.join(lines)


def _format_mapped_variables(mapped: list) -> str:
    """Render a MapSession.mapped_variables() result for the pp-file detail box."""
    if not mapped:
        return 'not mapped to any MIP variable in the currently loaded tables'
    lines = [f'  {table_name} / {cmip_var}' for table_name, cmip_var in mapped]
    return 'mapped MIP variables:\n' + '\n'.join(lines)


# ---------------------------------------------------------------------------
# MIP table variable_entry lookup, for the cmip-tree detail box
# ---------------------------------------------------------------------------

# variable_entry attributes shown in the detail box, in display order. Not every table
# variable has every attribute -- absent ones are simply skipped.
_VARIABLE_DETAIL_ATTRS = (
    'long_name', 'standard_name', 'units', 'cell_methods', 'cell_measures', 'dimensions',
    'out_name', 'type', 'positive', 'frequency', 'modeling_realm', 'comment',
)


def _format_variable_detail(var: str, definitions: dict) -> str:
    """Render a MapSession.variable_definitions() result for the cmip-tree detail box. For a
    CMIP7 variable with more than one brand, each brand's variable_entry key is shown as its
    own labeled section since their attributes (e.g. cell_methods) can differ."""
    if not definitions:
        return f'no MIP table definition found for "{var}"'

    multi = len(definitions) > 1
    sections = []
    for key, entry in definitions.items():
        lines = [f'[{key}]'] if multi else []
        for attr in _VARIABLE_DETAIL_ATTRS:
            value = entry.get(attr)
            if value not in (None, ''):
                lines.append(f'{attr}: {value}')
        sections.append('\n'.join(lines))
    return '\n\n'.join(sections)


# ---------------------------------------------------------------------------
# mapping-source lookup (unknown-mapped values need their sources too, unlike
# cmor_check's report which only keeps a flat list of the bad values)
# ---------------------------------------------------------------------------

def _mapped_sources(table_name: str, varlists_by_table: dict) -> dict:
    """component/local_key sources for every mapped value found in this table's varlists,
    keyed by the mapped value -- whether or not it's actually a real table variable."""
    mapped = defaultdict(list)
    for component, _path, data in varlists_by_table.get(table_name, []):
        for gfdl_key, cmip_var in data.items():
            if cmip_var:
                mapped[cmip_var].append((component, gfdl_key))
    return mapped


def _infer_varlist_dir(table_targets: Sequence[dict]) -> Optional[str]:
    """
    Directory shared by every variable_list path in table_targets -- used when creating a
    brand-new varlist for a (table, component) pair that has no existing entry in the yaml.
    ``fremor config`` always writes every component's varlist into one shared varlist_dir, so
    the first variable_list path found is representative of all of them.
    """
    for table_target in table_targets:
        for comp in table_target.get('target_components') or []:
            return str(Path(os.path.expandvars(comp['variable_list'])).parent)
    return None


# ---------------------------------------------------------------------------
# session: data model + mutation, no UI code
# ---------------------------------------------------------------------------

_UNSET = object()  # sentinel: local_key had no entry at all in a varlist's data dict

_Edit = namedtuple('_Edit', ['table_name', 'component_name', 'local_key', 'old_value'])


def _snapshot_varlists(varlists_by_table: dict) -> dict:
    """Copy a varlists_by_table structure deeply enough (each component's data dict is
    copied) that later in-place edits to the original can't leak into the snapshot."""
    return {
        table_name: [(component, path, dict(data)) for component, path, data in entries]
        for table_name, entries in varlists_by_table.items()
    }


class MapSession:
    """Loads MIP tables + varlists for a `fremor map` session, computes per-table mapping
    status reports, and stages mapping edits in memory until ``save_pending`` is called --
    edits are visible immediately (e.g. in ``table_report``) but not written to disk until
    then, so the caller can batch many edits into one explicit save. Each staged edit is
    also pushed onto an undo history (``undo()``), and the whole batch can be discarded at
    once back to the last save (``restore_pending()``).

    A table_target's ``disabled`` flag (toggled via ``toggle_disabled()``) lives in the cmor
    yaml itself rather than in a varlist JSON file, so it's staged and saved through a
    separate ``disabled_dirty``/``_baseline_disabled`` pair instead of ``dirty_keys`` --
    but ``has_pending_changes``, ``save_pending()``, and ``restore_pending()`` all account
    for both kinds of staged edit together."""

    def __init__(self, yamlfile: str, table_patterns: Sequence[str] = (),
                 ncinfo_bin: Optional[str] = None, dmls_bin: Optional[str] = None):
        cmor_yaml_ctx = _load_config_yaml(yamlfile)
        table_targets = cmor_yaml_ctx['table_targets']

        all_table_names = sorted({table_target['table_name'] for table_target in table_targets})
        if not all_table_names:
            raise ValueError(f'no table_targets found in {yamlfile}')
        table_names = _select_table_names(all_table_names, table_patterns)
        if not table_names:
            raise ValueError(
                f'no table_targets in {yamlfile} matched table_patterns {list(table_patterns)}')

        self.pp_dir = cmor_yaml_ctx['pp_dir']
        self.mip_era = cmor_yaml_ctx['mip_era']
        self.era_upper = self.mip_era.upper()
        self.ncinfo_bin = _find_ncinfo_bin(ncinfo_bin)
        self.dmls_bin = _find_dmls_bin(dmls_bin)

        self.table_paths = _mip_table_paths(cmor_yaml_ctx['mip_tables_dir'], self.mip_era,
                                            table_names)
        self.varlists_by_table = _varlists_by_table_from_yaml(table_targets)
        self.varlist_dir = _infer_varlist_dir(table_targets)
        self.dirty_keys = set()  # {(table_name, component_name, local_key), ...} -- unsaved
        self._baseline = _snapshot_varlists(self.varlists_by_table)  # state as of last save
        self._history = []  # [_Edit, ...] most-recent last -- undo() pops from the end

        # disabled-flag staging: table_targets_by_name holds live references into
        # cmor_yaml_ctx['yaml_doc'], so toggling a flag in place is immediately visible
        # everywhere (e.g. table_report / is_disabled) without a re-read, and save_pending
        # simply re-serializes yaml_doc to pick up every staged toggle at once.
        self._yamlfile = yamlfile
        self._yaml_doc = cmor_yaml_ctx['yaml_doc']
        self.table_targets_by_name = {
            table_target['table_name']: table_target for table_target in table_targets
        }
        self._baseline_disabled = {
            name: bool(table_target.get('disabled'))
            for name, table_target in self.table_targets_by_name.items()
        }
        self.disabled_dirty = set()  # {table_name, ...} -- staged disabled-flag toggles

    @property
    def table_names(self) -> list:
        """MIP table names loaded into this session, sorted."""
        return sorted(self.table_paths)

    def table_report(self, table_name: str) -> dict:
        """Mapping-status report for one table, always including one-to-one mappings and
        source info for unknown-mapped values (fremor check's report only keeps the bad
        values themselves, not where they came from)."""
        table_path = self.table_paths[table_name]
        report = _build_table_report(table_path, self.mip_era, self.varlists_by_table,
                                     show_mapped=True)
        report.pop('table_name', None)
        mapped_sources = _mapped_sources(table_name, self.varlists_by_table)
        report['unknown_sources'] = {
            val: mapped_sources.get(val, []) for val in report['unknown_mapped']
        }
        return report

    def variable_definitions(self, table_name: str, var: str) -> dict:
        """This MIP table's own variable_entry key -> attributes dict for every entry
        matching bare variable name `var` (more than one for a CMIP7 branded variable) --
        e.g. long_name, units, cell_methods -- independent of mapping status. Used for the
        cmip-tree detail box."""
        table_data = get_json_file_data(self.table_paths[table_name])
        variable_entry = table_data.get('variable_entry', {})
        keys = _matching_variable_keys(variable_entry, var, self.mip_era)
        return {key: variable_entry[key] for key in keys}

    def set_mapping(self, table_name: str, component_name: str, local_key: str,
                    cmip_var: str) -> None:
        """Stage data[local_key] = cmip_var for the component's variable_list, creating an
        in-memory entry (placed in the varlist directory inferred from the yaml's existing
        variable_list entries) if this component/table pair had no varlist yet in the yaml.
        The change is visible immediately (e.g. in table_report) but not written to disk
        until save_pending() is called."""
        entries = self.varlists_by_table.setdefault(table_name, [])
        for component, path, data in entries:
            if component == component_name:
                old_value = data.get(local_key, _UNSET)
                data[local_key] = cmip_var
                break
        else:
            if self.varlist_dir is None:
                raise ValueError(
                    f'cannot create a new varlist for component {component_name!r} in table '
                    f'{table_name!r}: no existing variable_list entries in the yaml to infer '
                    'a varlist directory from')
            fname = f'{self.era_upper}_{table_name}_{component_name}.list'
            path = f'{self.varlist_dir}/{fname}'
            data = get_json_file_data(path) if Path(path).is_file() else {}
            old_value = data.get(local_key, _UNSET)
            data[local_key] = cmip_var
            entries.append((component_name, path, data))

        self.dirty_keys.add((table_name, component_name, local_key))
        self._history.append(_Edit(table_name, component_name, local_key, old_value))
        fre_logger.info('staged mapping %s -> %s for %s/%s (not yet saved)',
                        local_key, cmip_var, table_name, component_name)

    def clear_mapping(self, table_name: str, component_name: str, local_key: str) -> None:
        """Stage the target key's value back to '' (matches make_simple_varlist's unmapped
        placeholder convention rather than deleting the key)."""
        self.set_mapping(table_name, component_name, local_key, '')

    def mapped_variables(self, component_name: str, local_key: str) -> list:
        """(table_name, cmip_var) pairs across every currently-loaded MIP table's varlist
        where local_key is mapped to a non-empty CMIP variable for the given component --
        i.e. which MIP variables a pp file for this component/local_key is actually
        referenced by. Reflects staged-but-unsaved edits too, since those mutate
        varlists_by_table directly."""
        mapped = []
        for table_name, entries in self.varlists_by_table.items():
            for component, _path, data in entries:
                if component == component_name:
                    cmip_var = data.get(local_key)
                    if cmip_var:
                        mapped.append((table_name, cmip_var))
        return sorted(mapped)

    def is_disabled(self, table_name: str) -> bool:
        """Whether table_name's table_target currently has its ``disabled`` flag set --
        reflects staged-but-unsaved toggle_disabled() calls immediately, since those mutate
        the table_target dict in place."""
        return bool(self.table_targets_by_name[table_name].get('disabled'))

    def toggle_disabled(self, table_name: str) -> bool:
        """Flip table_name's ``disabled`` flag in memory, staging the change until
        save_pending() writes it back to the cmor yaml. Toggling back to the last-saved
        value (e.g. disable then re-enable before saving) drops it from disabled_dirty again.

        :return: the new disabled state.
        :rtype: bool
        """
        table_target = self.table_targets_by_name[table_name]
        new_state = not bool(table_target.get('disabled'))
        table_target['disabled'] = new_state
        if new_state == self._baseline_disabled.get(table_name, False):
            self.disabled_dirty.discard(table_name)
        else:
            self.disabled_dirty.add(table_name)
        fre_logger.info('staged disabled=%s for table %s (not yet saved)', new_state, table_name)
        return new_state

    def usage_count(self, component_name: str, local_key: str) -> int:
        """Number of currently-loaded (table, component) varlists in which local_key is
        mapped to a non-empty CMIP variable for the given component -- i.e. how many times
        a pp file for this component/local_key is actually referenced by the mapping, across
        every MIP table loaded into this session. Reflects staged-but-unsaved edits too,
        since those mutate varlists_by_table directly."""
        return len(self.mapped_variables(component_name, local_key))

    def _value_in(self, snapshot: dict, table_name: str, component_name: str, local_key: str):
        """local_key's value for (table_name, component_name) in a varlists_by_table-shaped
        snapshot (either the live self.varlists_by_table or self._baseline), or _UNSET if
        that component/key has no entry there at all."""
        for component, _path, data in snapshot.get(table_name, []):
            if component == component_name:
                return data.get(local_key, _UNSET)
        return _UNSET

    def undo(self) -> Optional[_Edit]:
        """Revert the single most-recently staged mapping edit (an assign or a clear --
        clear_mapping stages through set_mapping too, so both land in the same history),
        restoring that (table, component, local_key)'s prior in-memory value, or removing
        the key entirely if it had no entry before that edit. If an earlier staged edit to
        the same key is still pending, the key remains marked dirty; otherwise it's dropped
        from dirty_keys since it now matches the last-saved state again.

        :return: the reverted _Edit, or None if there was nothing to undo.
        :rtype: _Edit or None
        """
        if not self._history:
            return None
        edit = self._history.pop()
        for component, _path, data in self.varlists_by_table.get(edit.table_name, []):
            if component == edit.component_name:
                if edit.old_value is _UNSET:
                    data.pop(edit.local_key, None)
                else:
                    data[edit.local_key] = edit.old_value
                break

        key = (edit.table_name, edit.component_name, edit.local_key)
        current = self._value_in(self.varlists_by_table, *key)
        baseline = self._value_in(self._baseline, *key)
        if current == baseline:
            self.dirty_keys.discard(key)
        fre_logger.info('undid staged edit for %s/%s:%s', edit.table_name, edit.component_name,
                        edit.local_key)
        return edit

    @property
    def has_pending_changes(self) -> bool:
        """True if any staged mapping edit or disabled-flag toggle hasn't been written to
        disk yet."""
        return bool(self.dirty_keys) or bool(self.disabled_dirty)

    def _write_yaml_doc(self) -> None:
        """Re-serialize the full cmor yaml document back to yamlfile -- used to persist
        staged disabled-flag toggles, which live in the yaml itself rather than a varlist
        JSON file. Rewrites the whole file (not just the changed lines), so hand-added
        comments/anchors/formatting in yamlfile won't survive a save with a disabled toggle
        staged."""
        with open(self._yamlfile, 'w', encoding='utf-8') as handle:
            yaml.safe_dump(self._yaml_doc, handle, sort_keys=False)
        fre_logger.info('saved disabled-flag changes to %s', self._yamlfile)

    def save_pending(self) -> int:
        """Write every varlist file with a staged change to disk, and the cmor yaml itself if
        any disabled flag was toggled, then clear dirty tracking and undo history, and
        re-baseline both for a future restore_pending().

        :return: number of staged edits (mapping edits plus disabled-flag toggles) that were
            saved.
        :rtype: int
        """
        saved_count = len(self.dirty_keys) + len(self.disabled_dirty)
        paths_to_write = {}
        for table_name, component_name, _local_key in self.dirty_keys:
            for component, path, data in self.varlists_by_table.get(table_name, []):
                if component == component_name:
                    paths_to_write[path] = data
                    break

        for path, data in paths_to_write.items():
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, indent=4)
            fre_logger.info('saved varlist %s', path)

        if self.disabled_dirty:
            self._write_yaml_doc()
            self._baseline_disabled = {
                name: bool(table_target.get('disabled'))
                for name, table_target in self.table_targets_by_name.items()
            }
            self.disabled_dirty.clear()

        self.dirty_keys.clear()
        self._history.clear()
        self._baseline = _snapshot_varlists(self.varlists_by_table)
        return saved_count

    def restore_pending(self) -> int:
        """Discard every staged-but-unsaved edit at once (mapping edits and disabled-flag
        toggles alike), restoring in-memory state to the last save_pending() call (or to the
        state at load time, if nothing has been saved yet). Also clears the undo history,
        since those edits no longer apply once the whole session has been rewound past them.

        :return: number of staged edits (mapping edits plus disabled-flag toggles) that were
            discarded.
        :rtype: int
        """
        discarded = len(self.dirty_keys) + len(self.disabled_dirty)
        self.varlists_by_table = _snapshot_varlists(self._baseline)
        self.dirty_keys.clear()
        self._history.clear()
        for table_name in self.disabled_dirty:
            self.table_targets_by_name[table_name]['disabled'] = self._baseline_disabled[table_name]
        self.disabled_dirty.clear()
        fre_logger.info('restored %d staged edit(s) to last save state', discarded)
        return discarded


# ---------------------------------------------------------------------------
# the TUI itself
# ---------------------------------------------------------------------------

class MapApp(App):
    """Two-pane TUI: MIP-table mapping-status tree on the left, pp-directory browser +
    NetCDF preview on the right. Press 'm' to stage assigning the selected pp file to the
    selected CMIP variable, 'd' to stage clearing a selected existing mapping, 't' to stage
    toggling the disabled flag of the MIP table currently selected (or containing the
    currently selected variable/source), 'u' to undo the single most recent staged mapping
    edit, 'R' to restore every staged edit back to the last save, 's' to save all staged
    changes to disk, 'r' to refresh the tree, and 'q' to quit ('q' again to confirm if there
    are unsaved staged changes). Staged-but-unsaved mapping nodes are marked in place
    (without rebuilding the tree, so expanded branches stay expanded while batching edits)
    and are only cleared once 's' actually writes them out; 'u' and 'R' instead rebuild the
    tree, since they can affect many nodes at once. A disabled-flag toggle always updates its
    table node's label in place, immediately -- it doesn't participate in 'u' undo (toggling
    again reverses it just as easily), but does count toward 'R' restore and 's' save like
    any other staged edit."""

    CSS = """
    #cmip_pane {
        width: 1fr;
    }
    #cmip_tree {
        height: 2fr;
        border: solid $accent;
    }
    #cmip_detail {
        height: 1fr;
        border: solid $accent;
        padding: 1 2;
        overflow-y: auto;
    }
    #pp_pane {
        width: 1fr;
    }
    #selected_cmip {
        height: 4;
        border: solid $accent;
        padding: 0 2;
    }
    #pp_tree {
        height: 2fr;
        border: solid $accent;
    }
    #preview {
        height: 1fr;
        border: solid $accent;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        ('m', 'assign_mapping', 'Stage mapping'),
        ('d', 'clear_mapping', 'Stage clear'),
        ('t', 'toggle_disabled', 'Toggle table disabled'),
        ('u', 'undo', 'Undo last edit'),
        ('R', 'restore_pending', 'Restore to last save'),
        ('s', 'save_pending', 'Save staged changes'),
        ('r', 'refresh_tree', 'Refresh'),
        ('q', 'quit', 'Quit'),
    ]

    # Tree.set_label() runs labels through Rich's markup parser: [strike]...[/strike] below is
    # honored intentionally, but literal square brackets elsewhere must be avoided since
    # they'd otherwise be silently swallowed as (invalid) style tags rather than shown as text.
    PENDING_SUFFIX = '  (unsaved)'

    NO_CMIP_SELECTION = 'no CMIP variable selected'

    NO_CMIP_DETAIL = 'select a CMIP variable to see its MIP table definition'

    def __init__(self, session: MapSession):
        super().__init__()
        self.session = session
        self.selected_cmip: Optional[dict] = None
        self.selected_cmip_node = None
        # tracks whichever MIP table is currently "in context" in the cmip tree -- set on
        # selecting the table node itself, or any var/source node within it -- so 't' can
        # toggle that table's disabled flag without requiring the table node exactly.
        self.selected_table_name: Optional[str] = None
        self.selected_pp: Optional[dict] = None
        self.table_nodes = {}
        self._quit_confirmed = False
        # bumped on every pp-file selection; a background preview worker's result is only
        # applied if it still matches the generation current when it finishes -- since a
        # thread already running ncinfo/netCDF4 I/O can't actually be killed, this is what
        # makes a superseded (still in-flight) preview effectively "cancelled" from the
        # user's point of view: its result is simply discarded once it arrives.
        self._preview_generation = 0
        # mapped-MIP-variables text for the currently selected pp file, computed synchronously
        # on selection and re-prepended to the preview box once the backgrounded nc preview
        # (which replaces the whole box) lands.
        self._selected_pp_mapped_text = ''

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id='cmip_pane'):
                yield Tree('MIP Tables', id='cmip_tree')
                yield Static(self.NO_CMIP_DETAIL, id='cmip_detail')
            with Vertical(id='pp_pane'):
                yield Static(self.NO_CMIP_SELECTION, id='selected_cmip')
                yield Tree(self.session.pp_dir, id='pp_tree')
                yield Static('select a pp file to preview', id='preview')
        yield Footer()

    def _format_selected_cmip(self, data: Optional[dict]) -> str:
        """Render the currently-selected CMIP variable for the box above the pp browser."""
        if data is None:
            return self.NO_CMIP_SELECTION
        label = f'selected: {data["table"]} / {data["var"]}'
        if data.get('kind') == 'source':
            label += f'\ncurrent source: {data["component"]}:{data["local_key"]}'
        return label

    def _update_cmip_detail(self, data: dict) -> None:
        """Refresh the detail box under the cmip tree with the selected variable's own MIP
        table definition (long_name, units, etc.) -- independent of its mapping status."""
        definitions = self.session.variable_definitions(data['table'], data['var'])
        self.query_one('#cmip_detail', Static).update(
            _format_variable_detail(data['var'], definitions))

    def on_mount(self) -> None:
        self._populate_cmip_tree()
        self._populate_pp_root()

    # ---- cmip tree ----

    def _table_label(self, table_name: str, report: dict) -> str:
        label = f'{table_name} ({report["reference_var_count"]} vars)'
        if self.session.is_disabled(table_name):
            label += '  (disabled)'
        pending = sum(1 for (t, _c, _k) in self.session.dirty_keys if t == table_name)
        if table_name in self.session.disabled_dirty:
            pending += 1
        if pending:
            label += f' -- {pending} unsaved'
        return label

    def _source_label(self, base_label: str, table_name: str, component: str,
                      local_key: str) -> str:
        """Applied while rebuilding the tree (e.g. on manual refresh before saving): a
        still-mapped node whose (table, component, local_key) has a staged-but-unsaved edit
        is flagged generically -- a cleared mapping never reaches here, since it no longer
        produces a mapped-source node at all once the report is recomputed."""
        if (table_name, component, local_key) in self.session.dirty_keys:
            return base_label + self.PENDING_SUFFIX
        return base_label

    def _populate_cmip_tree(self) -> None:
        tree = self.query_one('#cmip_tree', Tree)
        tree.root.remove_children()
        self.table_nodes = {}
        for table_name in self.session.table_names:
            report = self.session.table_report(table_name)
            table_node = tree.root.add(
                self._table_label(table_name, report),
                data={'kind': 'table', 'table': table_name},
                expand=True,
            )
            self.table_nodes[table_name] = table_node

            unmapped_node = table_node.add(
                f'Unmapped ({len(report["unmapped"])})', data={'kind': 'branch'}, expand=True)
            for var in report['unmapped']:
                unmapped_node.add_leaf(var, data={'kind': 'var', 'table': table_name, 'var': var})

            mapped_node = table_node.add(
                f'Mapped ({len(report["one_to_one_mapped"])})', data={'kind': 'branch'})
            for var, (comp, key) in report['one_to_one_mapped'].items():
                mapped_node.add_leaf(
                    self._source_label(f'{var} <- {comp}:{key}', table_name, comp, key),
                    data={'kind': 'source', 'table': table_name, 'var': var,
                          'component': comp, 'local_key': key})

            multi_node = table_node.add(
                f'Multiply-mapped ({len(report["multiply_mapped"])})', data={'kind': 'branch'})
            for var, entries in report['multiply_mapped'].items():
                var_node = multi_node.add(
                    f'{var} ({len(entries)})',
                    data={'kind': 'var', 'table': table_name, 'var': var})
                for comp, key in entries:
                    var_node.add_leaf(
                        self._source_label(f'{comp}:{key}', table_name, comp, key),
                        data={'kind': 'source', 'table': table_name, 'var': var,
                              'component': comp, 'local_key': key})

            unknown_node = table_node.add(
                f'Unknown ({len(report["unknown_mapped"])})', data={'kind': 'branch'}, expand=True)
            for val in report['unknown_mapped']:
                sources = report['unknown_sources'].get(val, [])
                val_node = unknown_node.add(
                    f'{val} ({len(sources)})',
                    data={'kind': 'var', 'table': table_name, 'var': val})
                for comp, key in sources:
                    val_node.add_leaf(
                        self._source_label(f'{comp}:{key}', table_name, comp, key),
                        data={'kind': 'source', 'table': table_name, 'var': val,
                              'component': comp, 'local_key': key})

    # ---- pp tree (lazily populated on expand) ----

    def _populate_pp_root(self) -> None:
        tree = self.query_one('#pp_tree', Tree)
        tree.root.data = {'kind': 'root'}
        for component_dir in _discover_pp_components(self.session.pp_dir):
            has_ts = (Path(component_dir) / 'ts').is_dir()
            tree.root.add(
                Path(component_dir).name,
                data={'kind': 'component', 'path': component_dir,
                      'component': Path(component_dir).name},
                allow_expand=has_ts,
            )
        tree.root.expand()

    @staticmethod
    def _pp_file_label(nc_path: str, local_var: str, usage_count: int) -> str:
        """Render one pp-file leaf's label, including how many of the currently-loaded
        MIP tables' varlists actually map this file's (component, local_key) to a CMIP
        variable -- so a heavily-reused file (or a never-used one) is obvious at a glance."""
        return f'{Path(nc_path).name}  [{local_var}]  (used {usage_count}x)'

    def _ensure_pp_node_populated(self, node) -> None:
        """Populate a lazily-loaded pp_tree node's children, if not already populated.
        Shared by on_tree_node_expanded (user-driven expand) and _navigate_pp_tree_to
        (programmatic navigation), which can't wait on the NodeExpanded message's async
        round-trip through the app's event handler."""
        if node.children:
            return  # already populated on a prior expand

        data = node.data or {}
        kind = data.get('kind')
        if kind == 'component':
            for freq in _discover_freqs(data['path']):
                node.add(freq, data={'kind': 'freq', 'path': data['path'],
                                     'component': data['component'], 'freq': freq})
        elif kind == 'freq':
            for chunk in _discover_chunks(data['path'], data['freq']):
                node.add(chunk, data={'kind': 'chunk', 'path': data['path'],
                                      'component': data['component'],
                                      'freq': data['freq'], 'chunk': chunk})
        elif kind == 'chunk':
            for nc_path in _discover_nc_files(data['path'], data['freq'], data['chunk']):
                local_var = _local_var_name_from_nc_path(nc_path)
                usage_count = self.session.usage_count(data['component'], local_var)
                node.add_leaf(
                    self._pp_file_label(nc_path, local_var, usage_count),
                    data={'kind': 'file', 'path': nc_path,
                          'component': data['component'], 'local_key': local_var})

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        if event.control.id != 'pp_tree':
            return
        self._ensure_pp_node_populated(event.node)

    def _navigate_pp_tree_to(self, component: str, local_key: str) -> bool:
        """Expand and select the pp_tree leaf for (component, local_key), if such a pp file
        is discoverable on disk under pp_dir. Populates whatever component/freq/chunk nodes
        are needed along the way (bypassing the lazy on-expand population, since that only
        runs in response to a user-driven expand). Selecting the leaf posts a NodeSelected
        message, so on_tree_node_selected picks it up and refreshes the preview box exactly
        as if the user had clicked it.

        :return: True if the file was found and navigated to, False otherwise (e.g. it isn't
            on disk, or the component directory itself isn't present under pp_dir).
        :rtype: bool
        """
        tree = self.query_one('#pp_tree', Tree)
        component_node = next(
            (n for n in tree.root.children if (n.data or {}).get('component') == component),
            None)
        if component_node is None:
            return False

        self._ensure_pp_node_populated(component_node)
        for freq_node in component_node.children:
            self._ensure_pp_node_populated(freq_node)
            for chunk_node in freq_node.children:
                self._ensure_pp_node_populated(chunk_node)
                for file_node in chunk_node.children:
                    if (file_node.data or {}).get('local_key') == local_key:
                        component_node.expand()
                        freq_node.expand()
                        chunk_node.expand()
                        # Tree.move_cursor (inside select_node) trusts file_node._line, which
                        # is only kept current by Tree's own on-idle rebuild -- accessing
                        # last_line forces that rebuild synchronously so the newly-expanded
                        # ancestors are accounted for before we jump the cursor to a child.
                        _ = tree.last_line
                        tree.select_node(file_node)
                        tree.scroll_to_node(file_node)
                        return True
        return False

    # ---- selection tracking ----

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data or {}
        kind = data.get('kind')

        if event.control.id == 'cmip_tree':
            if 'table' in data:
                self.selected_table_name = data['table']
            if kind not in ('var', 'source'):
                return
            self.selected_cmip = data
            self.selected_cmip_node = event.node
            self.query_one('#selected_cmip', Static).update(self._format_selected_cmip(data))
            self._update_cmip_detail(data)
            if kind == 'source':
                # covers both a one-to-one mapping and one particular source of a
                # multiply-mapped variable -- both carry component/local_key here.
                found = self._navigate_pp_tree_to(data['component'], data['local_key'])
                if not found:
                    self.notify(
                        f'pp file for {data["component"]}:{data["local_key"]} not found '
                        'under pp_dir', severity='warning')

        elif event.control.id == 'pp_tree':
            if kind != 'file':
                return
            self.selected_pp = data
            self._preview_generation += 1
            mapped = self.session.mapped_variables(data['component'], data['local_key'])
            self._selected_pp_mapped_text = _format_mapped_variables(mapped)
            self.query_one('#preview', Static).update(
                f'{self._selected_pp_mapped_text}\n\nloading preview for '
                f'"{data["local_key"]}"...')
            self._load_preview(data['path'], data['local_key'], self._preview_generation)

    # ---- pp file preview (backgrounded so the TUI stays responsive) ----

    @work(exclusive=True, thread=True, group='pp_preview')
    def _load_preview(self, path: str, local_key: str, generation: int) -> None:
        """Runs dmls/ncinfo/netCDF4 (all potentially slow, e.g. over a network filesystem) in
        a background thread. exclusive=True cancels the *previous* worker in this group as
        soon as a new selection starts one, but a Python thread already blocked in
        subprocess/file I/O can't actually be interrupted -- so `generation` is what actually
        prevents a stale, still-in-flight preview from clobbering a newer selection's result
        once it eventually finishes.

        Checks dmls status first: a file not yet retrieved from tape ('REG'/'DUL' via dmls,
        or the stat-based heuristic as a fallback) shows a "still on tape" message instead of
        a preview, since opening/inspecting it would otherwise silently trigger (or block on)
        a tape retrieval."""
        dmls_state = _dmls_state_for_file(Path(path), self.session.dmls_bin)
        on_tape = (dmls_state not in _DMLS_DISK_RESIDENT_STATES if dmls_state is not None
                  else not _is_file_staged(Path(path)))
        if on_tape:
            text = _format_tape_message(local_key, path, dmls_state)
        else:
            source, preview_data = _preview_nc_file(path, local_key, self.session.ncinfo_bin)
            text = _format_preview_text(local_key, source, preview_data)
        self.call_from_thread(self._apply_preview_result, generation, text)

    def _apply_preview_result(self, generation: int, text: str) -> None:
        if generation != self._preview_generation:
            return  # superseded by a newer pp-file selection -- discard this stale result
        self.query_one('#preview', Static).update(f'{self._selected_pp_mapped_text}\n\n{text}')

    # ---- actions ----

    def _refresh_table_pending_label(self, table_name: str) -> None:
        table_node = self.table_nodes.get(table_name)
        if table_node is not None:
            report = self.session.table_report(table_name)
            table_node.set_label(self._table_label(table_name, report))

    def _mark_assigned(self, node, table_name: str, component: str, local_key: str) -> None:
        """Flag a node as staged to be (re)mapped to a new pp source, in place -- no tree
        rebuild, so expanded branches stay expanded while the user keeps batching edits.
        Shows exactly what it's now pointing at rather than a generic 'unsaved' note."""
        if node is not None:
            suffix = f'  <- {component}:{local_key}'
            label = str(node.label)
            if not label.endswith(suffix):
                node.set_label(label + suffix)
        self._refresh_table_pending_label(table_name)

    def _mark_deleted(self, node, table_name: str) -> None:
        """Flag a node as staged for deletion, in place -- struck through and labeled
        '(deleted)' rather than a generic 'unsaved' note, which read ambiguously (deleted vs.
        still mapped but unsaved)."""
        if node is not None:
            label = str(node.label)
            if not label.endswith('  (deleted)'):
                node.set_label(f'[strike]{label}[/strike]  (deleted)')
        self._refresh_table_pending_label(table_name)

    def action_assign_mapping(self) -> None:
        if self.selected_cmip is None or self.selected_pp is None:
            self.notify('select both a CMIP variable and a pp file first', severity='warning')
            return
        table_name = self.selected_cmip['table']
        cmip_var = self.selected_cmip['var']
        component = self.selected_pp['component']
        local_key = self.selected_pp['local_key']
        self.session.set_mapping(table_name, component, local_key, cmip_var)
        self.notify(f'staged {local_key} ({component}) -> {cmip_var} in {table_name} '
                   "(press 's' to save)")
        self._mark_assigned(self.selected_cmip_node, table_name, component, local_key)
        self._quit_confirmed = False

    def action_clear_mapping(self) -> None:
        if self.selected_cmip is None or self.selected_cmip.get('kind') != 'source':
            self.notify('select an existing component:local_key mapping to clear',
                        severity='warning')
            return
        table_name = self.selected_cmip['table']
        component = self.selected_cmip['component']
        local_key = self.selected_cmip['local_key']
        self.session.clear_mapping(table_name, component, local_key)
        self.notify(f'staged clearing {local_key} ({component}) in {table_name} '
                   "(press 's' to save)")
        self._mark_deleted(self.selected_cmip_node, table_name)
        self.selected_cmip = None
        self.selected_cmip_node = None
        self.query_one('#selected_cmip', Static).update(self._format_selected_cmip(None))
        self.query_one('#cmip_detail', Static).update(self.NO_CMIP_DETAIL)
        self._quit_confirmed = False

    def action_toggle_disabled(self) -> None:
        table_name = self.selected_table_name
        if table_name is None:
            self.notify('select a MIP table (or one of its variables) first', severity='warning')
            return
        new_state = self.session.toggle_disabled(table_name)
        self.notify(f"staged {'disabling' if new_state else 're-enabling'} {table_name} "
                   "(press 's' to save)")
        self._refresh_table_pending_label(table_name)
        self._quit_confirmed = False

    def action_undo(self) -> None:
        edit = self.session.undo()
        if edit is None:
            self.notify('nothing to undo', severity='information')
            return
        self.notify(f'undid staged edit for {edit.local_key} ({edit.component_name}) in '
                   f'{edit.table_name}')
        self._populate_cmip_tree()
        self._quit_confirmed = False

    def action_restore_pending(self) -> None:
        if not self.session.has_pending_changes:
            self.notify('no staged changes to restore', severity='information')
            return
        discarded = self.session.restore_pending()
        self.notify(f'restored {discarded} staged change(s) to last save state')
        self._populate_cmip_tree()
        self._quit_confirmed = False

    def action_save_pending(self) -> None:
        if not self.session.has_pending_changes:
            self.notify('no staged changes to save', severity='information')
            return
        saved_count = self.session.save_pending()
        self.notify(f'saved {saved_count} staged change(s)')
        self._populate_cmip_tree()

    def action_refresh_tree(self) -> None:
        self._populate_cmip_tree()

    async def action_quit(self) -> None:
        if self.session.has_pending_changes and not self._quit_confirmed:
            self._quit_confirmed = True
            count = len(self.session.dirty_keys)
            self.notify(
                f'{count} staged change(s) not saved -- press q again to quit anyway, '
                "or 's' to save first",
                severity='warning')
            return
        await super().action_quit()


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def cmor_map_subtool(
        yamlfile: str,
        table_patterns: Sequence[str] = (),
        ncinfo_bin: Optional[str] = None,
        dmls_bin: Optional[str] = None
) -> None:
    """
    Launch the interactive ``fremor map`` TUI.

    pp_dir, the MIP tables directory, the MIP era, and each component's variable_list path
    are all derived from ``yamlfile``, a self-contained CMOR YAML file as written by
    ``fremor config``. Mapping edits made in the TUI are staged in memory and only written
    back to the variable_list files referenced there once the user presses 's' to save.

    :param yamlfile: path to a CMOR YAML file produced by ``fremor config``.
    :type yamlfile: str
    :param table_patterns: optional glob-style patterns selecting which MIP tables to load.
        If empty, every MIP table in yamlfile's table_targets is loaded.
    :type table_patterns: Sequence[str]
    :param ncinfo_bin: optional explicit path to the ncinfo binary for richer NetCDF previews.
        If omitted, 'ncinfo' is looked up on PATH; if not found, previews fall back to netCDF4.
    :type ncinfo_bin: str or None
    :param dmls_bin: optional explicit path to the dmls binary, used to check whether a
        selected pp file has actually been retrieved from tape ('REG'/'DUL') before previewing
        it. If omitted, 'dmls' is looked up on PATH; if not found either, falls back to a
        stat-only residency heuristic.
    :type dmls_bin: str or None
    :raises FileNotFoundError: if yamlfile, its pp_dir, or its table_dir do not exist, or a
        table_target's MIP table JSON file is missing.
    :raises ValueError: if yamlfile has no table_targets, or none of the given table_patterns
        match any table_target.
    :return: None
    :rtype: None
    """
    session = MapSession(yamlfile, table_patterns, ncinfo_bin, dmls_bin)
    MapApp(session).run()
