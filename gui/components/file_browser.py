"""Custom modal file browser for StemForge.

Replaces all dpg.file_dialog usage with a consistent, icon-rich UI.
Supports three modes:

  "open" — navigate and select an existing file matching ``extensions``.
  "save" — navigate to a destination directory, enter a filename.
  "dir"  — navigate and select a directory.

Icon and colour scheme
----------------------
  ▶ DirName        — blue   (directory)
  ♪ audio.wav      — green  (audio file)
  ♩ file.mid       — cyan   (MIDI file)
  · other          — gray   (everything else)

Sortable columns
----------------
Clicking the Name, Modified, or Type header button sorts the listing by
that field.  The active header shows ↑ (ascending) or ↓ (descending).
Dirs always appear before files within the same sort pass.

Usage
-----
    browser = FileBrowser("fb_loader", callback=my_cb,
                          extensions={".wav", ...}, mode="open")
    browser.build()     # call at top DearPyGUI level, outside windows
    browser.show()      # open the modal

Callbacks receive:
  "open" — pathlib.Path of the selected file
  "save" — pathlib.Path of (cwd / typed_filename)
  "dir"  — pathlib.Path of the selected directory
"""

import pathlib
import time
import logging
import datetime

import dearpygui.dearpygui as dpg

from gui.icons import get_icon_tag, ICON_SIZE

log = logging.getLogger("stemforge.gui.file_browser")

# Text extensions that use the file-text icon
_TEXT_EXTS = frozenset({
    ".txt", ".md", ".json", ".toml", ".yaml", ".yml",
    ".csv", ".xml", ".ini", ".cfg", ".py", ".js", ".ts",
    ".html", ".htm", ".css",
})

_DOUBLE_CLICK_INTERVAL = 0.35  # seconds

# Last directory where a file was successfully opened or saved.
# Shared across all FileBrowser instances so any dialog picks up where the
# last one left off.
_last_successful_dir: pathlib.Path | None = None

# Four shared colour themes — created lazily once per process
_theme_dir:   int | None = None
_theme_audio: int | None = None
_theme_midi:  int | None = None
_theme_other: int | None = None


def _ensure_themes() -> None:
    global _theme_dir, _theme_audio, _theme_midi, _theme_other
    if _theme_dir is not None:
        return

    def _make(r: int, g: int, b: int) -> int:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvSelectable):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (r, g, b, 255))
        return t

    _theme_dir   = _make( 80, 160, 240)
    _theme_audio = _make( 80, 210, 120)
    _theme_midi  = _make( 80, 220, 220)
    _theme_other = _make(130, 130, 140)


# ---------------------------------------------------------------------------
# Sort helpers
# ---------------------------------------------------------------------------

_COL_NAME  = "name"
_COL_MTIME = "mtime"
_COL_TYPE  = "type"


def _sort_key(entry: pathlib.Path, col: str):
    """Return a sort key for *entry* under the given column."""
    if col == _COL_MTIME:
        try:
            return entry.stat().st_mtime
        except OSError:
            return 0.0
    if col == _COL_TYPE:
        return entry.suffix.lower()
    return entry.name.lower()


def _fmt_mtime(entry: pathlib.Path) -> str:
    try:
        ts = entry.stat().st_mtime
        dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime("%b %d, %Y")
    except OSError:
        return "—"


# ---------------------------------------------------------------------------
# FileBrowser
# ---------------------------------------------------------------------------

class FileBrowser:
    """Modal file/directory browser with icons and sortable column headers."""

    def __init__(
        self,
        tag: str,
        callback,
        extensions: frozenset[str] | None = None,
        mode: str = "open",
    ) -> None:
        """
        Parameters
        ----------
        tag:
            Unique tag prefix.
        callback:
            Callable invoked with a pathlib.Path on OK/Save.
        extensions:
            Lower-case extensions with leading dot, used to colour-code audio
            files.  Ignored for ``mode="dir"``.
        mode:
            ``"open"`` | ``"save"`` | ``"dir"``.
        """
        assert mode in ("open", "save", "dir"), f"Unknown mode: {mode!r}"
        self._tag = tag
        self._callback = callback
        self._extensions = extensions or frozenset()
        self._mode = mode
        self._cwd = pathlib.Path.home()
        self._selected: pathlib.Path | None = None
        self._last_click_sender: str = ""
        self._last_click_time: float = 0.0
        # Sort state
        self._sort_col: str = _COL_NAME
        self._sort_asc: bool = True

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def _t(self, name: str) -> str:
        return f"{self._tag}_{name}"

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Create the modal window at the top DearPyGUI level."""
        _ensure_themes()

        title = {
            "open": "Browse for file",
            "save": "Choose save location",
            "dir":  "Choose folder",
        }[self._mode]

        ok_label = "Save" if self._mode == "save" else "OK"

        with dpg.window(
            tag=self._t("win"),
            label=title,
            modal=True,
            show=False,
            width=700,
            height=510,
            no_resize=False,
            no_scrollbar=True,
        ):
            # Navigation bar
            with dpg.group(horizontal=True):
                dpg.add_button(label="  ..  Up", callback=self._on_up)
                dpg.add_button(label="  ~  Home", callback=self._on_home)
                dpg.add_text("", tag=self._t("cwd_label"), color=(180, 180, 200, 255))

            dpg.add_separator()

            # Column sort headers
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Name ^",
                    tag=self._t("hdr_name"),
                    callback=self._on_sort_name,
                    width=300,
                )
                dpg.add_button(
                    label="Modified",
                    tag=self._t("hdr_mtime"),
                    callback=self._on_sort_mtime,
                    width=180,
                )
                dpg.add_button(
                    label="Type",
                    tag=self._t("hdr_type"),
                    callback=self._on_sort_type,
                    width=130,
                )

            dpg.add_separator()

            # Scrollable listing
            with dpg.child_window(
                tag=self._t("listing"),
                height=300,
                width=-1,
                border=True,
            ):
                pass

            dpg.add_separator()

            # Mode-specific bottom area
            if self._mode == "save":
                with dpg.group(horizontal=True):
                    dpg.add_text("Filename:", color=(140, 140, 160, 255))
                    dpg.add_input_text(
                        tag=self._t("filename"),
                        hint="enter filename",
                        width=-1,
                    )
            else:
                with dpg.group(horizontal=True):
                    dpg.add_text("Selected:", color=(140, 140, 160, 255))
                    dpg.add_text("", tag=self._t("sel_label"), color=(200, 200, 220, 255))

            dpg.add_spacer(height=6)

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label=f"  {ok_label}  ",
                    callback=self._on_ok,
                    width=80,
                    height=32,
                )
                dpg.add_button(
                    label="  Cancel  ",
                    callback=self._on_cancel,
                    width=80,
                    height=32,
                )

        self._refresh_list()

    # ------------------------------------------------------------------
    # Show / hide
    # ------------------------------------------------------------------

    def show(self) -> None:
        global _last_successful_dir
        if _last_successful_dir is not None and _last_successful_dir.is_dir():
            self._cwd = _last_successful_dir
        self._selected = None
        self._refresh_list()
        if dpg.does_item_exist(self._t("sel_label")):
            dpg.set_value(self._t("sel_label"), "")
        if self._mode == "save" and dpg.does_item_exist(self._t("filename")):
            dpg.set_value(self._t("filename"), "")
        if dpg.does_item_exist(self._t("win")):
            dpg.configure_item(self._t("win"), show=True)

    def hide(self) -> None:
        if dpg.does_item_exist(self._t("win")):
            dpg.configure_item(self._t("win"), show=False)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_up(self, sender, app_data, user_data) -> None:
        parent = self._cwd.parent
        if parent != self._cwd:
            self._navigate(parent)

    def _on_home(self, sender, app_data, user_data) -> None:
        self._navigate(pathlib.Path.home())

    def _navigate(self, path: pathlib.Path) -> None:
        self._cwd = path
        self._selected = None
        if dpg.does_item_exist(self._t("sel_label")):
            dpg.set_value(self._t("sel_label"), "")
        self._refresh_list()

    # ------------------------------------------------------------------
    # Sort callbacks
    # ------------------------------------------------------------------

    def _on_sort_name(self, sender, app_data, user_data) -> None:
        if self._sort_col == _COL_NAME:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = _COL_NAME
            self._sort_asc = True
        self._update_sort_headers()
        self._refresh_list()

    def _on_sort_mtime(self, sender, app_data, user_data) -> None:
        if self._sort_col == _COL_MTIME:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = _COL_MTIME
            self._sort_asc = False   # newest first by default
        self._update_sort_headers()
        self._refresh_list()

    def _on_sort_type(self, sender, app_data, user_data) -> None:
        if self._sort_col == _COL_TYPE:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = _COL_TYPE
            self._sort_asc = True
        self._update_sort_headers()
        self._refresh_list()

    def _update_sort_headers(self) -> None:
        """Refresh sort-indicator markers on the three header buttons."""
        marker = "^" if self._sort_asc else "v"
        labels = {
            _COL_NAME:  ("Name", self._t("hdr_name")),
            _COL_MTIME: ("Modified", self._t("hdr_mtime")),
            _COL_TYPE:  ("Type", self._t("hdr_type")),
        }
        for col, (base, tag) in labels.items():
            label = f"{base} {marker}" if col == self._sort_col else base
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, label=label)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def _refresh_list(self) -> None:
        if not dpg.does_item_exist(self._t("listing")):
            return

        dpg.delete_item(self._t("listing"), children_only=True)

        if dpg.does_item_exist(self._t("cwd_label")):
            dpg.set_value(self._t("cwd_label"), str(self._cwd))

        try:
            entries = list(self._cwd.iterdir())
        except PermissionError:
            dpg.add_text(
                "  (permission denied)",
                color=(200, 80, 80, 255),
                parent=self._t("listing"),
            )
            return

        entries = [e for e in entries if not e.name.startswith(".")]

        dirs  = [e for e in entries if e.is_dir()]
        files = [e for e in entries if not e.is_dir()]

        def _key(e: pathlib.Path):
            return _sort_key(e, self._sort_col)

        dirs.sort(key=_key, reverse=not self._sort_asc)
        files.sort(key=_key, reverse=not self._sort_asc)

        for entry in dirs + files:
            is_dir   = entry.is_dir()
            ext      = entry.suffix.lower()
            is_audio = ext in self._extensions
            is_midi  = ext in (".mid", ".midi")
            is_text  = ext in _TEXT_EXTS

            # Pick colour theme for the filename selectable
            if is_dir:
                theme = _theme_dir
            elif is_audio:
                theme = _theme_audio
            elif is_midi:
                theme = _theme_midi
            else:
                theme = _theme_other

            # Pick icon kind
            if is_dir:
                icon_kind = "folder"
            elif is_audio:
                icon_kind = "audio"
            elif is_midi:
                icon_kind = "midi"
            elif is_text:
                icon_kind = "text"
            else:
                icon_kind = "file"

            icon_tex = get_icon_tag(icon_kind)

            # Build a row: [icon | name selectable | mtime | type]
            mtime_str = _fmt_mtime(entry)
            type_str  = entry.suffix.upper().lstrip(".") if not is_dir else "Folder"

            sel_tag = f"{self._t('sel')}_{id(entry)}"
            with dpg.group(horizontal=True, parent=self._t("listing")):
                # Fixed-width icon column (18 px) — spacer keeps alignment when
                # the texture is unavailable.
                if icon_tex is not None:
                    dpg.add_image(icon_tex, width=ICON_SIZE, height=ICON_SIZE)
                else:
                    dpg.add_spacer(width=ICON_SIZE)

                # Name selectable: width = Name-header (300) − icon (18) − spacing (8)
                dpg.add_selectable(
                    label=entry.name,
                    tag=sel_tag,
                    callback=self._on_item_click,
                    user_data={"path": entry, "is_dir": is_dir},
                    width=274,
                )
                if theme is not None:
                    dpg.bind_item_theme(sel_tag, theme)
                dpg.add_text(mtime_str, color=(160, 160, 160, 255))
                dpg.add_spacer(width=8)
                dpg.add_text(type_str, color=(140, 140, 160, 255))

    # ------------------------------------------------------------------
    # Item click
    # ------------------------------------------------------------------

    def _on_item_click(self, sender, app_data, user_data) -> None:
        info    = user_data
        path: pathlib.Path = info["path"]
        is_dir: bool       = info["is_dir"]

        now        = time.monotonic()
        is_double  = (
            sender == self._last_click_sender
            and (now - self._last_click_time) < _DOUBLE_CLICK_INTERVAL
        )
        self._last_click_sender = sender
        self._last_click_time   = now

        if is_dir:
            if is_double:
                self._navigate(path)
            else:
                # Single click on dir: select it (for dir mode) or just highlight
                if self._mode == "dir":
                    self._selected = path
                    if dpg.does_item_exist(self._t("sel_label")):
                        dpg.set_value(self._t("sel_label"), str(path))
        else:
            if self._mode == "dir":
                return  # files are not selectable in dir mode
            self._selected = path
            if dpg.does_item_exist(self._t("sel_label")):
                dpg.set_value(self._t("sel_label"), path.name)
            if self._mode == "save" and dpg.does_item_exist(self._t("filename")):
                dpg.set_value(self._t("filename"), path.name)
            if is_double and self._mode == "open":
                self._on_ok(None, None, None)

    # ------------------------------------------------------------------
    # OK / Save / Cancel
    # ------------------------------------------------------------------

    def _on_ok(self, sender, app_data, user_data) -> None:
        self.hide()

        result: pathlib.Path | None = None

        if self._mode == "save":
            fn = ""
            if dpg.does_item_exist(self._t("filename")):
                fn = dpg.get_value(self._t("filename")).strip()
            if not fn:
                return
            result = self._cwd / fn

        elif self._mode == "dir":
            result = self._selected if self._selected else self._cwd

        else:  # "open"
            if self._selected is None:
                return
            result = self._selected

        if result is not None and self._callback is not None:
            try:
                self._callback(result)
                global _last_successful_dir
                _last_successful_dir = self._cwd
            except Exception as exc:
                log.error("FileBrowser callback error: %s", exc)

    def _on_cancel(self, sender, app_data, user_data) -> None:
        self.hide()
