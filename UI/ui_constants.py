"""Shared constants for the UI Flask application."""

DATA_PATH = "data/ppirl.csv"
SECTION2_PATH = "data/section2.csv"

ATTRIBUTE_COLUMNS = [
    ("ID", "id_reveal_level"),
    ("First Name", "first_name_reveal_level"),
    ("Last Name", "last_name_reveal_level"),
    ("DoB(M/D/Y)", "dob_reveal_level"),
    ("Sex", "sex_reveal_level"),
    ("Race", "race_reveal_level"),
]

ADMIN_LOGIN_LOCK_SECONDS = 300
ADMIN_MAX_FAILED_ATTEMPTS = 5
