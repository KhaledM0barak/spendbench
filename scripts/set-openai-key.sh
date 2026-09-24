#!/usr/bin/env bash
# Write the OpenAI API key from the clipboard into spendbench/.env at 0600,
# alongside any existing ANTHROPIC_API_KEY line (updates OPENAI_API_KEY in
# place if one is already there, appends otherwise). Never prints the key —
# only its shape. See set-key.sh for the Anthropic equivalent.
set -euo pipefail

env_file="$HOME/spendbench/.env"

key="$(pbpaste)"
key="${key//[$'\t\r\n ']/}"

if [[ "$key" != sk-* ]]; then
  printf 'Clipboard holds something else — nothing written.\n' >&2
  printf '  length: %d, starts with: %s\n' "${#key}" "${key:0:12}" >&2
  printf 'Copy the key from platform.openai.com/account/api-keys, then run this again.\n' >&2
  exit 1
fi

touch "$env_file"
chmod 600 "$env_file"

tmp="$(mktemp)"
grep -v '^OPENAI_API_KEY=' "$env_file" > "$tmp" || true
printf 'OPENAI_API_KEY=%s\n' "$key" >> "$tmp"
( umask 077; mv "$tmp" "$env_file" )

printf 'written: %s\n' "$env_file"
printf '  %d chars, prefix ok, perms %s\n' "${#key}" "$(stat -f '%Lp' "$env_file")"
