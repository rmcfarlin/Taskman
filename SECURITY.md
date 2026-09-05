# Security policy

## Supported versions

Security fixes target the latest stable release listed on [GitHub Releases](https://github.com/rmcfarlin/Taskman/releases/latest). Older releases and prereleases do not receive separate security updates. Upgrade to the latest stable release before checking whether an issue still occurs, using a copy of your vault if reproduction could change files.

## Report a vulnerability privately

Use GitHub's [Report a vulnerability](https://github.com/rmcfarlin/Taskman/security/advisories/new) form. Reports are private to the repository's security maintainers and invited collaborators. A GitHub account is required.

Do not disclose vulnerabilities in public issues or pull requests. If the private form is unavailable, open an issue asking the maintainer to restore private reporting, without including vulnerability details or reproduction steps.

Include:

- The Taskman version, operating system, terminal, and installation method. For source installations, include the Python version.
- A description of the issue and its potential impact.
- Minimal reproduction steps using synthetic Markdown files in a disposable vault.
- Any suggested fix or workaround, if available.

Do not attach a real vault, credentials, or private task content. Review diagnostic reports before sharing: tracebacks can contain file paths or error messages with sensitive details even though Taskman does not capture local variables.

The maintainer will assess the report and coordinate a fix and disclosure through the private report. Response and resolution times depend on maintainer availability; no fixed timeline is guaranteed. Please keep details private while a fix is being coordinated.

Ordinary bugs and feature requests belong in [GitHub Issues](https://github.com/rmcfarlin/Taskman/issues).
