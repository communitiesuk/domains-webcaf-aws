#!/bin/bash
#Wrapper file to pass the environment variables for the mypy pre-commit command

export SSO_MODE=none
export DEBUG=True

# macOS strips DYLD_* variables when starting the system Bash used by this
# wrapper. Restore Homebrew's native library path so WeasyPrint can load Pango.
if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
fi

exec mypy "$@"
