# -*- coding: utf-8 -*-
from flask import Flask, render_template, redirect, url_for, session, jsonify, request, g, Response, send_file
from functools import wraps
import time
from random import randint
import json
import hashlib
import collections
import os
import redis
import logging
import csv
from flask_mail import Mail, Message
import StringIO 

import data_loader as dl
import data_display as dd
import data_model as dm

app = Flask(__name__)

app.config.update(
    MAIL_SERVER='smtp.gmail.com',
    MAIL_PORT=587,
    MAIL_USE_TLS=True,
    MAIL_USE_SSL=False,
    MAIL_USERNAME='',
    MAIL_PASSWORD='',
    MAIL_DEFAULT_SENDER='',
    MAIL_DEBUG=False
)

MAIL_SENDER = ''
MAIL_RECEIVER = ''

mail = Mail(app)

if 'DYNO' in os.environ:
    r = redis.from_url(os.environ.get("REDIS_URL"))
else:
    r = redis.Redis(host='localhost', port=6379, db=0)

DATASET = dl.load_data_from_csv('data/section2.csv')
data_pairs = dl.load_data_from_csv('data/ppirl.csv')
DATA_PAIR_LIST = dm.DataPairList(data_pairs)
flag = False

ADMIN_PASSWORD = 'admin123'

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.form.get('password') != ADMIN_PASSWORD and request.args.get('password') != ADMIN_PASSWORD:
            return "Unauthorized access. Please provide the correct admin password.", 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/<filename>')
def load_csv(filename):
    global DATA_PAIR_LIST, data_pairs, flag
    mode = 'PPIRL' if filename.startswith('Mindfirl_') else 'CDIRL'
    actual_filename = filename[9:] if filename.startswith('Mindfirl_') else filename
    file_path = os.path.join('data', actual_filename)
    
    if filename.endswith('.csv') and os.path.exists(file_path):
        data_pairs = dl.load_data_from_csv(file_path)
        DATA_PAIR_LIST = dm.DataPairList(data_pairs)
        flag = True
        session['current_filename'] = filename
        session['is_custom'] = True
        return show_survey_link(mode=mode)
    else:
        return "Invalid file type or file not found", 400

@app.route('/')
@app.route('/survey_link')
def show_survey_link(mode=None):
    global DATA_PAIR_LIST, data_pairs, flag
    mode = mode or request.args.get('mode', 'PPIRL')

    if not flag:
        data_pairs = dl.load_data_from_csv('data/ppirl.csv')
        DATA_PAIR_LIST = dm.DataPairList(data_pairs)
        session['current_filename'] = 'ppirl.csv'
        session['is_custom'] = False

    if mode == 'CDIRL':
        pairs_formatted = DATA_PAIR_LIST.get_data_display('full')
        for index in range(0, len(pairs_formatted)):
            if index < len(data_pairs):
                pairs_formatted[index] = data_pairs[index][:9]
        title = 'Complete Data Interactive Record Linkage (CDIRL)'
    else:
        pairs_formatted = DATA_PAIR_LIST.get_data_display('masked')
        title = 'Privacy Preserving Interactive Record Linkage (PPIRL)'

    data = zip(pairs_formatted[0::2], pairs_formatted[1::2])
    M = len(data_pairs)
    num_pairs = len(pairs_formatted) // 2
    icons = DATA_PAIR_LIST.get_icons()[:num_pairs]
    ids_list = DATA_PAIR_LIST.get_ids()
    ids = zip(ids_list[0::2], ids_list[1::2])

    timestamp = time.time()
    session['user_cookie'] = hashlib.sha224("salt12138" + str(timestamp) + '.' + str(randint(1, 10000))).hexdigest()
    total_characters = DATA_PAIR_LIST.get_total_characters()
    mindfil_total_characters_key = session['user_cookie'] + '_mindfil_total_characters'
    r.set(mindfil_total_characters_key, total_characters)
    mindfil_disclosed_characters_key = session['user_cookie'] + '_mindfil_disclosed_characters'
    r.set(mindfil_disclosed_characters_key, 0)
    KAPR_key = session['user_cookie'] + '_KAPR'
    r.set(KAPR_key, 0)
    timestamp_key = session['user_cookie'] + '_timestamp'
    r.set(timestamp_key, str(timestamp))

    for id1 in ids_list:
        for i in range(6):
            key = session['user_cookie'] + '-' + id1[i]
            r.set(key, 'F' if mode == 'CDIRL' else 'M')

    delta = []
    delta_cdp = []
    if mode == 'PPIRL':
        for i in range(num_pairs):
            data_pair = DATA_PAIR_LIST.get_data_pair_by_index(i)
            delta += dm.KAPR_delta(DATASET, data_pair, ['M', 'M', 'M', 'M', 'M', 'M'], M)
            delta_cdp += dm.cdp_delta(data_pair, ['M', 'M', 'M', 'M', 'M', 'M'], 0, total_characters)

    if flag:
        flag = False

    choices_key = session['user_cookie'] + '_choices'
    previous_choices = r.get(choices_key)
    choices = json.loads(previous_choices) if previous_choices else {}

    return render_template('survey_link.html', data=data, icons=icons, ids=ids, title=title, thisurl='/record_linkage', page_number=16, delta=delta, delta_cdp=delta_cdp, mode=mode, choices=choices)

@app.route("/save_survey", methods=['POST'])
def save_survey():
    f = request.form
    user_cookie = session.get('user_cookie', 'unknown_user')
    mode = request.args.get('mode', 'PPIRL')

    choices = {}
    for key in f.keys():
        if key.startswith("choice_"):
            pair_num = key.split("_")[1]
            value = f.get(key)
            if value:
                choices[pair_num] = value
                choice_key = "{0}_choice_{1}".format(user_cookie, pair_num)
                r.set(choice_key, value)
                if mode == 'CDIRL':
                    r.expire(choice_key, 3600) 

    attribute_names = ['Mode', 'User ID', 'Submission Time', 'ID', 'First Name', 'Last Name', 'DoB(M/D/Y)', 'Sex', 'Race', 'Choice', 'Total_Characters', 'Disclosed_Characters', 'KAPR_Privacy_Risk']
    ids_list = DATA_PAIR_LIST.get_ids()
    paired_ids = list(zip(ids_list[0::2], ids_list[1::2]))
    submission_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    activity = []

    total_chars = disclosed_chars = kapr = 'N/A'
    if mode == 'PPIRL':
        mindfil_total_characters_key = user_cookie + '_mindfil_total_characters'
        mindfil_disclosed_characters_key = user_cookie + '_mindfil_disclosed_characters'
        KAPR_key = user_cookie + '_KAPR'
        total_chars = int(r.get(mindfil_total_characters_key) or 0)
        disclosed_chars = int(r.get(mindfil_disclosed_characters_key) or 0)
        kapr = float(r.get(KAPR_key) or 0)

    for i, pair_ids in enumerate(paired_ids, start=1):
        pair_data = {
            'Mode': mode,
            'User ID': user_cookie,
            'Submission Time': submission_time,
            'Pair Number': str(i - 1)
        }
        for j, attr_name in enumerate(['ID', 'First Name', 'Last Name', 'DoB(M/D/Y)', 'Sex', 'Race']):
            key1 = user_cookie + '-' + pair_ids[0][j]
            key2 = user_cookie + '-' + pair_ids[1][j]
            status1 = r.get(key1) or 'M'
            status2 = r.get(key2) or 'M'
            status1 = status1.decode('utf-8') if status1 else 'M'
            status2 = status2.decode('utf-8') if status2 else 'M'
            pair_data[attr_name] = status1 if status1 == status2 else '{0}|{1}'.format(status1, status2)
        
        pair_num = str(i)
        pair_data['Choice'] = choices[pair_num]
        pair_data['Total_Characters'] = total_chars
        pair_data['Disclosed_Characters'] = disclosed_chars
        pair_data['KAPR_Privacy_Risk'] = kapr
        activity.append(pair_data)

    output = StringIO.StringIO()
    writer = csv.writer(output)
    headers = ['Pair Number'] + attribute_names
    writer.writerow(headers)

    for act in activity:
        row = [act['Pair Number']] + [act[attr] for attr in attribute_names]
        writer.writerow(row)

    csv_content = output.getvalue()
    output.close()

    redirect_url = None
    if session.get('is_custom', False):
        filename = session.get('current_filename', 'ppirl.csv')
        redirect_url = url_for('load_csv', filename=filename)
    else:
        redirect_url = url_for('show_survey_link', mode=mode)

    try:
        msg = Message(subject='User Activity Report for {0}'.format(user_cookie), 
                      sender=MAIL_SENDER, 
                      recipients=[MAIL_RECEIVER])
        msg.body = "Attached is the user activity report in CSV format."
        msg.attach("user_activity_{0}.csv".format(user_cookie), "text/csv", csv_content)
        mail.send(msg)
        print "Email sent successfully!"
        
        choices_key = user_cookie + '_choices'
        r.set(choices_key, json.dumps(choices))
        if mode == 'CDIRL':
            r.expire(choices_key, 3600)

        return jsonify({
            "message": "Thank you, your response has been recorded. You may close your browser or try again.",
            "redirect": redirect_url
        })
    except Exception as e:
        print "Email error:", str(e)
        return jsonify({
            "message": "Failed to send email: {0}. Your response was not recorded.".format(str(e)),
            "redirect": redirect_url
        }), 500

@app.route('/get_cell', methods=['GET', 'POST'])
def open_cell():
    id1 = request.args.get('id1')
    id2 = request.args.get('id2')
    mode = request.args.get('mode')

    pair_num = str(id1.split('-')[0])
    attr_num = str(id1.split('-')[2])

    pair_id = int(pair_num)
    attr_id = int(attr_num)

    pair = DATA_PAIR_LIST.get_data_pair(pair_id)
    attr = pair.get_attributes(attr_id)
    attr1 = attr[0]
    attr2 = attr[1]
    helper = pair.get_helpers(attr_id)
    helper1 = helper[0]
    helper2 = helper[1]

    if mode == 'CDIRL':
        return jsonify({"value1": attr1, "value2": attr2, "mode": "full"})

    attr_display_next = pair.get_next_display(attr_id=attr_id, attr_mode=mode)
    ret = {"value1": attr_display_next[1][0], "value2": attr_display_next[1][1], "mode": attr_display_next[0]}

    cdp_previous = pair.get_character_disclosed_num(1, attr_id, mode) + pair.get_character_disclosed_num(2, attr_id, mode)
    cdp_post = pair.get_character_disclosed_num(1, attr_id, ret['mode']) + pair.get_character_disclosed_num(2, attr_id, ret['mode'])
    cdp_increment = cdp_post - cdp_previous

    mindfil_disclosed_characters_key = session['user_cookie'] + '_mindfil_disclosed_characters'
    r.incrby(mindfil_disclosed_characters_key, cdp_increment)
    mindfil_total_characters_key = session['user_cookie'] + '_mindfil_total_characters'
    cdp = 100.0 * int(r.get(mindfil_disclosed_characters_key)) / int(r.get(mindfil_total_characters_key))
    ret['cdp'] = round(cdp, 1)

    old_display_status1 = []
    old_display_status2 = []
    key1_prefix = session['user_cookie'] + '-' + pair_num + '-1-'
    key2_prefix = session['user_cookie'] + '-' + pair_num + '-2-'
    for attr_i in range(6):
        old_display_status1.append(r.get(key1_prefix + str(attr_i)))
        old_display_status2.append(r.get(key2_prefix + str(attr_i)))

    key1 = session['user_cookie'] + '-' + pair_num + '-1-' + attr_num
    key2 = session['user_cookie'] + '-' + pair_num + '-2-' + attr_num
    if ret['mode'] == 'full':
        r.set(key1, 'F')
        r.set(key2, 'F')
    elif ret['mode'] == 'partial':
        r.set(key1, 'P')
        r.set(key2, 'P')
    else:
        print "Error: invalid display status."

    display_status1 = []
    display_status2 = []
    for attr_i in range(6):
        display_status1.append(r.get(key1_prefix + str(attr_i)))
        display_status2.append(r.get(key2_prefix + str(attr_i)))
    M = len(data_pairs)
    old_KAPR = dm.get_KAPR_for_dp(DATASET, pair, old_display_status1, M)
    KAPR = dm.get_KAPR_for_dp(DATASET, pair, display_status1, M)
    KAPRINC = KAPR - old_KAPR
    KAPR_key = session['user_cookie'] + '_KAPR'
    overall_KAPR = float(r.get(KAPR_key) or 0)
    overall_KAPR += KAPRINC
    r.incrbyfloat(KAPR_key, KAPRINC)
    ret['KAPR'] = round(100 * overall_KAPR, 1)

    new_delta_list = dm.KAPR_delta(DATASET, pair, display_status1, M)
    ret['new_delta'] = new_delta_list
    new_delta_cdp_list = dm.cdp_delta(pair, display_status1, int(r.get(mindfil_disclosed_characters_key)), int(r.get(mindfil_total_characters_key)))
    ret['new_delta_cdp'] = new_delta_cdp_list

    return jsonify(ret)

@app.route('/pull_survey')
def pull_survey():
    ret = ''
    for key in r.scan_iter("survey_*"):
        user_data = r.get(key)
        ret = ret + 'key: ' + key + ';'
        ret = ret + user_data + '<br/><br/><br/>'
    return ret

app.secret_key = 'a9%z$/`9h8FMnh893;*g783'

@app.route('/view_all_redis_data')
def view_all_redis_data():
    try:
        ret = '<h1>All Stored Data in Redis</h1>'
        for key in r.scan_iter("*"):
            user_data = r.get(key)
            if user_data:
                user_data = user_data.decode('utf-8')
            ret += '<strong>{0}:</strong> {1}<br/><br/>'.format(key.decode("utf-8"), user_data)
        return ret
    except redis.ConnectionError as e:
        return "Error connecting to Redis: {0}".format(str(e)), 500

@app.route('/clear_redis')
def clear_redis():
    try:
        r.flushall()
        return 'All data cleared from Redis!'
    except redis.ConnectionError as e:
        return "Error clearing Redis: {0}".format(str(e)), 500

@app.route('/admin')
@admin_required
def admin_interface():
    try:
        r.ping()
        redis_status = u"✅"
        key_count = len(list(r.scan_iter("*_choices")))
    except redis.ConnectionError:
        redis_status = u"❌"
        key_count = 0  # Default to 0 when Redis is unavailable
    
    return render_template('admin.html', redis_status=redis_status, key_count=key_count)

@app.route('/admin/submission_count', methods=['POST'])
@admin_required
def submission_count():
    try:
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        
        choice_keys = list(r.scan_iter("*_choices"))
        if start_date_str and end_date_str:
            start_time = time.mktime(time.strptime(start_date_str, '%Y-%m-%d'))
            end_time = time.mktime(time.strptime(end_date_str + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
            key_count = 0
            for key in choice_keys:
                user = key.split('_choices')[0]
                timestamp_key = user + '_timestamp'
                timestamp = r.get(timestamp_key)
                if timestamp:
                    timestamp = float(timestamp)
                    if start_time <= timestamp <= end_time:
                        key_count += 1
        else:
            key_count = len(choice_keys)
        return jsonify({"key_count": key_count})
    except redis.ConnectionError:
        return jsonify({"key_count": 0})  

@app.route('/admin/clear_db', methods=['POST'])
@admin_required
def admin_clear_db():
    try:
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        
        if start_date_str and end_date_str:
            start_time = time.mktime(time.strptime(start_date_str, '%Y-%m-%d'))
            end_time = time.mktime(time.strptime(end_date_str + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
            all_keys = list(r.scan_iter("*"))
            choice_keys = list(r.scan_iter("*_choices"))
            choice_users = set([key.split('_choices')[0] for key in choice_keys])
            
            for user in choice_users:
                timestamp_key = user + '_timestamp'
                timestamp = r.get(timestamp_key)
                if timestamp:
                    timestamp = float(timestamp)
                    if start_time <= timestamp <= end_time:
                        for key in all_keys:
                            if key.startswith(user):
                                r.delete(key)
            return jsonify({"message": "Data cleared from Redis for specified date range!", "status": "success"})
        else:
            r.flushall()
            return jsonify({"message": "All data cleared from Redis successfully!", "status": "success"})
    except redis.ConnectionError as e:
        return jsonify({"message": "Failed to clear Redis: {0}".format(str(e)), "status": "error"}), 500

@app.route('/admin/dump_db', methods=['POST'])
@admin_required
def admin_dump_db():
    try:
        start_date_str = request.form.get('start_date')
        end_date_str = request.form.get('end_date')
        
        all_keys = list(r.scan_iter("*"))
        choice_keys = list(r.scan_iter("*_choices"))
        choice_users = set([key.split('_choices')[0] for key in choice_keys])
        
        if start_date_str and end_date_str:
            start_time = time.mktime(time.strptime(start_date_str, '%Y-%m-%d'))
            end_time = time.mktime(time.strptime(end_date_str + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))
            relevant_users = set()
            for user in choice_users:
                timestamp_key = user + '_timestamp'
                timestamp = r.get(timestamp_key)
                if timestamp:
                    timestamp = float(timestamp)
                    if start_time <= timestamp <= end_time:
                        relevant_users.add(user)
            relevant_keys = [key for key in all_keys if any(key.startswith(user) for user in relevant_users)]
        else:
            relevant_keys = [key for key in all_keys if any(key.startswith(user) for user in choice_users)]
        
        output = StringIO.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Key', 'Value'])
        
        if not relevant_keys:
            writer.writerow(['No submission data', 'No survey submissions found in Redis'])
        else:
            for key in relevant_keys:
                user_data = r.get(key)
                if user_data:
                    user_data = user_data.decode('utf-8')
                decoded_key = key.decode('utf-8')
                writer.writerow([decoded_key, user_data])
        
        csv_content = output.getvalue()
        output.close()

        if not csv_content.strip():
            return jsonify({"message": "No data to dump", "status": "warning"}), 200

        response = send_file(
            StringIO.StringIO(csv_content),
            mimetype='text/csv',
            as_attachment=True,
            attachment_filename='redis_dump_{0}.csv'.format(time.strftime('%Y%m%d_%H%M%S'))
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response
    except redis.ConnectionError as e:
        return jsonify({"message": "Failed to dump Redis: {0}".format(str(e)), "status": "error"}), 500
