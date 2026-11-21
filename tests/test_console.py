"""The command table, checked against the handlers it names.

Sixty-two commands, most of which are run once a month or never. A handler
renamed or removed used to surface as an AttributeError the next time someone
reached for that command, which could be months later and was — four
undefined names once sat in auto_draft.py through a dozen nightly runs.

Nothing here runs a command. Resolving the handler is enough: it imports the
module, which imports its layer, which is where a broken reference shows up.
"""

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from console import commands as table


class TestTable(unittest.TestCase):
    def test_every_handler_resolves(self):
        """The one failure mode a table introduces, closed."""
        broken = []
        for command in table.COMMANDS:
            try:
                handler = command.handler()
            except (ImportError, AttributeError) as exc:
                broken.append(f"{command.name} -> {command.run}: {exc}")
                continue
            if not callable(handler):
                broken.append(f"{command.name} -> {command.run} is not callable")
        self.assertEqual(broken, [])

    def test_no_handler_is_orphaned(self):
        """A handler no command names is dead code that reads as a feature."""
        import console.handlers as handlers
        named = {c.run.split(":")[-1] for c in table.COMMANDS}
        on_disk = {n for n in dir(handlers) if n.startswith("cmd_")}
        self.assertEqual(sorted(on_disk - named), [])

    def test_names_are_unique(self):
        names = [c.name for c in table.COMMANDS]
        self.assertEqual(sorted(names), sorted(set(names)))

    def test_every_command_has_a_known_group_and_help(self):
        for command in table.COMMANDS:
            self.assertIn(command.group, table.GROUPS, command.name)
            self.assertTrue(command.help, f"{command.name} has no help line")

    def test_every_group_has_commands(self):
        used = {c.group for c in table.COMMANDS}
        self.assertEqual(sorted(used), sorted(table.GROUPS))


class TestParser(unittest.TestCase):
    """The parser argparse actually builds from the table."""

    @classmethod
    def setUpClass(cls):
        import cli
        cls.parser = cli.build_parser()

    def test_every_command_parses_its_own_defaults(self):
        """A command whose arguments are all optional must parse bare."""
        for command in table.COMMANDS:
            required = [a for a in command.args if a.kwargs.get("required")]
            if required:
                continue
            with self.subTest(command.name):
                args = self.parser.parse_args([command.name])
                self.assertIs(args._command, command)

    def test_a_required_argument_is_still_required(self):
        """address-for takes --name and --domain and must refuse without
        them. Losing a `required=True` in the move would turn a clear error
        into a crash inside the handler."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["address-for"])

    def test_three_state_flag_survived_the_move(self):
        """`watch --deep/--no-deep` writes both flags to one dest, and --deep
        defaults to None rather than False so "unset" can mean "decide from
        the time of day". Generating the table flattened it to an ordinary
        boolean the first time, which would have made every watch run a deep
        one."""
        self.assertIsNone(self.parser.parse_args(["watch"]).deep)
        self.assertIs(self.parser.parse_args(["watch", "--deep"]).deep, True)
        self.assertIs(self.parser.parse_args(["watch", "--no-deep"]).deep, False)

    def test_help_lists_every_command_under_a_group(self):
        text = self.parser.format_help()
        for command in table.COMMANDS:
            self.assertIn(command.name, text, command.name)
        for group in table.GROUPS:
            self.assertIn(group, text)


class TestEntryPointStaysThin(unittest.TestCase):
    def test_cli_defines_no_command_logic(self):
        """cli.py builds the parser and dispatches. Anything shaped like a
        handler has drifted back into it."""
        tree = ast.parse(open(os.path.join(ROOT, "cli.py")).read())
        defined = {n.name for n in tree.body
                   if isinstance(n, ast.FunctionDef)}
        self.assertEqual(
            sorted(n for n in defined if n.startswith("cmd_")), [])


if __name__ == "__main__":
    unittest.main()
