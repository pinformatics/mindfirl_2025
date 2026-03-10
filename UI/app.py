import csv
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime
from functools import wraps
from io import StringIO
from typing import cast

import redis
from flask import Flask, Response, jsonify, make_response, render_template, request, session

import data_loader as dl
import data_model as dm
from admin_reporting import (
    build_graph_payload,
    build_pair_record_details,
    build_redis_csv_fieldnames,
    build_redis_csv_rows,
    process_redis_data,
)
from redis_factory import create_redis_client
from ui_constants import (
    ADMIN_LOGIN_LOCK_SECONDS,
    ADMIN_MAX_FAILED_ATTEMPTS,
    DATA_PATH,
    SECTION2_PATH,
)
from user_state import (
    build_pair_reveal_levels,
    get_pair_numbers,
    get_partial_level_flags,
    get_snapshot_key_for_response_key,
    load_temp_selections,
    save_temp_selections,
)

app = Flask(__name__)

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


def _build_results_context(user_id, disclosure_setting):
    """Initialize a user session and return data needed for results templates."""
    data_pairs = dl.load_data_from_csv(DATA_PATH)
    dataset = dl.load_data_from_csv(SECTION2_PATH)

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


def _display_results_page(filename, template_name, disclosure_setting):
    """Render an interactive results page and initialize user session keys."""
    user_id = request.cookies.get("user_id") or str(uuid.uuid4())

    try:
        context = _build_results_context(user_id, disclosure_setting)
    except redis.ConnectionError as exc:
        return (
            "Redis connection failed: {}. Configure REDIS_URL in UI/.env or run a local Redis instance."
            .format(exc),
            500,
        )
    except Exception as exc:
        return "Can not open invalid or nonexistent file {} {} {}".format(filename, exc, os.getcwd()), 500

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
    response.set_cookie("user_id", user_id)
    return response


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_page():
    """Render admin dashboard."""
    return render_template("admin/dashboard.html")


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


@app.route("/admin/export_graph", methods=["GET"])
@admin_required
def export_graph_view():
    """Render admin analytics charts derived from submission data."""
    rows, pair_numbers = build_redis_csv_rows(r, DATA_PATH)
    graph_data = build_graph_payload(rows, pair_numbers)
    graph_data["pair_record_details"] = build_pair_record_details(DATA_PATH)
    return render_template("admin/graph.html", title="Response Graphs", student_count=len(rows), graph_data=graph_data)


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


@app.route("/")
def index():
    """Serve the landing page."""
    return render_template("landing.html", title="MiNDFIRL")


@app.route("/disclosing_desktop")
def disclosing_desktop():
    """Serve desktop UI with full disclosure mode."""
    return _display_results_page(DATA_PATH, "desktop_base/base.html", "full")


@app.route("/mobile")
def mobile():
    """Serve mobile UI with full disclosure mode."""
    return _display_results_page(DATA_PATH, "mobile_base/mobile.html", "full")


@app.route("/privacy_desktop")
def privacy_desktop():
    """Serve desktop UI with privacy-preserving display mode."""
    return _display_results_page(DATA_PATH, "desktop_privacypreserving/base_privacy.html", "masked")


@app.route("/admin/results")
@admin_required
def results_template():
    """Render results page with aggregate selection breakdowns."""
    try:
        data_pairs, selection_html_elements = process_redis_data(r, DATA_PATH)

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
        )
    except Exception as exc:
        return "Can not open invalid or nonexistent file {} {}".format(DATA_PATH, exc), 500


@app.route("/update_selection", methods=["POST"])
def update_selection():
    """Update one temporary selection for the active user."""
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
        pair_count = len(dl.load_data_from_csv(DATA_PATH)) // 2
        user_selections = ["" for _ in range(pair_count)]

    if index < 0 or index >= len(user_selections):
        return jsonify(success=False, error="Invalid pair index"), 400

    user_selections[index] = selection
    save_temp_selections(r, user_id, user_selections)
    return jsonify(success=True)


@app.route("/submit_selections", methods=["POST"])
def submit_selections():
    """Validate and persist final selections for the active user."""
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

    response_key = "id:" + user_id + "___file:" + DATA_PATH
    r.set(response_key, ",".join(user_selections))

    current_data_pairs = dl.load_data_from_csv(DATA_PATH)
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


@app.route("/get_cell", methods=["GET", "POST"])
def open_cell():
    """Reveal the next level for a cell and return updated privacy metrics."""
    id1 = request.args.get("id1")
    mode = request.args.get("mode")

    pair_num = str(id1.split("-")[0])
    attr_num = str(id1.split("-")[2])
    pair_id = int(pair_num)
    attr_id = int(attr_num)

    data_pairs = dl.load_data_from_csv(DATA_PATH)
    dataset = dl.load_data_from_csv(SECTION2_PATH)
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