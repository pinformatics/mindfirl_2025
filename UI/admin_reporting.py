"""Admin reporting helpers for CSV export and graph payload generation."""

import data_loader as dl
import data_model as dm

from ui_constants import ATTRIBUTE_COLUMNS
from user_state import (
    extract_user_id_from_response_key,
    get_pair_numbers,
    get_partial_level_flags,
    get_response_keys_for_filename,
    get_snapshot_key_for_response_key,
    safe_parse_json,
    selection_to_response_label,
    status_to_level,
)


def build_redis_csv_rows(redis_client, filename):
    """Build export rows by combining Redis state with dataset metadata."""
    data_pairs = dl.load_data_from_csv(filename)
    data_pair_list = dm.DataPairList(data_pairs)
    pair_numbers = get_pair_numbers(data_pairs)
    partial_level_flags = get_partial_level_flags(data_pair_list)

    response_keys = get_response_keys_for_filename(redis_client, filename)
    response_entries = []
    for key in response_keys:
        user_id = extract_user_id_from_response_key(key)
        if user_id:
            response_entries.append((key, user_id))

    response_values = [redis_client.get(key) for key, _ in response_entries] if response_entries else []

    rows = []
    for student_index, (response_key, user_id) in enumerate(response_entries):
        selection_string = response_values[student_index] or ""
        selections = selection_string.split(",") if selection_string else []

        snapshot_key = get_snapshot_key_for_response_key(response_key)
        snapshot = safe_parse_json(redis_client.get(snapshot_key), {})
        snapshot_reveal_levels = snapshot.get("pair_reveal_levels", {})

        if "character_disclosed_percent_value" in snapshot:
            character_disclosed_percent_value = float(snapshot.get("character_disclosed_percent_value", 0.0))
        else:
            disclosed_characters = int(redis_client.get(user_id + "_mindfil_disclosed_characters") or 0)
            total_characters = int(redis_client.get(user_id + "_mindfil_total_characters") or 0)
            character_disclosed_percent_value = 0.0
            if total_characters > 0:
                character_disclosed_percent_value = round(100.0 * disclosed_characters / total_characters, 1)

        if "privacy_risk_percent_value" in snapshot:
            privacy_risk_percent_value = float(snapshot.get("privacy_risk_percent_value", 0.0))
        else:
            kapr_value = float(redis_client.get(user_id + "_KAPR") or 0.0)
            privacy_risk_percent_value = round(100.0 * kapr_value, 1)

        row = {
            "student_index": student_index + 1,
            "student_id": user_id,
            "datetime": snapshot.get("saved_at", ""),
            "character_disclosed_percent_value": character_disclosed_percent_value,
            "privacy_risk_percent_value": privacy_risk_percent_value,
        }

        for pair_index, pair_number in enumerate(pair_numbers):
            pair_num = pair_index + 1
            selection = selections[pair_index].strip() if pair_index < len(selections) else ""
            row[f"pair_{pair_num}_response"] = selection_to_response_label(selection)

            snapshot_pair_levels = snapshot_reveal_levels.get(str(pair_num), [])

            for attr_index, (_, column_name) in enumerate(ATTRIBUTE_COLUMNS):
                key = f"pair_{pair_num}_{column_name}"
                if attr_index < len(snapshot_pair_levels):
                    row[key] = int(snapshot_pair_levels[attr_index])
                    continue

                key_row_1 = f"{user_id}-{pair_number}-1-{attr_index}"
                key_row_2 = f"{user_id}-{pair_number}-2-{attr_index}"

                status_1 = redis_client.get(key_row_1) or "M"
                status_2 = redis_client.get(key_row_2) or "M"

                has_partial_level = partial_level_flags[pair_index][attr_index]
                level_1 = status_to_level(status_1, has_partial_level)
                level_2 = status_to_level(status_2, has_partial_level)
                row[key] = max(level_1, level_2)

        rows.append(row)

    return rows, pair_numbers


def build_redis_csv_fieldnames(pair_numbers):
    """Return ordered CSV columns for the admin export."""
    fieldnames = ["student_index", "student_id", "datetime"]

    for pair_index, _ in enumerate(pair_numbers):
        pair_num = pair_index + 1
        for _, column_name in ATTRIBUTE_COLUMNS:
            fieldnames.append(f"pair_{pair_num}_{column_name}")

    for pair_index, _ in enumerate(pair_numbers):
        pair_num = pair_index + 1
        fieldnames.append(f"pair_{pair_num}_response")

    fieldnames.extend(["character_disclosed_percent_value", "privacy_risk_percent_value"])
    return fieldnames


def build_graph_payload(rows, pair_numbers):
    """Build chart payloads used by the admin graph template."""
    pair_labels = [f"Pair {index + 1}" for index in range(len(pair_numbers))]
    reveal_datasets = []
    reveal_distribution = []
    reveal_distribution_cutoff = 10
    reveal_type_labels = [label for label, _ in ATTRIBUTE_COLUMNS]
    reveal_type_raw_values = []
    reveal_type_stats = []
    reveal_pair_labels = [f"Pair {index + 1}" for index in range(len(pair_numbers))]
    reveal_pair_raw_values = []
    reveal_pair_stats = []

    def _percentile(sorted_values, p):
        if not sorted_values:
            return 0.0
        if len(sorted_values) == 1:
            return float(sorted_values[0])

        rank = (len(sorted_values) - 1) * p
        lower = int(rank)
        upper = min(lower + 1, len(sorted_values) - 1)
        weight = rank - lower
        return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)

    for _, column_name in ATTRIBUTE_COLUMNS:
        values = []
        raw_values_by_pair = []
        stats_by_pair = []

        for pair_index, _ in enumerate(pair_numbers):
            pair_num = pair_index + 1
            key = f"pair_{pair_num}_{column_name}"
            avg_value = sum(float(row.get(key, 0) or 0) for row in rows) / len(rows) if rows else 0
            values.append(round(avg_value, 2))

            pair_values = []
            for row in rows:
                raw_level = row.get(key, 0)
                try:
                    level = int(raw_level)
                except (TypeError, ValueError):
                    level = 0
                level = min(max(level, 0), 2)
                pair_values.append(level)

            sorted_values = sorted(pair_values)
            if sorted_values:
                min_value = float(sorted_values[0])
                max_value = float(sorted_values[-1])
                q1 = _percentile(sorted_values, 0.25)
                median = _percentile(sorted_values, 0.5)
                q3 = _percentile(sorted_values, 0.75)
            else:
                min_value = 0.0
                max_value = 0.0
                q1 = 0.0
                median = 0.0
                q3 = 0.0

            raw_values_by_pair.append([float(v) for v in pair_values])
            stats_by_pair.append(
                {
                    "min": round(min_value, 2),
                    "max": round(max_value, 2),
                    "q1": round(q1, 2),
                    "median": round(median, 2),
                    "q3": round(q3, 2),
                    "avg": round(avg_value, 2),
                    "count": len(pair_values),
                }
            )

        reveal_datasets.append({"label": column_name, "data": values})
        reveal_distribution.append(
            {
                "label": column_name,
                "raw_values": raw_values_by_pair,
                "stats": stats_by_pair,
            }
        )

    # Field-level distributions pooled across all pairs and students for clearer comparison.
    for _, column_name in ATTRIBUTE_COLUMNS:
        field_values = []

        for row in rows:
            for pair_index, _ in enumerate(pair_numbers):
                pair_num = pair_index + 1
                key = f"pair_{pair_num}_{column_name}"
                raw_level = row.get(key, 0)
                try:
                    level = int(raw_level)
                except (TypeError, ValueError):
                    level = 0
                level = min(max(level, 0), 2)
                field_values.append(level)

        sorted_values = sorted(field_values)
        if sorted_values:
            min_value = float(sorted_values[0])
            max_value = float(sorted_values[-1])
            q1 = _percentile(sorted_values, 0.25)
            median = _percentile(sorted_values, 0.5)
            q3 = _percentile(sorted_values, 0.75)
            avg_value = sum(float(v) for v in sorted_values) / len(sorted_values)
        else:
            min_value = 0.0
            max_value = 0.0
            q1 = 0.0
            median = 0.0
            q3 = 0.0
            avg_value = 0.0

        reveal_type_raw_values.append([float(v) for v in field_values])
        reveal_type_stats.append(
            {
                "min": round(min_value, 2),
                "max": round(max_value, 2),
                "q1": round(q1, 2),
                "median": round(median, 2),
                "q3": round(q3, 2),
                "avg": round(avg_value, 2),
                "count": len(field_values),
            }
        )

    # Pair-level distributions pooled across all fields and students.
    for pair_index, _ in enumerate(pair_numbers):
        pair_num = pair_index + 1
        pair_values = []

        for row in rows:
            for _, column_name in ATTRIBUTE_COLUMNS:
                key = f"pair_{pair_num}_{column_name}"
                raw_level = row.get(key, 0)
                try:
                    level = int(raw_level)
                except (TypeError, ValueError):
                    level = 0
                level = min(max(level, 0), 2)
                pair_values.append(level)

        sorted_values = sorted(pair_values)
        if sorted_values:
            min_value = float(sorted_values[0])
            max_value = float(sorted_values[-1])
            q1 = _percentile(sorted_values, 0.25)
            median = _percentile(sorted_values, 0.5)
            q3 = _percentile(sorted_values, 0.75)
            avg_value = sum(float(v) for v in sorted_values) / len(sorted_values)
        else:
            min_value = 0.0
            max_value = 0.0
            q1 = 0.0
            median = 0.0
            q3 = 0.0
            avg_value = 0.0

        reveal_pair_raw_values.append([float(v) for v in pair_values])
        reveal_pair_stats.append(
            {
                "min": round(min_value, 2),
                "max": round(max_value, 2),
                "q1": round(q1, 2),
                "median": round(median, 2),
                "q3": round(q3, 2),
                "avg": round(avg_value, 2),
                "count": len(pair_values),
            }
        )

    response_labels = ["1", "2", "3", "4", "5", "6"]
    response_counts = {label: 0 for label in response_labels}
    pair_response_counts = {label: [0 for _ in pair_numbers] for label in response_labels}

    for row in rows:
        for pair_index, _ in enumerate(pair_numbers):
            pair_num = pair_index + 1
            response_key = f"pair_{pair_num}_response"
            response_value = str(row.get(response_key, "")).strip()
            if response_value in response_counts:
                response_counts[response_value] += 1
                pair_response_counts[response_value][pair_index] += 1

    field_level_counts = {
        column_name: {"0": 0, "1": 0, "2": 0}
        for _, column_name in ATTRIBUTE_COLUMNS
    }

    for row in rows:
        for pair_index, _ in enumerate(pair_numbers):
            pair_num = pair_index + 1
            for _, column_name in ATTRIBUTE_COLUMNS:
                key = f"pair_{pair_num}_{column_name}"
                raw_level = row.get(key, 0)
                try:
                    level = int(raw_level)
                except (TypeError, ValueError):
                    level = 0
                level = min(max(level, 0), 2)
                field_level_counts[column_name][str(level)] += 1

    student_labels = []
    student_character_disclosed = []
    student_privacy_risk = []
    for row in rows:
        student_labels.append(str(row.get("student_index", len(student_labels) + 1)))
        student_character_disclosed.append(float(row.get("character_disclosed_percent_value", 0) or 0))
        student_privacy_risk.append(float(row.get("privacy_risk_percent_value", 0) or 0))

    if rows:
        avg_character_disclosed = round(
            sum(float(row.get("character_disclosed_percent_value", 0) or 0) for row in rows) / len(rows),
            2,
        )
        avg_privacy_risk = round(
            sum(float(row.get("privacy_risk_percent_value", 0) or 0) for row in rows) / len(rows),
            2,
        )
    else:
        avg_character_disclosed = 0
        avg_privacy_risk = 0

    # Paper-aligned metrics:
    # 1) Privacy-utility frontier: pair-level disclosure vs consensus rate
    # 2) Field disclosure contribution: per-field disclosure pressure
    # 3) Response distribution by pair difficulty (hard/medium/easy)
    # 4) Budget efficiency summary
    privacy_utility_frontier_points = []
    difficulty_buckets = {
        "Hard": {label: 0 for label in response_labels},
        "Medium": {label: 0 for label in response_labels},
        "Easy": {label: 0 for label in response_labels},
    }
    reviewed_pair_count = 0
    high_consensus_pair_count = 0

    for pair_index, _ in enumerate(pair_numbers):
        pair_label = pair_labels[pair_index]
        response_vector = [pair_response_counts[label][pair_index] for label in response_labels]
        response_total = sum(response_vector)

        if response_total > 0:
            reviewed_pair_count += 1

        max_votes = max(response_vector) if response_vector else 0
        consensus_rate = round((100.0 * max_votes / response_total), 2) if response_total > 0 else 0.0
        if consensus_rate >= 75.0 and response_total > 0:
            high_consensus_pair_count += 1

        pair_avg_reveal_level = 0.0
        if pair_index < len(reveal_pair_stats):
            pair_avg_reveal_level = float(reveal_pair_stats[pair_index].get("avg", 0.0) or 0.0)
        disclosure_percent = round((pair_avg_reveal_level / 2.0) * 100.0, 2)

        privacy_utility_frontier_points.append(
            {
                "pair_label": pair_label,
                "disclosure_percent": disclosure_percent,
                "consensus_percent": consensus_rate,
                "response_count": response_total,
            }
        )

        if consensus_rate < 50.0:
            bucket = "Hard"
        elif consensus_rate < 75.0:
            bucket = "Medium"
        else:
            bucket = "Easy"

        for label, count in zip(response_labels, response_vector):
            difficulty_buckets[bucket][label] += count

    field_contribution_labels = []
    field_disclosure_pressure = []
    field_partial_or_full_exposure = []
    column_display_names = {column_name: label for label, column_name in ATTRIBUTE_COLUMNS}

    for _, column_name in ATTRIBUTE_COLUMNS:
        level_zero = field_level_counts[column_name]["0"]
        level_one = field_level_counts[column_name]["1"]
        level_two = field_level_counts[column_name]["2"]
        total = level_zero + level_one + level_two

        if total > 0:
            avg_level = (level_one + 2 * level_two) / float(total)
            disclosure_pressure = round((avg_level / 2.0) * 100.0, 2)
            partial_or_full = round((100.0 * (level_one + level_two) / float(total)), 2)
        else:
            disclosure_pressure = 0.0
            partial_or_full = 0.0

        field_contribution_labels.append(column_display_names.get(column_name, column_name))
        field_disclosure_pressure.append(disclosure_pressure)
        field_partial_or_full_exposure.append(partial_or_full)

    pair_total_count = len(pair_numbers)
    reviewed_pair_rate = round((100.0 * reviewed_pair_count / pair_total_count), 2) if pair_total_count > 0 else 0.0
    high_consensus_pair_rate = (
        round((100.0 * high_consensus_pair_count / reviewed_pair_count), 2)
        if reviewed_pair_count > 0
        else 0.0
    )

    preferred_difficulty_order = ["Hard", "Medium", "Easy"]
    difficulty_labels = [
        level
        for level in preferred_difficulty_order
        if sum(difficulty_buckets[level].values()) > 0
    ]
    if not difficulty_labels:
        difficulty_labels = preferred_difficulty_order

    difficulty_response_datasets = [
        {"label": label, "data": [difficulty_buckets[level][label] for level in difficulty_labels]}
        for label in response_labels
    ]

    return {
        "pair_labels": pair_labels,
        "reveal_datasets": reveal_datasets,
        "reveal_distribution": reveal_distribution,
        "reveal_distribution_cutoff": reveal_distribution_cutoff,
        "reveal_type_labels": reveal_type_labels,
        "reveal_type_raw_values": reveal_type_raw_values,
        "reveal_type_stats": reveal_type_stats,
        "reveal_pair_labels": reveal_pair_labels,
        "reveal_pair_raw_values": reveal_pair_raw_values,
        "reveal_pair_stats": reveal_pair_stats,
        "student_count": len(rows),
        "response_labels": response_labels,
        "response_counts": [response_counts[label] for label in response_labels],
        "pair_response_datasets": [
            {"label": label, "data": pair_response_counts[label]}
            for label in response_labels
        ],
        "field_level_labels": [column_name for _, column_name in ATTRIBUTE_COLUMNS],
        "field_level_zero": [field_level_counts[column_name]["0"] for _, column_name in ATTRIBUTE_COLUMNS],
        "field_level_one": [field_level_counts[column_name]["1"] for _, column_name in ATTRIBUTE_COLUMNS],
        "field_level_two": [field_level_counts[column_name]["2"] for _, column_name in ATTRIBUTE_COLUMNS],
        "student_labels": student_labels,
        "student_character_disclosed": student_character_disclosed,
        "student_privacy_risk": student_privacy_risk,
        "avg_character_disclosed": avg_character_disclosed,
        "avg_privacy_risk": avg_privacy_risk,
        "privacy_utility_frontier_points": privacy_utility_frontier_points,
        "field_contribution_labels": field_contribution_labels,
        "field_disclosure_pressure": field_disclosure_pressure,
        "field_partial_or_full_exposure": field_partial_or_full_exposure,
        "difficulty_labels": difficulty_labels,
        "difficulty_response_datasets": difficulty_response_datasets,
        "budget_efficiency_labels": [
            "Avg Character Disclosure %",
            "High-Consensus Pair Rate %",
            "Pairs Reviewed %",
        ],
        "budget_efficiency_values": [
            avg_character_disclosed,
            high_consensus_pair_rate,
            reviewed_pair_rate,
        ],
    }


def build_pair_record_details(filename):
    """Return row-level pair details to support hover drill-down on graph page."""
    data_pairs = dl.load_data_from_csv(filename)
    pair_details = []

    for index in range(0, len(data_pairs), 2):
        row_a = data_pairs[index]
        row_b = data_pairs[index + 1] if index + 1 < len(data_pairs) else ["" for _ in row_a]

        pair_details.append(
            {
                "pair_label": "Pair {}".format((index // 2) + 1),
                "pair_number": row_a[0] if row_a else "",
                "record_a": {
                    "id": row_a[1] if len(row_a) > 1 else "",
                    "ffreq": row_a[2] if len(row_a) > 2 else "",
                    "first_name": row_a[3] if len(row_a) > 3 else "",
                    "last_name": row_a[4] if len(row_a) > 4 else "",
                    "lfreq": row_a[5] if len(row_a) > 5 else "",
                    "dob": row_a[6] if len(row_a) > 6 else "",
                    "sex": row_a[7] if len(row_a) > 7 else "",
                    "race": row_a[8] if len(row_a) > 8 else "",
                },
                "record_b": {
                    "id": row_b[1] if len(row_b) > 1 else "",
                    "ffreq": row_b[2] if len(row_b) > 2 else "",
                    "first_name": row_b[3] if len(row_b) > 3 else "",
                    "last_name": row_b[4] if len(row_b) > 4 else "",
                    "lfreq": row_b[5] if len(row_b) > 5 else "",
                    "dob": row_b[6] if len(row_b) > 6 else "",
                    "sex": row_b[7] if len(row_b) > 7 else "",
                    "race": row_b[8] if len(row_b) > 8 else "",
                },
            }
        )

    return pair_details


def process_redis_data(redis_client, filename):
    """Build per-pair aggregated selection summaries for results view."""
    data_pairs = dl.load_data_from_csv(filename)

    filename_keys = get_response_keys_for_filename(redis_client, filename)
    html_elements_list = []
    value_data = [[0, 0, 0, 0, 0, 0, 0] for _ in range(len(data_pairs))]

    if filename_keys:
        filename_values = [redis_client.get(key) for key in filename_keys] or []

        num_pairs = len(filename_values[0].split(","))
        data_len = len(data_pairs) / 2
        if num_pairs != data_len:
            raise Exception("The lengths of the dataset and responses are not aligned")

        value_data = [[0, 0, 0, 0, 0, 0, 0] for _ in range(num_pairs)]

        for selection_string in filename_values:
            selections = selection_string.split(",")
            for selection_index, selection in enumerate(selections):
                if selection == "":
                    value_data[selection_index][0] += 1
                else:
                    value_data[selection_index][int(selection)] += 1

    for selection_counts in value_data:
        render_values = [
            selection_counts[1] + selection_counts[2] + selection_counts[3],
            selection_counts[4] + selection_counts[5] + selection_counts[6],
        ] + [selection_counts[i] for i in range(1, 7)]

        selection_element = """
            <div id="overall"
            style=
            "
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: space-around;
            margin-top: 4%;
            ">

                <div id="simple-breakdown" style="display: flex; flex-direction: row; justify-content: space-around; align-items: center; font-size: 1.2em; width: 80%">
                    <div id="different-simple" style="flex: 1;">
                        <div>Different</div>
                        <div>{}</div>
                    </div>
                    <div id="same-simple" style="flex: 1">
                        <div>Same</div>
                        <div>{}</div>
                    </div>
                </div>
                <div id="verbose-breakdown" style="display: flex; flex-direction: column; align-items: center; font-size: 0.8em; width: 100%; margin: 2%">
                    <div id="verbose-labels" style="display: flex; flex-direction: row; justify-content: space-between; align-items: center; width: 50%">
                        <span>H</span>
                        <span>M</span>
                        <span>L</span>
                        <span>L</span>
                        <span>M</span>
                        <div>H</div>
                    </div>
                    <div id="verbose-values" style="display: inline-flex; flex-direction: row; justify-content: space-between; width: 50%">
                        <span>{}</span>
                        <span>{}</span>
                        <span>{}</span>
                        <span>{}</span>
                        <span>{}</span>
                        <span>{}</span>
                    </div>
                </div>
            </div>
        """.format(*render_values)

        html_elements_list.append(selection_element)

    return data_pairs, html_elements_list
