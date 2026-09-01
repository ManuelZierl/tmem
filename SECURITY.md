# Security policy

## Supported versions

The latest release on the default branch receives security fixes. This project
is currently in its initial `0.1.x` release series.

## Trust model

`tmem` is a local, single-user tool. It does not sync commands or send telemetry,
but it handles sensitive and executable data:

- complete commands, working directories, hostnames, and session identifiers
  are stored in a local SQLite database;
- selected history entries and saved memories are executed in the current Bash
  process so they can change shell state;
- anyone who can read the database may learn secrets present in command-line
  arguments;
- anyone who can modify the database or replace the installed `tmem-core` can
  influence commands that the user later chooses to execute.

The data directory and database are created with restrictive permissions where
the filesystem permits it. These permissions do not protect against another
process already running as the same user.

Ignore patterns are a recording convenience, not a guarantee that secrets will
be detected. Prefer environment variables, standard input, or a secret manager
over placing credentials directly in command arguments. Use `tmem pause` before
sensitive work and `tmem resume` afterward.

## Reporting a vulnerability

Report vulnerabilities through GitHub's private vulnerability reporting form:

https://github.com/ManuelZierl/tmem/security/advisories/new

Include the affected version, required conditions, impact, and a minimal safe
reproduction. Do not include real credentials or private command history. If
private reporting is unavailable, open a public issue requesting a private
contact channel without disclosing vulnerability details.
