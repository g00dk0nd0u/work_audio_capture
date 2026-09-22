#!/bin/zsh

if [[ $# -gt 1 ]]; then
  echo "Usage: ./record_mac.command [duration-seconds]" >&2
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: record_mac.command requires macOS 15 or newer." >&2
  exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
  echo "Error: swift is unavailable. Install Xcode Command Line Tools and try again." >&2
  exit 1
fi

repo_root="$(cd -- "$(dirname -- "$0")" && pwd -P)" || exit $?
package_path="$repo_root/experiments/macos_capture/sck"

echo "Work Audio Capture — macOS"
swift build -c release --package-path "$package_path" || exit $?

timestamp=$(date '+%Y-%m-%d_%H-%M-%S')
session="$repo_root/recordings/mac/$timestamp"
suffix=1
mkdir -p "$repo_root/recordings/mac" || exit $?
while ! mkdir "$session" 2>/dev/null; do
  if [[ ! -e "$session" ]]; then
    echo "Error: could not create session directory: $session" >&2
    exit 1
  fi
  session="$repo_root/recordings/mac/${timestamp}-$suffix"
  (( suffix++ ))
done

echo "Recording..."
echo "Press Ctrl+C to stop."
echo "Session:"
echo "$session"

recorder_command=("$package_path/.build/release/sck-audio-spike" --output-dir "$session")
if [[ $# -eq 1 ]]; then
  recorder_command+=(--duration "$1")
fi
"${recorder_command[@]}"
status=$?

echo "Recording stopped."
echo "Saved:"
echo "$session"
exit $status
