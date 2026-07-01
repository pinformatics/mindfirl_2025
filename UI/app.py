import csv
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from functools import wraps
from io import StringIO
from typing import cast

import redis
from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, session, url_for

import data_loader as dl
import data_model as dm
from admin_reporting import (
    build_graph_payload,
    build_pair_record_details,
    build_redis_csv_fieldnames,
    build_redis_csv_rows,
    process_redis_data,
    summarize_response_datetime_range,
)
from redis_factory import create_redis_client
from ui_constants import (
    ADMIN_LOGIN_LOCK_SECONDS,
    ADMIN_MAX_FAILED_ATTEMPTS,
    ATTRIBUTE_COLUMNS,
    DATA_PATH,
    PRIVACY_DATA_PATH,
    PRIVACY_SECTION2_PATH,
    SECTION2_PATH,
)
from user_state import (
    extract_user_id_from_response_key,
    build_pair_reveal_levels,
    get_response_keys_for_filename,
    get_pair_numbers,
    get_partial_level_flags,
    get_snapshot_key_for_response_key,
    load_temp_selections,
    safe_parse_json,
    save_temp_selections,
)

app = Flask(__name__)


def _env_flag(name, default=False):
    """Parse a boolean environment variable with a safe default."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


is_production = os.environ.get("APP_ENV", "").strip().lower() == "production"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", is_production),
)

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
if not FLASK_SECRET_KEY:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is required")
app.secret_key = FLASK_SECRET_KEY

_admin_password = os.environ.get("ADMIN_PASSWORD")
if not _admin_password:
    raise RuntimeError("ADMIN_PASSWORD environment variable is required")
ADMIN_PASSWORD: str = cast(str, _admin_password)

r = create_redis_client()
settings = dl.load_config_settings()
ADMIN_EXPERIMENT_LABELS_KEY = "admin:experiment_labels"


def _get_or_create_csrf_token():
    """Return a stable CSRF token for the active session."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_template_tokens():
    """Expose CSRF token to all templates."""
    return {"csrf_token": _get_or_create_csrf_token()}


def _is_valid_csrf_request():
    """Validate CSRF token from form or request headers."""
    expected = session.get("csrf_token")
    provided = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not provided:
        return False
    return secrets.compare_digest(expected, provided)


def admin_required(handler):
    """Protect admin endpoints with password auth and CSRF checks."""

    @wraps(handler)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin", False) and request.method == "POST":
            now = int(time.time())
            locked_until = int(session.get("admin_locked_until", 0) or 0)
            if locked_until and now < locked_until:
                return "Admin login temporarily locked. Please wait and try again.", 429

            provided_password = request.form.get("password") or ""
            if provided_password and secrets.compare_digest(provided_password, ADMIN_PASSWORD):
                session["is_admin"] = True
                session["admin_failed_attempts"] = 0
                session["admin_locked_until"] = 0
                _get_or_create_csrf_token()
            else:
                failed_attempts = int(session.get("admin_failed_attempts", 0) or 0) + 1
                session["admin_failed_attempts"] = failed_attempts
                if failed_attempts >= ADMIN_MAX_FAILED_ATTEMPTS:
                    session["admin_locked_until"] = now + ADMIN_LOGIN_LOCK_SECONDS
                return "Unauthorized access. Please provide the correct admin password.", 403

        if not session.get("is_admin", False):
            return "Unauthorized access. Please provide the correct admin password.", 403

        if request.method == "POST" and request.endpoint != "admin_page" and not _is_valid_csrf_request():
            return "CSRF validation failed.", 403

        return handler(*args, **kwargs)

    return decorated_function


def _build_results_context(user_id, disclosure_setting, data_path, section2_path):
    """Initialize a user session and return data needed for results templates."""
    data_pairs = dl.load_data_from_csv(data_path)
    dataset = dl.load_data_from_csv(section2_path)

    data_pair_list = dm.DataPairList(data_pairs)
    pairs_formatted = data_pair_list.get_data_display(disclosure_setting)
    ids_list = data_pair_list.get_ids()
    icons = data_pair_list.get_icons()[: (len(pairs_formatted) // 2)]

    data = list(zip(pairs_formatted[0::2], pairs_formatted[1::2]))
    ids = list(zip(ids_list[0::2], ids_list[1::2]))

    save_temp_selections(r, user_id, ["" for _ in range(len(pairs_formatted) // 2)])

    total_characters = data_pair_list.get_total_characters()
    r.set(user_id + "_mindfil_total_characters", total_characters)
    r.set(user_id + "_mindfil_disclosed_characters", 0)
    r.set(user_id + "_KAPR", 0)

    for id_row in ids_list:
        for attribute_index in range(6):
            r.set(user_id + "-" + id_row[attribute_index], "M")

    delta = []
    delta_cdp = []
    for pair_index in range(len(pairs_formatted) // 2):
        data_pair = data_pair_list.get_data_pair_by_index(pair_index)
        display_status = ["M", "M", "M", "M", "M", "M"]
        delta += dm.KAPR_delta(dataset, data_pair, display_status, len(data_pairs))
        delta_cdp += dm.cdp_delta(data_pair, display_status, 0, total_characters)

    choices_key = user_id + "_choices"
    previous_choices = r.get(choices_key)
    choices = json.loads(previous_choices) if previous_choices else {}

    return {
        "data": data,
        "ids": ids,
        "icons": icons,
        "delta": delta,
        "delta_cdp": delta_cdp,
        "choices": choices,
    }


def _display_results_page(data_path, section2_path, template_name, disclosure_setting):
    """Render an interactive results page and initialize user session keys."""
    user_id = request.cookies.get("user_id") or str(uuid.uuid4())

    try:
        context = _build_results_context(user_id, disclosure_setting, data_path, section2_path)
    except redis.ConnectionError as exc:
        return (
            "Redis connection failed: {}. Configure REDIS_URL in UI/.env or run a local Redis instance."
            .format(exc),
            500,
        )
    except Exception as exc:
        return "Can not open invalid or nonexistent file {} {} {}".format(data_path, exc, os.getcwd()), 500

    logging.error("display_results_page{}".format(user_id))

    response = make_response(
        render_template(
            template_name,
            data=context["data"],
            ids=context["ids"],
            title="Interactive Record Linkage",
            icons=context["icons"],
            delta=context["delta"],
            delta_cdp=context["delta_cdp"],
            mode="PPIRL",
            choices=context["choices"],
            marker_position=int(settings["privacy_budget"]),
        )
    )
    active_dataset = "privacy" if data_path == PRIVACY_DATA_PATH else "irl"
    response.set_cookie(
        "user_id",
        user_id,
        secure=app.config["SESSION_COOKIE_SECURE"],
        httponly=True,
        samesite=app.config["SESSION_COOKIE_SAMESITE"],
    )
    response.set_cookie(
        "active_dataset",
        active_dataset,
        secure=app.config["SESSION_COOKIE_SECURE"],
        httponly=True,
        samesite=app.config["SESSION_COOKIE_SAMESITE"],
    )
    return response


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_page():
    """Render admin dashboard."""
    # Prevent form-resubmission warnings when navigating back from admin subpages.
    if request.method == "POST":
        return redirect(url_for("admin_page"))
    return render_template("admin/dashboard.html")


def _parse_admin_time_window(raw_window):
    """Normalize supported admin response windows to days + display metadata."""
    normalized = str(raw_window or "all").strip().lower()
    mapping = {
        "1d": (1, "Last 1 Day"),
        "7d": (7, "Last 7 Days"),
        "30d": (30, "Last 30 Days"),
        "1y": (365, "Last 1 Year"),
        "all": (None, "All Time"),
    }
    days, label = mapping.get(normalized, mapping["all"])
    key = normalized if normalized in mapping else "all"
    return days, key, label


def _parse_admin_datetime_input(raw_value):
    """Parse flexible datetime query inputs as UTC-aware values."""
    if not raw_value:
        return None

    normalized = str(raw_value).strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_datetime_local_value(dt_value):
    """Format UTC datetime for datetime-local input fields."""
    if dt_value is None:
        return ""
    return dt_value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _resolve_ui_relative_path(path_value):
    """Resolve a UI-relative file path to an absolute filesystem path."""
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), path_value)


def _collect_experiment_names(redis_client, filename):
    """Return sorted non-empty experiment names from stored snapshots for a file."""
    names = set()
    try:
        stored_labels = redis_client.smembers(ADMIN_EXPERIMENT_LABELS_KEY)
        for label in stored_labels:
            normalized = str(label or "").strip()
            if normalized:
                names.add(normalized)
    except redis.RedisError:
        pass

    for response_key in get_response_keys_for_filename(redis_client, filename):
        snapshot_key = get_snapshot_key_for_response_key(response_key)
        snapshot = safe_parse_json(redis_client.get(snapshot_key), {})
        exp_name = str(snapshot.get("exp_name", "") or "").strip()
        if exp_name:
            names.add(exp_name)
    return sorted(names, key=lambda item: item.lower())


def _update_experiment_labels_for_range(
    redis_client,
    filename,
    new_exp_name,
    start_datetime=None,
    end_datetime=None,
    match_exp_name=None,
):
    """Update exp_name for snapshots in range; optionally require existing exp_name match."""
    updated = 0
    normalized_new = str(new_exp_name or "").strip()

    for response_key in get_response_keys_for_filename(redis_client, filename):
        snapshot_key = get_snapshot_key_for_response_key(response_key)
        snapshot = safe_parse_json(redis_client.get(snapshot_key), {})
        snapshot_datetime = _parse_admin_datetime_input(snapshot.get("saved_at", ""))
        if snapshot_datetime is None:
            continue

        if start_datetime is not None and snapshot_datetime < start_datetime:
            continue
        if end_datetime is not None and snapshot_datetime > end_datetime:
            continue

        existing_exp_name = str(snapshot.get("exp_name", "") or "").strip()
        if match_exp_name is not None and existing_exp_name != str(match_exp_name).strip():
            continue

        snapshot["exp_name"] = normalized_new
        redis_client.set(snapshot_key, json.dumps(snapshot))
        updated += 1

    return updated


def _build_experiment_summary(rows, experiment_names=None):
    """Build per-experiment counts and datetime coverage from report rows."""
    summary = {}
    for exp_name in experiment_names or []:
        normalized = str(exp_name or "").strip()
        if not normalized:
            continue
        summary.setdefault(
            normalized,
            {
                "exp_name": normalized,
                "count": 0,
                "min_datetime": None,
                "max_datetime": None,
            },
        )

    for row in rows:
        exp_name = str(row.get("exp_name", "") or "").strip()
        if not exp_name:
            continue

        dt_value = _parse_admin_datetime_input(row.get("datetime", ""))
        item = summary.setdefault(
            exp_name,
            {
                "exp_name": exp_name,
                "count": 0,
                "min_datetime": None,
                "max_datetime": None,
            },
        )
        item["count"] += 1
        if dt_value is not None:
            if item["min_datetime"] is None or dt_value < item["min_datetime"]:
                item["min_datetime"] = dt_value
            if item["max_datetime"] is None or dt_value > item["max_datetime"]:
                item["max_datetime"] = dt_value

    items = sorted(summary.values(), key=lambda entry: entry["exp_name"].lower())
    for entry in items:
        entry["min_iso"] = entry["min_datetime"].strftime("%Y-%m-%d %H:%M UTC") if entry["min_datetime"] else ""
        entry["max_iso"] = entry["max_datetime"].strftime("%Y-%m-%d %H:%M UTC") if entry["max_datetime"] else ""
        entry["min_local"] = _to_datetime_local_value(entry["min_datetime"])
        entry["max_local"] = _to_datetime_local_value(entry["max_datetime"])
    return items


def _build_experiment_range_timeline(rows, experiment_names=None):
    """Build horizontal date-range payload for experiments on separate y-axis bands."""
    palette = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#17becf",
        "#bcbd22",
        "#9467bd",
        "#8c564b",
    ]

    grouped = {}
    for name in experiment_names or []:
        normalized = str(name or "").strip()
        if normalized:
            grouped.setdefault(
                normalized,
                {
                    "label": normalized,
                    "count": 0,
                    "min_ts": None,
                    "max_ts": None,
                },
            )

    min_ts = None
    max_ts = None
    for row in rows:
        exp_name = str(row.get("exp_name", "") or "").strip()
        if not exp_name:
            continue
        dt_value = _parse_admin_datetime_input(row.get("datetime", ""))
        if dt_value is None:
            continue
        ts = int(dt_value.timestamp() * 1000)
        item = grouped.setdefault(
            exp_name,
            {
                "label": exp_name,
                "count": 0,
                "min_ts": None,
                "max_ts": None,
            },
        )
        item["count"] += 1
        if item["min_ts"] is None or ts < item["min_ts"]:
            item["min_ts"] = ts
        if item["max_ts"] is None or ts > item["max_ts"]:
            item["max_ts"] = ts

        if min_ts is None or ts < min_ts:
            min_ts = ts
        if max_ts is None or ts > max_ts:
            max_ts = ts

    ranges = []
    candidates = [item for item in grouped.values() if item["min_ts"] is not None and item["max_ts"] is not None]
    candidates.sort(key=lambda item: (item["min_ts"], -(item["max_ts"] - item["min_ts"]), item["label"].lower()))
    for idx, item in enumerate(candidates):
        ranges.append(
            {
                "label": item["label"],
                "count": item["count"],
                "start_ts": item["min_ts"],
                "end_ts": item["max_ts"],
                "color": palette[idx % len(palette)],
            }
        )

    return {
        "ranges": ranges,
        "has_ranges": bool(ranges),
        "min_ts": min_ts,
        "max_ts": max_ts,
    }


def _validate_uploaded_dataset_csv(csv_text):
    """Validate uploaded CSV against exported admin report format."""
    data_pairs = dl.load_data_from_csv(DATA_PATH)
    pair_numbers = get_pair_numbers(data_pairs)
    expected_fieldnames = build_redis_csv_fieldnames(pair_numbers)

    reader = csv.reader(StringIO(csv_text))
    rows = [row for row in reader if row and any(str(cell).strip() for cell in row)]
    if not rows:
        return False, "Not right format: empty CSV."

    header = [str(col).strip() for col in rows[0]]
    if header != expected_fieldnames:
        return (
            False,
            "Not right format: header mismatch. Expected exported report columns.",
        )

    data_rows = rows[1:]
    if not data_rows:
        return False, "Not right format: no data rows found."

    expected_columns = len(expected_fieldnames)
    for row_index, row in enumerate(data_rows, start=2):
        if len(row) != expected_columns:
            return (
                False,
                "Not right format: row {} has {} columns, expected {}.".format(
                    row_index,
                    len(row),
                    expected_columns,
                ),
            )

    return True, rows


def _import_uploaded_report_rows_to_redis(redis_client, csv_rows, pair_numbers):
    """Upsert uploaded admin report rows into Redis response/snapshot keys."""
    if not csv_rows:
        return 0, 0

    header = [str(col).strip() for col in csv_rows[0]]
    data_rows = csv_rows[1:]
    inserted = 0
    updated = 0

    for raw_row in data_rows:
        row = {header[index]: (raw_row[index] if index < len(raw_row) else "") for index in range(len(header))}
        student_id = str(row.get("student_id", "")).strip()
        if not student_id:
            continue

        response_key = "id:{}___file:{}".format(student_id, DATA_PATH)
        existed = bool(redis_client.exists(response_key))

        selections = []
        pair_reveal_levels = {}
        for pair_index, _ in enumerate(pair_numbers):
            pair_num = pair_index + 1
            response_value = str(row.get("pair_{}_response".format(pair_num), "")).strip()
            if response_value not in ["1", "2", "3", "4", "5", "6"]:
                response_value = ""
            selections.append(response_value)

            reveal_levels = []
            for _, column_name in ATTRIBUTE_COLUMNS:
                key = "pair_{}_{}".format(pair_num, column_name)
                raw_level = str(row.get(key, "0")).strip()
                try:
                    level_value = int(raw_level)
                except ValueError:
                    level_value = 0
                level_value = min(max(level_value, 0), 2)
                reveal_levels.append(level_value)
            pair_reveal_levels[str(pair_num)] = reveal_levels

        redis_client.set(response_key, ",".join(selections))

        snapshot = {
            "pair_reveal_levels": pair_reveal_levels,
            "character_disclosed_percent_value": float(row.get("character_disclosed_percent_value", 0.0) or 0.0),
            "privacy_risk_percent_value": float(row.get("privacy_risk_percent_value", 0.0) or 0.0),
            "exp_name": str(row.get("exp_name", "") or "").strip(),
            "saved_at": str(row.get("datetime", "")).strip() or datetime.utcnow().isoformat(),
        }
        redis_client.set(get_snapshot_key_for_response_key(response_key), json.dumps(snapshot))

        if existed:
            updated += 1
        else:
            inserted += 1

    return inserted, updated


@app.route("/admin/download_redis_data", methods=["POST"])
@admin_required
def generate_redis_csv():
    """Export all submitted responses and reveal levels as CSV."""
    rows, pair_numbers = build_redis_csv_rows(r, DATA_PATH)
    fieldnames = build_redis_csv_fieldnames(pair_numbers)

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=mindfirl_report_{}.csv".format(timestamp)},
    )


@app.route("/admin/upload_csv", methods=["GET"])
@admin_required
def upload_csv_page():
    """Render dedicated admin page for dataset CSV uploads."""
    return render_template("admin/upload_csv.html")


@app.route("/admin/upload_data_csv", methods=["POST"])
@admin_required
def upload_data_csv():
    """Upload an exported admin report CSV when format is valid."""
    uploaded = request.files.get("csv_file")
    if uploaded is None or not uploaded.filename:
        return redirect(
            url_for(
                "upload_csv_page",
                upload_status="error",
                upload_message="Not right format: choose a CSV file.",
            )
        )

    try:
        decoded_text = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return redirect(
            url_for(
                "upload_csv_page",
                upload_status="error",
                upload_message="Not right format: file must be UTF-8 CSV.",
            )
        )

    is_valid, result = _validate_uploaded_dataset_csv(decoded_text)
    if not is_valid:
        return redirect(
            url_for(
                "upload_csv_page",
                upload_status="error",
                upload_message=result,
            )
        )

    output_path = _resolve_ui_relative_path("data/uploaded_admin_report.csv")
    with open(output_path, "w", newline="", encoding="utf-8") as fileout:
        writer = csv.writer(fileout)
        for row in result:
            writer.writerow(row)

    pair_numbers = get_pair_numbers(dl.load_data_from_csv(DATA_PATH))
    inserted, updated = _import_uploaded_report_rows_to_redis(r, result, pair_numbers)

    return redirect(
        url_for(
            "upload_csv_page",
            upload_status="success",
            upload_message="CSV imported to Redis (inserted: {}, updated: {}).".format(inserted, updated),
        )
    )


@app.route("/admin/export_graph", methods=["GET"])
@admin_required
def export_graph_view():
    """Render admin analytics charts derived from submission data."""
    window_days, window_key, window_label = _parse_admin_time_window(request.args.get("window"))
    selected_exp_name = str(request.args.get("exp_name", "") or "").strip()
    custom_start = _parse_admin_datetime_input(request.args.get("start"))
    custom_end = _parse_admin_datetime_input(request.args.get("end"))
    has_custom_range = custom_start is not None or custom_end is not None
    if has_custom_range:
        window_key = "all"
        window_label = "Date Range"

    rows, pair_numbers = build_redis_csv_rows(
        r,
        DATA_PATH,
        time_window_days=window_days,
        start_datetime=custom_start,
        end_datetime=custom_end,
        experiment_name=selected_exp_name or None,
    )
    graph_data = build_graph_payload(rows, pair_numbers)
    graph_data["pair_record_details"] = build_pair_record_details(DATA_PATH)
    datetime_range = summarize_response_datetime_range(rows)
    return render_template(
        "admin/graph.html",
        title="Response Graphs",
        student_count=len(rows),
        graph_data=graph_data,
        window_key=window_key,
        window_label=window_label,
        min_datetime_display=datetime_range["min_iso"],
        max_datetime_display=datetime_range["max_iso"],
        has_datetime_range=bool(datetime_range["count_with_datetime"]),
        has_custom_range=has_custom_range,
        custom_start_value=_to_datetime_local_value(custom_start),
        custom_end_value=_to_datetime_local_value(custom_end),
        experiment_names=_collect_experiment_names(r, DATA_PATH),
        selected_exp_name=selected_exp_name,
    )


@app.route("/admin/experiments", methods=["GET"])
@admin_required
def experiments_page():
    """Render admin experiment management page."""
    rows, _ = build_redis_csv_rows(r, DATA_PATH)
    experiment_names = _collect_experiment_names(r, DATA_PATH)
    return render_template(
        "admin/experiments.html",
        title="Experiments",
        experiment_names=experiment_names,
        experiment_range_timeline=_build_experiment_range_timeline(rows, experiment_names=experiment_names),
        experiment_summary=_build_experiment_summary(rows, experiment_names=experiment_names),
        status=str(request.args.get("status", "") or ""),
        message=str(request.args.get("message", "") or ""),
    )


@app.route("/admin/experiments/create", methods=["POST"])
@admin_required
def create_experiment():
    """Create an experiment label and assign responses within a required datetime range."""
    exp_name = str(request.form.get("exp_name", "") or "").strip()
    start_dt = _parse_admin_datetime_input(request.form.get("start"))
    end_dt = _parse_admin_datetime_input(request.form.get("end"))

    if not exp_name:
        return redirect(url_for("experiments_page", status="error", message="Experiment name is required."))

    if start_dt is None or end_dt is None:
        return redirect(
            url_for(
                "experiments_page",
                status="error",
                message="Start and end datetimes are required to create an experiment.",
            )
        )

    if start_dt > end_dt:
        return redirect(url_for("experiments_page", status="error", message="Start datetime must be before end datetime."))

    r.sadd(ADMIN_EXPERIMENT_LABELS_KEY, exp_name)

    updated = _update_experiment_labels_for_range(
        r,
        DATA_PATH,
        exp_name,
        start_datetime=start_dt,
        end_datetime=end_dt,
    )
    return redirect(
        url_for(
            "experiments_page",
            status="success",
            message="Experiment '{}' created and {} data points assigned by range.".format(exp_name, updated),
        )
    )


@app.route("/admin/experiments/assign_range", methods=["POST"])
@admin_required
def assign_experiment_range():
    """Assign exp_name to responses within a datetime range."""
    exp_name = str(request.form.get("exp_name", "") or "").strip()
    original_exp_name = str(request.form.get("original_exp_name", "") or "").strip()
    start_dt = _parse_admin_datetime_input(request.form.get("start"))
    end_dt = _parse_admin_datetime_input(request.form.get("end"))

    if not exp_name:
        return redirect(url_for("experiments_page", status="error", message="Experiment name is required."))
    if start_dt is None or end_dt is None:
        return redirect(url_for("experiments_page", status="error", message="Start and end datetime are required."))
    if start_dt > end_dt:
        return redirect(url_for("experiments_page", status="error", message="Start datetime must be before end datetime."))

    renamed = 0
    if original_exp_name and original_exp_name != exp_name:
        existing_labels = set(_collect_experiment_names(r, DATA_PATH))
        if original_exp_name not in existing_labels:
            return redirect(
                url_for(
                    "experiments_page",
                    status="error",
                    message="Only existing labels can be edited.",
                )
            )
        renamed = _update_experiment_labels_for_range(
            r,
            DATA_PATH,
            exp_name,
            match_exp_name=original_exp_name,
        )
        # Keep label registry in sync so edit replaces old name instead of duplicating it.
        r.srem(ADMIN_EXPERIMENT_LABELS_KEY, original_exp_name)

    updated = _update_experiment_labels_for_range(
        r,
        DATA_PATH,
        exp_name,
        start_datetime=start_dt,
        end_datetime=end_dt,
    )
    r.sadd(ADMIN_EXPERIMENT_LABELS_KEY, exp_name)
    if renamed:
        message = "Renamed {} data points to '{}' and assigned {} data points in range.".format(
            renamed,
            exp_name,
            updated,
        )
    else:
        message = "Assigned {} data points to experiment '{}'".format(updated, exp_name)
    return redirect(
        url_for(
            "experiments_page",
            status="success",
            message=message,
        )
    )


@app.route("/admin/experiments/relabel", methods=["POST"])
@admin_required
def relabel_experiment():
    """Rename experiment labels across all data points."""
    from_exp = str(request.form.get("from_exp_name", "") or "").strip()
    to_exp = str(request.form.get("to_exp_name", "") or "").strip()
    if not from_exp or not to_exp:
        return redirect(url_for("experiments_page", status="error", message="Both from/to experiment names are required."))

    existing_labels = set(_collect_experiment_names(r, DATA_PATH))
    if from_exp not in existing_labels or to_exp not in existing_labels:
        return redirect(
            url_for(
                "experiments_page",
                status="error",
                message="Relabel supports existing labels only.",
            )
        )
    if from_exp == to_exp:
        return redirect(url_for("experiments_page", status="error", message="Choose different labels for relabeling."))

    updated = _update_experiment_labels_for_range(
        r,
        DATA_PATH,
        to_exp,
        match_exp_name=from_exp,
    )
    r.srem(ADMIN_EXPERIMENT_LABELS_KEY, from_exp)
    r.sadd(ADMIN_EXPERIMENT_LABELS_KEY, to_exp)
    return redirect(
        url_for(
            "experiments_page",
            status="success",
            message="Relabeled {} data points from '{}' to '{}'".format(updated, from_exp, to_exp),
        )
    )


@app.route("/admin/experiments/clear", methods=["POST"])
@admin_required
def clear_experiment_points():
    """Clear experiment label from all data points in the selected group."""
    exp_name = str(request.form.get("exp_name", "") or "").strip()
    if not exp_name:
        return redirect(url_for("experiments_page", status="error", message="Experiment name is required."))

    existing_labels = set(_collect_experiment_names(r, DATA_PATH))
    if exp_name not in existing_labels:
        return redirect(
            url_for(
                "experiments_page",
                status="error",
                message="Clear supports existing labels only.",
            )
        )

    updated = _update_experiment_labels_for_range(
        r,
        DATA_PATH,
        "",
        match_exp_name=exp_name,
    )
    r.srem(ADMIN_EXPERIMENT_LABELS_KEY, exp_name)
    return redirect(
        url_for(
            "experiments_page",
            status="success",
            message="Cleared experiment label '{}' from {} data points".format(exp_name, updated),
        )
    )


@app.route("/admin/clear_redis", methods=["POST"])
@admin_required
def clear_redis():
    """Delete all Redis keys used by the app."""
    try:
        r.flushall()
        return jsonify({"success": True, "message": "All data cleared."})
    except redis.ConnectionError as exc:
        return jsonify({"success": False, "message": "Error clearing Redis: {0}".format(str(exc))}), 500


@app.route("/admin/view_all_redis_data", methods=["GET"])
@admin_required
def view_all_redis_data():
    """Render a raw key/value Redis browser for administrators."""
    try:
        redis_items = [{"key": key, "value": r.get(key)} for key in sorted(list(r.scan_iter("*")))]
        return render_template("admin/redis_data.html", redis_items=redis_items)
    except redis.ConnectionError as exc:
        return "Error connecting to Redis: {0}".format(str(exc)), 500


@app.route("/favicon.ico")
def favicon():
    """Avoid unnecessary favicon 404s."""
    return "", 204


@app.route("/healthz", methods=["GET"])
def healthz():
    """Return minimal health state for load balancers and uptime checks."""
    try:
        r.ping()
    except redis.ConnectionError as exc:
        return jsonify(status="degraded", redis="down", detail=str(exc)), 503
    return jsonify(status="ok", redis="up"), 200


@app.route("/")
def index():
    """Serve the landing page."""
    return render_template("landing.html", title="MiNDFIRL")


def get_active_paths():
    """Return (data_path, section2_path) for the current session's dataset."""
    if request.cookies.get("active_dataset") == "privacy":
        return PRIVACY_DATA_PATH, PRIVACY_SECTION2_PATH
    return DATA_PATH, SECTION2_PATH


@app.route("/disclosing_desktop")
def disclosing_desktop():
    """Serve desktop UI with full disclosure mode."""
    return _display_results_page(DATA_PATH, SECTION2_PATH, "desktop_base/base.html", "full")


@app.route("/mobile")
def mobile():
    """Serve mobile UI with full disclosure mode."""
    return _display_results_page(DATA_PATH, SECTION2_PATH, "mobile_base/mobile.html", "full")


@app.route("/privacy_desktop")
def privacy_desktop():
    """Serve desktop UI with privacy-preserving display mode."""
    return _display_results_page(PRIVACY_DATA_PATH, PRIVACY_SECTION2_PATH, "desktop_privacypreserving/base_privacy.html", "masked")


@app.route("/admin/results")
@admin_required
def results_template():
    """Render results page with aggregate selection breakdowns."""
    try:
        window_days, window_key, window_label = _parse_admin_time_window(request.args.get("window"))
        custom_start = _parse_admin_datetime_input(request.args.get("start"))
        custom_end = _parse_admin_datetime_input(request.args.get("end"))
        if custom_start is not None or custom_end is not None:
            window_key = "all"
            window_label = "Date Range"

        data_pairs, selection_html_elements = process_redis_data(
            r,
            DATA_PATH,
            time_window_days=window_days,
            start_datetime=custom_start,
            end_datetime=custom_end,
        )

        data_pair_list = dm.DataPairList(data_pairs)
        pairs_formatted = data_pair_list.get_data_display("full")
        data = list(zip(pairs_formatted[0::2], pairs_formatted[1::2]))
        ids_list = data_pair_list.get_ids()
        icons = data_pair_list.get_icons()[: (len(pairs_formatted) // 2)]
        ids = list(zip(ids_list[0::2], ids_list[1::2]))

        return render_template(
            "results/results_base.html",
            data=data,
            ids=ids,
            title="Interactive Record Linkage Results",
            icons=icons,
            results_selections=selection_html_elements,
            window_key=window_key,
            window_label=window_label,
            custom_start_value=_to_datetime_local_value(custom_start),
            custom_end_value=_to_datetime_local_value(custom_end),
        )
    except Exception as exc:
        return "Can not open invalid or nonexistent file {} {}".format(DATA_PATH, exc), 500


@app.route("/update_selection", methods=["POST"])
def update_selection():
    """Update one temporary selection for the active user."""
    if not _is_valid_csrf_request():
        return jsonify(success=False, error="CSRF validation failed."), 403

    user_id = request.cookies.get("user_id")
    if not user_id:
        return jsonify(success=False, error="No User ID"), 400

    payload = request.get_json()
    button_id = payload["id"]
    a_pos = button_id.find("a")
    index = int(button_id[1:a_pos])
    selection = button_id[a_pos + 1 :]

    user_selections = load_temp_selections(r, user_id)
    if not user_selections:
        active_data_path, _ = get_active_paths()
        pair_count = len(dl.load_data_from_csv(active_data_path)) // 2
        user_selections = ["" for _ in range(pair_count)]

    if index < 0 or index >= len(user_selections):
        return jsonify(success=False, error="Invalid pair index"), 400

    user_selections[index] = selection
    save_temp_selections(r, user_id, user_selections)
    return jsonify(success=True)


@app.route("/submit_selections", methods=["POST"])
def submit_selections():
    """Validate and persist final selections for the active user."""
    if not _is_valid_csrf_request():
        return jsonify(success=False, error="CSRF validation failed."), 403

    user_id = request.cookies.get("user_id")
    if not user_id:
        return jsonify(success=False, error="No User ID"), 400

    user_selections = load_temp_selections(r, user_id)
    if not user_selections:
        return jsonify(
            success=False,
            error="No responses found for this session. Please answer all pairs before submitting.",
        ), 400

    missing_pairs = [index + 1 for index, value in enumerate(user_selections) if not str(value).strip()]
    if missing_pairs:
        return jsonify(
            success=False,
            error="Please answer all pairs before submitting. Missing pair(s): {}".format(
                ", ".join(map(str, missing_pairs))
            ),
        ), 400

    active_data_path, _ = get_active_paths()
    response_key = "id:" + user_id + "___file:" + active_data_path
    r.set(response_key, ",".join(user_selections))

    current_data_pairs = dl.load_data_from_csv(active_data_path)
    current_data_pair_list = dm.DataPairList(current_data_pairs)
    pair_numbers = get_pair_numbers(current_data_pairs)
    partial_level_flags = get_partial_level_flags(current_data_pair_list)
    pair_reveal_levels = build_pair_reveal_levels(r, user_id, pair_numbers, partial_level_flags)

    disclosed_characters = int(r.get(user_id + "_mindfil_disclosed_characters") or 0)
    total_characters = int(r.get(user_id + "_mindfil_total_characters") or 0)
    character_disclosed_percent_value = 0.0
    if total_characters > 0:
        character_disclosed_percent_value = round(100.0 * disclosed_characters / total_characters, 1)

    kapr_value = float(r.get(user_id + "_KAPR") or 0.0)
    privacy_risk_percent_value = round(100.0 * kapr_value, 1)

    snapshot = {
        "pair_reveal_levels": pair_reveal_levels,
        "character_disclosed_percent_value": character_disclosed_percent_value,
        "privacy_risk_percent_value": privacy_risk_percent_value,
        "saved_at": datetime.utcnow().isoformat(),
    }
    r.set(get_snapshot_key_for_response_key(response_key), json.dumps(snapshot))

    logging.error("submit_selections{}".format(user_id))
    return jsonify(success=True)


@app.route("/get_cell", methods=["POST"])
def open_cell():
    """Reveal the next level for a cell and return updated privacy metrics."""
    if not _is_valid_csrf_request():
        return jsonify(success=False, error="CSRF validation failed."), 403

    payload = request.get_json(silent=True) or {}
    id1 = payload.get("id1") or request.form.get("id1")
    mode = payload.get("mode") or request.form.get("mode")

    if not id1 or not mode:
        return jsonify(success=False, error="Missing request fields"), 400

    pair_num = str(id1.split("-")[0])
    attr_num = str(id1.split("-")[2])
    pair_id = int(pair_num)
    attr_id = int(attr_num)

    active_data_path, active_section2_path = get_active_paths()
    data_pairs = dl.load_data_from_csv(active_data_path)
    dataset = dl.load_data_from_csv(active_section2_path)
    data_pair_list = dm.DataPairList(data_pairs)
    pair = data_pair_list.get_data_pair(pair_id)
    assert pair is not None, "pair of DATA_PAIR_LIST is null"

    attr_display_next = pair.get_next_display(attr_id=attr_id, attr_mode=mode)
    ret = {"value1": attr_display_next[1][0], "value2": attr_display_next[1][1], "mode": attr_display_next[0]}

    cdp_previous = pair.get_character_disclosed_num(1, attr_id, mode) + pair.get_character_disclosed_num(2, attr_id, mode)
    cdp_post = pair.get_character_disclosed_num(1, attr_id, ret["mode"]) + pair.get_character_disclosed_num(
        2, attr_id, ret["mode"]
    )
    cdp_increment = cdp_post - cdp_previous

    user_id = request.cookies.get("user_id")
    mindfil_disclosed_characters_key = user_id + "_mindfil_disclosed_characters"
    r.incrby(mindfil_disclosed_characters_key, cdp_increment)
    mindfil_total_characters_key = user_id + "_mindfil_total_characters"
    cdp = 100.0 * int(r.get(mindfil_disclosed_characters_key)) / int(r.get(mindfil_total_characters_key))
    ret["cdp"] = round(cdp, 1)

    old_display_status1 = []
    old_display_status2 = []
    key1_prefix = user_id + "-" + pair_num + "-1-"
    key2_prefix = user_id + "-" + pair_num + "-2-"
    for attr_i in range(6):
        old_display_status1.append(r.get(key1_prefix + str(attr_i)))
        old_display_status2.append(r.get(key2_prefix + str(attr_i)))

    key1 = user_id + "-" + pair_num + "-1-" + attr_num
    key2 = user_id + "-" + pair_num + "-2-" + attr_num
    if ret["mode"] == "full":
        r.set(key1, "F")
        r.set(key2, "F")
    elif ret["mode"] == "partial":
        r.set(key1, "P")
        r.set(key2, "P")

    display_status1 = []
    display_status2 = []
    for attr_i in range(6):
        display_status1.append(r.get(key1_prefix + str(attr_i)))
        display_status2.append(r.get(key2_prefix + str(attr_i)))

    data_size = len(data_pairs)
    old_kapr = dm.get_KAPR_for_dp(dataset, pair, old_display_status1, data_size)
    kapr = dm.get_KAPR_for_dp(dataset, pair, display_status1, data_size)
    kapr_increment = kapr - old_kapr

    kapr_key = user_id + "_KAPR"
    overall_kapr = float(r.get(kapr_key) or 0) + kapr_increment
    r.incrbyfloat(kapr_key, kapr_increment)
    ret["KAPR"] = round(100 * overall_kapr, 1)

    ret["new_delta"] = dm.KAPR_delta(dataset, pair, display_status1, data_size)
    ret["new_delta_cdp"] = dm.cdp_delta(
        pair,
        display_status1,
        int(r.get(mindfil_disclosed_characters_key)),
        int(r.get(mindfil_total_characters_key)),
    )

    logging.error("open_cell{}".format(user_id))
    return jsonify(ret)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)