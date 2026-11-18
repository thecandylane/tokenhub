# Source from ~/.zshrc. Sends Claude Code, Codex and Grok Build through tokenhub when it is up,
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

# Codex with a ChatGPT login. The custom provider turns WebSockets off so Codex streams over plain HTTP, which the hub can log.
# With an API key instead, use `openai_base_url = "http://127.0.0.1:4000/openai/v1"` in ~/.codex/config.toml.
codex() {
  if _tokenhub_up; then
    command codex -c "model_providers.tokenhub={name=\"tokenhub\",base_url=\"$(_tokenhub_url)/chatgpt\",wire_api=\"responses\",requires_openai_auth=true,supports_websockets=false}" -c 'model_provider="tokenhub"' "$@"
  else command codex "$@"; fi
}
