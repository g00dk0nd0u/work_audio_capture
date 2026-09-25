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

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is unavailable." >&2
  exit 1
fi

repo_root="$(cd -- "$(dirname -- "$0")" && pwd -P)" || exit $?
package_path="$repo_root/platforms/macos/sck"

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

session_log="$session/session.log"
temporary_session_log=""
if temporary_session_log=$(mktemp "$repo_root/recordings/mac/.session-log.XXXXXX") 2>/dev/null; then
  # Keep the terminal interactive while preserving both output streams for
  # troubleshooting. A tee failure must never invalidate captured audio.
  exec > >(trap '' INT; exec tee -a "$temporary_session_log") \
    2> >(trap '' INT; exec tee -a "$temporary_session_log" >&2)
else
  echo "Warning: could not create session log: $session_log" >&2
fi

echo "Recording..."
echo "Press Ctrl+C to stop."
echo "Session:"
echo "$session"

recorder_command=("$package_path/.build/release/sck-audio-spike" --output-dir "$session")
if [[ $# -eq 1 ]]; then
  recorder_command+=(--duration "$1")
fi

# Ctrl+C is handled by the Swift recorder. Ignore SIGINT in this wrapper while
# the child is running so the wrapper can continue to MP3 creation afterward.
trap '' INT
"${recorder_command[@]}"
recording_exit_code=$?
trap - INT

if [[ -n "$temporary_session_log" ]] && ! mv "$temporary_session_log" "$session_log"; then
  echo "Warning: could not move session log to: $session_log" >&2
fi

mp3_exit_code=0
if [[ -f "$session/system.caf" && -f "$session/microphone.caf" && -f "$session/result.json" ]]; then
  echo "Creating listening MP3..."
  python3 "$repo_root/make_mac_mp3.py" "$session"
  mp3_exit_code=$?
fi

echo "Recording stopped."
echo "Saved:"
echo "$session"
if [[ -f "$session/recording.mp3" ]]; then
  echo "MP3:"
  echo "$session/recording.mp3"
  if [[ $recording_exit_code -eq 0 && $mp3_exit_code -eq 0 ]] && ! open "$session"; then
    echo "Recording saved, but could not open output folder: $session" >&2
  fi
fi

if [[ $recording_exit_code -ne 0 ]]; then
  exit $recording_exit_code
fi
exit $mp3_exit_code
