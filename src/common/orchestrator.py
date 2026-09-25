# -*- coding: utf-8 -*-
"""Declarative test-plan facade for the existing trial runtime.

This module is deliberately placed in ``common``.  It coordinates a test's
order and cleanup, but it does not import or call serial, SWD, GDB, or
protocol drivers.  A step receives ``common.trial.Ctx`` and uses the driver
that belongs to its own layer.

The runtime remains ``common.trial.run_subitem``.  ``Plan`` is a compatible
builder around it, so existing test scripts can keep their current entry
point while new scripts get one explicit lifecycle:

    plan = Plan(...)
    plan.step("observe", observe)
    plan.cleanup("restore", restore)
    raise SystemExit(plan.run())
"""
from __future__ import annotations


class PlanError(ValueError):
    """Raised when a plan is assembled with an invalid lifecycle."""


class Plan(object):
    """Build one test item without owning any hardware driver.

    ``step`` callbacks run in declaration order after the serial resource has
    been opened.  A callback may request the injected GDB session through the
    supplied ``Ctx``; it must not open a second serial or probe handle.
    Cleanup callbacks run through ``trial.run_subitem`` after the debug
    session is closed and before the serial port is closed.
    """

    def __init__(self, title, criteria, *, name, gdb=None, allow=("--no-gdb", "--observe"),
                 known=(), obs=None, obs_waived_note="用户指定只做黑盒", banner=None,
                 logdir=None, session_kw=None):
        if not title or not isinstance(title, str):
            raise PlanError("title must be a non-empty string")
        if not callable(criteria):
            raise PlanError("criteria must be callable")
        if not name or not isinstance(name, str):
            raise PlanError("name must be a non-empty string")
        self.title = title
        self.criteria = criteria
        self.name = name
        self.gdb = gdb
        self.allow = tuple(allow or ())
        self.known = tuple(known or ())
        self.obs = tuple(obs) if obs is not None else None
        self.obs_waived_note = obs_waived_note
        self.banner = banner
        self.logdir = logdir
        self.session_kw = dict(session_kw or {})
        self._steps = []
        self._cleanup = []
        self._sealed = False

    def step(self, label, callback):
        """Append an observation/action callback and return ``self``."""
        self._append(self._steps, label, callback, "step")
        return self

    def cleanup(self, label, callback):
        """Append a post-session cleanup callback and return ``self``."""
        self._append(self._cleanup, label, callback, "cleanup")
        return self

    def _append(self, target, label, callback, kind):
        if self._sealed:
            raise PlanError("cannot add %s after run()" % kind)
        if not label or not isinstance(label, str):
            raise PlanError("%s label must be a non-empty string" % kind)
        if not callable(callback):
            raise PlanError("%s %r callback must be callable" % (kind, label))
        if any(old_label == label for old_label, _ in target):
            raise PlanError("duplicate %s label: %s" % (kind, label))
        target.append((label, callback))

    def run(self):
        """Run the plan using the established trial lifecycle.

        The import is intentionally local: importing this builder stays
        dependency-light, and the existing ``trial`` module remains the sole
        owner of serial/session open and close ordering.
        """
        if not self._steps:
            raise PlanError("a plan needs at least one step")
        self._sealed = True
        from common import trial
        return trial.run_subitem(
            self.title,
            self.criteria,
            name=self.name,
            parts=list(self._steps),
            allow=self.allow,
            known=self.known,
            obs=self.obs,
            gdb=self.gdb,
            obs_waived_note=self.obs_waived_note,
            banner=self.banner,
            logdir=self.logdir,
            session_kw=self.session_kw,
            cleanup=list(self._cleanup),
        )


__all__ = ["Plan", "PlanError"]
