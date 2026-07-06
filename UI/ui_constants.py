"""Shared constants for the UI Flask application."""

IRL_DATA_PATH = "data/ppirl.csv"
MINDFIRL_DATA_PATH = "data/ppirl_priv.csv"
SECTION2_PATH = "data/section2_priv.csv"

# What the single DATA_PATH constant pointed to before tracks existed; used only
# to scan for pre-split submissions in the read-only "legacy" admin view.
LEGACY_DATA_PATH = "data/ppirl.csv"

TRACKS = {
    "irl_desktop": {"data_path": IRL_DATA_PATH, "label": "IRL Desktop"},
    "irl_mobile": {"data_path": IRL_DATA_PATH, "label": "IRL Mobile"},
    "mindfirl": {"data_path": MINDFIRL_DATA_PATH, "label": "MiNDFiRL"},
}

# Admin-only combined pseudo-track: "irl" reports on irl_desktop + irl_mobile
# together. Not a real submission track -- visitors always submit against one
# of the two concrete sub-tracks below.
IRL_GROUP_TRACK = "irl"
IRL_GROUP_SUB_TRACKS = ["irl_desktop", "irl_mobile"]

# Fallback for visitor-facing code (cookie-based track resolution, CSV upload
# target) which must always resolve to one concrete submission track, never
# the combined "irl" pseudo-track or "legacy".
DEFAULT_TRACK = "irl_desktop"

# Fallback for admin viewing pages (results/graph/experiments/download), which
# default to the combined IRL view.
DEFAULT_ADMIN_TRACK = IRL_GROUP_TRACK

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
