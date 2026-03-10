"""Helpers for user session state persisted in Redis."""

import json
import os

from ui_constants import ATTRIBUTE_COLUMNS


def safe_parse_json(raw_value, default_value):
    """Parse JSON safely and return a default value on parse failure."""
    if not raw_value:
        return default_value
    try:
        return json.loads(raw_value)
    except (TypeError, ValueError):
        return default_value


def temp_selection_key(user_id):
    """Build the Redis key for temporary pair selections."""
    return f"{user_id}_temp_user_selections"


def load_temp_selections(redis_client, user_id):
    """Load temporary selections for a user from Redis."""
    if not user_id:
        return []

    raw = redis_client.get(temp_selection_key(user_id))
    parsed = safe_parse_json(raw, [])
    return parsed if isinstance(parsed, list) else []


def save_temp_selections(redis_client, user_id, selections):
    """Persist temporary selections for a user to Redis."""
    if user_id:
        redis_client.set(temp_selection_key(user_id), json.dumps(selections))


def extract_user_id_from_response_key(key):
    """Extract the user id from a key like id:<user>___file:<file>."""
    if "id:" not in key or "___file:" not in key:
        return None
    return key.split("id:", 1)[1].split("___file:", 1)[0]


def extract_file_part_from_response_key(key):
    """Extract the file component from a response key."""
    if "___file:" not in key:
        return ""
    return key.split("___file:", 1)[1].strip()


def get_snapshot_key_for_response_key(response_key):
    """Build the snapshot key used to store submission metadata."""
    return response_key + "___snapshot"


def get_response_keys_for_filename(redis_client, filename):
    """Return response keys that match a dataset filename."""
    candidate_keys = list(redis_client.scan_iter("id:*___file:*"))
    if not candidate_keys:
        return []

    requested = filename.strip()
    requested_base = os.path.basename(requested)
    matched = []

    for key in candidate_keys:
        file_part = extract_file_part_from_response_key(key)
        file_base = os.path.basename(file_part)

        if (
            file_part == requested
            or file_base == requested_base
            or file_part.endswith("/" + requested_base)
            or file_part.endswith(requested)
        ):
            matched.append(key)

    return sorted(set(matched))


def get_pair_numbers(data_pairs):
    """Return ordered pair numbers from raw pair rows."""
    return [str(data_pairs[i][0]) for i in range(0, len(data_pairs), 2)]


def get_partial_level_flags(data_pair_list):
    """Determine whether each attribute supports partial display for each pair."""
    partial_flags = []

    for pair_index in range(len(data_pair_list.get_ids()) // 2):
        data_pair = data_pair_list.get_data_pair_by_index(pair_index)
        attr_flags = []
        for attr_index in range(len(ATTRIBUTE_COLUMNS)):
            next_mode = data_pair.get_next_display(attr_index, "M")[0]
            attr_flags.append(next_mode == "partial")
        partial_flags.append(attr_flags)

    return partial_flags


def status_to_level(status, has_partial_level):
    """Map attribute display state to an integer level for reporting."""
    if status == "P":
        return 1
    if status == "F":
        return 2 if has_partial_level else 1
    return 0


def selection_to_response_label(selection):
    """Normalize response selection labels to valid response codes."""
    if not selection:
        return ""

    normalized = str(selection).strip()
    return normalized if normalized in ["1", "2", "3", "4", "5", "6"] else ""


def build_pair_reveal_levels(redis_client, user_id, pair_numbers, partial_level_flags):
    """Build reveal level snapshot for each pair and attribute."""
    pair_reveal_levels = {}

    for pair_index, pair_number in enumerate(pair_numbers):
        pair_num = pair_index + 1
        attr_levels = []

        for attr_index in range(len(ATTRIBUTE_COLUMNS)):
            key_row_1 = f"{user_id}-{pair_number}-1-{attr_index}"
            key_row_2 = f"{user_id}-{pair_number}-2-{attr_index}"

            status_1 = redis_client.get(key_row_1) or "M"
            status_2 = redis_client.get(key_row_2) or "M"

            has_partial_level = partial_level_flags[pair_index][attr_index]
            level_1 = status_to_level(status_1, has_partial_level)
            level_2 = status_to_level(status_2, has_partial_level)
            attr_levels.append(max(level_1, level_2))

        pair_reveal_levels[str(pair_num)] = attr_levels

    return pair_reveal_levels
