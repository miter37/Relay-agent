#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYZ="$ROOT/relay.pyz"
OS=$(uname -s)

case "$OS" in
  Darwin)
    DEFAULT_HOME="$HOME/Library/Application Support/Relay"
    ;;
  *)
    DEFAULT_HOME="$HOME/.relay"
    ;;
esac

INSTALL_DIR=${INSTALL_DIR:-"$HOME/.local/bin"}
RELAY_HOME=${RELAY_HOME:-"$DEFAULT_HOME"}
PYTHON=${PYTHON:-python3}

if [ ! -f "$PYZ" ]; then
  echo "relay.pyz not found: $PYZ" >&2
  exit 1
fi

"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)' || {
  echo "Python 3.11+ is required." >&2
  exit 1
}

mkdir -p "$INSTALL_DIR" "$RELAY_HOME"
cp "$PYZ" "$INSTALL_DIR/relay.pyz"
chmod 755 "$INSTALL_DIR/relay.pyz"

cat > "$INSTALL_DIR/relay" <<EOF
#!/usr/bin/env sh
export RELAY_HOME='$(printf "%s" "$RELAY_HOME" | sed "s/'/'\\\\''/g")'
exec '$PYTHON' '$INSTALL_DIR/relay.pyz' "\$@"
EOF
chmod 755 "$INSTALL_DIR/relay"

RELAY_HOME="$RELAY_HOME" "$PYTHON" "$INSTALL_DIR/relay.pyz" init

printf '\nRelay installed.\n'
printf '  command: %s/relay\n' "$INSTALL_DIR"
printf '  home:    %s\n' "$RELAY_HOME"
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    printf '\n%s is not currently on PATH. Add this line to your shell profile:\n' "$INSTALL_DIR"
    printf '  export PATH="%s:$PATH"\n' "$INSTALL_DIR"
    ;;
esac
printf '\nNext:\n'
printf '  relay doctor --worker claude --deep\n'
printf '  relay doctor --worker codex --deep\n'
