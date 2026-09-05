"""Build only the explicitly listed runtime modules, never a vault or tests."""
from setuptools import setup
from setuptools.command.build_py import build_py

RUNTIME_MODULES = {
    "__init__", "__main__", "app", "commands", "diagnostics", "history",
    "settings", "shortcut_bar", "taskman", "vault_screen", "vaults",
    "notes", "notes_ui", "notes_actions",
}


class RuntimeBuild(build_py):
    def find_package_modules(self, package, package_dir):
        return [item for item in super().find_package_modules(package, package_dir)
                if item[1] in RUNTIME_MODULES]


setup(cmdclass={"build_py": RuntimeBuild})
