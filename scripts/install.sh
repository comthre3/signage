#!/usr/bin/env bash
#
# Kept for compatibility: the README has pointed here since the project began.
# The real work now lives in redeploy.sh, which additionally validates every
# prerequisite and verifies the stack actually came up.
#
# The previous version of this script wrote a .env that no longer matched the
# project -- sqlite instead of Postgres, no POSTGRES_PASSWORD, an 8-character
# ADMIN_PASSWORD the backend rejects, and wildcard CORS that was removed as a
# pentest finding -- so following it produced a stack that could not start.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/redeploy.sh" "$@"
