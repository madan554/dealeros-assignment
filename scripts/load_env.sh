# Sourced by the other scripts. Loads .env as *defaults*, never as overrides.
#
# This exists because the obvious one-liner is subtly wrong:
#
#     set -a; . ./.env; set +a
#
# That lets .env clobber the environment, whereas Django's `load_dotenv` does
# the opposite and leaves an already-set variable alone. With both in play,
# `POSTGRES_DB=other make setup` creates the database named in .env and then
# migrates against the one named in the environment, and the error you get is
# "database does not exist" pointing at a database you did ask for.
#
# So: a variable already set in the environment wins, and .env fills the rest.

load_env_defaults() {
  local file="${1:-.env}"
  [ -f "$file" ] || return 0

  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    line="${line#export }"
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac

    key="${line%%=*}"
    value="${line#*=}"

    # Trim surrounding whitespace from the name, and matching quotes from the
    # value, the way a .env reader is expected to.
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    [ -n "$key" ] || continue
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac

    # Already set (including deliberately set to empty)? Leave it alone.
    if [ -z "${!key+x}" ]; then
      export "$key=$value"
    fi
  done < "$file"
}
