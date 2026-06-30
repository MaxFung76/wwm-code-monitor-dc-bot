# Security Policy

## Supported Versions

This project is actively maintained on the latest `main` branch and the latest
published container image.

| Version | Supported |
| ------- | --------- |
| `main`  | Yes       |
| older commits / older images | No |

## Reporting a Vulnerability

Please do not open a public GitHub issue for security-sensitive reports.

If you discover a vulnerability, report it privately to the maintainer with:

- a clear description of the issue
- steps to reproduce
- affected environment details
- impact assessment if known

Recommended contact method:

- GitHub private security reporting, if enabled for the repository
- otherwise contact the maintainer directly before public disclosure

When reporting issues related to this bot, avoid including live secrets such as:

- Discord bot tokens
- SSH private keys
- GitHub personal access tokens
- database files containing operational data

## Response Process

The maintainer will aim to:

- acknowledge receipt within 7 days
- assess severity and reproduce the issue
- prepare and validate a fix
- coordinate a responsible disclosure timeline if needed

## Scope

Security reports are especially helpful for:

- secret handling and accidental credential exposure
- Discord permission or command abuse paths
- unsafe deployment defaults
- container or image supply-chain risks
- snapshot ingestion or remote data trust issues
