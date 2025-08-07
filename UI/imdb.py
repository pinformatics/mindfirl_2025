#! /usr/bin/python
# encoding=utf-8

import data_loader as dl

"""
In Memory Database
"""

class IMDB(object):
    def __init__():
        databases = dict()

    def load_database_from_csv(file_name, database_name):
        data = dl.load_data_from_csv(file_name)
        databases[database_name] = data
