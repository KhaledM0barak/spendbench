#!/usr/bin/env bash
# Write the Anthropic API key from the clipboard into spendbench/.env at 0600.
# Never prints the key — only its shape.
#
# The destination is hardcoded rather than derived from BASH_SOURCE, so this
# still writes the right file when invoked through a symlink.
set -euo pipefail

env_file="$HOME/spendbench/.env"

key="$(pbpaste)"
key="${key//[$'\t\r\n ']/}"

if [[ "$key" != sk-ant-* ]]; then
  printf 'Clipboard holds something else — nothing written.\n' >&2
  printf '  length: %d, starts with: %s\n' "${#key}" "${key:0:12}" >&2
  printf 'Copy the key from console.anthropic.com, then run this again.\n' >&2
  exit 1
fi

( umask 077; printf 'ANTHROPIC_API_KEY=%s\n' "$key" > "$env_file" )
printf 'written: %s\n' "$env_file"
printf '  %d chars, prefix ok, perms %s\n' "${#key}" "$(stat -f '%Lp' "$env_file")"
