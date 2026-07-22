#!/usr/bin/env sh
set -eu
INSTALL_DIR=${INSTALL_DIR:-"$HOME/.local/bin"}
rm -f "$INSTALL_DIR/relay" "$INSTALL_DIR/relay.pyz"
echo "Relay executable removed from $INSTALL_DIR"
echo "Relay data was preserved. Delete RELAY_HOME manually if desired."
