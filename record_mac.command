#!/bin/zsh

repo_root="$(cd -- "$(dirname -- "$0")" && pwd -P)" || exit $?
exec "$repo_root/platforms/macos/record_mac.command" "$@"
