import json
import logging
import time
from flask import Flask, render_template, jsonify, request, make_response, Response, url_for, session
from functools import wraps
from datetime import datetime

import redis
import csv
import pandas as pd
import io

import data_loader as dl
import data_display as dd
import data_model as dm
import os
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "mindfirl_session_secret")

# Initialize Redis connection
redis_url = os.environ.get("REDIS_URL")
if redis_url:
    r = redis.Redis(
        host='mindfirl-cache.redis.cache.windows.net',    
        port=6380,                  
        username='default',         
        password=os.environ.get("REDIS_PASSWORD", None),
        ssl=True,                  
        decode_responses=True
    )
else:
    # Local development fallback
    r = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        password=os.environ.get("REDIS_PASSWORD", None),
        ssl=(os.environ.get("REDIS_USE_TLS", "false").lower() == "true"),
        decode_responses=True
    )

# Global variables
data_path = 'data/ppirl.csv'
DATA_PAIR_LIST = None
flag = False
user_selections = None
pair = None

DATASET = dl.load_data_from_csv('data/section2.csv')
data_pairs = dl.load_data_from_csv('data/ppirl.csv')
settings = dl.load_config_settings()
# settings = {"privacy_budget": 80}
# print(settings)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "backuppassword")

same_different = {
    "1": "Different",
    "2": "Different",
    "3": "Different",
    "4": "Same",
    "5": "Same",
    "6": "Same"
}

high_low = {
    "1": "High",
    "2": "Moderate",
    "3": "Low",
    "4": "Low",
    "5": "Moderate",
    "6": "High"
}

ATTRIBUTE_COLUMNS = [
    ("ID", "id_reveal_level"),
    ("First Name", "first_name_reveal_level"),
    ("Last Name", "last_name_reveal_level"),
    ("DoB(M/D/Y)", "dob_reveal_level"),
    ("Sex", "sex_reveal_level"),
    ("Race", "race_reveal_level")
]


def _get_pair_numbers(data_pairs):
    pair_numbers = []
    for i in range(0, len(data_pairs), 2):
        pair_numbers.append(str(data_pairs[i][0]))
    return pair_numbers


def _get_partial_level_flags(data_pair_list):
    partial_flags = []
    for pair_index in range(len(data_pair_list.get_ids()) // 2):
        data_pair = data_pair_list.get_data_pair_by_index(pair_index)
        attr_flags = []
        for attr_index in range(6):
            next_mode = data_pair.get_next_display(attr_index, 'M')[0]
            attr_flags.append(next_mode == 'partial')
        partial_flags.append(attr_flags)
    return partial_flags


def _status_to_level(status, has_partial_level):
    if status == 'P':
        return 1
    if status == 'F':
        return 2 if has_partial_level else 1
    return 0


def _selection_to_conclusion(selection):
    if not selection:
        return {
            "conclusion_code": "",
            "conclusion_same_different": "No Response",
            "conclusion_confidence": ""
        }

    return {
        "conclusion_code": selection,
        "conclusion_same_different": same_different.get(selection, "Unknown"),
        "conclusion_confidence": high_low.get(selection, "Unknown")
    }


def _selection_to_conclusion_label(selection):
    if not selection:
        return ""

    mapping = {
        "1": "H different",
        "2": "M different",
        "3": "L different",
        "4": "L same",
        "5": "M same",
        "6": "H same"
    }
    return mapping.get(selection, "")


def _build_redis_csv_rows(filename):
    data_pairs = dl.load_data_from_csv(filename)
    data_pair_list = dm.DataPairList(data_pairs)
    pair_numbers = _get_pair_numbers(data_pairs)
    partial_level_flags = _get_partial_level_flags(data_pair_list)

    response_keys = sorted(list(r.scan_iter('id:*___file:{}*'.format(filename))))
    user_ids = [key.split('id:')[1].split('___file:')[0] for key in response_keys]
    response_values = r.mget(response_keys) if response_keys else []

    rows = []

    for student_index, user_id in enumerate(user_ids):
        selection_string = response_values[student_index] or ""
        selections = selection_string.split(',') if selection_string else []

        disclosed_characters = int(r.get(user_id + '_mindfil_disclosed_characters') or 0)
        total_characters = int(r.get(user_id + '_mindfil_total_characters') or 0)
        character_disclosed_percent_value = 0.0
        if total_characters > 0:
            character_disclosed_percent_value = round(100.0 * disclosed_characters / total_characters, 1)

        kapr_value = float(r.get(user_id + '_KAPR') or 0.0)
        privacy_risk_percent_value = round(100.0 * kapr_value, 1)

        row = {
            "student_index": student_index + 1,
            "student_id": user_id,
            "character_disclosed_percent_value": character_disclosed_percent_value,
            "privacy_risk_percent_value": privacy_risk_percent_value
        }

        for pair_index, pair_number in enumerate(pair_numbers):
            pair_num = pair_index + 1
            selection = selections[pair_index].strip() if pair_index < len(selections) else ""
            conclusion_label = _selection_to_conclusion_label(selection)

            row['pair_{}_conclusion'.format(pair_num)] = conclusion_label

            for attr_index, (_, column_name) in enumerate(ATTRIBUTE_COLUMNS):
                key_row_1 = '{}-{}-1-{}'.format(user_id, pair_number, attr_index)
                key_row_2 = '{}-{}-2-{}'.format(user_id, pair_number, attr_index)

                status_1 = r.get(key_row_1) or 'M'
                status_2 = r.get(key_row_2) or 'M'

                has_partial_level = partial_level_flags[pair_index][attr_index]
                level_1 = _status_to_level(status_1, has_partial_level)
                level_2 = _status_to_level(status_2, has_partial_level)
                row['pair_{}_{}'.format(pair_num, column_name)] = max(level_1, level_2)

        rows.append(row)

    return rows, pair_numbers

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True

        if not session.get('is_admin', False):
            return "Unauthorized access. Please provide the correct admin password.", 403
        
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin_page():
    return render_template('admin.html')

@app.route('/admin/download_redis_data', methods=['POST'])
@admin_required
def generate_redis_csv():
    filename = 'data/ppirl.csv'
    rows, pair_numbers = _build_redis_csv_rows(filename)

    output = io.StringIO()
    fieldnames = [
        "student_index",
        "student_id"
    ]

    for pair_index, _ in enumerate(pair_numbers):
        pair_num = pair_index + 1
        for _, column_name in ATTRIBUTE_COLUMNS:
            fieldnames.append('pair_{}_{}'.format(pair_num, column_name))

    for pair_index, _ in enumerate(pair_numbers):
        pair_num = pair_index + 1
        fieldnames.append('pair_{}_conclusion'.format(pair_num))

    fieldnames.extend([
        "character_disclosed_percent_value",
        "privacy_risk_percent_value"
    ])

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=mindfirl_report_{}.csv'.format(timestamp)
        }
    )
    
@app.route('/admin/clear_redis', methods=['POST'])
@admin_required
def clear_redis():
    try:
        r.flushall()
        return '<a href="/admin">Back to Admin</a><br/><a href="/">Home</a><br/><br/>All data cleared from Redis!'
    except redis.ConnectionError as e:
        return "Error clearing Redis: {0}".format(str(e)), 500

@app.route('/admin/view_all_redis_data', methods=['GET'])
@admin_required
def view_all_redis_data():
    try:
        ret = '<a href="/admin">Back to Admin</a><br/><a href="/">Home</a><h1>All Stored Data in Redis</h1>'
        for key in r.scan_iter("*"):
            user_data = r.get(key)
            # if user_data:
            #     user_data = user_data.decode('utf-8')
            ret += '<strong>{0}:</strong> {1}<br/><br/>'.format(key, user_data)
        return ret
    except redis.ConnectionError as e:
        return "Error connecting to Redis: {0}".format(str(e)), 500
    

def process_redis_data(filename):
    global DATASET, data_pairs
    
    data_pairs = dl.load_data_from_csv('data/ppirl.csv')
    DATASET = dl.load_data_from_csv('data/section2.csv')

    all_keys = list(r.scan_iter('*file:data/ppirl.csv*'))
    print("all keys")
    print(all_keys)
    html_elements_list = []
    value_data = [[0, 0, 0, 0, 0, 0, 0] for _ in range(len(data_pairs))]

    if len(all_keys) > 0:    
        filename_keys = [key for key in all_keys if filename in key]
        filename_values = r.mget(filename_keys)
        if filename_values is None:
            filename_values = []

        num_pairs = len(filename_values[0].split(','))
        data_len = len(data_pairs) / 2
        if num_pairs != data_len:
            raise Exception("The lengths of the dataset and responses are not aligned")

        # html_elements_list = []
        value_data = [[0, 0, 0, 0, 0, 0, 0] for _ in range(num_pairs)]

        for user_index, selection_string in enumerate(filename_values):
            selections = selection_string.split(',')
            for selection_index, selection in enumerate(selections):
                if selection == '':
                    value_data[selection_index][0] += 1
                else:
                    value_data[selection_index][int(selection)] += 1
        

    for selection_counts in value_data:
        render_values = [selection_counts[1] + selection_counts[2] + selection_counts[3], selection_counts[4] + selection_counts[5] + selection_counts[6]] + \
            [selection_counts[i] for i in range(1, 7)]

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

'''
    for record_index, value in enumerate(filename_values):
        print(value)
        selection_counts = [0] * 7
        selections = value.split(',')
        print(selections)

        for selection_index, selection in enumerate(selections):
            if selection == '':
                selection_counts[0] += 1
            else:
                selection_counts[int(selection)] += 1
    
        
'''


@app.route('/favicon.ico')
def favicon():
    return '', 204  # No Content

@app.route('/<filename>')
def display_results_page(filename, template_name, disclosure_setting):
    global DATA_PAIR_LIST, data_pairs, DATASET, user_selections, settings
    
    user_id = request.cookies.get("user_id")

    if not user_id:
        user_id = str(uuid.uuid4())

    try:
        data_pairs = dl.load_data_from_csv('data/ppirl.csv')
        DATASET = dl.load_data_from_csv('data/section2.csv')

        DATA_PAIR_LIST = dm.DataPairList(data_pairs)
        pairs_formatted = DATA_PAIR_LIST.get_data_display(disclosure_setting)
        title = 'Interactive Record Linkage'
        data = list(zip(pairs_formatted[0::2], pairs_formatted[1::2]))
        ids_list = DATA_PAIR_LIST.get_ids()
        icons = DATA_PAIR_LIST.get_icons()[:(len(pairs_formatted) // 2)]
        user_selections = [""] * (len(pairs_formatted) // 2)
        ids = list(zip(ids_list[0::2], ids_list[1::2]))

        total_characters = DATA_PAIR_LIST.get_total_characters()
        mindfil_total_characters_key = user_id + '_mindfil_total_characters'
        r.set(mindfil_total_characters_key, total_characters)
        mindfil_disclosed_characters_key = user_id + '_mindfil_disclosed_characters'
        r.set(mindfil_disclosed_characters_key, 0)
        KAPR_key = user_id + '_KAPR'
        r.set(KAPR_key, 0)

        for id1 in ids_list:
            for i in range(6):
                key = user_id + '-' + id1[i]
                r.set(key, 'M')

        delta = []
        delta_cdp = []
        for i in range(len(pairs_formatted) // 2):
            data_pair = DATA_PAIR_LIST.get_data_pair_by_index(i)
            delta += dm.KAPR_delta(DATASET, data_pair, ['M', 'M', 'M', 'M', 'M', 'M'], len(data_pairs))
            delta_cdp += dm.cdp_delta(data_pair, ['M', 'M', 'M', 'M', 'M', 'M'], 0, total_characters)

        choices_key = user_id + '_choices'
        previous_choices = r.get(choices_key)
        choices = json.loads(previous_choices) if previous_choices else {}

    except Exception as e:
        return "Can not open invalid or nonexistent file {} {} {}".format(filename, e, os.getcwd()), 500
    
    logging.error("display_results_page{}".format(user_id))

    response = make_response(render_template(template_name, data=data, ids=ids, title="Interactive Record Linkage", icons=icons, delta=delta, delta_cdp=delta_cdp, mode="PPIRL", choices=choices, marker_position=int(settings["privacy_budget"])))
    
    response.set_cookie("user_id", user_id)
    return response

# def default_display(screen_width):
#     return display_results_page("data/ppirl.csv", screen_width)

@app.route('/')
def index():
    # Just serves the base page with JavaScript
    return render_template('landing.html', title="MiNDFIRL")

@app.route('/disclosing_desktop')
def disclosing_desktop():
    return display_results_page("data/ppirl.csv", "desktop_base/base.html", "full")

@app.route('/mobile')
def mobile():
    return display_results_page("data/ppirl.csv", "mobile_base/mobile.html", "full")

@app.route('/privacy_desktop')
def privacy_desktop():
    return display_results_page("data/ppirl.csv", "desktop_privacypreserving/base_privacy.html", "masked")

@app.route('/admin/results')
@admin_required
def results_template():
    filename = "data/ppirl.csv"
    try:
        data_pairs, selection_html_elements = process_redis_data(filename)
        
        DATA_PAIR_LIST = dm.DataPairList(data_pairs)
        pairs_formatted = DATA_PAIR_LIST.get_data_display('full')
        title = 'Interactive Record Linkage Results'
        data = list(zip(pairs_formatted[0::2], pairs_formatted[1::2]))
        ids_list = DATA_PAIR_LIST.get_ids()
        icons = DATA_PAIR_LIST.get_icons()[:(len(pairs_formatted) // 2)]
        ids = list(zip(ids_list[0::2], ids_list[1::2]))

        return render_template("results/results_base.html", data=data, ids=ids, title=title, icons=icons, results_selections=selection_html_elements)
    except Exception as e:
        return "Can not open invalid or nonexistent file {} {}".format(filename, e), 500

@app.route('/update_selection', methods=['POST'])
def update_selection():
    global user_selections
    data = request.get_json()
    button_id = data['id']
    index = int(button_id[1:2])
    selection = button_id[3:]
    user_selections[index] = selection
    return jsonify(success=True)

@app.route('/submit_selections', methods=['POST'])
def submit_selections():
    global user_selections
    user_id = request.cookies.get("user_id")
    if not user_id:
        return jsonify(success=False, error="No User ID"), 400
        # user_id = "no_user_id"
    
    r.set("id:" + user_id + "___file:" + data_path, ','.join(user_selections))
    logging.error("submit_selections{}".format(user_id))
    return jsonify(success=True)


@app.route('/get_cell', methods=['GET', 'POST'])
def open_cell():
    global DATA_PAIR_LIST, data_pairs, DATASET, pair
    id1 = request.args.get('id1')
    id2 = request.args.get('id2')
    mode = request.args.get('mode')

    pair_num = str(id1.split('-')[0])
    attr_num = str(id1.split('-')[2])

    pair_id = int(pair_num)
    attr_id = int(attr_num)

    assert DATA_PAIR_LIST is not None, "DATA_PAIR_LIST failed to initialize"

    pair = DATA_PAIR_LIST.get_data_pair(pair_id)
    assert pair is not None, "pair of DATA_PAIR_LIST is null"

    attr = pair.get_attributes(attr_id)
    attr1 = attr[0]
    attr2 = attr[1]
    helper = pair.get_helpers(attr_id)
    helper1 = helper[0]
    helper2 = helper[1]

    attr_display_next = pair.get_next_display(attr_id=attr_id, attr_mode=mode)
    ret = {"value1": attr_display_next[1][0], "value2": attr_display_next[1][1], "mode": attr_display_next[0]}


    cdp_previous = pair.get_character_disclosed_num(1, attr_id, mode) + pair.get_character_disclosed_num(2, attr_id, mode)
    cdp_post = pair.get_character_disclosed_num(1, attr_id, ret['mode']) + pair.get_character_disclosed_num(2, attr_id, ret['mode'])
    cdp_increment = cdp_post - cdp_previous

    user_id = request.cookies.get("user_id")
    mindfil_disclosed_characters_key = user_id + '_mindfil_disclosed_characters'
    r.incrby(mindfil_disclosed_characters_key, cdp_increment)
    mindfil_total_characters_key = user_id + '_mindfil_total_characters'
    cdp = 100.0 * int(r.get(mindfil_disclosed_characters_key)) / int(r.get(mindfil_total_characters_key))
    print(cdp)
    ret['cdp'] = round(cdp, 1)

    old_display_status1 = []
    old_display_status2 = []
    key1_prefix = user_id + '-' + pair_num + '-1-'
    key2_prefix = user_id + '-' + pair_num + '-2-'
    for attr_i in range(6):
        old_display_status1.append(r.get(key1_prefix + str(attr_i)))
        old_display_status2.append(r.get(key2_prefix + str(attr_i)))

    key1 = user_id + '-' + pair_num + '-1-' + attr_num
    key2 = user_id + '-' + pair_num + '-2-' + attr_num
    if ret['mode'] == 'full':
        r.set(key1, 'F')
        r.set(key2, 'F')
    elif ret['mode'] == 'partial':
        r.set(key1, 'P')
        r.set(key2, 'P')
    else:
        pass

    display_status1 = []
    display_status2 = []
    for attr_i in range(6):
        display_status1.append(r.get(key1_prefix + str(attr_i)))
        display_status2.append(r.get(key2_prefix + str(attr_i)))
    
    M = len(data_pairs)
    
    old_KAPR = dm.get_KAPR_for_dp(DATASET, pair, old_display_status1, M)
    KAPR = dm.get_KAPR_for_dp(DATASET, pair, display_status1, M)

    KAPRINC = KAPR - old_KAPR
    KAPR_key = user_id + '_KAPR'
    overall_KAPR = float(r.get(KAPR_key) or 0)
    overall_KAPR += KAPRINC
    
    r.incrbyfloat(KAPR_key, KAPRINC)
    ret['KAPR'] = round(100 * overall_KAPR, 1)

    r.set("1a", "1a")
    new_delta_list = dm.KAPR_delta(DATASET, pair, display_status1, M)
    ret['new_delta'] = new_delta_list
    r.set("2b", "2b")

    new_delta_cdp_list = dm.cdp_delta(pair, display_status1, int(r.get(mindfil_disclosed_characters_key)), int(r.get(mindfil_total_characters_key)))
    ret['new_delta_cdp'] = new_delta_cdp_list

    logging.error("open_cell{}".format(user_id))
    return jsonify(ret)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 80))
    app.run(host='0.0.0.0', port=port)