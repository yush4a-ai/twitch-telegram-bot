"""Guard: user-facing bot and Mini App copy must not contain an em dash.

The owner's rule is that an em dash reads like machine-written text, so the
visible copy uses a colon, a full stop or a comma instead. This test reads the
modules that build human-facing messages, cuts comments and docstrings out and
fails if the em dash comes back into the copy.

Comments and logging are not copy: they are excluded on purpose. Nothing under
``bot/admin_ui/`` or ``bot/admin_web.py`` is inspected — that surface is owned
by a parallel writer.
"""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EM_DASH = '\u2014'

PYTHON_COPY_FILES = (
    'main.py',
    'bot/poller.py',
    'bot/report.py',
    'bot/twitch.py',
    'bot/owner_alerts.py',
    'bot/plan_catalog.py',
    'bot/middlewares.py',
    'bot/telegram_ui.py',
    'bot/telegram_home.py',
    'bot/telegram_lists.py',
    'bot/telegram_replay.py',
    'bot/live_post.py',
    'bot/oauth_result.py',
    'bot/handlers/streams.py',
    'bot/handlers/telegram_help.py',
    'bot/handlers/telegram_plus.py',
    'bot/handlers/telegram_streamer.py',
    'bot/handlers/auth.py',
)

FRONTEND_COPY_GLOBS = (
    'bot/mini_app_ui/*.js',
    'bot/mini_app_ui/*.html',
    'bot/oauth_result_ui/*.js',
    'bot/oauth_result_ui/*.html',
    'bot/oauth_result_ui/*.css',
    'bot/legal_ui/*.html',
    'bot/legal_ui/*.css',
    'bot/streamer_ui/*.js',
    'bot/streamer_ui/*.html',
    'bot/streamer_ui/*.css',
    'bot/viewer_ui/*.js',
    'bot/viewer_ui/*.html',
    'bot/viewer_ui/*.css',
)

# Markup keeps code inside the strings that build the HTML report. Those inner
# comments are not copy either, so they are cut out before the check.
_LAYOUT_COMMENTS = (
    re.compile(r'<!--.*?-->', re.S),
    re.compile(r'/\*.*?\*/', re.S),
    re.compile(r'^[ \t]*//[^\n]*', re.M),
)

_LOG_LEVELS = {'debug', 'info', 'warning', 'error', 'exception', 'critical'}
_LOGGER_NAMES = {'logger', 'log', 'logging'}


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, 'body', None) or []
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            ids.add(id(first.value))
    return ids


def _logging_string_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOG_LEVELS:
            continue
        owner = node.func.value
        name = owner.id if isinstance(owner, ast.Name) else getattr(owner, 'attr', None)
        if name not in _LOGGER_NAMES:
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                ids.add(id(argument))
    return ids


def _without_layout_comments(value):
    for pattern in _LAYOUT_COMMENTS:
        value = pattern.sub('', value)
    return value


def python_copy_values(source):
    """Every string value that can reach the interface, without comments or logs."""
    tree = ast.parse(source)
    ignored = _docstring_ids(tree) | _logging_string_ids(tree)
    values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in ignored:
            continue
        values.append((node.lineno, _without_layout_comments(node.value)))
    return values


def frontend_copy_values(source):
    """Line-by-line values outside //, /* */ and <!-- --> comments."""
    cleaned = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.S)
    values = []
    for number, line in enumerate(cleaned.splitlines(), 1):
        values.append((number, re.sub(r'(?<!:)//.*$', '', line)))
    return values


def em_dash_lines(values):
    return [(number, text.strip()[:160]) for number, text in values if EM_DASH in text]


class UserFacingCopyHasNoEmDashTests(unittest.TestCase):
    def test_python_copy_has_no_em_dash(self):
        self.assertTrue(PYTHON_COPY_FILES, 'the guarded file list must not be empty')
        for relative in PYTHON_COPY_FILES:
            path = ROOT / relative
            with self.subTest(path=relative):
                self.assertTrue(path.is_file(), f'{relative} is missing from the guarded set')
                hits = em_dash_lines(python_copy_values(path.read_text(encoding='utf-8-sig')))
                self.assertEqual(hits, [], f'{relative}: em dash in user copy at {hits}')

    def test_frontend_copy_has_no_em_dash(self):
        paths = sorted({path for pattern in FRONTEND_COPY_GLOBS for path in ROOT.glob(pattern)})
        self.assertTrue(paths, 'no frontend copy files were found')
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            with self.subTest(path=relative):
                hits = em_dash_lines(frontend_copy_values(path.read_text(encoding='utf-8')))
                self.assertEqual(hits, [], f'{relative}: em dash in user copy at {hits}')

    def test_guard_reacts_when_the_em_dash_returns(self):
        python_source = 'def screen():\n    """Docstring with \u2014 dash."""\n    logger.info("log line \u2014 dash")\n    return "Подписка \u2014 150 \u20bd"\n'
        python_hits = em_dash_lines(python_copy_values(python_source))
        self.assertEqual([number for number, _ in python_hits], [4])
        self.assertIn('Подписка', python_hits[0][1])
        frontend_source = "// comment \u2014 dash\nconst label = 'Подписка \u2014 150 \u20bd';\n"
        frontend_hits = em_dash_lines(frontend_copy_values(frontend_source))
        self.assertEqual([number for number, _ in frontend_hits], [2])
        self.assertIn('Подписка', frontend_hits[0][1])

    def test_guarded_set_keeps_the_parallel_admin_surface_out(self):
        for relative in PYTHON_COPY_FILES:
            self.assertFalse(relative.startswith('bot/admin_ui/'), relative)
            self.assertNotEqual(relative, 'bot/admin_web.py')
        for pattern in FRONTEND_COPY_GLOBS:
            self.assertNotIn('admin', pattern)


if __name__ == '__main__':
    unittest.main()
