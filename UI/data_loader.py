#! /usr/bin/python
# encoding=utf-8

import json
import logging

def load_data_from_csv(filename):
    data = []
    with open(filename, 'r') as filein:
        for line in filein:
            record = line.strip().split(',')
            new_record = []
            for field in record:
                new_record.append(field[0:12])
            data.append(new_record)
    return data

def load_config_settings():
    settings = {}
    with open("data/settings.csv", 'r') as settingsin:
        for line in settingsin:
            line_data = line.strip().split(',')
            settings[line_data[0]] = line_data[1]
    return settings

def save_data_to_json(filename, data):
    fileout = open(filename, 'w+')
    fileout.write(data)
    fileout.close()


def get_pair(filename, pair_num):
    filein = open(filename, 'r')
    ret = list()
    for line in filein:
        record = line.split(',')
        if record[0] == pair_num:
            ret.append(record)
            if len(ret) == 2:
                break
    return ret

