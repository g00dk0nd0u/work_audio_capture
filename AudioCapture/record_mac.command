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

build_log=""
if build_log=$(mktemp "$repo_root/.record-mac-build.XXXXXX") 2>/dev/null; then
  swift build -c release --package-path "$package_path" >"$build_log" 2>&1
  build_exit_code=$?
  if [[ $build_exit_code -ne 0 ]]; then
    echo "Could not start recording: Swift build failed." >&2
    cat "$build_log" >&2
    rm -f "$build_log"
    exit $build_exit_code
  fi
  rm -f "$build_log"
else
  swift build -c release --package-path "$package_path"
  build_exit_code=$?
  if [[ $build_exit_code -ne 0 ]]; then
    echo "Could not start recording: Swift build failed." >&2
    exit $build_exit_code
  fi
fi

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
if ! temporary_session_log=$(mktemp "$repo_root/recordings/mac/.session-log.XXXXXX") 2>/dev/null; then
  echo "Warning: could not create session log: $session_log" >&2
  temporary_session_log=""
fi

log_line() {
  local message="$1"
  echo "$message"
  if [[ -n "$temporary_session_log" && -f "$temporary_session_log" ]]; then
    echo "$message" >> "$temporary_session_log" 2>/dev/null || true
  elif [[ -f "$session_log" ]]; then
    echo "$message" >> "$session_log" 2>/dev/null || true
  fi
}

log_line "Session active. Press Ctrl + C to end."

recorder_command=("$package_path/.build/release/sck-audio-spike" --output-dir "$session")
if [[ $# -eq 1 ]]; then
  recorder_command+=(--duration "$1")
fi

# Ctrl+C is handled by the Swift recorder. Ignore SIGINT in this wrapper while
# the child is running so the wrapper can continue to MP3 creation afterward.
trap '' INT
if [[ -n "$temporary_session_log" ]]; then
  "${recorder_command[@]}" >> "$temporary_session_log" 2>&1
else
  "${recorder_command[@]}"
fi
recording_exit_code=$?
trap - INT

active_log=""
if [[ -n "$temporary_session_log" ]]; then
  if mv "$temporary_session_log" "$session_log"; then
    active_log="$session_log"
    temporary_session_log=""
  else
    echo "Warning: could not move session log to: $session_log" >&2
    active_log="$temporary_session_log"
  fi
fi

append_status() {
  local message="$1"
  echo "$message"
  if [[ -n "$active_log" && -f "$active_log" ]]; then
    echo "$message" >> "$active_log" 2>/dev/null || true
  fi
}

mp3_exit_code=0
if [[ -f "$session/system.caf" && -f "$session/microphone.caf" && -f "$session/result.json" ]]; then
  append_status "Analyzing..."
  if [[ -n "$active_log" ]]; then
    python3 "$repo_root/make_mac_mp3.py" "$session" >> "$active_log" 2>&1
  else
    python3 "$repo_root/make_mac_mp3.py" "$session"
  fi
  mp3_exit_code=$?
  if [[ $mp3_exit_code -eq 0 && -f "$session/recording.mp3" ]]; then
    append_status "Finalizing... 100%"
  fi
fi

show_log_tail() {
  if [[ -n "$active_log" && -f "$active_log" ]]; then
    tail -n 20 "$active_log" >&2
  fi
}

if [[ $recording_exit_code -ne 0 ]]; then
  echo "Recording failed; diagnostics: ${active_log:-$session}" >&2
  show_log_tail
  exit $recording_exit_code
fi

if [[ $mp3_exit_code -ne 0 ]]; then
  echo "MP3 creation failed; diagnostics: ${active_log:-$session}" >&2
  show_log_tail
  exit $mp3_exit_code
fi

if [[ -f "$session/recording.mp3" ]]; then
  append_status "Completed."
  if ! open "$session"; then
    echo "Recording saved, but could not open output folder: $session" >&2
  fi
fi

exit 0
