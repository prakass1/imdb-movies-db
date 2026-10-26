#!/usr/bin/env bash
# scripts/bob_sync_trigger.sh
# ----------------------------
# Trigger the GitHub Schema Sync Bot from a Bob Shell session,
# git post-merge hook, or CI/CD pipeline.
#
# Usage:
#   ./scripts/bob_sync_trigger.sh [OPTIONS]
#
# Options:
#   --dry-run            Simulate GitHub calls without network side-effects.
#   --simulate-1         Run built-in Scenario 1 (column rename + drop).
#   --simulate-2         Run built-in Scenario 2 (new table addition).
#   --base-ref REF       Git ref for prior DDL state (default: HEAD~1).
#   --target-sql FILE    Path to current DDL file (default: db/movies.sql).
#   --commit-sha SHA     Override commit SHA.
#   --mr-ref REF         MR/PR reference number or URL.
#   --verbose            Enable verbose logging.
#   --quiet              Suppress non-error output.
#   --help               Show this help and exit.
#
# Environment variables:
#   GITHUB_TOKEN         Personal Access Token for GitHub API calls.
#   GITHUB_OWNER         GitHub owner / organisation name.
#   MOVIES_DB_REPO       Source repository name (default: movies_db).
#   MOVIES_ETL_REPO      Target ETL repository name (default: movies_etl).
#
# Examples:
#   # Dry-run against HEAD~1
#   ./scripts/bob_sync_trigger.sh --dry-run
#
#   # Scenario 1 simulation (no real GitHub calls)
#   ./scripts/bob_sync_trigger.sh --simulate-1 --dry-run
#
#   # Real run pointing at a specific base ref
#   GITHUB_TOKEN=ghp_xxx GITHUB_OWNER=myorg ./scripts/bob_sync_trigger.sh --base-ref v1.0.0

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory and project root
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DRY_RUN_FLAG=""
SIM_FLAG=""
BASE_REF_ARG=""
TARGET_SQL_ARG=""
COMMIT_SHA_ARG=""
MR_REF_ARG=""
VERBOSE_FLAG=""
QUIET_FLAG=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN_FLAG="--dry-run"
            shift
            ;;
        --simulate-1)
            SIM_FLAG="--simulate-scenario-1"
            shift
            ;;
        --simulate-2)
            SIM_FLAG="--simulate-scenario-2"
            shift
            ;;
        --base-ref)
            BASE_REF_ARG="--base-ref $2"
            shift 2
            ;;
        --target-sql)
            TARGET_SQL_ARG="--target-sql $2"
            shift 2
            ;;
        --commit-sha)
            COMMIT_SHA_ARG="--commit-sha $2"
            shift 2
            ;;
        --mr-ref)
            MR_REF_ARG="--mr-ref $2"
            shift 2
            ;;
        --verbose)
            VERBOSE_FLAG="--verbose"
            shift
            ;;
        --quiet)
            QUIET_FLAG="--quiet"
            shift
            ;;
        --help|-h)
            grep '^#' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Environment pre-flight checks
# ---------------------------------------------------------------------------
if [[ -z "${GITHUB_TOKEN:-}" && -z "${DRY_RUN_FLAG}" && -z "${SIM_FLAG}" ]]; then
    echo "[WARN] GITHUB_TOKEN is not set. Real GitHub API calls will fail." >&2
    echo "       Set GITHUB_TOKEN or use --dry-run / --simulate-1 / --simulate-2." >&2
fi

# ---------------------------------------------------------------------------
# Locate Python interpreter
# ---------------------------------------------------------------------------
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "[ERROR] Python interpreter not found in PATH." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
cd "${PROJECT_ROOT}"

# Build argument list (filter empty strings)
ARGS=()
[[ -n "${SIM_FLAG}" ]]        && ARGS+=("${SIM_FLAG}")
[[ -n "${BASE_REF_ARG}" ]]    && ARGS+=(${BASE_REF_ARG})
[[ -n "${TARGET_SQL_ARG}" ]]  && ARGS+=(${TARGET_SQL_ARG})
[[ -n "${COMMIT_SHA_ARG}" ]]  && ARGS+=(${COMMIT_SHA_ARG})
[[ -n "${MR_REF_ARG}" ]]      && ARGS+=(${MR_REF_ARG})
[[ -n "${DRY_RUN_FLAG}" ]]    && ARGS+=("${DRY_RUN_FLAG}")
[[ -n "${VERBOSE_FLAG}" ]]    && ARGS+=("${VERBOSE_FLAG}")
[[ -n "${QUIET_FLAG}" ]]      && ARGS+=("${QUIET_FLAG}")

echo "▶ sync_bot.py ${ARGS[*]:-}" >&2
"${PYTHON}" sync_bot.py "${ARGS[@]:-}"
