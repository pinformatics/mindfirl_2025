#! /usr/bin/python
# encoding=utf-8

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_path(filename):
    """Resolve a relative path from the UI module directory."""
    if os.path.isabs(filename):
        return filename
    return os.path.join(BASE_DIR, filename)


def load_data_from_csv(filename):
    """Load CSV rows and trim each field to at most 12 characters."""
    data = []
    with open(_resolve_path(filename), 'r') as filein:
        for line in filein:
            record = line.strip().split(',')
            new_record = [field[0:12] for field in record]
            data.append(new_record)
    return data


def load_config_settings():
    """Load key/value settings from data/settings.csv."""
    settings = {}
    with open(_resolve_path("data/settings.csv"), 'r') as settingsin:
        for line in settingsin:
            line_data = line.strip().split(',')
            settings[line_data[0]] = line_data[1]
    return settings


def save_data_to_json(filename, data):
    """Save a string payload to a JSON file path."""
    with open(filename, 'w+') as fileout:
        fileout.write(data)


def get_pair(filename, pair_num):
    """Return the two rows for a pair id from the CSV file."""
    ret = []
    with open(_resolve_path(filename), 'r') as filein:
        for line in filein:
            record = line.split(',')
            if record[0] == pair_num:
                ret.append(record)
                if len(ret) == 2:
                    break
    return ret

