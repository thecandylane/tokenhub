# Source from ~/.zshrc. Sends Claude Code and Grok Build through tokenhub when it is up,
# and runs them untouched when it is not, so a stopped hub never breaks them.
_tokenhub_url() { echo "http://127.0.0.1:${TOKENHUB_PORT:-4000}"; }
_tokenhub_up() { curl -sf -m 0.3 "$(_tokenhub_url)/_health" >/dev/null 2>&1; }

claude() {
  if _tokenhub_up; then ANTHROPIC_BASE_URL="$(_tokenhub_url)/anthropic" command claude "$@"
  else command claude "$@"; fi
}

grok() {
  if _tokenhub_up; then GROK_MODELS_BASE_URL="$(_tokenhub_url)/grok/v1" command grok "$@"
  else command grok "$@"; fi
}
