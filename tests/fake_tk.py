"""A stand-in for tkinter, shared by the dashboard and statistics-viewer
tests. Tests patch a module's `tk` with FAKE_TK, so no window is ever opened;
the fakes record what the code asked Tk to do, for the tests to inspect."""

from types import SimpleNamespace


class FakeTclError(Exception):
    pass


class FakeWidget:
    def __init__(self, *args, **options):
        self.options = dict(options)

    def pack(self, **kwargs):
        pass

    def grid(self, **kwargs):
        pass

    def config(self, **options):
        self.options.update(options)

    configure = config


class FakeTk(FakeWidget):
    """The main window. after() callbacks are recorded, not run: tests run
    them with flush() (all, in order, like Tk's loop between monitor checks)
    or run_next() (the latest scheduled one, e.g. the viewer's next refresh)."""

    def __init__(self):
        super().__init__()
        self.scheduled = []  # (delay in ms, callback)
        self.window_title, self.close_handler = None, None
        self.destroyed = self.loop_started = False

    def title(self, text):
        self.window_title = text

    def geometry(self, spec):
        pass

    def protocol(self, name, handler):
        self.close_handler = handler

    def after(self, delay_ms, callback):
        if self.destroyed:
            raise FakeTclError("application has been destroyed")
        self.scheduled.append((delay_ms, callback))
        return f"after#{len(self.scheduled)}"

    def after_cancel(self, ident):
        pass

    def flush(self):
        while self.scheduled:
            _, callback = self.scheduled.pop(0)
            callback()

    def run_next(self):
        _, callback = self.scheduled.pop()
        callback()

    def mainloop(self):
        self.loop_started = True

    def destroy(self):
        self.destroyed = True


class FakeToplevel:
    """A popup window; every one created is kept in FakeToplevel.created."""
    created = []

    def __init__(self, root):
        self.exists, self.window_title = True, None
        FakeToplevel.created.append(self)

    def title(self, text):
        self.window_title = text

    def geometry(self, spec):
        self.spec = spec

    def attributes(self, *args):
        pass

    def configure(self, **kwargs):
        pass

    def winfo_screenwidth(self):
        return 1920

    def winfo_exists(self):
        return self.exists

    def destroy(self):
        self.exists = False


class FakeCanvas(FakeWidget):
    """Records what is drawn on it."""

    def __init__(self, *args, **options):
        super().__init__(*args, **options)
        self.items = []

    def _add(self, kind, coords, options):
        self.items.append(SimpleNamespace(kind=kind, coords=coords, **options))
        return len(self.items)

    def create_arc(self, *coords, **options):
        return self._add("arc", coords, options)

    def create_oval(self, *coords, **options):
        return self._add("oval", coords, options)

    def create_rectangle(self, *coords, **options):
        return self._add("rectangle", coords, options)

    def create_text(self, *coords, **options):
        return self._add("text", coords, options)

    def delete(self, tag):
        assert tag == "all"
        self.items = []

    def of_kind(self, *kinds):
        return [item for item in self.items if item.kind in kinds]

    def texts(self):
        return [item.text for item in self.of_kind("text")]


FAKE_TK = SimpleNamespace(Tk=FakeTk, Toplevel=FakeToplevel, Canvas=FakeCanvas, Label=FakeWidget, Button=FakeWidget,
                          Frame=FakeWidget, LabelFrame=FakeWidget, Text=FakeWidget, TclError=FakeTclError)
