from __future__ import annotations

import unittest

from tmem.templates import (
    apply_parameterization,
    build_script,
    parameter_names,
    render_template,
    shell_tokens,
)


class TemplateTests(unittest.TestCase):
    def test_token_parameterization_and_shell_quoting(self) -> None:
        command = "kubectl logs deployment/api -n production"
        tokens = shell_tokens(command)
        workload = next(token for token in tokens if token.value == "deployment/api")
        namespace = next(token for token in tokens if token.value == "production")
        template = apply_parameterization(
            command,
            [
                (workload.start, workload.end, "workload"),
                (namespace.start, namespace.end, "namespace"),
            ],
        )
        self.assertEqual(
            template,
            "kubectl logs {{workload}} -n {{namespace}}",
        )
        rendered = render_template(
            template,
            {"workload": "deployment/worker", "namespace": "name with spaces"},
        )
        self.assertEqual(
            rendered,
            "kubectl logs deployment/worker -n 'name with spaces'",
        )

    def test_parameter_names_are_unique_and_ordered(self) -> None:
        self.assertEqual(
            parameter_names(["echo {{one}} {{two}}", "echo {{one}}"]),
            ["one", "two"],
        )

    def test_group_stays_in_current_shell_and_stops_on_failure(self) -> None:
        script = build_script(["cd /tmp", "export VALUE=ok"], stop_on_error=True)
        self.assertIn("{\ncd /tmp\n}", script)
        self.assertIn("&&", script)
        self.assertNotIn("(", script)

    def test_parameterization_preserves_attached_shell_operators(self) -> None:
        cases = {
            "echo value; echo next": "echo {{item}}; echo next",
            "echo value&&echo next": "echo {{item}}&&echo next",
            "cat<file": "cat<{{item}}",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                token = next(token for token in shell_tokens(command) if token.value in {"value", "file"})
                self.assertEqual(
                    apply_parameterization(command, [(token.start, token.end, "item")]),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
