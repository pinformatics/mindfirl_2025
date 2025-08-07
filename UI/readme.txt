# README.txt for Interactive Record Linkage Survey Application

## Overview
A Flask-based web app for Interactive Record Linkage (IRL) with two modes: PPIRL (privacy-preserving) and CDIRL (full data). Loads CSV data, tracks user choices and privacy metrics via Redis, and emails activity reports.

## Setup

### 1. Install Python 2.7
Download Python 2.7 from https://www.python.org/downloads/release/python-2718/ or use your package manager (`sudo apt-get install python2.7` on Ubuntu).

### 2. Create Virtual Environment
Install `virtualenv`:
Create and activate a Python 2.7 virtual environment:
virtualenv -p /usr/bin/python2.7 venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows


### 3. Install Dependencies
Inside the virtual environment:
pip install -r requirements.txt

### 4. Set Up Redis
Install (`sudo apt-get install redis-server`) and start Redis:
redis-server

### 5. Configure Email
Edit `app.py` with your Gmail details:
MAIL_USERNAME='your-email@gmail.com'
MAIL_PASSWORD='your-app-password'  # Generate at myaccount.google.com/security
MAIL_DEFAULT_SENDER='your-email@gmail.com'
MAIL_SENDER='your-email@gmail.com'
MAIL_RECEIVER='recipient@example.com'


### 6. Prepare Data
Place `section2.csv` and `ppirl.csv` in the `data/` directory.

### 7. Run the App
`./flask_run.sh`

### / (Root or /survey_link)
- **Purpose**: Default survey page in PPIRL mode (privacy-preserving).
- **How It Works**: Loads `data/ppirl.csv`, displays masked record pairs, tracks user choices and privacy metrics (KAPR, disclosed characters) in Redis, and renders `survey_link.html`. If no custom data was loaded, it resets to this default.

### /<filename>
- **Purpose**: Load a custom CSV file for survey.
- **How It Works**: Takes a `<filename>` (e.g., `ppirl.csv`), checks if it exists in `data/`, loads it into `DATA_PAIR_LIST`, sets mode to CDIRL (full data), and redirects to the survey page. Invalid files return a 400 error.

### /Mindfirl_<filename>.csv
- **Purpose**: Load a custom CSV file with PPIRL mode.
- **How It Works**: If `<filename>` starts with `Mindfirl_` (e.g., `Mindfirl_test.csv`), it strips the prefix, loads the file from `data/`, sets mode to PPIRL (masked data), and shows the survey. Ensures privacy-preserving display.

## Admin Features (/admin)
- **Access**: Requires password (`admin123`) via form or URL parameter.
- **Features**:
  - **View Redis Data**: `/view_all_redis_data` shows all key-value pairs.
  - **Clear Redis**: `/clear_redis` wipes all data.
  - **Submission Count**: `/admin/submission_count` (POST) counts submissions, filterable by date range.
  - **Clear DB**: `/admin/clear_db` (POST) clears all or date-filtered Redis data.
  - **Dump DB**: `/admin/dump_db` (POST) downloads a CSV of all or date-filtered Redis data.
- **Interface**: `admin.html` shows Redis status and submission stats.

## Usage
- Survey: `/` (PPIRL default).
- Custom Data: `/ppirl.csv` (CDIRL) or `/Mindfirl_test.csv` (PPIRL).
- Admin: `/admin` with password.


## Notes
- Deactivate: `deactivate`.
- Change `ADMIN_PASSWORD` in `app.py` for security.
- Vidal location : vidal/proj1/hck_pinfo/MINDFRIL-UI
